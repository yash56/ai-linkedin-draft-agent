"""Daily source-backed LinkedIn draft agent for AI and product news."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import html
import json
import logging
import os
import random
import re
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import requests
import yaml
from dateutil import parser as date_parser
from dotenv import load_dotenv


LOGGER = logging.getLogger("ai_linkedin_draft_agent")
UTC = dt.timezone.utc
DEFAULT_SOURCE_FILE = "sources.yml"
DEFAULT_FRESH_HOURS = 36
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
MIN_DRAFTS = 2
MAX_DRAFTS = 3
MAX_X_TRENDS = 8
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "ai-linkedin-draft-agent/0.4 (+source-backed LinkedIn drafts)"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
DEFAULT_HASHTAGS = (
    "#AI #ProductManagement #GenAI #ProductStrategy #AIAgents "
    "#AIProductManagement #LLMOps #TechCareers #Innovation #ProductBuilder"
)


@dataclasses.dataclass(frozen=True)
class NewsSource:
    name: str
    url: str
    category: str
    credibility: str


@dataclasses.dataclass(frozen=True)
class AgentConfig:
    topics: list[str]
    trusted_sources: list[str]
    sources: list[NewsSource]
    x_queries: list[str]


@dataclasses.dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    published_at: dt.datetime
    source_name: str
    category: str
    credibility: str
    summary: str
    source_excerpt: str = ""


@dataclasses.dataclass(frozen=True)
class TrendSignal:
    query: str
    text: str
    url: str
    created_at: dt.datetime | None
    engagement_score: int


@dataclasses.dataclass(frozen=True)
class Draft:
    topic: NewsItem
    title: str
    body: str
    source_links: list[str]
    fact_check_notes: list[str]
    risk_flags: list[str] = dataclasses.field(default_factory=list)


class AgentError(Exception):
    """Base exception for expected agent failures."""


def configure_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def load_config(path: str) -> AgentConfig:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise AgentError(f"Source file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise AgentError(f"Could not parse source file: {path}") from exc

    sources: list[NewsSource] = []
    for raw in data.get("sources", []):
        if raw.get("enabled", True) is False:
            LOGGER.info("Skipping disabled source: %s", raw.get("name", raw.get("url")))
            continue
        name = str(raw.get("name", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not name or not url:
            LOGGER.warning("Skipping source with missing name or url: %s", raw)
            continue
        sources.append(
            NewsSource(
                name=name,
                url=url,
                category=str(raw.get("category", "ai")).strip().lower(),
                credibility=str(raw.get("credibility", "news")).strip().lower(),
            )
        )
    if not sources:
        raise AgentError("No valid sources configured.")
    return AgentConfig(
        topics=[str(topic).strip() for topic in data.get("topics", []) if str(topic).strip()],
        trusted_sources=[str(source).strip() for source in data.get("trusted_sources", []) if str(source).strip()],
        sources=sources,
        x_queries=[str(query).strip() for query in data.get("x_queries", []) if str(query).strip()],
    )


def parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, tuple):
        parsed = dt.datetime(*value[:6], tzinfo=UTC)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            try:
                parsed = date_parser.parse(text)
            except (TypeError, ValueError, OverflowError):
                return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def clean_text(value: str, max_length: int = 360) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u2014", ",").replace("\u2013", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "..."
    return text


def sanitize_generated_text(value: str) -> str:
    text = clean_text(value, max_length=6000)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1", text)
    text = re.sub(r"(?m)^\s*\*\s+", "- ", text)
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    return text.strip()


def meaningful_tokens(text: str) -> set[str]:
    stopwords = {
        "about", "after", "again", "also", "another", "because", "before", "being", "between",
        "could", "does", "from", "have", "into", "itself", "just", "like", "more", "most",
        "only", "over", "should", "that", "their", "there", "these", "this", "those", "through",
        "what", "when", "where", "which", "while", "with", "would", "your",
    }
    tokens = set(re.findall(r"[a-zA-Z][a-zA-Z0-9']{2,}", text.lower()))
    return {token for token in tokens if token not in stopwords}


def source_pack_text(item: NewsItem) -> str:
    return " ".join(
        [
            item.title,
            item.url,
            item.source_name,
            item.published_at.strftime("%Y-%m-%d %H:%M UTC"),
            item.category,
            item.credibility,
            item.summary,
            item.source_excerpt,
        ]
    ).lower()


def is_factual_claim(claim: str) -> bool:
    lowered = claim.lower()
    if any(marker in lowered for marker in ["pm takeaway", "strong opinion", "question for", "my read"]):
        return False
    patterns = [
        r"\b(announced|launched|released|published|reported|named|recognized|introduced|created|built|supports|enables|uses|includes|confirmed|cited|runs on|powered by|testing|rolling out|available|preview|beta|demo|promises|designed to|deepfaking|trial|successfully)\b",
        r"\b\d+[\w%$]*\b",
        r"\b(zero|near-total|complete|major|mission-critical|technical debt|hard deadline|anything-to-anything|object retention|synthetic media|professional-grade|high-quality|highest-paying)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def claim_supported_by_source(claim: str, item: NewsItem) -> bool:
    if not is_factual_claim(claim):
        return True
    tokens = meaningful_tokens(claim)
    if not tokens:
        return True
    source_text = source_pack_text(item)
    supported = {token for token in tokens if token in source_text}
    return len(supported) / len(tokens) >= 0.45


def fact_check_text(text: str, item: NewsItem, section: str) -> tuple[str, list[str]]:
    kept_lines: list[str] = []
    flags: list[str] = []
    for line in text.splitlines():
        chunks = [chunk.strip() for chunk in re.split(r"(?<=[.!?])\s+", line) if chunk.strip()]
        if not chunks:
            kept_lines.append(line)
            continue
        kept_chunks = []
        for chunk in chunks:
            if claim_supported_by_source(chunk, item):
                kept_chunks.append(chunk)
            else:
                flags.append(f"{section}: removed weak claim: {chunk}")
        if kept_chunks:
            kept_lines.append(" ".join(kept_chunks))
    return "\n".join(kept_lines).strip(), flags


def fact_check_draft(draft: Draft) -> Draft:
    title, title_flags = fact_check_text(draft.title, draft.topic, "Title")
    body, body_flags = fact_check_text(draft.body, draft.topic, "Body")
    flags = title_flags + body_flags
    if not title:
        title = "The Product Question Behind This"
        flags.append("Title replaced after unsupported claims were removed.")
    if not body:
        body = (
            f"{draft.topic.source_name} published this on "
            f"{draft.topic.published_at.strftime('%Y-%m-%d %H:%M UTC')}.\n\n"
            "The generated body contained weak factual claims, so the draft was reduced to verified source metadata."
        )
        flags.append("Body replaced after unsupported claims were removed.")
    notes = list(draft.fact_check_notes)
    if flags:
        notes.append("Automated claim audit removed weak claims before Slack delivery.")
        LOGGER.warning("Claim audit adjusted %s claim(s) for %s", len(flags), draft.topic.url)
    else:
        notes.append("Automated claim audit found no weak factual claims.")
    return Draft(
        topic=draft.topic,
        title=sanitize_generated_text(title),
        body=sanitize_generated_text(ensure_hashtags(body)),
        source_links=draft.source_links,
        fact_check_notes=dedupe_text(notes),
        risk_flags=dedupe_text(flags),
    )


def fetch_feed(source: NewsSource) -> list[dict[str, Any]]:
    LOGGER.info("Fetching source: %s", source.name)
    try:
        response = requests.get(source.url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning("Failed to fetch %s: %s", source.name, exc)
        return []
    parsed = feedparser.parse(response.content)
    if parsed.bozo:
        LOGGER.warning("Feed parse warning for %s: %s", source.name, parsed.bozo_exception)
    return list(parsed.entries)


def is_fresh(published_at: dt.datetime, now: dt.datetime, fresh_hours: int) -> bool:
    return dt.timedelta(0) <= now - published_at <= dt.timedelta(hours=fresh_hours)


def collect_news(config: AgentConfig, fresh_hours: int, now: dt.datetime) -> list[NewsItem]:
    items: list[NewsItem] = []
    for source in config.sources:
        for entry in fetch_feed(source):
            title = clean_text(str(entry.get("title", "")), max_length=180)
            url = str(entry.get("link", "")).strip()
            published_at = parse_datetime(
                entry.get("published")
                or entry.get("updated")
                or entry.get("created")
                or entry.get("published_parsed")
                or entry.get("updated_parsed")
            )
            if not title or not url or not published_at:
                LOGGER.debug("Rejecting item missing title, url, or published date from %s", source.name)
                continue
            if not is_fresh(published_at, now, fresh_hours):
                LOGGER.debug("Rejecting stale item: %s", title)
                continue
            item = NewsItem(
                title=title,
                url=url,
                published_at=published_at,
                source_name=source.name,
                category=source.category,
                credibility=source.credibility,
                summary=clean_text(str(entry.get("summary") or entry.get("description") or ""), max_length=520),
            )
            if matches_topics(item, config.topics):
                items.append(item)
    return dedupe_items(items)


def fetch_article_context(url: str) -> str:
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning("Could not fetch article context for %s: %s", url, exc)
        return ""
    html_text = response.text
    meta_values = re.findall(
        r'<meta[^>]+(?:name|property)=["\'](?:description|og:description|twitter:description)["\'][^>]+content=["\']([^"\']+)["\']',
        html_text,
        flags=re.I,
    )
    paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html_text, flags=re.I | re.S)
    paragraph_text = " ".join(clean_text(paragraph, max_length=500) for paragraph in paragraphs[:8])
    context = " ".join(meta_values + [paragraph_text])
    return clean_text(context, max_length=1600)


def enrich_items_with_article_context(items: list[NewsItem]) -> list[NewsItem]:
    enriched: list[NewsItem] = []
    for item in items:
        excerpt = fetch_article_context(item.url)
        enriched.append(dataclasses.replace(item, source_excerpt=excerpt))
    return enriched


def matches_topics(item: NewsItem, topics: list[str]) -> bool:
    if not topics:
        return True
    text = f"{item.title} {item.summary} {item.source_name} {item.category}".lower()
    return any(topic.lower() in text for topic in topics)


def jaccard(first: set[str], second: set[str]) -> float:
    if not first or not second:
        return 0
    return len(first.intersection(second)) / len(first.union(second))


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        key = re.sub(r"[?#].*$", "", item.url.lower()).rstrip("/") or item.title.lower()
        if key in seen:
            continue
        item_tokens = meaningful_tokens(item.title)
        if any(jaccard(item_tokens, meaningful_tokens(existing.title)) > 0.72 for existing in deduped):
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def collect_x_trends(config: AgentConfig) -> list[TrendSignal]:
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if not token:
        LOGGER.info("X_BEARER_TOKEN is not set. Skipping X trend context.")
        return []
    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    trends: list[TrendSignal] = []
    for query in config.x_queries[:6]:
        params = {"query": f'"{query}" lang:en -is:retweet', "max_results": "10", "tweet.fields": "created_at,public_metrics"}
        try:
            response = requests.get(X_RECENT_SEARCH_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            if response.status_code in {401, 403, 429}:
                LOGGER.warning("X trend search skipped for %s: HTTP %s", query, response.status_code)
                continue
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            LOGGER.warning("X trend search failed for %s: %s", query, exc)
            continue
        except ValueError:
            LOGGER.warning("X trend search returned non-JSON response for %s", query)
            continue
        for tweet in payload.get("data", []):
            tweet_id = str(tweet.get("id", "")).strip()
            text = clean_text(str(tweet.get("text", "")), max_length=220)
            if not tweet_id or not text:
                continue
            metrics = tweet.get("public_metrics") or {}
            engagement = sum(int(metrics.get(name, 0) or 0) for name in ("like_count", "retweet_count", "reply_count", "quote_count"))
            trends.append(TrendSignal(query=query, text=text, url=f"https://x.com/i/web/status/{tweet_id}", created_at=parse_datetime(tweet.get("created_at")), engagement_score=engagement))
    trends.sort(key=lambda trend: trend.engagement_score, reverse=True)
    LOGGER.info("Collected %s X trend signal(s).", len(trends[:MAX_X_TRENDS]))
    return trends[:MAX_X_TRENDS]


def trend_bonus(item: NewsItem, trends: list[TrendSignal]) -> float:
    item_tokens = meaningful_tokens(f"{item.title} {item.summary} {item.source_name} {item.category}")
    bonus = 0.0
    for trend in trends:
        if item_tokens.intersection(meaningful_tokens(f"{trend.query} {trend.text}")):
            bonus += min(12, 4 + (trend.engagement_score / 100))
    return min(bonus, 24)


def score_item(item: NewsItem, now: dt.datetime, config: AgentConfig, trends: list[TrendSignal]) -> float:
    age_hours = max((now - item.published_at).total_seconds() / 3600, 0)
    credibility_bonus = {"official": 40, "primary": 35, "credible": 20, "news": 15}.get(item.credibility, 10)
    trusted_bonus = 10 if any(source.lower() in item.source_name.lower() for source in config.trusted_sources) else 0
    category_bonus = 8 if item.category in {"ai", "product", "product-management"} else 0
    searchable = f"{item.title} {item.summary} {item.source_excerpt}".lower()
    topic_bonus = min(sum(topic.lower() in searchable for topic in config.topics) * 4, 16)
    actionability_bonus = 18 if re.search(r"\b(agent|model|launch|developer|coding|product|api|builder|enterprise|workflow|eval|evaluation|gemini|claude|chatgpt|openai)\b", searchable) else 0
    low_signal_penalty = -28 if re.search(r"\b(partner|partnership|journalism|media deal|award|recognized|named a leader|magic quadrant)\b", searchable) else 0
    freshness_bonus = max(0, 48 - age_hours)
    return credibility_bonus + trusted_bonus + category_bonus + topic_bonus + actionability_bonus + low_signal_penalty + freshness_bonus + trend_bonus(item, trends)


def rank_items(items: list[NewsItem], now: dt.datetime, config: AgentConfig, trends: list[TrendSignal]) -> list[NewsItem]:
    ranked = sorted(items, key=lambda item: (score_item(item, now, config, trends), item.published_at), reverse=True)
    diverse: list[NewsItem] = []
    for item in ranked:
        item_tokens = meaningful_tokens(f"{item.title} {item.summary}")
        if any(jaccard(item_tokens, meaningful_tokens(f"{existing.title} {existing.summary}")) > 0.58 for existing in diverse):
            continue
        diverse.append(item)
        if len(diverse) == MAX_DRAFTS:
            break
    return diverse


def trend_context_for_item(item: NewsItem, trends: list[TrendSignal]) -> str:
    item_tokens = meaningful_tokens(f"{item.title} {item.summary} {item.source_name} {item.category}")
    matches = [trend for trend in trends if item_tokens.intersection(meaningful_tokens(f"{trend.query} {trend.text}"))]
    if not matches:
        return "No X trend context was available."
    lines = []
    for trend in matches[:3]:
        date_text = trend.created_at.strftime("%Y-%m-%d %H:%M UTC") if trend.created_at else "date unavailable"
        lines.append(f"- X angle signal for {trend.query} ({date_text}, engagement {trend.engagement_score}): {clean_text(trend.text, max_length=180)} Source: {trend.url}")
    return "\n".join(lines)


def style_for_day(now: dt.datetime) -> str:
    styles = ["Role shift narrative", "Why this fails breakdown", "Builder playbook", "Launch lesson", "Uncomfortable PM truth", "Trend-to-takeaway essay"]
    random.seed(now.strftime("%Y-%m-%d"))
    return random.choice(styles)


def write_draft(item: NewsItem, style: str, api_key: str, model: str, trend_context: str) -> Draft:
    if not api_key:
        if os.getenv("ALLOW_TEMPLATE_FALLBACK", "").lower() == "true":
            return polish_draft(write_template_draft(item))
        raise AgentError("GEMINI_API_KEY is required for publishable LinkedIn drafts.")
    try:
        return polish_draft(write_gemini_draft(item, style, api_key, model, trend_context))
    except AgentError as exc:
        if os.getenv("ALLOW_TEMPLATE_FALLBACK", "").lower() == "true":
            LOGGER.warning("Gemini draft failed for %s. Falling back to template: %s", item.title, exc)
            return polish_draft(write_template_draft(item))
        raise AgentError(f"Gemini draft failed for {item.title}: {exc}") from exc


def write_gemini_draft(item: NewsItem, style: str, api_key: str, model: str, trend_context: str) -> Draft:
    payload = {"contents": [{"role": "user", "parts": [{"text": build_gemini_prompt(item, style, trend_context)}]}], "generationConfig": {"temperature": 0.74, "responseMimeType": "application/json"}}
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "x-goog-api-key": api_key}
    try:
        response = requests.post(GEMINI_API_URL.format(model=model), headers=headers, json=payload, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        response_payload = response.json()
    except requests.RequestException as exc:
        raise AgentError(f"Gemini API request failed: {exc}") from exc
    except ValueError as exc:
        raise AgentError("Gemini API returned non-JSON response.") from exc
    draft_data = parse_gemini_json(extract_gemini_text(response_payload))
    notes = draft_data.get("fact_check_notes", [])
    if not isinstance(notes, list):
        raise AgentError("Gemini response field fact_check_notes must be a list.")
    return Draft(
        topic=item,
        title=sanitize_generated_text(require_text_field(draft_data, "title")),
        body=sanitize_generated_text(require_text_field(draft_data, "body")),
        source_links=[item.url],
        fact_check_notes=dedupe_text([clean_text(str(note), max_length=240) for note in notes if str(note).strip()] + [f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.", "Gemini generated wording only from supplied source metadata and summary.", "X trend context was used only for angle selection, not factual claims."]),
    )


def build_gemini_prompt(item: NewsItem, style: str, trend_context: str) -> str:
    published_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "No usable RSS summary was provided."
    return f"""
You are writing one LinkedIn post like a sharp Product Manager who tracks AI deeply.

Target style:
- Similar quality to posts titled "The Shift to the AI PM" or "Why AI Agents Actually Fail".
- A short title, then a punchy opening that contrasts what people assume with what is actually changing.
- Short, human paragraphs.
- 3 to 4 practical sections with a small emoji plus a crisp label, for example Building, Testing, Data Quality, Evaluation Gaps.
- A strong ending that leaves the reader with a useful PM takeaway.
- Relevant hashtags at the end.

Hard rules:
- Use only the source metadata below for facts.
- Every factual claim must be traceable to the source metadata.
- No hallucinations.
- No fake benchmarks, product capabilities, quotes, customer names, funding amounts, timelines, roles, salaries, or product claims.
- Do not browse or rely on memory.
- Release/status wording must match the source metadata exactly.
- If the source says rolling out, do not call it testing.
- If the source says preview, beta, demo, or testing, do not call it released.
- If availability is unclear, say less instead of guessing.
- Do not use em dashes.
- Avoid corporate fluff, generic AI hype, and complex insider jargon.
- Length: 260 to 430 words.
- Write for PMs, founders, and AI builders, but make it understandable for smart non-experts.
- Do not start with the source name, report title, company name, or publication date.
- If the source metadata is thin, make the sections practical PM lenses instead of fake specifics.
- End with 8 to 12 relevant hashtags.
- Output valid JSON only with keys: title, body, fact_check_notes.

Required post archetype: {style}

Source metadata:
Title: {item.title}
Source: {item.source_name}
Published at: {published_text}
Category: {item.category}
Credibility: {item.credibility}
URL: {item.url}
RSS summary: {summary}
Article excerpt:
{item.source_excerpt or "No article excerpt was available."}

Optional X trend context:
{trend_context}

Rules for X trend context:
- Use X only to choose a reader-friendly angle, hook, question, or archetype.
- Do not treat X posts as factual sources.
- Do not copy claims, numbers, product status, quotes, customer names, or examples from X unless the source metadata also supports them.
- If X conflicts with the source metadata, ignore X and trust the source metadata.
""".strip()


def write_template_draft(item: NewsItem) -> Draft:
    date_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "The RSS item did not include a usable summary."
    body = ("The headline is interesting.\n" "The product question underneath it is more useful.\n\n" f"{item.source_name} published this on {date_text}.\n\n" f"{summary}\n\n" "\U0001F5A5\ufe0f Building\n" "Ask what real workflow this could change, not just whether the announcement sounds impressive.\n\n" "\U0001F9EA Testing\n" "Look for proof that the product can be evaluated, trusted and improved after launch.\n\n" "\U0001F9ED PM Taste\n" "The useful question is what decision becomes easier for the user.\n\n" "\u2699\ufe0f What builders should watch\n" "Do not copy the headline. Look for the customer pain the headline points toward.\n\n" "PM takeaway: the best AI posts explain user impact before they try to sound visionary.\n\n" f"{DEFAULT_HASHTAGS}")
    return Draft(topic=item, title="The Product Question Behind This", body=body, source_links=[item.url], fact_check_notes=[f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.", "Template writer added only generic PM analysis around the source metadata."])


def polish_draft(draft: Draft) -> Draft:
    title = draft.title.strip()
    if len(re.findall(r"\b\w+\b", title)) > 12:
        title = "The Product Question Behind This"
    body = draft.body
    if not re.search(r"(?m)^[^\n]{0,4}(Building|Testing|Data Quality|Evaluation|PM Taste|What builders)", body, re.I):
        body = f"{body.strip()}\n\n\U0001F5A5\ufe0f Building\nWhat user workflow changes?\n\n\U0001F9EA Testing\nWhat proof would make this trustworthy?\n\n\U0001F9ED PM Taste\nWhat would a real user do differently?"
    return Draft(topic=draft.topic, title=sanitize_generated_text(title), body=sanitize_generated_text(ensure_hashtags(body)), source_links=draft.source_links, fact_check_notes=draft.fact_check_notes, risk_flags=draft.risk_flags)


def ensure_hashtags(text: str) -> str:
    if "#" in text:
        return text
    return f"{text.strip()}\n\n{DEFAULT_HASHTAGS}"


def extract_gemini_text(payload: dict[str, Any]) -> str:
    try:
        parts = payload["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AgentError("Gemini response did not include candidate text.") from exc
    text = "".join(str(part.get("text", "")) for part in parts if isinstance(part, dict))
    if not text.strip():
        raise AgentError("Gemini response text was empty.")
    return text.strip()


def parse_gemini_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AgentError("Gemini response was not valid JSON.") from exc
    if not isinstance(value, dict):
        raise AgentError("Gemini response JSON must be an object.")
    return value


def require_text_field(data: dict[str, Any], field_name: str) -> str:
    value = str(data.get(field_name, "")).strip()
    if not value:
        raise AgentError(f"Gemini response missing required field: {field_name}")
    return value


def dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = value.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(value)
    return deduped


def format_draft(draft: Draft, index: int) -> str:
    sources = "\n".join(f"- {link}" for link in draft.source_links)
    return f"Draft {index}:\n{draft.title}\n\n{draft.body}\n\nSources:\n{sources}\n"


def build_slack_message(drafts: list[Draft], now: dt.datetime) -> str:
    date_text = now.strftime("%Y-%m-%d")
    if not drafts:
        return f"Daily AI + PM LinkedIn Drafts\nDate: {date_text}\n\nNo qualifying source-backed AI/Product news items were found in the freshness window."
    parts = [f"Daily AI + PM LinkedIn Drafts\nDate: {date_text}"]
    parts.extend(format_draft(draft, index) for index, draft in enumerate(drafts, start=1))
    return "\n\n".join(parts)


def send_to_slack(message: str, webhook_url: str) -> None:
    try:
        response = requests.post(webhook_url, json={"text": message}, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AgentError(f"Slack webhook send failed: {exc}") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate source-backed LinkedIn drafts from fresh news.")
    parser.add_argument("--sources", default=DEFAULT_SOURCE_FILE, help="Path to sources.yml.")
    parser.add_argument("--fresh-hours", type=int, default=int(os.getenv("FRESH_HOURS", DEFAULT_FRESH_HOURS)))
    parser.add_argument("--dry-run", action="store_true", help="Print output instead of sending to Slack.")
    return parser.parse_args()


def run() -> int:
    load_dotenv()
    configure_logging()
    args = parse_args()
    now = dt.datetime.now(UTC)
    if args.fresh_hours < 24 or args.fresh_hours > 48:
        raise AgentError("Freshness window must be between 24 and 48 hours.")
    config = load_config(args.sources)
    items = collect_news(config, args.fresh_hours, now)
    LOGGER.info("Collected %s qualifying fresh item(s).", len(items))
    trends = collect_x_trends(config)
    items = enrich_items_with_article_context(items)
    selected = rank_items(items, now, config, trends)
    if len(selected) < MIN_DRAFTS:
        LOGGER.warning("Only %s qualifying item(s) found. The agent will not invent filler topics.", len(selected))
    style = style_for_day(now)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    LOGGER.info("Draft style for today: %s", style)
    LOGGER.info("Draft model: %s", model if api_key else "template fallback disabled")
    drafts: list[Draft] = []
    for item in selected:
        try:
            drafts.append(fact_check_draft(write_draft(item, style, api_key, model, trend_context_for_item(item, trends))))
        except AgentError as exc:
            LOGGER.error("Skipping draft for %s: %s", item.title, exc)
    if len(drafts) < MIN_DRAFTS:
        raise AgentError(f"Only {len(drafts)} publishable draft(s) were generated. Refusing to send generic fallback content.")
    message = build_slack_message(drafts, now)
    if args.dry_run:
        print(message)
        return 0
    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        raise AgentError("SLACK_WEBHOOK_URL is required unless --dry-run is used.")
    send_to_slack(message, webhook_url)
    LOGGER.info("Sent %s draft(s) to Slack.", len(drafts))
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except AgentError as exc:
        LOGGER.error("%s", exc)
        raise SystemExit(1)
    except Exception:
        LOGGER.exception("Unexpected failure.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

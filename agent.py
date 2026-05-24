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
DEFAULT_FRESH_HOURS = 48
DEFAULT_GEMINI_MODEL = "gemini-3-flash-preview"
MIN_DRAFTS = 2
MAX_DRAFTS = 3
MAX_X_TRENDS = 8
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "ai-linkedin-draft-agent/0.2 (+source-backed LinkedIn drafts)"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
X_RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


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


@dataclasses.dataclass(frozen=True)
class Draft:
    topic: NewsItem
    hook: str
    body: str
    ending: str
    source_links: list[str]
    fact_check_notes: list[str]
    risk_flags: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class TrendSignal:
    query: str
    text: str
    url: str
    created_at: dt.datetime | None
    engagement_score: int


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


def clean_text(value: str, max_length: int = 320) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("\u2014", ",").replace("\u2013", "-")
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "..."
    return text


def sanitize_generated_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u2014", ",").replace("\u2013", "-")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"\1:", text)
    text = re.sub(r"(?m)^\s*\*\s+", "- ", text)
    text = re.sub(r"\*([^*\n]+)\*", r"\1", text)
    text = re.sub(r"(?m)^([A-Za-z][A-Za-z\s]{2,40}):{2,}\s*$", r"\1:", text)
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
        ]
    ).lower()


def is_factual_claim(claim: str) -> bool:
    lowered = claim.lower()
    opinion_markers = [
        "i think", "my read", "my opinion", "pm takeaway", "the useful question",
        "this matters", "worth watching", "question for", "strong opinion",
    ]
    if any(marker in lowered for marker in opinion_markers):
        return False

    claim_patterns = [
        r"\b(announced|launched|released|published|reported|named|recognized|introduced|created|built|supports|enables|uses|includes|confirmed|cited|runs on|powered by|used|leveraged|accelerated|reached|achieved|delivered|proved|testing|rolling out|available|preview|beta|demo|promises|designed to|deepfaking|trial|successfully)\b",
        r"\b\d+[\w%$]*\b",
        r"\b(zero|near-total|complete|major|mission-critical|without sacrificing|technical debt|hard deadline|anything-to-anything|object retention|synthetic media|professional-grade|high-quality|manipulate)\b",
        r"\b(according to|source summary|published this|rss item)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in claim_patterns)


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
    risk_flags: list[str] = []

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
                risk_flags.append(f"{section}: removed weak claim: {chunk}")

        if kept_chunks:
            kept_lines.append(" ".join(kept_chunks))

    return "\n".join(kept_lines).strip(), risk_flags


def fact_check_draft(draft: Draft) -> Draft:
    hook, hook_flags = fact_check_text(draft.hook, draft.topic, "Hook")
    body, body_flags = fact_check_text(draft.body, draft.topic, "Body")
    ending, ending_flags = fact_check_text(draft.ending, draft.topic, "Ending")
    flags = hook_flags + body_flags + ending_flags

    if not hook:
        hook = "A useful AI update is boring until you ask what workflow it changes."
        flags.append("Hook replaced after unsupported claims were removed.")

    if not body:
        body = (
            f"{draft.topic.source_name} published this on "
            f"{draft.topic.published_at.strftime('%Y-%m-%d %H:%M UTC')}.\n\n"
            "The generated body contained weak factual claims, so the draft was reduced to verified source metadata."
        )
        flags.append("Body replaced after unsupported claims were removed.")

    if not ending:
        ending = "PM takeaway: keep the post narrower when the source pack is thin."
        flags.append("Ending replaced after unsupported claims were removed.")

    notes = list(draft.fact_check_notes)
    if flags:
        notes.append("Automated claim audit removed weak claims before Slack delivery.")
        LOGGER.warning("Claim audit adjusted %s claim(s) for %s", len(flags), draft.topic.url)
    else:
        notes.append("Automated claim audit found no weak factual claims.")

    return Draft(
        topic=draft.topic,
        hook=sanitize_generated_text(hook),
        body=sanitize_generated_text(body),
        ending=sanitize_generated_text(ending),
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
                summary=clean_text(str(entry.get("summary") or entry.get("description") or ""), max_length=480),
            )
            if matches_topics(item, config.topics):
                items.append(item)

    return dedupe_items(items)


def matches_topics(item: NewsItem, topics: list[str]) -> bool:
    if not topics:
        return True
    text = f"{item.title} {item.summary} {item.source_name} {item.category}".lower()
    return any(topic.lower() in text for topic in topics)


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        key = re.sub(r"[?#].*$", "", item.url.lower()).rstrip("/") or item.title.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def is_trusted_source(item: NewsItem, trusted_sources: list[str]) -> bool:
    source_name = item.source_name.lower()
    return any(trusted.lower() in source_name or source_name in trusted.lower() for trusted in trusted_sources)


def score_item(item: NewsItem, now: dt.datetime, config: AgentConfig) -> float:
    age_hours = max((now - item.published_at).total_seconds() / 3600, 0)
    credibility_bonus = {"official": 40, "primary": 35, "credible": 20, "news": 15}.get(item.credibility, 10)
    trusted_bonus = 10 if is_trusted_source(item, config.trusted_sources) else 0
    category_bonus = 8 if item.category in {"ai", "product", "product-management"} else 0
    topic_bonus = min(sum(topic.lower() in f"{item.title} {item.summary}".lower() for topic in config.topics) * 4, 16)
    freshness_bonus = max(0, 48 - age_hours)
    return credibility_bonus + trusted_bonus + category_bonus + topic_bonus + freshness_bonus


def rank_items(items: list[NewsItem], now: dt.datetime, config: AgentConfig) -> list[NewsItem]:
    ranked = sorted(items, key=lambda item: (score_item(item, now, config), item.published_at), reverse=True)
    return ranked[:MAX_DRAFTS]


def collect_x_trends(config: AgentConfig) -> list[TrendSignal]:
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if not token:
        LOGGER.info("X_BEARER_TOKEN is not set. Skipping X trend context.")
        return []
    if not config.x_queries:
        LOGGER.info("No x_queries configured. Skipping X trend context.")
        return []

    headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT}
    trends: list[TrendSignal] = []

    for query in config.x_queries[:6]:
        params = {
            "query": f'"{query}" lang:en -is:retweet',
            "max_results": "10",
            "tweet.fields": "created_at,public_metrics",
        }
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
            engagement = sum(
                int(metrics.get(name, 0) or 0)
                for name in ("like_count", "retweet_count", "reply_count", "quote_count")
            )
            trends.append(
                TrendSignal(
                    query=query,
                    text=text,
                    url=f"https://x.com/i/web/status/{tweet_id}",
                    created_at=parse_datetime(tweet.get("created_at")),
                    engagement_score=engagement,
                )
            )

    trends.sort(key=lambda trend: trend.engagement_score, reverse=True)
    LOGGER.info("Collected %s X trend signal(s).", len(trends[:MAX_X_TRENDS]))
    return trends[:MAX_X_TRENDS]


def trend_context_for_item(item: NewsItem, trends: list[TrendSignal]) -> str:
    item_tokens = meaningful_tokens(f"{item.title} {item.summary} {item.source_name} {item.category}")
    matches = [
        trend
        for trend in trends
        if item_tokens.intersection(meaningful_tokens(f"{trend.query} {trend.text}"))
    ]
    if not matches:
        return "No X trend context was available."

    lines = []
    for trend in matches[:3]:
        date_text = trend.created_at.strftime("%Y-%m-%d %H:%M UTC") if trend.created_at else "date unavailable"
        lines.append(
            f"- X angle signal for {trend.query} ({date_text}, engagement {trend.engagement_score}): "
            f"{clean_text(trend.text, max_length=180)} Source: {trend.url}"
        )
    return "\n".join(lines)


def style_for_day(now: dt.datetime) -> str:
    styles = [
        "Product teardown",
        "Launch analysis",
        "Founder/investor signal",
        "PM lesson",
        "Slightly sarcastic industry observation",
        "What this means for builders breakdown",
    ]
    random.seed(now.strftime("%Y-%m-%d"))
    return random.choice(styles)


def write_draft(item: NewsItem, style: str, api_key: str, model: str, trend_context: str) -> Draft:
    if api_key:
        try:
            return polish_draft(write_gemini_draft(item, style, api_key, model, trend_context), style)
        except AgentError as exc:
            LOGGER.warning("Gemini draft failed for %s. Falling back to template: %s", item.title, exc)
    return polish_draft(write_template_draft(item, style), style)


def write_gemini_draft(item: NewsItem, style: str, api_key: str, model: str, trend_context: str) -> Draft:
    payload = {
        "contents": [{"role": "user", "parts": [{"text": build_gemini_prompt(item, style, trend_context)}]}],
        "generationConfig": {"temperature": 0.72, "responseMimeType": "application/json"},
    }
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "x-goog-api-key": api_key}

    try:
        response = requests.post(
            GEMINI_API_URL.format(model=model),
            headers=headers,
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        response_payload = response.json()
    except requests.RequestException as exc:
        raise AgentError(f"Gemini API request failed: {exc}") from exc
    except ValueError as exc:
        raise AgentError("Gemini API returned non-JSON response.") from exc

    draft_data = parse_gemini_json(extract_gemini_text(response_payload))
    fact_notes = draft_data.get("fact_check_notes", [])
    if not isinstance(fact_notes, list):
        raise AgentError("Gemini response field fact_check_notes must be a list.")

    return Draft(
        topic=item,
        hook=sanitize_generated_text(require_text_field(draft_data, "hook")),
        body=sanitize_generated_text(require_text_field(draft_data, "body")),
        ending=sanitize_generated_text(require_text_field(draft_data, "ending")),
        source_links=[item.url],
        fact_check_notes=dedupe_text(
            [clean_text(str(note), max_length=240) for note in fact_notes if str(note).strip()]
            + [
                f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.",
                "Gemini generated wording only from supplied source metadata and summary.",
                "X trend context was used only for angle selection, not factual claims.",
            ]
        ),
    )


def build_gemini_prompt(item: NewsItem, style: str, trend_context: str) -> str:
    published_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "No usable RSS summary was provided."
    return f"""
You are writing one LinkedIn draft like a sharp Product Manager who tracks AI deeply.

Hard rules:
- Use only the source metadata below for facts.
- Every factual claim must be traceable to the source metadata.
- No hallucinations.
- No fake benchmarks, product capabilities, quotes, customer names, funding amounts, timelines, or product claims.
- Do not browse or rely on memory.
- Release/status wording must match the source metadata exactly.
- If the source says rolling out, do not call it testing.
- If the source says preview, beta, demo, or testing, do not call it released.
- If availability is unclear, say the source does not make availability clear instead of guessing.
- Do not use words like promises, proves, deepfaking, trial, successfully, professional-grade, object retention, synthetic media, or anything-to-anything unless those exact ideas are in the source metadata.
- Do not use em dashes.
- Do not use markdown bold or italic formatting.
- Keep the post readable and human.
- Use short paragraphs.
- Avoid corporate fluff and generic AI hype.
- Voice: sharp Product Manager who tracks AI deeply.
- Length: 180 to 300 words total across hook, body, and ending.
- Write for PMs, founders, and AI builders, but make it understandable for smart non-experts.
- Start with a human hook that creates curiosity, not a report summary.
- The hook must be 10 to 22 words.
- Do not start the hook with the source name, report title, company name, or publication date.
- Explain what happened in plain English using only the source metadata.
- If this is a launch or product update, state the availability/status plainly and only if supported.
- Explain why it matters for product teams, builders, or AI adoption.
- Include 2 short reader-friendly section labels.
- Include at least one bullet list with 3 bullets.
- Use section labels like What happened, Why PMs should care, What builders should watch, PM takeaway.
- Use bullets for helpful reader lenses, not vague strategy slogans.
- Avoid dense paragraphs longer than 2 sentences.
- Required style: {style}
- End with a strong opinion, question, or PM takeaway.
- Output valid JSON only with keys: hook, body, ending, fact_check_notes.

Source metadata:
Title: {item.title}
Source: {item.source_name}
Published at: {published_text}
Category: {item.category}
Credibility: {item.credibility}
URL: {item.url}
RSS summary: {summary}

Optional X trend context:
{trend_context}

Rules for X trend context:
- Use X only to choose a reader-friendly angle, hook, or question.
- Do not treat X posts as factual sources.
- Do not copy claims, numbers, product status, quotes, customer names, or examples from X unless the source metadata also supports them.
- If X conflicts with the source metadata, ignore X and trust the source metadata.

Quality bar:
- A non-technical reader should be able to summarize the point in one sentence.
- If the source metadata is thin, write a narrower post instead of filling gaps.
- Do not turn one news item into a broad industry conclusion unless the source metadata supports it.
- The post should feel skimmable on mobile.
- The middle should give the reader 2 to 3 concrete lenses, not generic commentary.
""".strip()


def write_template_draft(item: NewsItem, style: str) -> Draft:
    date_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "The RSS item did not include a usable summary."
    hook = "The headline is interesting. The product question underneath it is more useful."
    body = (
        f"What happened:\n{item.source_name} published this on {date_text}.\n\n"
        f"{summary}\n\n"
        "Why PMs should care:\n"
        "- What user workflow is affected?\n"
        "- What decision becomes easier?\n"
        "- What proof is still missing?\n\n"
        "What builders should watch:\n"
        "Do not copy the headline. Look for the customer pain the headline points toward."
    )
    ending = "PM takeaway: the best AI posts explain user impact before they try to sound visionary."
    return Draft(
        topic=item,
        hook=hook,
        body=body,
        ending=ending,
        source_links=[item.url],
        fact_check_notes=[
            f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.",
            "Template writer added only generic PM analysis around the source metadata.",
        ],
    )


def polish_draft(draft: Draft, style: str) -> Draft:
    hook = draft.hook
    if len(re.findall(r"\b\w+\b", hook)) > 24 or hook.lower().startswith(("gartner just released", "the latest report")):
        hook = "The headline is interesting. The product question underneath it is more useful."

    body = draft.body
    if not re.search(r"(?m)^\s*(?:-|\d+\.)\s+", body):
        body = (
            f"{body.strip()}\n\n"
            "Why PMs should care:\n"
            "- What user workflow changes?\n"
            "- What adoption friction remains?\n"
            "- What would a real user do differently?"
        )

    return Draft(
        topic=draft.topic,
        hook=sanitize_generated_text(hook),
        body=sanitize_generated_text(body),
        ending=sanitize_generated_text(draft.ending),
        source_links=draft.source_links,
        fact_check_notes=draft.fact_check_notes,
        risk_flags=draft.risk_flags,
    )


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
    post = "\n\n".join(part.strip() for part in [draft.hook, draft.body, draft.ending] if part.strip())
    sources = "\n".join(f"- {link}" for link in draft.source_links)
    return f"Draft {index}:\n{post}\n\nSources:\n{sources}\n"


def build_slack_message(drafts: list[Draft], now: dt.datetime) -> str:
    date_text = now.strftime("%Y-%m-%d")
    if not drafts:
        return (
            f"Daily AI + PM LinkedIn Drafts\nDate: {date_text}\n\n"
            "No qualifying source-backed AI/Product news items were found in the freshness window."
        )
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

    selected = rank_items(items, now, config)
    if len(selected) < MIN_DRAFTS:
        LOGGER.warning("Only %s qualifying item(s) found. The agent will not invent filler topics.", len(selected))

    trends = collect_x_trends(config)
    style = style_for_day(now)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    LOGGER.info("Draft style for today: %s", style)
    LOGGER.info("Draft model: %s", model if api_key else "template fallback")

    drafts = [
        fact_check_draft(write_draft(item, style, api_key, model, trend_context_for_item(item, trends)))
        for item in selected
    ]
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

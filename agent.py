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
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "ai-linkedin-draft-agent/0.1 (+source-backed LinkedIn drafts)"
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


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


class AgentError(Exception):
    """Base exception for expected agent failures."""


def configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def load_config(path: str) -> AgentConfig:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except FileNotFoundError as exc:
        raise AgentError(f"Source file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise AgentError(f"Could not parse source file: {path}") from exc

    sources: list[NewsSource] = []
    for raw_source in data.get("sources", []):
        if raw_source.get("enabled", True) is False:
            LOGGER.info("Skipping disabled source: %s", raw_source.get("name", raw_source.get("url")))
            continue

        name = str(raw_source.get("name", "")).strip()
        url = str(raw_source.get("url", "")).strip()
        category = str(raw_source.get("category", "ai")).strip().lower()
        credibility = str(raw_source.get("credibility", "news")).strip().lower()

        if not name or not url:
            LOGGER.warning("Skipping source with missing name or url: %s", raw_source)
            continue

        sources.append(
            NewsSource(
                name=name,
                url=url,
                category=category,
                credibility=credibility,
            )
        )

    if not sources:
        raise AgentError("No valid sources configured.")

    topics = [str(topic).strip() for topic in data.get("topics", []) if str(topic).strip()]
    trusted_sources = [
        str(source).strip()
        for source in data.get("trusted_sources", [])
        if str(source).strip()
    ]

    return AgentConfig(
        topics=topics,
        trusted_sources=trusted_sources,
        sources=sources,
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
    text = text.replace("\u2014", ",")
    text = text.replace("\u2013", "-")
    if len(text) > max_length:
        return text[: max_length - 1].rstrip() + "..."
    return text


def split_claims(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", normalized)
    return [part.strip() for part in parts if part.strip()]


def source_pack_text(item: NewsItem) -> str:
    return " ".join(
        [
            item.title,
            item.source_name,
            item.published_at.strftime("%Y-%m-%d %H:%M UTC"),
            item.category,
            item.credibility,
            item.url,
            item.summary,
        ]
    ).lower()


def meaningful_tokens(text: str) -> set[str]:
    stopwords = {
        "about",
        "after",
        "again",
        "also",
        "another",
        "because",
        "before",
        "being",
        "between",
        "could",
        "does",
        "from",
        "have",
        "into",
        "itself",
        "just",
        "like",
        "more",
        "most",
        "only",
        "over",
        "should",
        "that",
        "their",
        "there",
        "these",
        "this",
        "those",
        "through",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "would",
        "your",
    }
    tokens = set(re.findall(r"[a-zA-Z][a-zA-Z0-9']{2,}", text.lower()))
    return {token for token in tokens if token not in stopwords}


def is_factual_claim(claim: str) -> bool:
    lowered = claim.lower()
    claim_patterns = [
        r"\b(is|are|was|were|will|has|have|had|announced|launched|released|published|reported|named|recognized|introduced|created|built|supports|enables|uses|includes)\b",
        r"\b\d+[\w%$]*\b",
        r"\b(according to|source summary|published this|rss item)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in claim_patterns)


def claim_supported_by_source(claim: str, item: NewsItem) -> bool:
    if not is_factual_claim(claim):
        return True

    source_text = source_pack_text(item)
    tokens = meaningful_tokens(claim)
    if not tokens:
        return True

    supported_tokens = {token for token in tokens if token in source_text}
    support_ratio = len(supported_tokens) / len(tokens)

    # Conservative threshold: keep source-metadata claims and broad analysis, flag specifics not in source pack.
    if support_ratio >= 0.55:
        return True

    return False


def fact_check_text(text: str, item: NewsItem, section: str) -> tuple[str, list[str]]:
    chunks = text.split("\n")
    checked_chunks: list[str] = []
    risk_flags: list[str] = []

    for chunk in chunks:
        claims = split_claims(chunk)
        if not claims:
            checked_chunks.append(chunk)
            continue

        supported_claims = []
        for claim in claims:
            if claim_supported_by_source(claim, item):
                supported_claims.append(claim)
            else:
                risk_flags.append(f"{section}: removed weak claim: {claim}")

        if supported_claims:
            checked_chunks.append(" ".join(supported_claims))

    cleaned_text = "\n".join(checked_chunks).strip()
    return cleaned_text, risk_flags


def fact_check_draft(draft: Draft) -> Draft:
    hook, hook_flags = fact_check_text(draft.hook, draft.topic, "Hook")
    body, body_flags = fact_check_text(draft.body, draft.topic, "Body")
    ending, ending_flags = fact_check_text(draft.ending, draft.topic, "Ending")
    flags = hook_flags + body_flags + ending_flags

    if not hook:
        hook = f"PM read: {draft.topic.title}"
        flags.append("Hook: replaced with source title after unsupported claims were removed.")

    if not body:
        body = (
            f"{draft.topic.source_name} published this on "
            f"{draft.topic.published_at.strftime('%Y-%m-%d %H:%M UTC')}.\n\n"
            "The generated draft had weak factual claims, so the body was reduced to verified source metadata."
        )
        flags.append("Body: replaced with verified source metadata after unsupported claims were removed.")

    if not ending:
        ending = "PM takeaway: verify the linked source before adding any stronger claim."
        flags.append("Ending: replaced with a conservative PM takeaway.")

    fact_notes = list(draft.fact_check_notes)
    if flags:
        fact_notes.append("Automated claim audit removed or rewrote weak claims before Slack delivery.")
    else:
        fact_notes.append("Automated claim audit found no weak factual claims against the source pack.")

    return Draft(
        topic=draft.topic,
        hook=sanitize_generated_text(hook),
        body=sanitize_generated_text(body),
        ending=sanitize_generated_text(ending),
        source_links=draft.source_links,
        fact_check_notes=dedupe_text(fact_notes),
        risk_flags=dedupe_text(flags),
    )


def normalize_url(url: str) -> str:
    return re.sub(r"[?#].*$", "", url.strip().lower()).rstrip("/")


def is_fresh(published_at: dt.datetime, now: dt.datetime, fresh_hours: int) -> bool:
    age = now - published_at
    return dt.timedelta(0) <= age <= dt.timedelta(hours=fresh_hours)


def fetch_feed(source: NewsSource) -> list[dict[str, Any]]:
    LOGGER.info("Fetching source: %s", source.name)
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(source.url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.warning("Failed to fetch %s: %s", source.name, exc)
        return []

    parsed = feedparser.parse(response.content)
    if parsed.bozo:
        LOGGER.warning("Feed parse warning for %s: %s", source.name, parsed.bozo_exception)

    return list(parsed.entries)


def collect_news(config: AgentConfig, fresh_hours: int, now: dt.datetime) -> list[NewsItem]:
    items: list[NewsItem] = []

    for source in config.sources:
        for entry in fetch_feed(source):
            title = clean_text(str(entry.get("title", "")), max_length=180)
            url = str(entry.get("link", "")).strip()
            raw_date = (
                entry.get("published")
                or entry.get("updated")
                or entry.get("created")
                or entry.get("published_parsed")
                or entry.get("updated_parsed")
            )
            published_at = parse_datetime(raw_date)

            if not title or not url or not published_at:
                LOGGER.debug(
                    "Rejecting item missing title, url, or published date from %s",
                    source.name,
                )
                continue

            if not is_fresh(published_at, now, fresh_hours):
                LOGGER.debug("Rejecting stale item: %s", title)
                continue

            summary = clean_text(
                str(entry.get("summary") or entry.get("description") or ""),
                max_length=360,
            )
            item = NewsItem(
                title=title,
                url=url,
                published_at=published_at,
                source_name=source.name,
                category=source.category,
                credibility=source.credibility,
                summary=summary,
            )

            if not matches_configured_topics(item, config.topics):
                LOGGER.debug("Rejecting item outside configured topics: %s", title)
                continue

            items.append(item)

    return dedupe_items(items)


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    deduped: list[NewsItem] = []
    for item in items:
        key = normalize_url(item.url) or item.title.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def matches_configured_topics(item: NewsItem, topics: list[str]) -> bool:
    if not topics:
        return True

    searchable_text = " ".join(
        [
            item.title,
            item.summary,
            item.source_name,
            item.category,
        ]
    ).lower()

    return any(topic.lower() in searchable_text for topic in topics)


def is_trusted_source(item: NewsItem, trusted_sources: list[str]) -> bool:
    source_name = item.source_name.lower()
    return any(trusted.lower() in source_name or source_name in trusted.lower() for trusted in trusted_sources)


def topic_score(item: NewsItem, topics: list[str]) -> int:
    searchable_text = f"{item.title} {item.summary} {item.source_name} {item.category}".lower()
    return sum(1 for topic in topics if topic.lower() in searchable_text)


def score_item(item: NewsItem, now: dt.datetime, config: AgentConfig) -> float:
    age_hours = max((now - item.published_at).total_seconds() / 3600, 0)
    credibility_bonus = {
        "official": 40,
        "primary": 35,
        "credible": 20,
        "news": 15,
    }.get(item.credibility, 10)
    category_bonus = 8 if item.category in {"ai", "product", "product-management"} else 0
    title_bonus = 4 if re.search(r"\b(ai|product|pm|launch|update|model|agent)\b", item.title, re.I) else 0
    trusted_bonus = 10 if is_trusted_source(item, config.trusted_sources) else 0
    topic_bonus = min(topic_score(item, config.topics) * 4, 16)
    freshness_bonus = max(0, 48 - age_hours)
    return credibility_bonus + category_bonus + title_bonus + trusted_bonus + topic_bonus + freshness_bonus


def rank_items(items: list[NewsItem], now: dt.datetime, config: AgentConfig, limit: int = MAX_DRAFTS) -> list[NewsItem]:
    ranked = sorted(items, key=lambda item: (score_item(item, now, config), item.published_at), reverse=True)
    return ranked[:limit]


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


def write_draft(item: NewsItem, style: str, gemini_api_key: str, gemini_model: str) -> Draft:
    if gemini_api_key:
        try:
            return write_gemini_draft(item, style, gemini_api_key, gemini_model)
        except AgentError as exc:
            LOGGER.warning("Gemini draft failed for %s. Falling back to template: %s", item.title, exc)

    return write_template_draft(item, style)


def write_template_draft(item: NewsItem, style: str) -> Draft:
    date_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary_sentence = (
        f"The source summary says: {item.summary}"
        if item.summary
        else "The RSS item did not include a usable summary, so this draft sticks to the title and source link."
    )

    hooks = {
        "Product teardown": f"Product teardown: {item.title}",
        "Launch analysis": f"Launch analysis: {item.title}",
        "Founder/investor signal": f"Founder/investor signal: {item.title}",
        "PM lesson": f"PM lesson from today's AI news: {item.title}",
        "Slightly sarcastic industry observation": f"Another AI headline, yes. This one is worth a sharper PM read: {item.title}",
        "What this means for builders breakdown": f"What this means for builders: {item.title}",
    }

    body_templates = {
        "Product teardown": (
            f"{item.source_name} published this on {date_text}.\n\n"
            f"{summary_sentence}\n\n"
            "The PM read is simple: look past the announcement and inspect the user workflow.\n\n"
            "What changes for the user? What gets easier? What still needs proof?\n\n"
            "A product story is only useful if the value is specific enough to survive outside the launch post."
        ),
        "Launch analysis": (
            f"{item.source_name} published this on {date_text}.\n\n"
            f"{summary_sentence}\n\n"
            "My launch-analysis lens:\n"
            "1. Is the user problem clear?\n"
            "2. Is the adoption path obvious?\n"
            "3. Is the product claim supported by the source, not vibes?\n\n"
            "Launches are cheap. Changed behavior is expensive."
        ),
        "Founder/investor signal": (
            f"{item.source_name} published this on {date_text}.\n\n"
            f"{summary_sentence}\n\n"
            "The signal for founders and investors is not the headline itself. It is where attention, distribution, trust, or workflow gravity might shift.\n\n"
            "If this creates a new default behavior, it matters. If it only creates a louder demo, the market will move on by lunch."
        ),
        "PM lesson": (
            f"{item.source_name} published this on {date_text}.\n\n"
            f"{summary_sentence}\n\n"
            "PM lesson: do not confuse capability with product value.\n\n"
            "The useful question is not \"is this impressive?\" It is \"does this remove friction from a real decision, workflow, or habit?\"\n\n"
            "That is where AI products either become useful or become another tab nobody asked for."
        ),
        "Slightly sarcastic industry observation": (
            f"{item.source_name} published this on {date_text}.\n\n"
            f"{summary_sentence}\n\n"
            "The AI industry remains undefeated at making every announcement sound like the beginning of a new era.\n\n"
            "The PM job is less dramatic: check the source, identify the user impact, and separate actual product movement from well-lit theater."
        ),
        "What this means for builders breakdown": (
            f"{item.source_name} published this on {date_text}.\n\n"
            f"{summary_sentence}\n\n"
            "What this means for builders:\n"
            "1. Watch the workflow, not just the feature.\n"
            "2. Look for a repeated user behavior this could unlock.\n"
            "3. Do not add claims unless the source supports them.\n\n"
            "The opportunity is usually hiding in the boring operational detail."
        ),
    }

    endings = {
        "Product teardown": "Strong PM takeaway: if the workflow does not change, the launch is mostly noise.",
        "Launch analysis": "Question I would ask the team: what user behavior should be measurably different after this?",
        "Founder/investor signal": "My opinion: the durable signal is changed distribution or habit, not a prettier announcement.",
        "PM lesson": "PM takeaway: capability is table stakes. Adoption is the real product work.",
        "Slightly sarcastic industry observation": "Strong opinion: the AI market needs fewer victory laps and more proof of changed workflows.",
        "What this means for builders breakdown": "Builder takeaway: ship toward a behavior, not a buzzword.",
    }

    fact_notes = [
        f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.",
        "No benchmarks, funding amounts, timelines, or product claims were added beyond the source metadata and summary.",
        "Verify the linked article before posting if you want to include extra specifics.",
    ]

    return Draft(
        topic=item,
        hook=sanitize_generated_text(hooks[style]),
        body=sanitize_generated_text(body_templates[style]),
        ending=sanitize_generated_text(endings[style]),
        source_links=[item.url],
        fact_check_notes=fact_notes,
    )


def write_gemini_draft(item: NewsItem, style: str, api_key: str, model: str) -> Draft:
    prompt = build_gemini_prompt(item, style)
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": 0.75,
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "x-goog-api-key": api_key,
    }
    url = GEMINI_API_URL.format(model=model)

    try:
        response = requests.post(
            url,
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

    generated_text = extract_gemini_text(response_payload)
    draft_data = parse_gemini_json(generated_text)

    hook = require_text_field(draft_data, "hook")
    body = require_text_field(draft_data, "body")
    ending = require_text_field(draft_data, "ending")
    notes = draft_data.get("fact_check_notes", [])
    if not isinstance(notes, list):
        raise AgentError("Gemini response field fact_check_notes must be a list.")

    fact_notes = [clean_text(str(note), max_length=240) for note in notes if str(note).strip()]
    fact_notes.extend(
        [
            f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.",
            "Gemini generated wording only from supplied source metadata and summary.",
            "No extra facts should be posted unless verified in the linked article.",
        ]
    )

    return Draft(
        topic=item,
        hook=sanitize_generated_text(hook),
        body=sanitize_generated_text(body),
        ending=sanitize_generated_text(ending),
        source_links=[item.url],
        fact_check_notes=dedupe_text(fact_notes),
    )


def build_gemini_prompt(item: NewsItem, style: str) -> str:
    published_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "No usable RSS summary was provided."
    return f"""
You are writing one LinkedIn draft like a sharp Product Manager who tracks AI deeply.

Hard rules:
- Use only the source metadata below.
- Every factual claim must be traceable to the provided source metadata.
- No hallucinations.
- No fake benchmarks.
- No fake product capabilities.
- No fake quotes.
- No fake customer names.
- No exaggerated claims.
- Do not add facts, numbers, dates, benchmarks, funding amounts, product claims, quotes, customer names, or timelines.
- Do not browse or rely on memory.
- Do not use em dashes.
- Keep the post readable and human.
- Use short paragraphs.
- Avoid corporate fluff.
- Avoid generic AI hype.
- Voice: sharp Product Manager who tracks AI deeply.
- Required style for this draft: {style}
- The style must be one of: Product teardown, Launch analysis, Founder/investor signal, PM lesson, Slightly sarcastic industry observation, What this means for builders breakdown.
- End with a strong opinion, question, or PM takeaway.
- Output valid JSON only with these keys: hook, body, ending, fact_check_notes.
- fact_check_notes must be a list of short strings.

Source metadata:
Title: {item.title}
Source: {item.source_name}
Published at: {published_text}
Category: {item.category}
Credibility: {item.credibility}
URL: {item.url}
RSS summary: {summary}

Write:
1. hook: 1 to 2 sentences.
2. body: 3 to 6 short paragraphs or a concise numbered list.
3. ending: 1 question or closing line.
4. fact_check_notes: what was used and what must be verified before posting.
""".strip()


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
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

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


def sanitize_generated_text(value: str) -> str:
    return value.replace("\u2014", ",").replace("\u2013", "-")


def format_draft(draft: Draft, index: int) -> str:
    source_links = "\n".join(f"- {link}" for link in draft.source_links)
    fact_notes = "\n".join(f"- {note}" for note in draft.fact_check_notes)
    risk_flags = "\n".join(f"- {flag}" for flag in draft.risk_flags)
    risk_section = f"\n\nRisk flags:\n{risk_flags}" if risk_flags else ""
    return (
        f"Draft {index}:\n"
        f"Title: {draft.topic.title}\n\n"
        f"Hook:\n{draft.hook}\n\n"
        f"LinkedIn post body:\n{draft.body}\n\n"
        f"Suggested ending:\n{draft.ending}\n\n"
        f"Sources:\n{source_links}\n\n"
        f"Fact-check notes:\n{fact_notes}"
        f"{risk_section}"
    )


def build_slack_message(drafts: list[Draft], now: dt.datetime) -> str:
    date_text = now.strftime("%Y-%m-%d")
    if not drafts:
        return (
            f"Daily AI + PM LinkedIn Drafts\n"
            f"Date: {date_text}\n\n"
            "No qualifying source-backed AI/Product news items were found in the freshness window. "
            "No drafts were generated because making things up is not a content strategy."
        )

    parts = [
        (
            f"Daily AI + PM LinkedIn Drafts\n"
            f"Date: {date_text}\n\n"
            f"Generated {len(drafts)} source-backed LinkedIn draft(s)."
        ),
    ]
    total_risk_flags = sum(len(draft.risk_flags) for draft in drafts)
    if total_risk_flags:
        parts[0] = f"{parts[0]}\n\nRisk flags found: {total_risk_flags}"

    parts.extend(format_draft(draft, index) for index, draft in enumerate(drafts, start=1))
    return "\n\n---\n\n".join(parts)


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

    selected = rank_items(items, now, config, limit=MAX_DRAFTS)
    if len(selected) < MIN_DRAFTS:
        LOGGER.warning("Only %s qualifying item(s) found. The agent will not invent filler topics.", len(selected))

    style = style_for_day(now)
    gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
    gemini_model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    if gemini_api_key:
        LOGGER.info("Using Gemini model for draft writing: %s", gemini_model)
    else:
        LOGGER.info("GEMINI_API_KEY is not set. Using deterministic template writer.")

    drafts = [
        fact_check_draft(write_draft(item, style, gemini_api_key, gemini_model))
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

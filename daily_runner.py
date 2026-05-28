"""Runtime quality policy for the scheduled LinkedIn draft agent."""

from __future__ import annotations

import os
import re

import agent


def compact_source_title(title: str, max_words: int = 8) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&+-]*", agent.clean_text(title, max_length=160))
    if not words:
        return "The AI Builder Signal"
    return " ".join(words[:max_words]).strip(" -,:;") or "The AI Builder Signal"


def conservative_draft(item: agent.NewsItem) -> agent.Draft:
    summary = item.summary or item.source_excerpt or f"The source item is titled {item.title}."
    title = compact_source_title(item.title)
    body = (
        "Everyone will skim this as another AI update.\n"
        "PMs should read it as a workflow signal.\n\n"
        "The useful question is what it changes for people building, buying, or managing products.\n\n"
        f"Here is the source-backed context: {summary}\n\n"
        "What changed\n"
        "- Treat the announcement as a workflow signal, not a press release to admire from a distance.\n"
        "- Ask which user action becomes easier, faster, safer, or more measurable because of it.\n\n"
        "Why PMs should care\n"
        "- AI products are no longer judged only by model capability. They are judged by the job they remove from a real team.\n"
        "- If the update does not change a decision, handoff, review step, or delivery loop, it is probably less important than the headline suggests.\n\n"
        "Reader value\n"
        "- Turn the news into a checklist before turning it into a roadmap item.\n"
        "- The practical lens is simple: who uses it, what changes, what can fail, and how would the team know it worked?\n\n"
        "What builders should watch\n"
        "- Look for the boring parts: permissions, reliability, evaluation, latency, cost, and failure handling.\n"
        "- Those details usually decide whether an AI feature becomes a daily habit or a demo people forget after lunch.\n\n"
        "PM takeaway\n"
        "- The best read is not whether this sounds impressive. It is whether a team can trust it inside an actual workflow.\n\n"
        "If you were evaluating this for your product, what would you validate first: user demand, reliability, cost, or workflow fit?\n\n"
        f"{agent.DEFAULT_HASHTAGS}"
    )
    return agent.Draft(
        topic=item,
        title=title,
        body=body,
        source_links=[item.url],
        fact_check_notes=[
            f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.",
            "Conservative writer used only source metadata plus generic PM evaluation lenses.",
        ],
    )


def has_reader_question(text: str) -> bool:
    without_hashtags = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
    return "?" in without_hashtags[-700:]


def validate_draft_quality(draft: agent.Draft) -> agent.Draft:
    body = draft.body.strip()
    title = draft.title.strip()
    word_count = len(re.findall(r"\b[\w']+\b", body))
    bullet_count = len(re.findall(r"(?m)^\s*-\s+\S+", body))
    section_count = len(re.findall(r"(?m)^[^\n]{0,6}[A-Z][A-Za-z /&-]{2,40}$", body))
    section_count += len(re.findall(r"(?m)^[^\n]{0,4}[\U0001F300-\U0001FAFF]\ufe0f?\s*[A-Z][^\n]{2,45}$", body))
    low_quality_patterns = [
        r"^hook\s*:",
        r"^linkedin post body\s*:",
        r"^suggested ending\s*:",
        r"^fact-check notes\s*:",
        r"^risk flags\s*:",
        r"^sources\s*:",
        r"the headline is interesting",
        r"the product question underneath",
        r"verify the linked source",
        r"published this on \d{4}-\d{2}-\d{2}",
    ]
    if word_count < 220:
        raise agent.AgentError(f"Draft quality gate failed: body is too short ({word_count} words).")
    if word_count > 520:
        raise agent.AgentError(f"Draft quality gate failed: body is too long ({word_count} words).")
    if section_count < 2:
        raise agent.AgentError("Draft quality gate failed: missing useful section labels.")
    if bullet_count < 3:
        raise agent.AgentError("Draft quality gate failed: missing practical bullet points.")
    if not has_reader_question(body):
        raise agent.AgentError("Draft quality gate failed: missing a reader question near the end.")
    if any(re.search(pattern, f"{title}\n{body}", re.I) for pattern in low_quality_patterns):
        raise agent.AgentError("Draft quality gate failed: generic fallback language detected.")
    return draft


def build_gemini_prompt(item: agent.NewsItem, style: str, trend_context: str) -> str:
    published_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "No usable RSS summary was provided."
    return f"""
You are writing one LinkedIn post like a sharp Product Manager who tracks AI deeply.

Write for PMs, founders, and AI builders, but make it understandable for smart non-experts.
Use only the source metadata below for facts. Do not invent numbers, capabilities, quotes, customers, release dates, benchmarks, funding amounts, or product claims.

Structure:
- Short title, 3 to 8 words.
- Punchy opening with tension, not a report recap.
- One plain-language context paragraph grounded in the source.
- 3 to 4 labeled sections with short bullet points using "- ".
- A reader question near the end before hashtags.
- 8 to 12 relevant hashtags.

Style:
- Sharp PM who tracks AI deeply.
- Practical, readable, human, sometimes lightly sarcastic.
- Avoid corporate fluff, generic AI hype, and complex jargon.
- Do not use em dashes.
- Do not include Hook, LinkedIn post body, Suggested ending, Sources, Fact-check notes, or Risk flags.
- Length: 260 to 430 words.

Required archetype: {style}

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

Use X only for angle selection. Never use X as factual support.
Output valid JSON only with keys: title, body, fact_check_notes.
""".strip()


def write_draft(item: agent.NewsItem, style: str, api_key: str, model: str, trend_context: str) -> agent.Draft:
    if api_key:
        try:
            return agent.polish_draft(agent.write_gemini_draft(item, style, api_key, model, trend_context))
        except agent.AgentError as exc:
            agent.LOGGER.warning("Gemini draft failed for %s. Falling back to conservative writer: %s", item.title, exc)
    return agent.polish_draft(conservative_draft(item))


def main() -> None:
    os.environ.setdefault("ALLOW_CONSERVATIVE_FALLBACK", "true")
    os.environ["FRESH_HOURS"] = "48"
    agent.MAX_CANDIDATE_ITEMS = 12
    agent.build_gemini_prompt = build_gemini_prompt
    agent.write_draft = write_draft
    agent.validate_draft_quality = validate_draft_quality
    agent.main()


if __name__ == "__main__":
    main()

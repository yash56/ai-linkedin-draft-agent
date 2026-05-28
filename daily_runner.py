"""Runtime quality policy for the scheduled LinkedIn draft agent."""

from __future__ import annotations

import os
import re
import zlib

import agent


BACKUP_POST_SHAPES = [
    {
        "hook": (
            "Everyone will skim this as another AI update.\n"
            "PMs should read it as a workflow signal."
        ),
        "sections": [
            ("What changed", "Treat this as a signal about the product workflow, not a press release to admire from a distance.", "Map it to a real user moment before calling it strategically important."),
            ("Why PMs should care", "The useful question is which user action becomes easier, safer, faster, or more measurable.", "If that action is fuzzy, the product story is still fuzzy."),
            ("Reader value", "Turn the news into a checklist before turning it into a roadmap item.", "Ask what proof would make the update worth changing a roadmap, budget, or team process."),
            ("What builders should watch", "Look for the boring parts: permissions, reliability, evaluation, latency, cost, and failure handling.", "That is usually where AI products become useful, or quietly fall apart."),
        ],
        "question": "If you were evaluating this for your product, what would you validate first: user demand, reliability, cost, or workflow fit?",
    },
    {
        "hook": (
            "The announcement is the easy part.\n"
            "The harder part is figuring out what it changes for real teams."
        ),
        "sections": [
            ("Product read", "Start with the job-to-be-done, not the model or company name.", "A strong AI update should make one painful step easier to understand or complete."),
            ("Adoption risk", "Ask where the user still needs trust, control, review, or escalation before this becomes daily behavior.", "The gap between impressive and dependable is where most AI products get humbled."),
            ("Builder lens", "Separate a useful workflow improvement from a feature that only looks good in a demo.", "Demo value gets attention. Workflow value gets retention."),
            ("PM takeaway", "The best AI products reduce ambiguity for users instead of adding another shiny surface area.", "If the update creates more decisions than it removes, users will feel the tax immediately."),
        ],
        "question": "What would make this genuinely useful in your workflow: better accuracy, clearer controls, faster setup, or stronger proof it works?",
    },
    {
        "hook": (
            "This is not just a technology update.\n"
            "It is a small clue about where AI products are getting pulled next."
        ),
        "sections": [
            ("Market signal", "Watch which workflow the news points toward, because that usually reveals the buyer pain.", "The headline matters less than the repeated job underneath it."),
            ("Execution gap", "The hard work is rarely the announcement. It is deployment, measurement, support, and trust.", "This is where product teams need operating discipline, not just better prompts."),
            ("PM checklist", "Ask who owns success after launch and what evidence would prove the product is improving.", "Without that loop, the roadmap becomes a pile of AI experiments with nicer branding."),
            ("Builder takeaway", "Durable AI products make a repeated task easier to complete, not just easier to describe.", "A useful product shift should survive contact with messy users and imperfect data."),
        ],
        "question": "Would you treat this as a real product signal, or just another AI headline until users prove otherwise?",
    },
    {
        "hook": (
            "AI news moves fast enough to make every update sound urgent.\n"
            "Most of them are not. Some are signals."
        ),
        "sections": [
            ("Signal quality", "A strong signal points to a concrete workflow, user pain, or operating model change.", "If you cannot name the workflow, you probably only have a headline."),
            ("Noise filter", "Ignore the drama and inspect what can be shipped, measured, trusted, or adopted.", "A little skepticism is healthy. In AI, it is basically product hygiene."),
            ("Team impact", "The PM question is whether this changes prioritization, onboarding, evaluation, or customer expectations.", "That is where news becomes useful for operators."),
            ("What to watch", "The next proof point is not the launch language. It is whether users keep coming back.", "Retention beats announcement energy every single time."),
        ],
        "question": "What would you need to see before calling this a durable AI product shift?",
    },
]


def compact_source_title(title: str, max_words: int = 8) -> str:
    normalized = title.replace("\u2018", "'").replace("\u2019", "'").replace('"', "").replace("'", "")
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'&+-]*", agent.clean_text(normalized, max_length=160))
    if not words:
        return "The AI Builder Signal"
    return " ".join(words[:max_words]).strip(" -,:;") or "The AI Builder Signal"


def shape_for_item(item: agent.NewsItem) -> dict[str, object]:
    index = zlib.crc32(f"{item.url}|{item.title}".encode("utf-8")) % len(BACKUP_POST_SHAPES)
    return BACKUP_POST_SHAPES[index]


def conservative_draft(item: agent.NewsItem) -> agent.Draft:
    summary = item.summary or item.source_excerpt or f"The source item is titled {item.title}."
    title = compact_source_title(item.title)
    shape = shape_for_item(item)
    sections = shape["sections"]
    section_text = "\n\n".join(
        (
            f"{label}\n"
            f"- {point}\n"
            f"- {follow_up}"
        )
        for label, point, follow_up in sections
    )
    body = (
        f"{shape['hook']}\n\n"
        "The useful question is what it changes for people building, buying, or managing products.\n\n"
        f"Here is the source-backed context: {summary}\n\n"
        "A practical PM read is simple: do not ask whether the announcement sounds impressive. Ask what behavior it could change, what risk it introduces, and what proof would make a team comfortable using it.\n\n"
        f"{section_text}\n\n"
        f"{shape['question']}\n\n"
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


def drafts_are_too_similar(first: agent.Draft, second: agent.Draft) -> bool:
    if first.topic.url == second.topic.url:
        return True
    title_similarity = agent.jaccard(
        agent.meaningful_tokens(first.topic.title),
        agent.meaningful_tokens(second.topic.title),
    )
    if title_similarity > 0.58:
        return True
    body_similarity = agent.jaccard(
        agent.meaningful_tokens(f"{first.title} {first.body}"),
        agent.meaningful_tokens(f"{second.title} {second.body}"),
    )
    return body_similarity > 0.70


def main() -> None:
    os.environ.setdefault("ALLOW_CONSERVATIVE_FALLBACK", "true")
    os.environ["FRESH_HOURS"] = "48"
    agent.MAX_CANDIDATE_ITEMS = 12
    agent.build_gemini_prompt = build_gemini_prompt
    agent.write_draft = write_draft
    agent.validate_draft_quality = validate_draft_quality
    agent.drafts_are_too_similar = drafts_are_too_similar
    agent.main()


if __name__ == "__main__":
    main()

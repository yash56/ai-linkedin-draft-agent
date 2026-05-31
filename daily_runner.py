"""Sharper daily writing policy for the scheduled LinkedIn draft agent."""

from __future__ import annotations

import os
import re
import zlib

import agent


FORBIDDEN_PHRASES = [
    "PMs should read this as a workflow signal",
    "PMs should read it as a workflow signal",
    "Here is the source-backed context",
    "A practical PM read is simple",
    "The useful question is",
    "What changed",
    "Why PMs should care",
    "Reader value",
    "another AI update",
    "workflow signal",
]

POST_SHAPES = [
    {
        "hook": "{title} matters because it is not just a model story. It is a product trust story.",
        "label": "The product angle",
        "bullets": [
            "The first question is whether this makes a repeated user job easier.",
            "The second is whether teams can evaluate it without guessing.",
            "The third is what breaks when the product leaves the demo environment.",
        ],
        "insight": "My PM read: the winners in AI will not be the teams with the loudest launch. They will be the teams that make the new capability boringly reliable.",
        "question": "Would you trust this in a real customer workflow today, or would you still keep a human checkpoint in the loop?",
    },
    {
        "hook": "{title} is a reminder that AI product work is moving from features to operating systems.",
        "label": "Where this gets interesting",
        "bullets": [
            "The user does not care how impressive the underlying system is.",
            "They care whether it removes friction from a job they already need to do.",
            "For PMs, the hard part is turning capability into a habit.",
        ],
        "insight": "The sharp product question is not 'can it do this?' It is 'will users change behavior because of this?' That is a much higher bar.",
        "question": "If you owned this product, what would you measure first: adoption, trust, repeat usage, or failure rate?",
    },
    {
        "hook": "{title} matters because the AI market is starting to reward proof, not just possibility.",
        "label": "The builder lens",
        "bullets": [
            "A launch is only useful if it changes a real workflow.",
            "A claim is only useful if users can verify it in their own context.",
            "A product is only defensible if it gets better after real usage.",
        ],
        "insight": "This is where many AI products quietly struggle. The demo is clean. The customer environment is not.",
        "question": "What proof would make you believe this is a durable product shift rather than a strong announcement?",
    },
    {
        "hook": "{title} is the kind of update that looks technical, but the real story is user behavior.",
        "label": "The PM takeaway",
        "bullets": [
            "Do users understand what changed?",
            "Can they control the output when it matters?",
            "Can the team explain success without hiding behind model jargon?",
        ],
        "insight": "Good AI products do not make users admire the technology. They make users feel more capable.",
        "question": "Where do you think the biggest adoption risk sits here: trust, UX, cost, or unclear user value?",
    },
]


KEYWORD_SHAPES = [
    {
        "keywords": ["spark", "calendar", "emails", "assistant"],
        "title": "Gemini Spark's Agent Problem",
        "hook": "Gemini Spark shows the uncomfortable truth about AI agents: access to your data is not the same as judgment.",
        "label": "The product lesson",
        "bullets": [
            "Personal agents need context, but they also need taste.",
            "The risk is not that the agent fails loudly. It is that it misses something obvious and still sounds confident.",
            "For PMs, this is a reminder that permissions are not the same as product value.",
        ],
        "insight": "Agent products will win when they understand the user's real priority, not just the user's available data.",
        "question": "Where would you draw the line between useful automation and a human decision that should stay human?",
    },
    {
        "keywords": ["coding agent", "coding agents", "devin", "programmers", "replace humans", "codex"],
        "title": "Coding Agents Need A Human Plan",
        "hook": "The interesting part of AI coding agents is not whether they replace developers. It is how they change the developer's job.",
        "label": "The PM lesson",
        "bullets": [
            "A coding agent is most useful when the task is clear and the feedback loop is tight.",
            "It becomes risky when ownership, review, and debugging are vague.",
            "The product challenge is not code generation. It is trustable delivery.",
        ],
        "insight": "The best developer tools do not remove judgment. They move human judgment to a higher-leverage part of the workflow.",
        "question": "Would you position coding agents as replacements, teammates, or a new layer of engineering infrastructure?",
    },
    {
        "keywords": ["ai-pilled", "replace your job", "workforce", "layoffs", "job truly involves"],
        "title": "The AI Replacement Trap",
        "hook": "The fastest way to misuse AI at work is to assume you understand a job better than the person doing it.",
        "label": "The business risk",
        "bullets": [
            "AI can remove tasks, but roles are usually messier than task lists.",
            "Leaders who skip workflow discovery will automate the wrong thing with impressive confidence.",
            "The product question is where AI improves leverage without destroying context.",
        ],
        "insight": "Replacing work you do not understand is not strategy. It is cost-cutting with a nicer deck.",
        "question": "What is the first workflow you would audit before replacing any role with an AI agent?",
    },
    {
        "keywords": ["governance", "safety", "security", "risk", "regulations"],
        "title": "AI Governance Becomes Product Work",
        "hook": "AI governance is no longer a policy side quest. It is becoming part of the product itself.",
        "label": "The PM angle",
        "bullets": [
            "Trust has to show up in the user experience, not just in a PDF.",
            "Risk controls need owners, metrics, and release gates.",
            "If governance slows the team down, teams will route around it. If it is designed well, it becomes infrastructure.",
        ],
        "insight": "The next generation of AI products will need safety work that feels operational, not ceremonial.",
        "question": "Should AI governance sit with legal, product, engineering, or a dedicated risk team?",
    },
    {
        "keywords": ["omni", "demo", "videos", "model", "available on aws", "bedrock", "opus"],
        "title": "The Model Launch Test",
        "hook": "A new AI model only matters when it changes what builders can ship with confidence.",
        "label": "The builder test",
        "bullets": [
            "Does it make an existing workflow meaningfully better?",
            "Can teams evaluate quality without relying on vibes?",
            "Does the product make adoption easier, or just add another option to compare?",
        ],
        "insight": "Model launches create attention. Product packaging creates usage.",
        "question": "What would make you switch models in a real product: quality, cost, latency, tooling, or trust?",
    },
]


def compact_source_title(title: str, max_words: int = 8) -> str:
    normalized = (
        title.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace('"', "")
        .replace("'", "")
    )
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&+.-]*", agent.clean_text(normalized, max_length=160))
    while words and words[-1].lower() in {"and", "or", "with", "for", "to", "at", "on", "in", "when", "from"}:
        words.pop()
    if not words:
        return "The AI Product Signal"
    return " ".join(words[:max_words]).strip(" -,:;") or "The AI Product Signal"


def keyword_shape_for_item(item: agent.NewsItem) -> dict[str, object] | None:
    searchable = f"{item.title} {item.summary} {item.source_excerpt}".lower()
    for shape in KEYWORD_SHAPES:
        if any(keyword in searchable for keyword in shape["keywords"]):
            return shape
    return None


def plain_news(item: agent.NewsItem, max_words: int = 52) -> str:
    text = item.summary or item.source_excerpt or item.title
    text = agent.clean_text(text, max_length=700)
    words = text.split()
    if len(words) <= max_words:
        return text.rstrip(".")
    return " ".join(words[:max_words]).rstrip(" ,.;:") + "..."


def shape_for_item(item: agent.NewsItem) -> dict[str, object]:
    index = zlib.crc32(f"{item.url}|{item.title}".encode("utf-8")) % len(POST_SHAPES)
    return POST_SHAPES[index]


def conservative_draft(item: agent.NewsItem) -> agent.Draft:
    shape = keyword_shape_for_item(item) or shape_for_item(item)
    title = str(shape.get("title") or compact_source_title(item.title))
    news = plain_news(item)
    bullets = "\n".join(f"- {bullet}" for bullet in shape["bullets"])
    body = (
        f"{shape['hook'].format(title=title)}\n\n"
        f"The news: {news}.\n\n"
        f"{shape['label']}\n"
        f"{bullets}\n\n"
        f"{shape['insight']}\n\n"
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
            "Conservative writer used source metadata plus generic product analysis only.",
        ],
    )


def has_reader_question(text: str) -> bool:
    without_hashtags = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
    return "?" in without_hashtags[-500:]


def validate_draft_quality(draft: agent.Draft) -> agent.Draft:
    body = draft.body.strip()
    title = draft.title.strip()
    combined = f"{title}\n{body}"
    word_count = len(re.findall(r"\b[\w']+\b", body))
    bullet_count = len(re.findall(r"(?m)^\s*-\s+\S+", body))
    if word_count < 115:
        raise agent.AgentError(f"Draft quality gate failed: body is too short ({word_count} words).")
    if word_count > 280:
        raise agent.AgentError(f"Draft quality gate failed: body is too long ({word_count} words).")
    if bullet_count < 2:
        raise agent.AgentError("Draft quality gate failed: missing useful bullet points.")
    if not has_reader_question(body):
        raise agent.AgentError("Draft quality gate failed: missing a reader question near the end.")
    if any(phrase.lower() in combined.lower() for phrase in FORBIDDEN_PHRASES):
        raise agent.AgentError("Draft quality gate failed: banned generic wording detected.")
    return draft


def build_gemini_prompt(item: agent.NewsItem, style: str, trend_context: str) -> str:
    published_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "No usable RSS summary was provided."
    return f"""
You are writing one LinkedIn post like a sharp Product Manager who tracks AI closely.

Goal:
Write a clear, specific, human LinkedIn draft about the actual news. The reader should understand what happened, why it matters, and what product lesson to take away.

Hard rules:
- Use only the source metadata below for factual claims.
- Do not invent numbers, capabilities, quotes, customers, timelines, benchmarks, funding amounts, or product claims.
- Do not use em dashes.
- Do not use these phrases: "PMs should read this as a workflow signal", "Here is the source-backed context", "A practical PM read is simple", "The useful question is", "What changed", "Why PMs should care", "Reader value".
- Do not sound like a press release, generic AI newsletter, or ChatGPT.
- Keep the post around 120 to 220 words.
- Use short paragraphs.
- Include 2 to 4 useful bullet points.
- End with a thoughtful reader question.
- End with 8 to 12 relevant hashtags.

Post structure:
1. Strong simple hook explaining why the news matters.
2. Plain-English explanation of the actual news.
3. Product/PM insight with a point of view.
4. Practical takeaway or question.
5. Hashtags.

Quality bar:
- Every sentence must explain the news, add a useful product insight, or create curiosity.
- Avoid abstract commentary and filler.
- Make it readable for PMs, founders, and tech professionals.

Style to vary today: {style}

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
    return body_similarity > 0.62


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

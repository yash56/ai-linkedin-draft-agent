"""Daily writing policy for the scheduled LinkedIn draft agent.

This wrapper keeps the core collector intact, but makes the writing layer much
stricter: source-backed facts only, natural paragraphs, no generic headers, and
no textbook-style post templates.
"""

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
    "Market signal",
    "Builder takeaway",
    "Key Takeaways",
    "The News",
    "The news:",
    "The PM Lesson",
    "The product angle",
    "Where this gets interesting",
    "The builder lens",
    "The PM takeaway",
    "The product lesson",
    "The business risk",
    "The PM angle",
    "The builder test",
    "another AI update",
    "workflow signal",
    "deploy today",
    "high-performance engine",
]

GENERIC_SECTION_HEADERS = [
    "news",
    "the news",
    "pm lesson",
    "the pm lesson",
    "key takeaways",
    "takeaways",
    "what changed",
    "why pms should care",
    "reader value",
    "market signal",
    "builder takeaway",
    "the product angle",
    "where this gets interesting",
    "the builder lens",
    "the pm takeaway",
    "the product lesson",
    "the business risk",
    "the pm angle",
    "the builder test",
]

MULTIPLE_CHOICE_ENDING_PATTERNS = [
    r"\bA,\s*B,\s*or\s*C\b",
    r"\badoption,\s*trust,\s*repeat usage,\s*or\s*failure rate\b",
    r"\bquality,\s*cost,\s*latency,\s*tooling,\s*or\s*trust\b",
    r"\btrust,\s*UX,\s*cost,\s*or\s*unclear user value\b",
    r"\blegal,\s*product,\s*engineering,\s*or\b",
]

SOURCE_FACT_VERBS = [
    "announced",
    "introduced",
    "launched",
    "released",
    "updated",
    "added",
    "removed",
    "retired",
    "expanded",
    "partnered",
    "reported",
    "published",
    "says",
    "said",
    "found",
    "named",
    "ranked",
    "funded",
    "raised",
    "acquired",
    "available",
    "rolling out",
    "testing",
    "preview",
    "beta",
]

KNOWN_ENTITY_TOKENS = [
    "OpenAI",
    "ChatGPT",
    "Codex",
    "Google",
    "Gemini",
    "DeepMind",
    "Anthropic",
    "Claude",
    "Perplexity",
    "Microsoft",
    "GitHub",
    "Copilot",
    "AWS",
    "Meta",
    "Llama",
    "Hugging Face",
    "TechCrunch",
    "The Verge",
    "Wired",
    "Gartner",
]

ANGLE_SHAPES = [
    {
        "hook": "{title} matters because it shows a quieter shift in AI adoption: the winning product is often the one that removes decisions, not the one that adds more knobs.",
        "middle": "That is an organizational efficiency story more than a model story. When AI gets closer to daily work, teams do not just ask whether the technology is impressive. They ask whether it reduces handoffs, shortens review cycles, or makes the next step easier for the person using it.",
        "insight": "The product marketing lesson is to stop selling the capability in isolation. The better message is the workflow it simplifies and the confusion it removes.",
        "closing": "If a user needs a long explainer before they can feel the value, the product is still doing too much homework on their behalf.",
    },
    {
        "hook": "{title} is a good reminder that AI adoption rarely moves because of one dramatic feature. It moves when a product becomes easier to understand, trust, and repeat.",
        "middle": "This is where many AI launches get misread. The announcement creates attention, but adoption comes from the boring parts: fewer choices, clearer defaults, less context switching, and a user experience that does not make people feel like they are debugging the product.",
        "insight": "For product marketing, the useful angle is not 'look how advanced this is.' It is 'look how little extra behavior we are asking from the user.'",
        "closing": "The real adoption question is whether this makes the user come back tomorrow without needing to be convinced again.",
    },
    {
        "hook": "{title} sounds like a product update, but the bigger signal is how AI companies are trying to make advanced tools feel less like experiments and more like everyday software.",
        "middle": "That matters for teams because the cost of AI adoption is not only money or latency. It is attention. Every new option, model, setting, or workflow asks users to carry more mental overhead before they get value.",
        "insight": "A sharper product marketing message would focus less on the technology stack and more on the moment where the end user feels the product getting easier.",
        "closing": "The best AI feature is not always the most powerful one. Sometimes it is the one that makes the product feel obvious.",
    },
    {
        "hook": "{title} is worth watching because the AI market is moving from novelty to packaging. The capability matters, but the way it is delivered may matter even more.",
        "middle": "For organizations, this is the difference between a tool people try once and a product that changes a workflow. Adoption curves bend when the product fits existing habits, reduces friction, and gives teams a clean way to explain the value internally.",
        "insight": "That is where Product Marketing has real leverage. It turns a technical update into a buyer-understandable story: who it helps, what pain it removes, and why now is different.",
        "closing": "The question I would ask in a product review is simple: what job gets easier the first week a user touches this?",
    },
    {
        "hook": "{title} points to the part of AI adoption that does not show up in demos: trust is built through repeated, useful interactions.",
        "middle": "A launch can create curiosity, but the end-user experience decides whether curiosity becomes habit. People do not adopt AI because a company says the model is smarter. They adopt it when the product saves effort without making them nervous about the outcome.",
        "insight": "The organizational efficiency angle is practical. If the update helps teams spend less time choosing, checking, or explaining the tool, it has a better chance of becoming part of normal work.",
        "closing": "The strongest AI products will make users feel more capable, not more dependent on understanding model politics.",
    },
]

KEYWORD_ANGLES = [
    {
        "keywords": ["coding agent", "coding agents", "devin", "programmers", "codex", "copilot"],
        "hook": "{title} is not really about whether AI replaces developers. It is about whether engineering teams can make AI useful without losing review quality.",
        "middle": "That is an organizational efficiency problem. Coding agents and copilots only create leverage when the task is clear, the handoff is clean, and the team still knows who owns the final output.",
        "insight": "The product marketing mistake is to frame this as magic automation. The stronger story is simpler: fewer low-value steps, faster feedback, and more time for engineers to focus on the parts of software that still need judgment.",
        "closing": "The real test is not whether AI can write code. It is whether the team can ship better software with less coordination drag.",
    },
    {
        "keywords": ["governance", "safety", "security", "risk", "regulation", "compliance"],
        "hook": "{title} shows why AI governance is becoming part of the product experience, not just a policy document sitting somewhere nobody reads.",
        "middle": "For buyers and end users, trust has to be visible in the workflow. If safeguards are hidden, slow, or hard to explain, adoption becomes harder even when the underlying technology is strong.",
        "insight": "This is where product marketing needs to get more concrete. The message should not be 'we are responsible.' It should explain how the product helps teams use AI with fewer surprises and clearer accountability.",
        "closing": "Trust becomes easier to sell when users can see how it works.",
    },
    {
        "keywords": ["assistant", "calendar", "email", "emails", "agent", "agents", "spark"],
        "hook": "{title} highlights a hard truth about AI assistants: access to context is useful, but it is not the same thing as good judgment.",
        "middle": "The adoption curve for assistants depends on whether people feel helped, not watched. A product can have more context and still fail if the user has to double-check every suggestion or repair every awkward action.",
        "insight": "The sharper product marketing angle is not 'the assistant knows more about you.' It is 'the assistant saves effort while keeping you in control.' That distinction matters because trust is the product.",
        "closing": "The assistant that wins will probably feel less like a genius and more like a reliable teammate who knows when not to touch things.",
    },
    {
        "keywords": ["model", "models", "omni", "opus", "sonnet", "flash", "bedrock", "llama"],
        "hook": "{title} is a useful reminder that model news only becomes product news when it changes what users can do with less friction.",
        "middle": "A stronger model or a different model menu may create attention, but adoption depends on packaging. Can teams evaluate it easily? Can end users understand what changed? Can the product owner explain the value without turning the launch into a technical lecture?",
        "insight": "This is a Product Marketing problem hiding inside a technical update. The market does not just need more capability. It needs clearer reasons to switch, trust, or keep using the product.",
        "closing": "A model announcement earns its place when it changes behavior, not just the comparison chart.",
    },
    {
        "keywords": ["job", "jobs", "workforce", "layoff", "layoffs", "replace", "replacing"],
        "hook": "{title} is the kind of AI workforce story that deserves a little less panic and a lot more workflow diagnosis.",
        "middle": "Roles are rarely just task lists. When organizations rush to automate without understanding the handoffs, exceptions, and judgment calls inside a job, they risk making the operation look leaner while making the customer experience worse.",
        "insight": "The adoption lesson is uncomfortable but useful: AI creates efficiency only when the company knows which parts of the work are repeatable and which parts still need human ownership.",
        "closing": "Before replacing a role, the smarter question is whether the company can even describe the work accurately.",
    },
]


def compact_source_title(title: str, max_words: int = 9) -> str:
    normalized = (
        title.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace('"', "")
        .replace("'", "")
    )
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&+.-]*", agent.clean_text(normalized, max_length=180))
    while words and words[-1].lower() in {"and", "or", "with", "for", "to", "at", "on", "in", "when", "from"}:
        words.pop()
    if not words:
        return "AI Adoption Is Getting More Operational"
    return " ".join(words[:max_words]).strip(" -,:;") or "AI Adoption Is Getting More Operational"


def plain_news(item: agent.NewsItem, max_words: int = 46) -> str:
    text = item.summary or item.source_excerpt or item.title
    text = agent.clean_text(text, max_length=700).strip(" .")
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(" ,.;:") + "..."


def shape_for_item(item: agent.NewsItem) -> dict[str, str]:
    searchable = f"{item.title} {item.summary} {item.source_excerpt}".lower()
    for shape in KEYWORD_ANGLES:
        if any(keyword in searchable for keyword in shape["keywords"]):
            return shape
    index = zlib.crc32(f"{item.url}|{item.title}".encode("utf-8")) % len(ANGLE_SHAPES)
    return ANGLE_SHAPES[index]


def sentence_subject(item: agent.NewsItem) -> str:
    searchable = f"{item.title} {item.summary} {item.source_excerpt}"
    lowered = searchable.lower()
    company = next((entity for entity in KNOWN_ENTITY_TOKENS if entity.lower() in lowered), "")
    product = next(
        (
            entity
            for entity in ["ChatGPT", "Codex", "Gemini", "Claude", "Copilot", "Llama"]
            if entity.lower() in lowered
        ),
        "",
    )
    if company and product and company != product:
        return f"{company}'s {product} update"
    if company:
        return f"{company}'s update"
    return f"{item.source_name}'s update"


def conservative_draft(item: agent.NewsItem) -> agent.Draft:
    shape = shape_for_item(item)
    title = compact_source_title(item.title)
    source_sentence = f"According to {item.source_name}, {plain_news(item)}."
    body = (
        f"{shape['hook'].format(title=sentence_subject(item))}\n\n"
        f"{source_sentence} {shape['middle']}\n\n"
        f"{shape['insight']}\n\n"
        f"{shape['closing']}\n\n"
        f"{agent.DEFAULT_HASHTAGS}"
    )
    return agent.Draft(
        topic=item,
        title=title,
        body=body,
        source_links=[item.url],
        fact_check_notes=[
            f"Title, source, URL, and published date came from {item.source_name}'s RSS feed.",
            "Conservative writer used source metadata and clearly framed analysis only.",
        ],
    )


def is_factual_claim(claim: str) -> bool:
    stripped = claim.strip()
    lowered = stripped.lower()
    if not stripped or stripped.startswith("#"):
        return False
    if any(marker in lowered for marker in ["my read", "my takeaway", "the real test", "the question i would ask"]):
        return False
    if re.search(r"\b(should|could|might|may|probably|often|rarely|usually|if|when|where|would|needs?|matters?)\b", lowered):
        if not re.search(r"\b\d+[\w%$]*\b", lowered):
            return False
    if any(verb in lowered for verb in SOURCE_FACT_VERBS):
        return True
    if re.search(r"\b\d{4}\b|\b\d+(\.\d+)?\s*(%|million|billion|trillion|users?|customers?|developers?)\b|[$]\d", lowered):
        return True
    entity_pattern = r"\b(" + "|".join(re.escape(entity) for entity in KNOWN_ENTITY_TOKENS) + r")\b"
    if re.search(entity_pattern, stripped, re.I) and re.search(r"\b(is|are|was|were|has|have|will|can)\b", lowered):
        return True
    return False


def has_natural_closing(text: str) -> bool:
    without_hashtags = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#")).strip()
    ending = without_hashtags[-450:]
    return "?" in ending or any(
        phrase in ending.lower()
        for phrase in [
            "the real test",
            "the strongest ai products",
            "the best ai feature",
            "trust becomes easier",
            "earns its place",
        ]
    )


def has_template_header(text: str) -> bool:
    for line in text.splitlines():
        normalized = line.strip().strip(":").lower()
        if normalized in GENERIC_SECTION_HEADERS:
            return True
    return False


def validate_draft_quality(draft: agent.Draft) -> agent.Draft:
    body = draft.body.strip()
    title = draft.title.strip()
    combined = f"{title}\n{body}"
    word_count = len(re.findall(r"\b[\w']+\b", body))
    bullet_count = len(re.findall(r"(?m)^\s*[-*]\s+\S+", body))
    question_count = combined.count("?")

    if word_count < 110:
        raise agent.AgentError(f"Draft quality gate failed: body is too short ({word_count} words).")
    if word_count > 255:
        raise agent.AgentError(f"Draft quality gate failed: body is too long ({word_count} words).")
    if bullet_count > 0:
        raise agent.AgentError("Draft quality gate failed: textbook-style bullet points are not allowed.")
    if has_template_header(body):
        raise agent.AgentError("Draft quality gate failed: generic section header detected.")
    if not has_natural_closing(body):
        raise agent.AgentError("Draft quality gate failed: missing a natural closing thought or question.")
    if question_count > 1:
        raise agent.AgentError("Draft quality gate failed: too many questions for a natural closing.")
    if any(phrase.lower() in combined.lower() for phrase in FORBIDDEN_PHRASES):
        raise agent.AgentError("Draft quality gate failed: banned generic wording detected.")
    if any(re.search(pattern, combined, re.I) for pattern in MULTIPLE_CHOICE_ENDING_PATTERNS):
        raise agent.AgentError("Draft quality gate failed: multiple-choice engagement question detected.")
    return draft


def build_gemini_prompt(item: agent.NewsItem, style: str, trend_context: str) -> str:
    published_text = item.published_at.strftime("%Y-%m-%d %H:%M UTC")
    summary = item.summary or "No usable RSS summary was provided."
    forbidden = ", ".join(f'"{phrase}"' for phrase in FORBIDDEN_PHRASES)
    return f"""
You are writing one LinkedIn post like a sharp Product Manager who tracks AI closely.

Write a clear, specific, human LinkedIn draft about the actual news. The reader should quickly understand what happened, why it matters, and what practical market or product marketing lesson to take away.

Strict factual rules:
- Use only the source metadata below for factual claims.
- Every factual claim must be traceable to the source title, source name, published date, URL, RSS summary, or article excerpt.
- Never invent product versions, model names, platform partnerships, benchmarks, funding amounts, timelines, quotes, customer names, release status, or product capabilities.
- If the source does not explicitly say a product is released, available, preview, beta, testing, or rolling out, do not add that status.
- Do not use X trend context as factual support. Use it only to choose a timely angle.
- Do not browse or rely on memory.

Writing rules:
- Keep the post around 120 to 220 words.
- Use short natural paragraphs.
- Do not use bullets unless the source itself is naturally a list, and avoid textbook-style lists.
- Do not use hard-coded headers or labels such as "The News:", "The PM Lesson:", "Key Takeaways:", "What changed", "Why PMs should care", "Market signal", or "Builder takeaway".
- Do not use these phrases: {forbidden}.
- Do not use em dashes.
- Do not sound like ChatGPT, a press release, a launch note, or a generic AI newsletter.
- Translate marketing language into what the update means for organizational efficiency, adoption curves, Product Marketing, the market, or end-user experience.
- Start with a strong, simple hook that clearly explains why the news matters.
- Mention the specific company, product, launch, funding, or update in plain English, only if supported by the source.
- End with one practical takeaway or one thoughtful peer-style question. Do not end with a multiple-choice engagement question.
- End with 8 to 12 relevant hashtags.

Quality bar:
- Every sentence must either explain the news, add source-grounded context, give a practical insight, or create useful curiosity.
- Avoid abstract PM jargon.
- Avoid generic hype.
- Vary the structure so today's drafts do not all look the same.
- Sources stay outside the post body and will be appended by the Slack formatter.

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
    agent.is_factual_claim = is_factual_claim
    agent.main()


if __name__ == "__main__":
    main()

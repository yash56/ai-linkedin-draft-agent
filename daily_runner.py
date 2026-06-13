"""Daily writing policy for the scheduled LinkedIn draft agent.

The goal is not a polite news recap. The daily output should read like a sharp
LinkedIn post from a PM who understands AI adoption, failure modes, GTM, and
why the news matters to builders.
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
    "The headline is interesting",
    "The product question underneath it is more useful",
    "What changed",
    "Why PMs should care",
    "Reader value",
    "Market signal",
    "Builder takeaway",
    "Key Takeaways",
    "The News",
    "The news:",
    "The PM Lesson",
    "another AI update",
    "workflow signal",
    "quieter shift",
    "model politics",
    "doing too much homework",
    "look how advanced this is",
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

STORY_SHAPES = [
    {
        "hook": "{subject} looks like a product update. The more interesting part is what it says about adoption.",
        "setup": "The source says {news}. That is the fact pattern worth using. The PM question is not whether the announcement sounds impressive. It is whether the update makes the product easier to understand, easier to trust, or easier to sell internally.",
        "bridge": "Here is where this kind of AI update usually wins or loses:",
        "sections": [
            ("User Clarity", "If the user cannot explain what changed in one sentence, adoption slows down before the product even gets tested."),
            ("Workflow Fit", "A useful AI feature removes a step from an existing habit. A weak one asks users to build a new habit just to see the value."),
            ("Trust Surface", "The product has to show where the system is confident, where it is unsure, and where a human should still stay in the loop."),
            ("PMM Angle", "The strongest message is not capability. It is the before-and-after story users can repeat to their own team."),
        ],
        "wrap": "This is why packaging matters so much in AI. The model may create the capability, but the product and marketing decide whether anyone knows what to do with it.",
        "closing": "What would make this become a real behavior change instead of another headline people bookmark and forget?",
    },
    {
        "hook": "Everyone watches the launch. The better signal is what the launch forces users to stop tolerating.",
        "setup": "The verified update is simple: {news}. The interesting read is how it changes the expectation for similar AI products in the market.",
        "bridge": "The adoption curve will probably come down to four boring things:",
        "sections": [
            ("Setup Friction", "If it takes too much configuration, only enthusiasts try it. Mainstream users need the value to show up fast."),
            ("Default Quality", "Most users do not tune systems. They judge the first useful output and decide whether the product deserves another chance."),
            ("Internal Selling", "Enterprise adoption depends on whether one team can explain the value to another team without sounding like a vendor deck."),
            ("Failure Recovery", "When AI gets something wrong, the product needs a graceful path back to trust. Otherwise every mistake becomes a retention event."),
        ],
        "wrap": "That is the Product Marketing challenge hiding inside most AI news. The market is not short on capability. It is short on simple reasons to believe the capability will survive real usage.",
        "closing": "If you were selling this internally, what proof would make a skeptical team actually try it?",
    },
    {
        "hook": "{subject} is a reminder that AI products do not fail only because the model is weak. They fail because the system around the model is unfinished.",
        "setup": "According to the source, {news}. That gives us the news. The more useful question is what has to be true for users to feel the value in a real workflow.",
        "bridge": "The fragile parts are usually not glamorous:",
        "sections": [
            ("Data Quality", "The product can only reason over the context it can actually retrieve. Bad inputs make good models look careless."),
            ("Orchestration", "Every tool call, permission, and handoff becomes part of the user experience. If those break, the AI looks dumber than it is."),
            ("Escalation", "A serious product needs to know when to stop guessing and route the user to a safer path."),
            ("Evaluation", "Without repeatable tests, teams cannot tell whether yesterday's improvement made today's product worse."),
        ],
        "wrap": "That is the part of AI adoption that rarely fits into a launch headline. The hard work is not only generating output. It is making the output dependable enough for people to build a habit around it.",
        "closing": "What part of the AI lifecycle do you think most teams are still underestimating?",
    },
    {
        "hook": "{subject} is not just a technical update. It is a positioning problem.",
        "setup": "The source-backed news: {news}. On its own, that may sound narrow. In the market, though, narrow updates can matter when they reduce confusion for buyers or end users.",
        "bridge": "A good PMM read would focus on three things:",
        "sections": [
            ("Category Clarity", "Does this help users understand what the product is for, or does it add one more vague AI promise to the pile?"),
            ("Buyer Confidence", "The message needs to make risk feel manageable. If teams cannot explain the change, they will delay adoption."),
            ("End-User Relief", "The best AI updates remove small moments of friction that users already hate, even if the launch itself sounds technical."),
            ("Proof Loop", "The product needs a way to show that quality is improving over time, not just that the roadmap is moving."),
        ],
        "wrap": "This is why the clearest AI companies will have an advantage. They will not just ship features. They will make the value legible.",
        "closing": "How much product power is wasted when users cannot explain the value after one meeting?",
    },
]

KEYWORD_STORY_SHAPES = [
    {
        "keywords": ["agent", "agents", "assistant", "assistants", "spark"],
        "hook": "AI agents are not failing because the word agent is overused. They are failing because too many products confuse access with judgment.",
        "setup": "The source says {news}. That is the verified update. The bigger lesson is that users do not adopt agents just because the system can touch more context.",
        "bridge": "Here is where agent products usually break:",
        "sections": [
            ("Context Quality", "The agent cannot find truth inside messy docs, stale policies, and half-owned internal knowledge."),
            ("Tool Reliability", "If APIs are slow, brittle, or undocumented, the agent becomes a confident wrapper around fragile plumbing."),
            ("Escalation Logic", "The product needs to know when it is confused. Guessing is not autonomy. It is bad UX with a nicer name."),
            ("Evaluation Gaps", "Without regression tests, teams cannot prove whether the agent improved or quietly drifted."),
        ],
        "wrap": "The useful lesson is simple: agent quality is a system design problem. The model matters, but the surrounding workflow decides whether users trust it twice.",
        "closing": "What is the first weak link you would audit before putting an agent in front of customers?",
    },
    {
        "keywords": ["coding agent", "coding agents", "devin", "programmers", "codex", "copilot"],
        "hook": "AI coding tools are not really testing whether developers can be replaced. They are testing whether engineering teams can change how work moves.",
        "setup": "The verified news is this: {news}. The practical read is not that software suddenly writes itself. It is that the bottleneck moves.",
        "bridge": "The teams that get value will obsess over the parts around code generation:",
        "sections": [
            ("Spec Quality", "A coding agent is only as useful as the task definition. Vague tickets create confident but misaligned output."),
            ("Review Loops", "Human review does not disappear. It moves closer to architecture, tradeoffs, edge cases, and maintainability."),
            ("Test Discipline", "If tests are weak, speed becomes dangerous. The team ships faster and discovers the mess later."),
            ("Ownership", "Someone still owns the final decision. If nobody does, the agent becomes a very fast source of ambiguity."),
        ],
        "wrap": "The best positioning for coding agents is not replacement. It is leverage. Less time on repetitive implementation, more time on judgment-heavy engineering work.",
        "closing": "Can your team absorb AI-generated speed without lowering the engineering bar?",
    },
    {
        "keywords": ["model", "models", "omni", "opus", "sonnet", "flash", "bedrock", "llama"],
        "hook": "Model news gets attention. Product adoption comes from what teams can do with the model without needing a PhD in comparison charts.",
        "setup": "The source-backed update is: {news}. That is the factual base. The market question is whether this makes the product easier to choose, easier to trust, or easier to operationalize.",
        "bridge": "A useful product read has four layers:",
        "sections": [
            ("Use-Case Fit", "A stronger model only matters when it maps to a job users already care about."),
            ("Switching Cost", "Teams do not change models for vibes. They need a reason that beats migration work, risk, and re-testing."),
            ("Evaluation", "If quality cannot be measured in the user's context, the buying decision becomes guesswork."),
            ("Packaging", "The model is the engine. The product experience is what turns it into something people actually adopt."),
        ],
        "wrap": "That is why the best AI launches are not only about capability. They make the decision easier for builders, buyers, and end users at the same time.",
        "closing": "What would make a model update meaningful enough for your team to actually change behavior?",
    },
    {
        "keywords": ["governance", "safety", "security", "risk", "regulation", "compliance"],
        "hook": "AI governance is becoming a product feature whether teams like it or not.",
        "setup": "The source says {news}. That matters because trust is no longer something companies can hide in a policy page after the launch.",
        "bridge": "The real adoption blockers usually look like this:",
        "sections": [
            ("Auditability", "Users and buyers need to understand what happened, not just accept that the system produced an answer."),
            ("Control Design", "Good governance shows up as usable controls, not a compliance maze that everyone routes around."),
            ("Human Escalation", "The product needs a clean path for moments where automation should stop and human judgment should take over."),
            ("Buyer Trust", "Security and governance claims have to become easy to explain in procurement, legal, and customer conversations."),
        ],
        "wrap": "The companies that make governance feel operational will move faster than the companies that treat it as launch paperwork.",
        "closing": "Where does your team need trust to show up before AI adoption feels safe?",
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
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&+.-]*", agent.clean_text(normalized, max_length=180))
    while words and words[-1].lower() in {"and", "or", "with", "for", "to", "at", "on", "in", "when", "from"}:
        words.pop()
    if not words:
        return "The AI Adoption Test"
    return " ".join(words[:max_words]).strip(" -,:;") or "The AI Adoption Test"


def plain_news(item: agent.NewsItem, max_words: int = 42) -> str:
    text = item.summary or item.source_excerpt or item.title
    text = agent.clean_text(text, max_length=700).strip(" .")
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(" ,.;:") + "..."


def shape_for_item(item: agent.NewsItem) -> dict[str, object]:
    searchable = f"{item.title} {item.summary} {item.source_excerpt}".lower()
    for shape in KEYWORD_STORY_SHAPES:
        if any(keyword in searchable for keyword in shape["keywords"]):
            return shape
    index = zlib.crc32(f"{item.url}|{item.title}".encode("utf-8")) % len(STORY_SHAPES)
    return STORY_SHAPES[index]


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


def format_sections(sections: list[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {text}" for label, text in sections)


def conservative_draft(item: agent.NewsItem) -> agent.Draft:
    shape = shape_for_item(item)
    title = compact_source_title(item.title)
    news = plain_news(item)
    body = (
        f"{str(shape['hook']).format(subject=sentence_subject(item))}\n\n"
        f"{str(shape['setup']).format(news=news)}\n\n"
        f"{shape['bridge']}\n"
        f"{format_sections(shape['sections'])}\n\n"
        f"{shape['wrap']}\n\n"
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
    if any(marker in lowered for marker in ["my read", "my takeaway", "the real test", "the question is"]):
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


def has_reader_question(text: str) -> bool:
    without_hashtags = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#")).strip()
    return "?" in without_hashtags[-700:]


def has_template_header(text: str) -> bool:
    for line in text.splitlines():
        normalized = line.strip().strip(":").lower()
        if normalized in GENERIC_SECTION_HEADERS:
            return True
    return False


def labeled_section_count(text: str) -> int:
    pattern = r"(?m)^\s*(?:[\U0001F300-\U0001FAFF]\ufe0f?\s*)?[A-Z][A-Za-z0-9 /&+-]{2,38}:\s+\S+"
    return len(re.findall(pattern, text))


def validate_draft_quality(draft: agent.Draft) -> agent.Draft:
    body = draft.body.strip()
    title = draft.title.strip()
    combined = f"{title}\n{body}"
    word_count = len(re.findall(r"\b[\w']+\b", body))
    bullet_count = len(re.findall(r"(?m)^\s*[-*]\s+\S+", body))
    section_count = labeled_section_count(body)
    question_count = combined.count("?")

    if word_count < 190:
        raise agent.AgentError(f"Draft quality gate failed: body is too short ({word_count} words).")
    if word_count > 460:
        raise agent.AgentError(f"Draft quality gate failed: body is too long ({word_count} words).")
    if section_count < 3 and bullet_count < 3:
        raise agent.AgentError("Draft quality gate failed: missing a useful breakdown with at least 3 concrete points.")
    if has_template_header(body):
        raise agent.AgentError("Draft quality gate failed: generic section header detected.")
    if not has_reader_question(body):
        raise agent.AgentError("Draft quality gate failed: missing a reader question near the end.")
    if question_count > 4:
        raise agent.AgentError("Draft quality gate failed: too many questions.")
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
You are writing one LinkedIn post like a sharp Product Manager who tracks AI deeply.

The bar:
Write like the post is meant for PMs, founders, AI builders, and tech operators who want to understand the practical lesson behind the news. It should be readable by a smart non-expert. It should not sound like a newsletter summary, press release, or generic ChatGPT output.

The desired style:
- Start with a strong tension-led hook.
- Explain the verified news in plain English.
- Turn the news into a practical breakdown with 3 to 4 concrete labeled points.
- Use labels like "Data Quality:", "Escalation Logic:", "Review Loops:", "Buyer Confidence:", or other source-relevant labels.
- Each labeled point must teach something specific and understandable.
- Keep paragraphs short.
- Be slightly opinionated, but do not invent facts.
- End with one strong human question or takeaway before hashtags.
- Use 8 to 12 relevant hashtags.

Strict factual rules:
- Use only the source metadata below for factual claims.
- Every factual claim must be traceable to the source title, source name, published date, URL, RSS summary, or article excerpt.
- Never invent product versions, model names, platform partnerships, benchmarks, funding amounts, timelines, quotes, customer names, release status, or product capabilities.
- If the source does not explicitly say a product is released, available, preview, beta, testing, or rolling out, do not add that status.
- Do not use X trend context as factual support. Use it only to choose a timely angle.
- Do not browse or rely on memory.

Writing rules:
- Length: 230 to 380 words.
- Do not use em dashes.
- Do not use these phrases: {forbidden}.
- Avoid hard-coded generic headers like "The News:", "The PM Lesson:", "Key Takeaways:", "What changed", "Why PMs should care", "Market signal", or "Builder takeaway".
- Do not use fake certainty. If the source is thin, keep the factual recap short and make the breakdown a clearly framed product/market lesson.
- Do not end with a multiple-choice engagement question.
- Sources stay outside the post body and will be appended by the Slack formatter.

Useful angles to prefer:
- Why teams actually adopt or ignore the product.
- What the update changes for end users.
- Where enterprise AI systems fail in practice.
- What Product Marketing should make clear.
- Which operational bottleneck the news exposes.
- Why the market should care beyond the launch headline.

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

# ai-linkedin-draft-agent

A Python MVP that collects fresh AI and Product Management news, selects the best 2 to 3 source-backed topics, drafts LinkedIn posts, and sends the output to Slack through an Incoming Webhook.

## Flow

```text
News Sources
  |
  v
Research Collector
  |
  v
Fact Filter + Ranking
  |
  v
LinkedIn Draft Writer
  |
  v
Slack Sender
```

## Guardrails

- Uses only RSS items with a clear title, source, URL, and published date.
- Keeps the freshness window between 24 and 48 hours.
- Prefers official sources, then credible tech and product sources.
- Filters for configured topics such as AI agents, Gemini, ChatGPT, Claude, Product Management, launches, and coding agents.
- Gives configured trusted sources extra ranking weight.
- Uses Gemini 3 Flash Preview for draft writing when `GEMINI_API_KEY` is configured.
- Writes like a sharp Product Manager who tracks AI deeply, using one of six rotating styles.
- Runs an automated source-pack claim audit before sending drafts to Slack.
- Generates only 2 to 3 drafts per run when qualifying items are available.
- Includes hook, post body, suggested ending, source links, and fact-check notes.
- Avoids invented numbers, timelines, product names, funding amounts, benchmarks, and product claims.
- Avoids generic AI hype and strips em dashes from generated draft text.

## Source configuration

Edit `sources.yml` to tune:

- `topics`: keywords and themes that an item must match before it can become a draft.
- `trusted_sources`: sources that receive extra ranking weight.
- `sources`: enabled RSS feeds with source names, categories, and credibility labels.

Anthropic is listed as a trusted source and topic, but its requested RSS URL is disabled because it returned 404 during verification.

## Writing styles

Each generated post uses one of these styles:

- Product teardown
- Launch analysis
- Founder/investor signal
- PM lesson
- Slightly sarcastic industry observation
- What this means for builders breakdown

The prompt requires short paragraphs, no corporate fluff, no unsupported claims, and an ending with a strong opinion, question, or PM takeaway.

## Fact-check pass

Before sending to Slack, the agent audits each generated draft against the source pack: title, source, published date, URL, category, credibility, and RSS summary.

If a factual-looking claim is not supported by that source pack, the agent removes or rewrites it conservatively and adds a `Risk flags` section to the Slack output.

## Setup

1. Create a Slack Incoming Webhook and copy the webhook URL.
2. Copy `.env.example` to `.env`.
3. Add your webhook URL to `.env`.
4. Install dependencies:

```bash
pip install -r requirements.txt
```

## Run locally

Preview without sending to Slack:

```bash
python agent.py --dry-run
```

Send to Slack:

```bash
python agent.py
```

## GitHub Actions

The workflow in `.github/workflows/daily.yml` runs every day at 9:00 AM IST, which is 3:30 AM UTC.

Add this repository secret before enabling the workflow:

```text
SLACK_WEBHOOK_URL
GEMINI_API_KEY
```

## Notes

Gemini receives only the verified RSS metadata for each selected item: title, source, published date, URL, category, credibility, and summary. If `GEMINI_API_KEY` is missing or the Gemini call fails, the agent falls back to deterministic source-backed templates.

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
```

## Notes

This MVP intentionally uses deterministic source-backed templates instead of asking a language model to invent prose from memory. That keeps the daily output safer and easier to fact-check. If you later add an LLM rewrite step, keep the source metadata and fact-check notes as hard constraints.

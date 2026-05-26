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
- Keeps the freshness window between 24 and 48 hours, with the daily workflow set to 36 hours for fresher topics.
- Prefers official sources, then credible tech and product sources.
- Filters for configured topics such as AI agents, Gemini, ChatGPT, Claude, Product Management, launches, and coding agents.
- Gives configured trusted sources extra ranking weight.
- Uses Gemini 3 Flash Preview for draft writing when `GEMINI_API_KEY` is configured.
- Optionally reads recent X posts when `X_BEARER_TOKEN` is configured, but only as trend context for angle selection.
- Writes like a sharp Product Manager who tracks AI deeply, using narrative post archetypes inspired by role shifts, failure breakdowns, builder playbooks, launch lessons, and uncomfortable PM truths.
- Runs an automated source-pack claim audit before sending drafts to Slack.
- Fetches article excerpts for selected items so Gemini has richer source context than RSS metadata alone.
- Refuses to send generic fallback drafts unless `ALLOW_TEMPLATE_FALLBACK=true` is explicitly set.
- Generates only 2 to 3 drafts per run when qualifying items are available.
- Sends clean LinkedIn-ready drafts to Slack with source links only.
- Avoids invented numbers, timelines, product names, funding amounts, benchmarks, and product claims.
- Avoids generic AI hype and strips em dashes from generated draft text.

## Source configuration

Edit `sources.yml` to tune:

- `topics`: keywords and themes that an item must match before it can become a draft.
- `trusted_sources`: sources that receive extra ranking weight.
- `sources`: enabled RSS feeds with source names, categories, and credibility labels.
- `x_queries`: optional X search terms used to find trend signals. X results are not treated as factual sources.

Anthropic is listed as a trusted source and topic, but its requested RSS URL is disabled because it returned 404 during verification.

## Writing styles

Each generated post uses one of these archetypes:

- Role shift narrative
- Why this fails breakdown
- Builder playbook
- Launch lesson
- Uncomfortable PM truth
- Trend-to-takeaway essay

The prompt requires a short title, a punchy opening contrast, practical sections with reader-friendly labels, no corporate fluff, no unsupported claims, a strong ending, and relevant hashtags.

## Fact-check pass

Before sending to Slack, the agent audits each generated draft against the source pack: title, source, published date, URL, category, credibility, RSS summary, and fetched article excerpt.

If a factual-looking claim is not supported by that source pack, the agent removes or rewrites it conservatively. Risk flags remain internal logs and are not shown in the Slack draft output.

## Setup

1. Create a Slack Incoming Webhook and copy the webhook URL.
2. Copy `.env.example` to `.env`.
3. Add your webhook URL to `.env`.
4. Add `GEMINI_API_KEY` for Gemini draft writing.
5. Optional: add `X_BEARER_TOKEN` if you want X trend context.
6. Install dependencies:

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
X_BEARER_TOKEN
```

`X_BEARER_TOKEN` is optional. Without it, the agent still generates source-backed drafts from RSS feeds.

## Notes

Gemini receives verified source metadata plus a fetched article excerpt for each selected item. Optional X trend context is used only to shape the angle, not to add facts. If Gemini cannot produce enough publishable drafts, the agent fails instead of sending generic filler.

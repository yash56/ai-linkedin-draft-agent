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
- Keeps the freshness window between 24 and 48 hours, with the daily workflow allowed to backfill up to 48 hours so it can reliably produce 2 to 3 useful drafts.
- Prefers official sources, then credible tech and product sources.
- Filters for configured topics such as AI agents, Gemini, ChatGPT, Claude, Product Management, launches, and coding agents.
- Gives configured trusted sources extra ranking weight.
- Uses Gemini 3 Flash Preview for draft writing when `GEMINI_API_KEY` is configured.
- Optionally reads recent X posts when `X_BEARER_TOKEN` is configured, but only as trend context for angle selection.
- Writes like a sharp Product Manager who tracks AI deeply, with short, specific posts instead of generic AI commentary.
- Runs an automated source-pack claim audit before sending drafts to Slack.
- Rejects drafts that are too short, too generic, missing useful sections, or too similar to another draft.
- Ranks a larger candidate pool so one weak article does not block the whole daily run.
- Fetches article excerpts for selected items so Gemini has richer source context than RSS metadata alone.
- Uses `daily_runner.py` to apply the stricter daily writing policy and a conservative source-backed backup writer when Gemini/API issues would otherwise block Slack drafts.
- Sends a Slack failure alert instead of failing silently if it still cannot produce enough publishable drafts.
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

Each generated post uses one of these styles:

- Product teardown
- Launch analysis
- Founder/investor signal
- PM lesson
- Slightly sarcastic industry observation
- What this means for builders breakdown

The prompt requires a strong simple hook, a plain-English explanation of the actual news, one sharp product insight, useful bullets, no corporate fluff, no unsupported claims, a reader question near the end, and relevant hashtags.

Daily posts follow this LinkedIn-ready recipe:

- A specific short title.
- A hook that immediately explains why the news matters.
- A plain-English explanation of the actual news.
- 2 to 4 useful bullet points with a PM, product, builder, or business lens.
- One opinionated product takeaway.
- A thoughtful reader question near the end.
- Hashtags inside the post body, with source links appended separately in Slack.

The stricter daily runner rejects vague filler such as `PMs should read this as a workflow signal`, `Here is the source-backed context`, `A practical PM read is simple`, `The useful question is`, `What changed`, `Why PMs should care`, and `Reader value`.

## Fact-check pass

Before sending to Slack, the agent audits each generated draft against the source pack: title, source, published date, URL, category, credibility, RSS summary, and fetched article excerpt.

If a factual-looking claim is not supported by that source pack, the agent removes it conservatively. If the result becomes thin, generic, missing bullets, missing a reader question, or duplicate-looking, the agent rejects the draft instead of sending it to Slack. Risk flags remain internal logs and are not shown in the Slack draft output.

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
python daily_runner.py --dry-run
```

Send to Slack:

```bash
python daily_runner.py
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

Gemini receives verified source metadata plus a fetched article excerpt for each selected item. Optional X trend context is used only to shape the angle, not to add facts. The scheduled workflow runs `daily_runner.py`, which wraps `agent.py` with the latest writing policy and can still produce structured, source-backed drafts without inventing facts if Gemini cannot produce enough publishable drafts.

# Seller assistant: design

An LLM assistant inside the seller dashboard (`backend/assistant/`). It answers questions about
the store's sales, stock and pricing through tools, and can *propose* price changes, which only a
human can apply. This document describes the design as built; the price gateway never went beyond
dry-run before the product was stopped.

## Core loop

- `engine.run_turn` runs the tool-calling loop, capped by `ASSISTANT_MAX_TOOL_ITERATIONS`.
- Tools are registered with a `ToolContext` (store id, user role, dashboard section).
  **Store id, token and role come only from the context, never from model arguments**, so a
  prompt cannot reach another store's data.
- Write tools are disabled by default (`ASSISTANT_ALLOW_WRITE_TOOLS=0`) and otherwise require an
  explicit `confirmed` flag in the chat.
- `StubProvider` / `ScriptedProvider` replace the LLM in tests and evals; no network is needed.
- Conversations and token usage are stored per store (`storage.py`).

## Read tools

| Tool | What it does |
|---|---|
| `get_sales_stats` | Sales summary and daily series from the existing loaders |
| `build_report` | Sections `sales`, `finance` (commissions, margin), `stock` (forecast), `pricing` (repricer state), `problems` |
| `search_store_knowledge` | Retrieval over the store's own data (see below) |
| overview, orders, product search, repricer status | Thin wrappers over existing services |

Numbers are computed by code from the same loaders the dashboard uses; the model only comments
on them. Tool outputs have hard size caps.

### Store retrieval

- Per-store in-memory index with a TTL (`ASSISTANT_RAG_TTL_SECONDS`, default 300 s).
- Pure-Python BM25 (Russian and English). Documents: product cards and stock, daily sales,
  price-war snapshots, repricer logs, master stock groups. Individual orders are not indexed.
- Every hit carries its source. Size caps: 800 characters per product document, 1,500 per card.
- Behind a `Retriever` interface, so embedding search (as in
  [manual-rag](https://github.com/Venius24/manual-rag): BGE-M3 + pgvector) can be plugged in later.
- Kaspi's API has no product characteristics, so they come from `store_card_facts`: only rows
  marked as reviewed reach retrieval and the buyer-chat bot.

## Writes go through a human

1. The model calls `propose_price(sku, new_price, reason)` only when the user asks. It creates a
   `pending` row in `assistant_price_proposals` and changes nothing.
2. The price is validated against the product's floor and ceiling from the repricer config.
3. A manager or owner approves or rejects it: `GET /proposals`,
   `POST /proposals/{id}/approve|reject`. Approval re-validates and calls a `PriceGateway`.
4. Only `DryRunGateway` exists: it logs the change and does not call Kaspi.

The approval step is the barrier, so `propose_price` does not need the chat `confirmed` flag.
The number of pending proposals is capped (`ASSISTANT_MAX_PENDING_PROPOSALS`).

## Observability

- Every turn is stored as a run: total and per-step latency, tokens, cost, tool errors.
  Steps keep only kind, name, duration, ok flag and a truncated error; messages and tool
  arguments are not stored in traces.
- Cost uses per-million-token prices from config; an unknown price gives `cost_usd = null`
  rather than a guess.
- Run writes use a savepoint, so a failed insert never breaks the user's answer. On a provider
  error the turn's messages and proposals are rolled back, but the failed run is still recorded.
- Endpoints: `GET /runs`, `GET /runs/{id}` (another store's run is a 404),
  `GET /metrics/summary?days=7` (counts, tokens, cost, error and truncation rates, p50/p95).
- Prometheus metrics with fixed labels and no store, product or text values:
  `assistant_runs_total{outcome}`, `assistant_run_duration_seconds`,
  `assistant_tokens_total{type}`, `assistant_tool_calls_total{tool,ok}`,
  `assistant_cost_usd_total`, plus `chatbot_*` metrics for the buyer-chat autoresponder.
  Grafana dashboard: [deploy/monitoring](../deploy/monitoring/README.md).

## Evals

`python -m evals.run`: 19 deterministic cases on scripted provider scenarios, temporary SQLite,
fixtures instead of loaders, network blocked. They check tool trajectories and invariants:

- the agent never applies a price itself;
- a proposal below the floor is rejected;
- an empty search result does not lead to invented facts;
- another store's data is unreachable.

These test the loop and the guardrails, not the model's answer quality. A `--live` mode compares
trajectories with a real provider. Details: [evals/README.md](../evals/README.md).

## Configuration

| Variable | Default |
|---|---|
| `ASSISTANT_PROVIDER` / `ASSISTANT_MODEL` | `deepseek` / `deepseek-chat` (no key → stub) |
| `ASSISTANT_MAX_TOOL_ITERATIONS` | 4 |
| `ASSISTANT_HISTORY_LIMIT` | 20 |
| `ASSISTANT_TIMEOUT_SECONDS` | 45 |
| `ASSISTANT_MAX_OUTPUT_TOKENS` | 1200 |
| `ASSISTANT_ALLOW_WRITE_TOOLS` | 0 |
| `ASSISTANT_PRICE_GATEWAY` | `dry_run` (the only option) |
| `ASSISTANT_PRICE_PROMPT_PER_1M`, `ASSISTANT_PRICE_COMPLETION_PER_1M` | unset → cost unknown |

## Known limits

- The price gateway is dry-run only: approval does not change the price on Kaspi.
- Retrieval is lexical (BM25); embeddings were planned behind the `Retriever` interface.
- Evals are deterministic and do not measure answer quality; the live comparison was not run.
- The shared sync DB helper calls `Base.metadata.create_all()`, so new tables can appear before
  their migration runs.

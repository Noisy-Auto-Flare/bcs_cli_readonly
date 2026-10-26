# BCS Trade API readonly CLI - SPEC

Base: `https://be.broker.ru`
Docs: https://trade-api.bcs.ru/
Scope: readonly only. Python stdlib-only CLI will be built from this file.
Standard check instrument: board `TQBR`, ticker `SBER`.

Total surfaces: 17 = 1 auth + 9 strict GET + 7 POST-readonly.

---

## 1. Auth

| Method | Full URL | Body (x-www-form-urlencoded) | Purpose |
|---|---|---|---|
| POST | `https://be.broker.ru/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token` | `grant_type=refresh_token` + `client_id=trade-api-read` + `refresh_token=<из BCS_REFRESH_TOKEN>` (Content-Type: application/x-www-form-urlencoded) | Get Bearer access token for all calls below |

Rules:
- `client_id` is always `trade-api-read`.
- Never use `trade-api-write`.
- Token in header: `Authorization: Bearer <token>`.
- `.env` is never read by docs tools and secrets are never printed.

---

## 2. Strict GET (9)

| # | Method | Full URL | Key query params | Purpose |
|---|---|---|---|---|
| 1 | GET | `https://be.broker.ru/trade-api-bff-portfolio/api/v1/portfolio` | `portfolioId` / `accountId` | Portfolio positions, cash, totals |
| 2 | GET | `https://be.broker.ru/trade-api-bff-limit/api/v1/limits` | `portfolioId` | Limits and available funds |
| 3 | GET | `https://be.broker.ru/trade-api-bff-marginal-indicators/api/v1/instruments-discounts` | `board`, `ticker` | Discounts / risk rates by instrument |
| 4 | GET | `https://be.broker.ru/trade-api-market-data-connector/api/v1/candles-chart` | `board=TQBR`, `ticker=SBER`, `timeframe`, `from`, `to`, `count` | OHLC candles history |
| 5 | GET | `https://be.broker.ru/trade-api-market-data-connector/api/v1/order-book` | `board=TQBR`, `ticker=SBER`, `depth` | Current order book snapshot |
| 6 | GET | `https://be.broker.ru/trade-api-information-service/api/v1/instruments/by-type` | `type`, `board`, `size` (default 10), `page` (query-only) | Instrument directory filtered by type |
| 7 | GET | `https://be.broker.ru/trade-api-information-service/api/v1/trading-schedule/daily-schedule` | `date`, `board` | Daily trading schedule |
| 8 | GET | `https://be.broker.ru/trade-api-information-service/api/v1/trading-schedule/status` | `board` | Current trading status (open/closed) |
| 9 | GET | `https://be.broker.ru/trade-api-bff-operations/api/v1/orders/{id}` | path `id` (no body) | Single order by ID |

Notes:
- GET has no body.
- `{id}` is a path param, URL-encoded.
- Reference check for 4, 5: `board=TQBR&ticker=SBER`.
- Wire name for board is `classCode` (CLI `--board`/`--class-code` alias); discounts forwards `classCode`+`ticker`, by-type forwards `classCode`.

---

## 3. POST-readonly (7)

POST here is search / batch read only. No state change.

| # | Method | Full URL | Body form (JSON) | Purpose |
|---|---|---|---|---|
| 10 | POST | `https://be.broker.ru/trade-api-market-data-connector/api/v1/quotes` | `{"instruments": [{"board": "TQBR", "ticker": "SBER"}]}` | Batch quotes snapshot |
| 11 | POST | `https://be.broker.ru/trade-api-market-data-connector/api/v1/last-trades` | `{"ticker": "SBER", "classCode": "TQBR"}` + optional `startDateTime`/`endDateTime`/`side` | Last trades tape (single instrument) |
| 12 | POST | `https://be.broker.ru/trade-api-information-service/api/v1/instruments/by-tickers` | body `{"tickers": ["SBER", ...]}` (plain strings); `size`/`page` in query | Instrument cards by tickers |
| 13 | POST | `https://be.broker.ru/trade-api-information-service/api/v1/instruments/by-isins` | body `{"isins": ["<ISIN>"]}`; `size`/`page` in query | Instrument cards by ISINs |
| 14 | POST | `https://be.broker.ru/trade-api-bff-operations/api/v1/orders/search` | `{"portfolioId?": "<id>", "startDateTime": "<iso>", "endDateTime": "<iso>"}` | Search orders (docs-stale: live 404 HTML via Angie) |
| 15 | POST | `https://be.broker.ru/trade-api-bff-operations/api/v1/trades/search` | `{"portfolioId?": "<id>", "startDateTime": "<iso>", "endDateTime": "<iso>"}` | Search trades (docs-stale: live 404 HTML via Angie) |
| 16 | POST | `https://be.broker.ru/trade-api-bff-operations/api/v1/operations/search` | `{"portfolioId?": "<id>", "startDateTime": "<iso>", "endDateTime": "<iso>"}` | Search operations (docs-stale: live 404 HTML via Angie) |

Notes:
- `Content-Type: application/json` for all 7.
- Etalon body for 10 uses `TQBR` / `SBER`.
- Wire name is `classCode`; CLI accepts `--board` (and `--class-code`) as an alias and sends `classCode`.
- `last-trades` takes a single instrument only (`--tickers` with >1 fails, exit 2); no `limit` field on the wire.
- `by-tickers`/`by-isins` pagination (`size`/`page`) goes in the query string, not the body.
- `orders/search`, `trades/search`, `operations/search` are docs-stale: doc-aligned paths/bodies, live answers 404 HTML via Angie.

---

## 4. Excluded mutations (forbidden)

CLI must never call these. No command maps to them.

| Forbidden call | Reason |
|---|---|
| `POST /orders` (create order) | Mutation, needs `trade-api-write` |
| `POST /orders/{id}` (update order) | Mutation |
| `POST /orders/{id}/cancel` (cancel order) | Mutation |
| Any `/sync`, `/async` order placement variants | Mutations |
| Any flow with `client_id=trade-api-write` | Write scope, out of readonly CLI |
| Any DELETE / PUT / PATCH on Trade API | Mutations by definition |

If a spec URL matches create / update / cancel, refuse to implement.

---

## 5. Rate limits

| Rule | Value |
|---|---|
| Global | 10 RPS per token/IP |
| `POST operations/search` | 3 RPS (stricter) |
| On `429 Too Many Requests` | Exponential backoff + retry after `Retry-After` |
| On `401` | Refresh token once, then retry once |
| CLI default | Sequential calls, sleep to fit limits, no parallel flood |

---

## 6. CLI mapping (16 commands -> endpoint)

| # | CLI command | Endpoint |
|---|---|---|
| 1 | `portfolio` | `GET trade-api-bff-portfolio/api/v1/portfolio` |
| 2 | `limits` | `GET trade-api-bff-limit/api/v1/limits` |
| 3 | `discounts` | `GET trade-api-bff-marginal-indicators/api/v1/instruments-discounts` |
| 4 | `candles --board TQBR --ticker SBER` | `GET trade-api-market-data-connector/api/v1/candles-chart` |
| 5 | `book --board TQBR --ticker SBER` | `GET trade-api-market-data-connector/api/v1/order-book` |
| 6 | `inst-by-type` | `GET trade-api-information-service/api/v1/instruments/by-type` |
| 7 | `schedule-daily` | `GET trade-api-information-service/api/v1/trading-schedule/daily-schedule` |
| 8 | `schedule-status` | `GET trade-api-information-service/api/v1/trading-schedule/status` |
| 9 | `order-get --id {id}` | `GET trade-api-bff-operations/api/v1/orders/{id}` |
| 10 | `quotes` | `POST trade-api-market-data-connector/api/v1/quotes` |
| 11 | `last-trades` | `POST trade-api-market-data-connector/api/v1/last-trades` |
| 12 | `inst-by-tickers` | `POST trade-api-information-service/api/v1/instruments/by-tickers` |
| 13 | `inst-by-isins` | `POST trade-api-information-service/api/v1/instruments/by-isins` |
| 14 | `orders-search` | `POST trade-api-bff-operations/api/v1/orders/search` |
| 15 | `trades-search` | `POST trade-api-bff-operations/api/v1/trades/search` |
| 16 | `ops-search` | `POST trade-api-bff-operations/api/v1/operations/search` |

Auth is implicit before any command, not a separate CLI command.

---

## 7. Exit codes and --out

Exit codes (unified, no 3/4/5):

| Code | Meaning |
|---|---|
| 0 | ok |
| 1 | API or runtime error (`{"error": "..."}` on stderr) |
| 2 | usage error or missing `BCS_REFRESH_TOKEN` |

Global flag `--out <path>` duplicates the (masked) JSON output to a file.
A write failure fails with exit 1. Every byte to stdout, stderr, and `--out`
passes through masking.

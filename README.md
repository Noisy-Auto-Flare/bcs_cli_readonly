# BCS Trade API Readonly CLI

Single file, zero dependency CLI for read only access to the BCS Trade API. File: `bcs.py`. Standard library only. No mutations.

Base URL: `https://be.broker.ru`. Docs: https://trade-api.bcs.ru/

Reference instrument used across examples: board `TQBR`, ticker `SBER`.

## Install

Requirements: Python 3.10 or newer. No `pip install` step. No third party packages.

```powershell
python --version
python bcs.py --help
```

All output is JSON. Every command accepts these flags:

```powershell
python bcs.py --json --pretty --base-url https://be.broker.ru --timeout 30 portfolio
```

`--json` prints compact JSON. Without it (or with `--pretty`) output is indented. `--base-url` overrides the API host. `--timeout` sets HTTP timeout in seconds. `--out <path>` duplicates the masked JSON output to a file (write failure exits 1).

## Auth setup

The CLI needs `BCS_REFRESH_TOKEN`. It reads the value from the environment first, then from a `.env` file next to `bcs.py`. Environment wins when both exist.

```powershell
$env:BCS_REFRESH_TOKEN="paste-your-refresh-token-here"
python bcs.py portfolio
```

Alternative with a `.env` file:

```text
BCS_REFRESH_TOKEN=paste-your-refresh-token-here
```

Check presence without printing the value:

```powershell
if ($env:BCS_REFRESH_TOKEN) { "env present" } else { "env absent" }
Test-Path .\.env
python bcs.py portfolio --help
```

Rules:

* Never print the token. Never paste it into logs, tickets, or screenshots.
* Never commit `.env`. This repo ships a `.gitignore` that excludes it.
* `client_id` is always `trade-api-read`. The CLI never uses `trade-api-write`.
* Auth flow: `POST /trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token` with `grant_type=refresh_token`, form encoded. The access token lives about 24 hours and is cached in memory only. On `401` the CLI refreshes once and retries once.

## Readonly scope

Allowed verbs: `GET` and `POST` to search or batch read endpoints listed below. `POST` here never changes state.

Forbidden, and not implemented by any command:

| Forbidden call | Reason |
|---|---|
| `POST /orders` (create order) | Mutation, needs `trade-api-write` |
| `POST /orders/{id}` (update order) | Mutation |
| `POST /orders/{id}/cancel` (cancel order) | Mutation |
| Any `/sync`, `/async` order placement variants | Mutations |
| Any flow with `client_id=trade-api-write` | Write scope |
| Any `PUT`, `DELETE`, `PATCH` on the Trade API | Mutations by definition |

## Non-goals

* No WebSocket streaming.
* No trading, no order placement, no cancels, no edits.
* No portfolio analytics or advice. This tool prints raw API JSON.

## All 16 commands

| # | Command | Method and endpoint |
|---|---|---|
| 1 | `portfolio` | `GET trade-api-bff-portfolio/api/v1/portfolio` |
| 2 | `limits` | `GET trade-api-bff-limit/api/v1/limits` |
| 3 | `discounts` | `GET trade-api-bff-marginal-indicators/api/v1/instruments-discounts` |
| 4 | `candles` | `GET trade-api-market-data-connector/api/v1/candles-chart` |
| 5 | `book` | `GET trade-api-market-data-connector/api/v1/order-book` |
| 6 | `inst-by-type` | `GET trade-api-information-service/api/v1/instruments/by-type` |
| 7 | `schedule-daily` | `GET trade-api-information-service/api/v1/trading-schedule/daily-schedule` |
| 8 | `schedule-status` | `GET trade-api-information-service/api/v1/trading-schedule/status` |
| 9 | `order-get` | `GET trade-api-bff-operations/api/v1/orders/{id}` |
| 10 | `quotes` | `POST trade-api-market-data-connector/api/v1/quotes` |
| 11 | `last-trades` | `POST trade-api-market-data-connector/api/v1/last-trades` |
| 12 | `inst-by-tickers` | `POST trade-api-information-service/api/v1/instruments/by-tickers` |
| 13 | `inst-by-isins` | `POST trade-api-information-service/api/v1/instruments/by-isins` |
| 14 | `orders-search` | `POST trade-api-bff-operations/api/v1/orders/search` |
| 15 | `trades-search` | `POST trade-api-bff-operations/api/v1/trades/search` |
| 16 | `ops-search` | `POST trade-api-bff-operations/api/v1/operations/search` |

Auth is implicit before every command. There is no separate login command.

## Examples

Portfolio and limits:

```powershell
python bcs.py portfolio --json
python bcs.py portfolio --pretty
python bcs.py limits --json
python bcs.py limits --portfolio-id 12345 --json
```

Risk discounts by instrument:

```powershell
python bcs.py discounts --tickers SBER --json
python bcs.py discounts --tickers SBER,GAZP --pretty
```

Candles. The server wants full ISO (`yyyy-MM-dd'T'HH:mm:ss.SSSX`). The CLI also accepts short `YYYY-MM-DD` and expands it to `YYYY-MM-DDT00:00:00.000+03:00`:

```powershell
python bcs.py candles --board TQBR --ticker SBER --timeframe D --from 2026-10-19 --to 2026-10-23 --json
python bcs.py candles --class TQBR --ticker SBER --tf H1 --from 2026-10-18T00:00:00.000+03:00 --to 2026-10-25T00:00:00.000+03:00 --pretty
```

Large ranges are split into chunks by the CLI and merged without duplicates.

Order book snapshot:

```powershell
python bcs.py book --board TQBR --ticker SBER --json
python bcs.py book --class TQBR --ticker SBER --pretty
```

Instruments by type, with pagination:

```powershell
python bcs.py inst-by-type --type STOCK --size 1 --page 0 --json
python bcs.py inst-by-type --type STOCK --size 1 --page 1 --json
```

Trading schedule:

```powershell
python bcs.py schedule-status --board TQBR --json
python bcs.py schedule-status --class TQBR --pretty
python bcs.py schedule-daily --board TQBR --ticker SBER --json
python bcs.py schedule-daily --class TQBR --ticker SBER --date 2026-10-23 --json
```

See CONFLICT-1 below about `--date`.

Single order by ID. Unknown IDs return graceful JSON error, exit 1, no traceback:

```powershell
python bcs.py order-get --id test-000 --json
```

Quotes and last trades:

```powershell
python bcs.py quotes --tickers TQBR:SBER --json
python bcs.py quotes --tickers TQBR:SBER,TQBR:GAZP --pretty
python bcs.py last-trades --tickers TQBR:SBER --json
```

Body note: `last-trades` sends `{"ticker", "classCode"}` taken from the first instrument, per official docs. There is no `instruments` array and no `limit` in the request body.

Instrument cards:

```powershell
python bcs.py inst-by-tickers --tickers SBER --json
python bcs.py inst-by-tickers --tickers SBER,GAZP --size 10 --page 0 --json
python bcs.py inst-by-isins --isins RU0009029540 --json
```

Body note: `inst-by-tickers` sends `{"tickers": ["SBER", ...]}` as plain strings, board prefix stripped. `size` and `page` go in the query string. `inst-by-isins` sends `{"isins": [...]}`.

Search family. Request body is doc aligned: `{"startDateTime", "endDateTime"}` from `--from` and `--to`:

```powershell
python bcs.py orders-search --from 2026-01-26T00:00:00.000Z --to 2026-10-25T00:00:00.000Z --json
python bcs.py trades-search --from 2026-01-26T00:00:00.000Z --to 2026-10-25T00:00:00.000Z --json
python bcs.py ops-search --from 2026-01-26T00:00:00.000Z --to 2026-10-25T00:00:00.000Z --json
```

The parser also accepts `--portfolio`/`--portfolio-id` for these three commands (forwarded as `portfolioId` when given), but the request body stays doc aligned and only carries the date window plus optional portfolio. See CONFLICT-2 below: live currently answers 404 for all three paths.

Per command help:

```powershell
python bcs.py candles --help
python bcs.py book --help
python bcs.py quotes --help
python bcs.py orders-search --help
```

## Rate limits and retries

| Rule | Value |
|---|---|
| Global | 10 RPS per token or IP |
| `POST operations/search` | 3 RPS, stricter (`ops-search` sleeps 0.4 s before the call) |
| On `429` | Exponential backoff up to 3 retries, honors `Retry-After` |
| On `401` | Refresh token once, retry once |
| Default | Sequential calls, no parallel flood |

## Masking rule

Every byte printed to stdout or stderr passes through `mask_text()`. Any `Bearer <token>` value and any `refresh_token` value becomes `***`. Long chains of 20 or more characters were replaced with `***` before live results were recorded. If you see `***` in output, that is the mask working, not missing data.

Error format is always JSON: `{"error": "..."}` on stderr. Exit codes: `0` success, `1` API or runtime error, `2` usage error or missing `BCS_REFRESH_TOKEN`.

## Known live CONFLICTs

These come from `tests/LIVE_RESULTS.md` (run 2026-10-25, 11 pass plus 5 graceful). Paths and bodies follow the official docs. SPEC.md disagrees in places; docs win.

* CONFLICT-1, `schedule-daily` date. Official docs list only `classCode` plus `ticker`, with no date param. SPEC.md names `date`. The CLI forwards `--date` as query `date` when given and omits it otherwise. Live ignores it: even a Friday date (2026-10-23) returns `NOT_FOUND, dailyScheduleLine is empty`. Forwarding stays because it is harmless.
* CONFLICT-2, search paths return 404. The CLI uses doc correct paths `POST /api/v1/orders|trades|operations/search` on `trade-api-bff-operations` with doc correct bodies. Live answers `404 Not Found` HTML via Angie ingress for all three. Probes of sibling routes (`trade-api-operations` prefix, `GET orders`, `GET all-order-list`) show no replacement list route on this deployment. Bodies stay doc aligned so the CLI is correct the day routes reappear. The live suite accepts the JSON wrapped HTML error as graceful.
* Candles input. Docs require full ISO. The CLI accepts short `YYYY-MM-DD` and expands it. Covered by live case 4b.
* `last-trades` and `by-tickers` bodies. Docs define `{"ticker", "classCode"}` and `{"tickers": ["SBER", ...]}`. SPEC.md shows older `instruments[]` and object forms. The CLI sends the doc forms.
* CONFLICT-3, server-side wire flags dropped (docs have no such fields) vs client-side envelope kept: `last-trades --limit` and `orders/trades/ops-search --limit/--offset` are never sent on the wire; search keeps `--from/--to` (+`--portfolio/--portfolio-id`); client-side `--limit/--offset` envelope is post-processing only and stays; exit codes are 0/1/2 only and `--out <path>` duplicates masked output to a file.

## Verification

Offline suite, no network:

```powershell
pytest tests/test_unit.py tests/test_readonly_guard.py
```

Result recorded: 20 passed (14 unit plus 6 readonly guard).

Live suite, needs `BCS_REFRESH_TOKEN`, recorded 2026-10-25: 16 passed. Do not rerun it casually; each run hits the real API.

```powershell
pytest tests/test_live.py
```

The readonly guard asserts: only `GET` and `POST` to allowlisted paths, no `trade-api-write`, no `PUT`/`DELETE`/`PATCH`, no `requests` dependency, secrets masked.

## Agent use

Global flags are mirrored on every command: --json, --pretty, --base-url, --timeout, --out, --limit, --offset, --count, --depth, --fields, --quiet.

| Flag | Behavior |
|---|---|
| --limit N [--offset M] | Opt-in envelope. No --limit returns raw API JSON unchanged. |
| --count N | Passthrough to query count on candles only. Integer, else exit 2. |
| --depth N | Passthrough to query depth on book only. Integer, else exit 2. |
| --fields a,b | Projection of dict keys or list items. Envelope aware. |
| --out <path> | Duplicate masked JSON to file. Write failure exits 1. |
| --quiet --out <path> | Full masked JSON goes to file, stdout gets summary only. |
| size default 10 | inst-by-type, inst-by-tickers, inst-by-isins use 10 when --size omitted. |

Copy-paste (needs BCS_REFRESH_TOKEN in env or .env):

```powershell
python bcs.py portfolio --limit 1 --json
python bcs.py candles --board TQBR --ticker SBER --timeframe D --from 2026-10-19 --to 2026-10-23 --limit 2 --json
python bcs.py candles --board TQBR --ticker SBER --timeframe D --from 2026-10-19 --to 2026-10-23 --count 5 --json
python bcs.py portfolio --fields ticker,bars --out dump.json --quiet --json
```

Envelope shape from paginate_envelope (list input shown, dict input keeps outer keys and slices inner candles, bars, records, data, items, or single list key):

```json
{"data": [], "total": 10, "returned": 1, "truncated": true, "offset": 0}
```

No list key in dict adds total 0, returned 0, truncated false, plus offset. Multi-list dicts use priority candles/bars/records/data/items then single-list fallback, else total 0 with no slice. Non integer --limit or --offset exits 2.

Quiet summary shape from emit (stdout when --quiet plus --out is used):

```json
{"savedTo": "dump.json", "records": 1, "bytes": 1234, "sample": []}
```

records counts list items, or inner list items, or 1 when no list. sample holds first 2 items. bytes is file size of saved path.

Notes: every byte on stdout and stderr passes mask_text. Bearer values and refresh_token values become ***. Errors are {"error": "..."} on stderr. Exits are 0 success, 1 API or runtime error, 2 usage error or missing token. Readonly scope is GET and POST to allowlisted paths only. No mutations.

## Lightweight audit (read only, no code changes)

* `bcs.py`: 936 lines, one file. Over the 500 line budget because GET and POST regions were merged into a single file with doc aligned bodies, chunked candles fetching, retry logic, and masking. Still single file, still stdlib only.
* Imports (AST scan): `argparse`, `datetime`, `json`, `os`, `re`, `sys`, `time`, `urllib` (with `urllib.error`, `urllib.parse`, `urllib.request`). All standard library. No third party imports.
* Secret scan: `Select-String "trade-api-write|requests|PUT|DELETE"` over `bcs.py` finds no forbidden scope, no `requests` package, no mutation verbs outside negative guard strings and help text. `trade-api-read` is the only client id.
* Tests present: `tests/test_unit.py`, `tests/test_readonly_guard.py`, `tests/test_live.py`, plus `tests/LIVE_RESULTS.md`. Counts: 14 unit plus 6 guard equals 20 offline, 16 live.
* `.env` exists next to `bcs.py` and is excluded by `.gitignore`. Its value was never read during this audit. Presence was checked with `Test-Path` only.

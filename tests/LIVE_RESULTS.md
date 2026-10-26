# LIVE matrix BCS CLI - 2026-10-25 (masked, NO tokens)

Offline: `pytest tests/test_unit.py tests/test_readonly_guard.py` -> 20 passed.
Live: `pytest tests/test_live.py` -> 16 passed.
Key read ONLY by `bcs.py` (`load_refresh_token`); token value never printed/stored.
Long chains >= 20 chars replaced with `***` before recording.

Format: command | exit | JSON key names | verdict.

## Contract source of truth (official docs, fetched 2026-10-25)

| # | Service prefix | Method + path | Body / query per docs |
|---|---|---|---|
| candles | trade-api-market-data-connector | GET /api/v1/candles-chart | query: classCode, ticker, startDate, endDate, timeFrame; full ISO `yyyy-MM-dd'T'HH:mm:ss.SSSX` |
| schedule-daily | trade-api-information-service | GET /api/v1/trading-schedule/daily-schedule | query: classCode (req), ticker (req). NO date param in docs |
| last-trades | trade-api-market-data-connector | POST /api/v1/last-trades | body: {ticker (req), classCode (req), startDateTime?, endDateTime?, side?}. NO instruments, NO limit |
| by-tickers | trade-api-information-service | POST /api/v1/instruments/by-tickers | body: {tickers: ["SBER",...] (strings, req)}; page/size as query |
| orders-search | trade-api-bff-operations | POST /api/v1/orders/search | body: {startDateTime (req), endDateTime (req), side?, orderStatus?, orderTypes?, tickers?, classCodes?} |
| trades-search | trade-api-bff-operations | POST /api/v1/trades/search | body: {startDateTime (req), endDateTime (req), side?, tickers?} |
| ops-search | trade-api-bff-operations | POST /api/v1/operations/search | body (all optional): {operationTypes?, statuses?, startDateTime?, endDateTime?, isins?, tickers?, currencies?} |

Cross-checked against wire-model schemas (brokapi-bcs-protocol docs.rs, schema checked 2026-10-19)
and the Go client service map (Orders+Deals share trade-api-bff-operations). SPEC.md bodies for
11/12/14-16 disagree with the docs above; docs win per task brief.

## PASS (exit 0, valid JSON, no leaks)

| # | Command | exit | JSON keys | Verdict |
|---|---------|------|-----------|---------|
| 1 | `portfolio` | 0 | list: type, subAccountId, agreementId, account, exchange, ticker, displayName, ... | PASS |
| 2 | `limits` | 0 | depoLimit, moneyLimits | PASS |
| 3 | `discounts --tickers SBER` | 0 | list: ticker, discountLong, ... | PASS |
| 4a | `candles --class TQBR --ticker SBER --from 2026-10-18T00:00:00.000+03:00 --to 2026-10-25T00:00:00.000+03:00 --tf D` | 0 | ticker, classCode, startDate, endDate, timeFrame, bars | PASS (unchanged) |
| 4b | `candles --class TQBR --ticker SBER --from 2026-10-19 --to 2026-10-23 --tf D` (short dates, NEW) | 0 | ticker, classCode, startDate, endDate, timeFrame, bars | PASS (was INVALID_FORMAT startDate) |
| 5 | `book --class TQBR --ticker SBER` | 0 | depth, dateTime, ticker, classCode, bidVolume, askVolume, bids, asks | PASS |
| 6 | `inst-by-type --type STOCK --size 1 --page 0/1` | 0 | list[1], pages differ | PASS |
| 7 | `schedule-status --class TQBR` | 0 | tradingSessionTypeId, tradingSessionType, tradingSessionStatus, nextSessionDate | PASS |
| 8 | `quotes --tickers TQBR:SBER` | 0 | records | PASS |
| 9 | `inst-by-isins --isins ***` (ISIN from inst-by-type) | 0 | list: ticker, boards, shortName, isin, ... | PASS (untouched) |
| 10 | `last-trades --tickers TQBR:SBER` (NEW body) | 0 | records | PASS (was VALIDATION_ERROR REQUIRED_FIELD ticker/classCode) |
| 11 | `inst-by-tickers --tickers SBER` (NEW body) | 0 | list[2] | PASS (was VALIDATION_ERROR INVALID_FORMAT tickers) |

## Graceful-API (valid JSON error, no traceback, no leaks)

| # | Command | exit | Error body (keys/type) | Verdict |
|---|---------|------|------------------------|---------|
| 12 | `schedule-daily --class TQBR --ticker SBER [--date 2026-10-23]` | 1 | {"error": type=NOT_FOUND, dailyScheduleLine is empty} | GRACEFUL (Sunday 2026-10-25: no sessions; --date forwarded as `date` but server ignores it, see CONFLICT-1) |
| 13 | `order-get --id test-000` | 1 | {"error": 404 Not Found (HTML Angie, truncated)} | GRACEFUL (expected, untouched) |
| 14 | `orders-search --from 2026-10-18T00:00:00.000+03:00 --to 2026-10-19T00:00:00.000+03:00 --limit 5` (client envelope, not wire) | 1 | {"error": 404 Not Found (HTML Angie, truncated)} | GRACEFUL-FINDING, see CONFLICT-2 |
| 15 | `trades-search --from 2026-10-18T00:00:00.000+03:00 --to 2026-10-19T00:00:00.000+03:00 --limit 5` (client envelope, not wire) | 1 | {"error": 404 Not Found (HTML Angie, truncated)} | GRACEFUL-FINDING, see CONFLICT-2 |
| 16 | `ops-search --from 2026-10-18T00:00:00.000+03:00 --to 2026-10-19T00:00:00.000+03:00 --limit 5` (client envelope, not wire) | 1 | {"error": 404 Not Found (HTML Angie, truncated)} | GRACEFUL-FINDING, see CONFLICT-2 |

## Negatives

| Check | exit | Result |
|-------|------|--------|
| `book --class TQBR --ticker NOSUCHTICKER123` | 1 | {"error": type=NOT_FOUND, traceId=***} - graceful, no traceback |
| Missing key (FileNotFoundError branch -> exit 2 + `missing BCS_REFRESH_TOKEN`) | 2 | clean, masked, no leaks |

## Masking

- Every stdout/stderr scanned: `Bearer \S{20,}` without `***` - 0 matches; JWT-like blobs - 0; raw refresh tokens - 0.
- Only exit codes, JSON key names and error types recorded. No raw tokens anywhere.

## Code changes (bcs.py, point edits only)

1. `_norm_dt()` added (no new imports): short `YYYY-MM-DD` -> `YYYY-MM-DDT00:00:00.000+03:00`; full ISO passes through. `cmd_candles` normalizes `--from/--to` before building query; chunk-format fixed to full `"%Y-%m-%dT%H:%M:%S"` (was re-emitting short dates for D chunks).
2. `cmd_schedule_daily` forwards `--date` (parser flag already existed) as query `date` only when given; omitted otherwise (request identical to before when flag absent).
3. `cmd_last_trades`: body is now `{"ticker", "classCode"}` from the first instrument (docs); undocumented `instruments[]`/`limit` removed.
4. `cmd_inst_by_tickers`: body is now `{"tickers": ["SBER",...]}` (plain strings, board prefix stripped); `size`/`page` moved to query string; `_post_cmd` gained optional `query` passthrough.
5. `_search_body`: `{"startDateTime", "endDateTime"}` from `--from/--to` (docs); undocumented `portfolioId`/`from`/`to`/`limit`/`offset` removed. Paths and `ops` service prefix verified against docs and left unchanged.

## CONFLICTs (docs vs live - PASS-version kept, no guessing)

- CONFLICT-1 schedule-daily `--date`: official docs list ONLY classCode+ticker (no date param); SPEC.md row 7 names `date`. CLI forwards `--date` as query `date`, but live still returns NOT_FOUND/empty for a Friday date (2026-10-23), i.e. the server ignores it. Kept: forwarding is harmless (param omitted when flag absent, offline+live green).
- CONFLICT-2 search paths: docs paths kept verbatim (POST /api/v1/orders|trades|operations/search on trade-api-bff-operations, also probed trade-api-operations -> same 404). With doc-correct bodies live still answers 404 HTML via Angie ingress for all three, so the live deployment does not serve these routes (docs stale or routes moved). Probes of GET `orders` (JSON REQUIRED_FIELD orderId) and GET `all-order-list` (404 HTML) show no replacement list route on this service. Bodies stay doc-aligned so the CLI is correct the day routes reappear; live suite `_graceful` accepts the JSON-wrapped HTML error.

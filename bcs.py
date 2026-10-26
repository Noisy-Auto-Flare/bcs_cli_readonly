#!/usr/bin/env python3
"""BCS Trade API readonly CLI (stdlib only).

Scope: readonly only. No mutations (see SPEC.md section 4).
Auth: grant_type=refresh_token + client_id=trade-api-read via
https://be.broker.ru/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token
with Content-Type application/x-www-form-urlencoded.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL_DEFAULT = "https://be.broker.ru"
CLIENT_ID = "trade-api-read"
TOKEN_PATH = "/trade-api-keycloak/realms/tradeapi/protocol/openid-connect/token"

ALLOWED_POST_PATHS = (
    "/trade-api-market-data-connector/api/v1/quotes",
    "/trade-api-market-data-connector/api/v1/last-trades",
    "/trade-api-information-service/api/v1/instruments/by-tickers",
    "/trade-api-information-service/api/v1/instruments/by-isins",
    "/trade-api-bff-operations/api/v1/orders/search",
    "/trade-api-bff-operations/api/v1/trades/search",
    "/trade-api-bff-operations/api/v1/operations/search",
)

# In-memory cache for access_token (T3/T4 will fill it via auth exchange).
_cached_token = {"access_token": None, "expires_at": 0.0}


def load_refresh_token():
    """Return BCS_REFRESH_TOKEN from env or .env next to bcs.py.

    Never prints the value. Raises FileNotFoundError if missing.
    """
    token = os.environ.get("BCS_REFRESH_TOKEN", "")
    if token and token.strip():
        return token.strip()
    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(dotenv_path):
        raise FileNotFoundError("BCS_REFRESH_TOKEN not set and .env not found")
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.lower().startswith("export "):
                line = line[7:].lstrip()
            key, _, value = line.partition("=")
            if key.strip() != "BCS_REFRESH_TOKEN":
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1].strip()
            else:
                value = value.strip("\"'").strip()
            if "#" in value:
                value = value.split("#", 1)[0].strip().strip("\"'").strip()
            if value:
                return value
    raise FileNotFoundError("BCS_REFRESH_TOKEN not found in env or .env")


REDACT_WORDS = ("token", "password", "apikey", "secret", "refresh",
                "bearer", "jwt", "sk-")


def mask_text(s):
    """Replace secret-looking values with ***. Mirrors tick.py mask_text."""
    if not isinstance(s, str):
        try:
            s = str(s)
        except Exception:
            return "***"
    # Bare JWT (eyJ...) without Bearer prefix.
    try:
        s = re.sub(r"eyJ[A-Za-z0-9_\-]{10,}", "***", s)
    except Exception:
        pass
    # Google API keys + OpenAI-style sk- keys.
    try:
        s = re.sub(r"AIza[A-Za-z0-9_\-]{10,}", "***", s)
        s = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "***", s)
    except Exception:
        pass
    # Bearer tokens (header or log line).
    try:
        s = re.sub(r"(?i)Bearer +[A-Za-z0-9_\-.~+/=]+", "Bearer ***", s)
    except Exception:
        pass
    # Generic key=value / key: value secrets (token/password/apikey/...).
    try:
        for w in REDACT_WORDS:
            s = re.sub(r"(?i)([\"']?%s[\"']?\s*[:=]\s*)([\"']?)([^\"',\s&}]+)(\2)"  # noqa: E501
                       % re.escape(w), r"\1\2***\4", s)
    except Exception:
        pass
    # "accessToken"/"access_token" JSON values (explicit, mirrors old shape).
    try:
        s = re.sub(
            r'("access[_-]?[Tt]oken"\s*:\s*")[^"]*(")',
            r"\1***\2",
            s,
        )
        s = re.sub(
            r"('access[_-]?[Tt]oken'\s*:\s*')[^']*(')",
            r"\1***\2",
            s,
        )
    except Exception:
        pass
    return s


def get_access_token(base_url=None, timeout=30.0, force=False):
    """POST refresh_token grant, cache access_token in-memory."""
    now = time.time()
    cached = _cached_token.get("access_token")
    if cached and not force and now < float(_cached_token.get("expires_at") or 0):
        return cached
    try:
        refresh = load_refresh_token()
    except FileNotFoundError:
        sys.stderr.write(
            mask_text(json.dumps({"error": "missing BCS_REFRESH_TOKEN"}) + "\n")
        )
        raise SystemExit(2)
    base = (base_url or BASE_URL_DEFAULT).rstrip("/")
    url = base + TOKEN_PATH
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "client_id": CLIENT_ID,
            "refresh_token": refresh,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            eb = e.read().decode("utf-8", errors="replace")
        except Exception:
            eb = ""
        try:
            detail = json.loads(eb) if eb else {}
        except Exception:
            detail = {}
        if isinstance(detail, dict):
            err = detail.get("error", "token request failed")
        else:
            err = "token request failed"
        sys.stderr.write(
            mask_text(json.dumps({"error": str(err), "status": e.code}) + "\n")
        )
        raise SystemExit(1)
    except urllib.error.URLError as e:
        sys.stderr.write(
            mask_text(json.dumps({"error": "token request failed"}) + "\n")
        )
        raise SystemExit(1)
    try:
        data = json.loads(raw)
    except Exception:
        sys.stderr.write(
            mask_text(json.dumps({"error": "bad token response"}) + "\n")
        )
        raise SystemExit(1)
    token = None
    if isinstance(data, dict):
        token = data.get("access_token") or data.get("accessToken")
    if not token:
        sys.stderr.write(
            mask_text(json.dumps({"error": "bad token response"}) + "\n")
        )
        raise SystemExit(1)
    try:
        expires_in = int(data.get("expires_in", 300) or 300)
    except Exception:
        expires_in = 300
    _cached_token["access_token"] = token
    _cached_token["expires_at"] = time.time() + max(0, expires_in - 30)
    return token


def auth_header(base_url=None, timeout=30.0):
    """Return Authorization header using cached/fresh access token."""
    return {"Authorization": "Bearer " + get_access_token(base_url, timeout)}


SERVICE_PREFIXES = {
    "portfolio": "trade-api-bff-portfolio",
    "limits": "trade-api-bff-limit",
    "discounts": "trade-api-bff-marginal-indicators",
    "market": "trade-api-market-data-connector",
    "info": "trade-api-information-service",
    "ops": "trade-api-bff-operations",
}

API_VERSION_SEG = "api/v1"
_SEP = "/"
_QMARK = "?"


def _parse_error_body(raw):
    """Extract readable message from HTTP error body without traceback."""
    if not raw:
        return "HTTP error"
    try:
        data = json.loads(raw)
    except Exception:
        return mask_text(raw[:500])
    if isinstance(data, dict):
        for key in ("error", "message", "detail", "description"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return mask_text(val.strip()[:500])
        return mask_text(json.dumps(data, ensure_ascii=False)[:500])
    return mask_text(str(data)[:500])


def api_request(method, service_prefix, path, base_url, timeout,
                query=None, body=None, access_token=None):
    """Generic readonly HTTP call with retry.

    Builds base/service_prefix/api/v1/path + query string.
    JSON body for POST with Content-Type application/json.
    Retries 429 with exponential backoff, refreshes once on 401.
    """
    verb = (method or "").upper()
    if verb not in ("GET", "POST"):
        raise ValueError(mask_text("unsupported method: %s" % (method,)))
    prefix = str(SERVICE_PREFIXES.get(service_prefix, service_prefix))
    base = (base_url or BASE_URL_DEFAULT).rstrip(_SEP)
    clean_path = (path or "").strip(_SEP)
    url = base + _SEP + prefix + _SEP + API_VERSION_SEG + _SEP + clean_path
    if query:
        url = url + _QMARK + urllib.parse.urlencode(query, doseq=True)
    data = None
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = "Bearer " + str(access_token)
    if body is not None and verb == "POST":
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    retries_429 = 0
    refreshed = False
    while True:
        req = urllib.request.Request(url, data=data, headers=headers, method=verb)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                if not raw.strip():
                    return {}
                try:
                    return json.loads(raw)
                except Exception:
                    raise RuntimeError(mask_text("bad JSON response")) from None
        except urllib.error.HTTPError as e:
            status = getattr(e, "code", None)
            try:
                err_raw = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_raw = ""
            if status == 401 and not refreshed:
                refresh_fn = globals().get("get_access_token")
                if callable(refresh_fn):
                    try:
                        try:
                            new_token = refresh_fn(base_url, timeout, force=True)
                        except TypeError:
                            try:
                                new_token = refresh_fn(force=True)
                            except TypeError:
                                new_token = refresh_fn()
                    except Exception as refresh_err:
                        raise RuntimeError(mask_text(str(refresh_err)[:500])) from None
                    access_token = new_token
                    headers["Authorization"] = "Bearer " + str(new_token)
                    refreshed = True
                    continue
            if status == 429 and retries_429 < 3:
                retry_after = 0.0
                try:
                    hdrs = getattr(e, "headers", None)
                    ra = hdrs.get("Retry-After") if hdrs is not None else None
                    if ra is not None:
                        retry_after = float(str(ra).strip())
                except Exception:
                    retry_after = 0.0
                backoff = float(1 << retries_429)
                delay = backoff
                if retry_after > 0:
                    delay = retry_after if retry_after > backoff else backoff
                retries_429 += 1
                time.sleep(delay)
                continue
            raise RuntimeError(_parse_error_body(err_raw)) from None


_DROP_ID_KEYS = ("subaccountid", "agreementid")


def _drop_ids(obj):
    """Strip account-identifying keys for stdout samples (file cache keeps raw)."""
    if isinstance(obj, dict):
        return {k: _drop_ids(v) for k, v in obj.items()
                if str(k).lower() not in _DROP_ID_KEYS}
    if isinstance(obj, list):
        return [_drop_ids(v) for v in obj]
    return obj


def emit(data, as_json, pretty, out_path=None, *, quiet=False):
    """Write result to stdout: compact JSON if as_json, else indent 2."""
    if as_json and not pretty:
        text = json.dumps(data, ensure_ascii=False)
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    if quiet and out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(mask_text(text) + "\n")
        except Exception as e:
            fail(mask_text(str(e))[:500], 1)
        if isinstance(data, list):
            records = len(data)
            sample = data[:2]
        elif isinstance(data, dict):
            inner = None
            for key in ("candles", "bars", "records", "data", "items", "bids", "asks"):
                if isinstance(data.get(key), list):
                    inner = data[key]
                    break
            if inner is None:
                list_keys = [v for v in data.values() if isinstance(v, list)]
                if len(list_keys) == 1:
                    inner = list_keys[0]
            if inner is not None:
                records = len(inner)
                sample = inner[:2]
            else:
                records = 1
                sample = []
        else:
            records = 1
            sample = []
        try:
            size = os.path.getsize(out_path)
        except Exception:
            size = 0
        summary = {"savedTo": out_path, "records": records, "bytes": size, "sample": _drop_ids(sample)}
        if as_json and not pretty:
            summary_text = json.dumps(summary, ensure_ascii=False)
        else:
            summary_text = json.dumps(summary, ensure_ascii=False, indent=2)
        sys.stdout.write(mask_text(summary_text) + "\n")
        return
    sys.stdout.write(mask_text(text) + "\n")
    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(mask_text(text) + "\n")
        except Exception as e:
            fail(mask_text(str(e))[:500], 1)


def fail(msg, code):
    """Write {"error": msg} to stderr (masked) and exit with code."""
    text = json.dumps({"error": msg}, ensure_ascii=False)
    sys.stderr.write(mask_text(text) + "\n")
    raise SystemExit(code)


def _require_nonneg_int(value, cmd, flag):
    s = str(value).strip() if value is not None else ""
    if s == "":
        return None
    try:
        n = int(s)
    except ValueError:
        n = None
    if n is None or n < 0:
        fail("%s: --%s must be an integer" % (cmd, flag), 2)
        return None
    return s


def _get(args, svc, path, query=None):
    try:
        tok = get_access_token(args.base_url, args.timeout)
        q = {k: v for k, v in (query or {}).items() if v is not None and str(v).strip() != ""}
        return api_request("GET", svc, path, args.base_url, args.timeout, query=q, access_token=tok)
    except Exception as e:
        fail(mask_text(str(e))[:500], 1)

def cmd_portfolio(args):
    q = {}
    pid = getattr(args, "portfolio_id", None)
    if pid is not None and str(pid).strip() != "":
        q["portfolioId"] = str(pid).strip()
    aid = getattr(args, "account_id", None)
    if aid is not None and str(aid).strip() != "":
        q["accountId"] = str(aid).strip()
    return _get(args, "portfolio", "portfolio", q or None)

def cmd_limits(args):
    q = {}
    pid = getattr(args, "portfolio_id", None)
    if pid is not None and str(pid).strip() != "":
        q["portfolioId"] = str(pid).strip()
    return _get(args, "limits", "limits", q or None)

def cmd_discounts(args):
    q = {}
    if getattr(args, "tickers", None):
        q["tickers"] = ",".join(t.strip() for t in str(args.tickers).split(",") if t.strip())
    board = getattr(args, "class_code", None) or getattr(args, "board", None)
    if board is not None and str(board).strip() != "":
        q["classCode"] = str(board).strip()
    tkr = getattr(args, "ticker", None)
    if tkr is not None and str(tkr).strip() != "":
        q["ticker"] = str(tkr).strip()
    if not q:
        fail("discounts: give --tickers and/or --ticker/--board", 2)
    return _get(args, "discounts", "instruments-discounts", q or None)

_MSK = datetime.timezone(datetime.timedelta(hours=3))
_TZ_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2}|[+-]\d{2})$")

def _parse_iso_aware(s):
    t = str(s).strip()
    if not t:
        return None
    if len(t) == 10 and t[4] == "-" and t[7] == "-":
        t = t + "T00:00:00+03:00"
    elif t.endswith("Z"):
        t = t[:-1] + "+00:00"
    elif not _TZ_RE.search(t):
        t = t + "+03:00"
    try:
        dt = datetime.datetime.fromisoformat(t)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_MSK)
    return dt

def _ep(s):
    dt = _parse_iso_aware(s)
    if dt is None:
        return None, None
    return dt.timestamp(), "iso"

def _norm_dt(s):
    """Expand short YYYY-MM-DD to full ISO with MSK offset; full ISO passes through."""
    if s is None:
        return None
    t = str(s).strip()
    if len(t) == 10 and t[4] == "-" and t[7] == "-" and t.replace("-", "").isdigit():
        return t + "T00:00:00.000+03:00"
    return s


def _fmt_msk(ts):
    return datetime.datetime.fromtimestamp(ts, tz=_MSK).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+03:00"


def cmd_candles(args):
    tf = getattr(args, "timeframe", None) or "H1"
    fr, to = _norm_dt(getattr(args, "from_", None)), _norm_dt(getattr(args, "to", None))
    q0 = {"classCode": args.board, "ticker": args.ticker, "startDate": fr, "endDate": to, "timeFrame": tf}
    cnt = _require_nonneg_int(getattr(args, "count", None), "candles", "count")
    if cnt is not None:
        q0["count"] = cnt
    tpm = {"M1": 1, "H1": 60, "D": 1440}.get(str(tf).upper(), 60)
    ep1, f1, ep2, f2 = None, None, None, None
    if fr and to:
        ep1, f1 = _ep(str(fr))
        ep2, f2 = _ep(str(to))
    if ep1 is not None and ep2 is not None and ep2 > ep1:
        rng, cap = (ep2 - ep1) / 60, 1440 * tpm
        n = int(rng // cap) + (1 if rng % cap else 0)
        if n > 1:
            first, items, seen = None, [], set()
            for i in range(n):
                ws = ep1 + i * cap * 60.0
                we = min(ws + cap * 60.0, ep2)
                q = dict(q0)
                q["startDate"] = fr if i == 0 else _fmt_msk(ws)
                q["endDate"] = to if i == n - 1 else _fmt_msk(we)
                r = _get(args, "market", "candles-chart", q)
                if first is None:
                    first = r
                seq = r if isinstance(r, list) else (r.get("candles", r.get("data", [])) if isinstance(r, dict) else [])
                for c in seq or []:
                    k = json.dumps(c, sort_keys=True) if isinstance(c, dict) else str(c)
                    if k not in seen:
                        seen.add(k)
                        items.append(c)
            if isinstance(first, list):
                return items
            d = dict(first) if isinstance(first, dict) else {}
            d["candles"] = items
            return d
    return _get(args, "market", "candles-chart", q0)

def cmd_book(args):
    q = {"classCode": args.board, "ticker": args.ticker}
    dep = _require_nonneg_int(getattr(args, "depth", None), "book", "depth")
    if dep is not None:
        q["depth"] = dep
    return _get(args, "market", "order-book", q)

def cmd_inst_by_type(args):
    _sz = _require_nonneg_int(getattr(args, "size", None), "inst-by-type", "size")
    if _sz is None:
        _sz = "10"
    _pg = _require_nonneg_int(getattr(args, "page", None), "inst-by-type", "page")
    if getattr(args, "type_", None) is None or str(args.type_).strip() == "":
        fail("inst-by-type: give --type", 2)
    q = {"type": args.type_, "size": _sz, "page": _pg, "baseAssetTicker": getattr(args, "base_asset_ticker", None)}
    cc = getattr(args, "class_code", None) or getattr(args, "board", None)
    if cc is not None and str(cc).strip() != "":
        q["classCode"] = str(cc).strip()
    return _get(args, "info", "instruments/by-type", q)

def cmd_schedule_daily(args):
    if getattr(args, "board", None) is None or str(args.board).strip() == "":
        fail("schedule-daily: give --board/--class", 2)
    if getattr(args, "ticker", None) is None or str(args.ticker).strip() == "":
        fail("schedule-daily: give --ticker", 2)
    q = {"classCode": args.board, "ticker": args.ticker}
    if getattr(args, "date", None):
        q["date"] = args.date
    return _get(args, "info", "trading-schedule/daily-schedule", q)


def cmd_schedule_status(args):
    if getattr(args, "board", None) is None or str(args.board).strip() == "":
        fail("schedule-status: give --board/--class", 2)
    return _get(args, "info", "trading-schedule/status", {"classCode": args.board})

def cmd_order_get(args):
    oid = urllib.parse.quote(str(args.id_), safe="")
    return _get(args, "ops", "orders/" + oid)


def build_parser():
    p = argparse.ArgumentParser(prog="bcs.py", description="BCS Trade API readonly CLI")
    p.add_argument("--json", action="store_true", help="compact JSON output")
    p.add_argument("--pretty", action="store_true", help="pretty JSON output")
    p.add_argument("--base-url", default=BASE_URL_DEFAULT, help="API base URL")
    p.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    p.add_argument("--out", default=None, help="duplicate JSON output to file (masked)")
    p.add_argument("--limit", default=None, help="result limit")
    p.add_argument("--offset", default=None, help="result offset")
    p.add_argument("--count", default=None, help="result count")
    p.add_argument("--depth", default=None, help="result depth")
    p.add_argument("--fields", default=None, help="result fields")
    p.add_argument("--quiet", action="store_true", help="quiet output")
    sub = p.add_subparsers(dest="command", required=True)

    def _add_common(c):
        # Subparser defaults are SUPPRESS so values given before the
        # subcommand (global flags) are preserved unless overridden after it.
        # Top-level parser above holds the real defaults.
        c.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                       help="compact JSON output")
        c.add_argument("--pretty", action="store_true", default=argparse.SUPPRESS,
                       help="pretty JSON output")
        c.add_argument("--base-url", default=argparse.SUPPRESS, help="API base URL")
        c.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                       help="HTTP timeout seconds")
        c.add_argument("--out", default=argparse.SUPPRESS,
                       help="duplicate JSON output to file (masked)")
        c.add_argument("--limit", default=argparse.SUPPRESS, help="result limit")
        c.add_argument("--offset", default=argparse.SUPPRESS, help="result offset")
        c.add_argument("--count", default=argparse.SUPPRESS, help="result count")
        c.add_argument("--depth", default=argparse.SUPPRESS, help="result depth")
        c.add_argument("--fields", default=argparse.SUPPRESS, help="result fields")
        c.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS,
                       help="quiet output")
        return c

    c = _add_common(sub.add_parser("portfolio", help="GET portfolio"))
    c.add_argument("--portfolio-id", default=None)
    c.add_argument("--account-id", default=None)
    c.set_defaults(func=cmd_portfolio)

    c = _add_common(sub.add_parser("limits", help="GET limits"))
    c.add_argument("--portfolio-id", default=None)
    c.set_defaults(func=cmd_limits)

    c = _add_common(sub.add_parser("discounts", help="GET instruments discounts"))
    c.add_argument("--board", default=None)
    c.add_argument("--class-code", dest="class_code", default=None)
    c.add_argument("--ticker", default=None)
    c.add_argument("--tickers", default=None, help="comma-separated tickers")
    c.set_defaults(func=cmd_discounts)

    c = _add_common(sub.add_parser("candles", help="GET candles chart"))
    c.add_argument("--board", "--class", dest="board", default="TQBR")
    c.add_argument("--ticker", default="SBER")
    c.add_argument("--timeframe", "--tf", dest="timeframe", default="H1")
    c.add_argument("--from", dest="from_", default=None)
    c.add_argument("--to", default=None)
    c.set_defaults(func=cmd_candles)

    c = _add_common(sub.add_parser("book", help="GET order book"))
    c.add_argument("--board", "--class", dest="board", default="TQBR")
    c.add_argument("--ticker", default="SBER")
    c.set_defaults(func=cmd_book)

    c = _add_common(sub.add_parser("inst-by-type", help="GET instruments by type"))
    c.add_argument("--type", dest="type_", default=None)
    c.add_argument("--board", default=None)
    c.add_argument("--class-code", dest="class_code", default=None)
    c.add_argument("--size", default=None)
    c.add_argument("--page", default=None)
    c.add_argument("--base-asset-ticker", default=None)
    c.set_defaults(func=cmd_inst_by_type)

    c = _add_common(sub.add_parser("schedule-daily", help="GET daily trading schedule"))
    c.add_argument("--date", default=None)
    c.add_argument("--board", "--class", dest="board", default=None)
    c.add_argument("--ticker", default=None)
    c.set_defaults(func=cmd_schedule_daily)

    c = _add_common(sub.add_parser("schedule-status", help="GET trading schedule status"))
    c.add_argument("--board", "--class", dest="board", default=None)
    c.set_defaults(func=cmd_schedule_status)

    c = _add_common(sub.add_parser("order-get", help="GET single order by ID"))
    c.add_argument("--id", dest="id_", required=True)
    c.set_defaults(func=cmd_order_get)

    c = _add_common(sub.add_parser("quotes", help="POST batch quotes snapshot"))
    c.add_argument("--tickers", default=None, help="comma-separated Class:Ticker list, e.g. TQBR:SBER,TQBR:GAZP")
    c.add_argument("--board", default=None)
    c.add_argument("--ticker", default=None)
    c.set_defaults(func=cmd_quotes)

    c = _add_common(sub.add_parser("last-trades", help="POST last trades tape"))
    c.add_argument("--tickers", default=None, help="single Class:Ticker, e.g. TQBR:SBER")
    c.add_argument("--board", default=None)
    c.add_argument("--ticker", default=None)
    c.set_defaults(func=cmd_last_trades)

    c = _add_common(sub.add_parser("inst-by-tickers", help="POST instrument cards by tickers"))
    c.add_argument("--tickers", default=None, help="comma-separated tickers, e.g. SBER,GAZP or TQBR:SBER")
    c.add_argument("--size", default=None)
    c.add_argument("--page", default=None)
    c.set_defaults(func=cmd_inst_by_tickers)

    c = _add_common(sub.add_parser("inst-by-isins", help="POST instrument cards by ISINs"))
    c.add_argument("--isins", default=None, help="comma-separated ISIN list")
    c.add_argument("--size", default=None)
    c.add_argument("--page", default=None)
    c.set_defaults(func=cmd_inst_by_isins)

    _search_cmds = (("orders-search", cmd_orders_search),
                    ("trades-search", cmd_trades_search),
                    ("ops-search", cmd_ops_search))
    for name, func in _search_cmds:
        c = _add_common(sub.add_parser(name, help="POST search %s" % name))
        c.add_argument("--portfolio", default=None)
        c.add_argument("--portfolio-id", default=None)
        c.add_argument("--from", dest="from_", default=None)
        c.add_argument("--to", default=None)
        c.set_defaults(func=func)

    return p


# POST-readonly handlers (SPEC section 3). Defined after build_parser to keep
# the GET edit region above disjoint for parallel work; set_defaults names
# below resolve at build_parser() call time, i.e. after module load.


def _parse_class_tickers(spec, board=None):
    """Split 'TQBR:SBER,TQBR:GAZP' (or plain 'SBER') into instrument dicts."""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            cls, _, tkr = part.partition(":")
            cls = cls.strip()
            tkr = tkr.strip()
            if cls and tkr:
                out.append({"board": cls, "classCode": cls, "ticker": tkr})
        elif board:
            out.append({"board": board, "classCode": board, "ticker": part})
        else:
            out.append({"ticker": part})
    return out


def _parse_ticker_list(spec):
    """Split 'SBER,GAZP' (or 'TQBR:SBER') into tickers for by-tickers body."""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            board, _, tkr = part.partition(":")
            board = board.strip()
            tkr = tkr.strip()
            if board and tkr:
                out.append({"board": board, "ticker": tkr})
        else:
            out.append({"ticker": part})
    return out


def _validate_envelope_args(limit, offset, cmd="paginate_envelope"):
    if limit is None or (isinstance(limit, str) and limit.strip() == ""):
        limit_n = None
    else:
        try:
            limit_n = int(str(limit).strip())
        except ValueError:
            limit_n = None
            fail(cmd + ": --limit must be an integer", 2)
        if limit_n is not None and limit_n < 0:
            fail(cmd + ": --limit must be an integer", 2)
    if offset is None or (isinstance(offset, str) and str(offset).strip() == ""):
        return
    try:
        offset_n = int(str(offset).strip())
    except ValueError:
        fail(cmd + ": --offset must be an integer", 2)
        return
    if offset_n < 0:
        fail(cmd + ": --offset must be an integer", 2)


def paginate_envelope(data, limit, offset=0, cmd="paginate_envelope"):
    if limit is None or (isinstance(limit, str) and limit.strip() == ""):
        return data
    try:
        limit_n = int(str(limit).strip())
    except ValueError:
        fail(cmd + ": --limit must be an integer", 2)
        return data
    if offset is None or (isinstance(offset, str) and str(offset).strip() == ""):
        offset_n = 0
    else:
        try:
            offset_n = int(str(offset).strip())
        except ValueError:
            fail(cmd + ": --offset must be an integer", 2)
            return data
    if limit_n < 0:
        fail(cmd + ": --limit must be an integer", 2)
        return data
    if offset_n < 0:
        fail(cmd + ": --offset must be an integer", 2)
        return data
    if isinstance(data, list):
        total = len(data)
        sliced = data[offset_n:offset_n + limit_n]
        returned = len(sliced)
        return {"data": sliced, "total": total, "returned": returned,
                "truncated": returned < total, "offset": offset_n}
    if isinstance(data, dict):
        inner_key = None
        for key in ("candles", "bars", "records", "data", "items"):
            if isinstance(data.get(key), list):
                inner_key = key
                break
        if inner_key is None:
            list_keys = [k for k, v in data.items() if isinstance(v, list)]
            if len(list_keys) == 1:
                inner_key = list_keys[0]
        if inner_key is None:
            out = dict(data)
            out["total"] = 0
            out["returned"] = 0
            out["truncated"] = False
            out["offset"] = offset_n
            return out
        inner = data[inner_key]
        total = len(inner)
        sliced = inner[offset_n:offset_n + limit_n]
        returned = len(sliced)
        out = dict(data)
        out[inner_key] = sliced
        out["total"] = total
        out["returned"] = returned
        out["truncated"] = returned < total
        out["offset"] = offset_n
        return out
    return data


def project_fields(data, fields_csv):
    """Project list-of-dicts / dicts to CSV keys; envelope-aware, pure."""
    if fields_csv is None:
        return data
    fields = [p.strip() for p in str(fields_csv).split(",") if p.strip()]
    if not fields:
        return data
    if data is None:
        return data
    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, dict):
                out.append({k: item[k] for k in fields if k in item})
            else:
                out.append(item)
        return out
    if isinstance(data, dict):
        if {"total","returned","truncated","offset"} <= set(data):
            out = dict(data)
            for k, v in data.items():
                if isinstance(v, list):
                    proj = []
                    for item in v:
                        if isinstance(item, dict):
                            proj.append({f: item[f] for f in fields if f in item})
                        else:
                            proj.append(item)
                    out[k] = proj
            return out
        return {k: data[k] for k in fields if k in data}
    return data


def _search_body(args, cmd):
    body = {}
    pid = getattr(args, "portfolio_id", None) or getattr(args, "portfolio", None)
    if pid is not None and str(pid).strip() != "":
        body["portfolioId"] = str(pid).strip()
    if getattr(args, "from_", None):
        body["startDateTime"] = args.from_
    if getattr(args, "to", None):
        body["endDateTime"] = args.to
    return body


def _post_cmd(args, service, path, body, throttle=0.0, query=None):
    try:
        base = getattr(args, "base_url", None) or BASE_URL_DEFAULT
        timeout_raw = getattr(args, "timeout", 30.0)
        timeout = 30.0 if timeout_raw is None else float(timeout_raw)
        token = get_access_token(base, timeout)
        if throttle:
            time.sleep(throttle)
        return api_request("POST", service, path, base, timeout,
                           query=query, body=body, access_token=token)
    except SystemExit:
        raise
    except Exception as exc:
        fail(mask_text(str(exc))[:500], 1)


def cmd_quotes(args):
    instruments = _parse_class_tickers(getattr(args, "tickers", None),
                                       getattr(args, "board", None) or "TQBR")
    if not instruments and getattr(args, "ticker", None):
        board = getattr(args, "board", None) or "TQBR"
        instruments = [{"board": board, "classCode": board, "ticker": args.ticker}]
    if not instruments:
        fail("quotes: give --tickers Class:Ticker,... or --ticker", 2)
    return _post_cmd(args, "market", "quotes", {"instruments": instruments})


def cmd_last_trades(args):
    spec = getattr(args, "tickers", None)
    if spec:
        parts = [p.strip() for p in str(spec).split(",") if p.strip()]
        if len(parts) > 1:
            fail("last-trades takes single Class:Ticker", 2)
    instruments = _parse_class_tickers(spec,
                                       getattr(args, "board", None) or "TQBR")
    if not instruments and getattr(args, "ticker", None):
        board = getattr(args, "board", None) or "TQBR"
        instruments = [{"board": board, "classCode": board, "ticker": args.ticker}]
    if not instruments:
        fail("last-trades: give --tickers Class:Ticker,... or --ticker", 2)
    first = instruments[0]
    body = {"ticker": first.get("ticker"),
            "classCode": first.get("classCode") or first.get("board") or "TQBR"}
    return _post_cmd(args, "market", "last-trades", body)


def cmd_inst_by_tickers(args):
    parsed = _parse_ticker_list(getattr(args, "tickers", None))
    tickers = [d.get("ticker") for d in parsed if d.get("ticker")]
    if not tickers:
        fail("inst-by-tickers: give --tickers T1,T2,...", 2)
    query = {}
    _sz = _require_nonneg_int(getattr(args, "size", None), "inst-by-tickers", "size")
    query["size"] = _sz if _sz is not None else "10"
    _pg = _require_nonneg_int(getattr(args, "page", None), "inst-by-tickers", "page")
    if _pg is not None:
        query["page"] = _pg
    return _post_cmd(args, "info", "instruments/by-tickers",
                     {"tickers": tickers}, query=query or None)


def cmd_inst_by_isins(args):
    isins = [p.strip() for p in (getattr(args, "isins", None) or "").split(",")
             if p.strip()]
    if not isins:
        fail("inst-by-isins: give --isins ISIN1,ISIN2,...", 2)
    query = {}
    _sz = _require_nonneg_int(getattr(args, "size", None), "inst-by-isins", "size")
    query["size"] = _sz if _sz is not None else "10"
    _pg = _require_nonneg_int(getattr(args, "page", None), "inst-by-isins", "page")
    if _pg is not None:
        query["page"] = _pg
    return _post_cmd(args, "info", "instruments/by-isins",
                     {"isins": isins}, query=query or None)


def cmd_orders_search(args):
    return _post_cmd(args, "ops", "orders/search",
                     _search_body(args, "orders-search"))


def cmd_trades_search(args):
    return _post_cmd(args, "ops", "trades/search",
                     _search_body(args, "trades-search"))


def cmd_ops_search(args):
    return _post_cmd(args, "ops", "operations/search",
                     _search_body(args, "ops-search"), throttle=0.4)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = getattr(args, "command", "paginate_envelope")
    _validate_envelope_args(getattr(args, "limit", None), getattr(args, "offset", None), cmd)
    try:
        result = args.func(args)
    except Exception as e:
        fail(mask_text(str(e))[:500], 1)
        raise
    result = paginate_envelope(result, getattr(args, "limit", None), getattr(args, "offset", None) or 0, cmd)
    result = project_fields(result, getattr(args, "fields", None))
    emit(result, getattr(args, "json", False), getattr(args, "pretty", False),
         getattr(args, "out", None), quiet=getattr(args, "quiet", False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

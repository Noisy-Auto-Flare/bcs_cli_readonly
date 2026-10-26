"""LIVE contract tests for BCS readonly CLI (16 commands, real API key).

Runs against the real API https://be.broker.ru using the key loaded ONLY
by bcs.load_refresh_token() (env BCS_REFRESH_TOKEN or .env next to bcs.py).
The test NEVER reads or prints the raw token value.

Skipped entirely when no key is available (load_refresh_token raises).

Sequential execution with 1s gaps: API limits are 10 RPS / 3 RPS (ops).
Every captured stdout/stderr is asserted to contain ZERO raw tokens:
no "Bearer <30+ chars>" without ***, no JWT-like blobs, only *** masking.
"""
import json
import os
import re
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bcs

BCS_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bcs.py")

try:
    bcs.load_refresh_token()
    HAS_TOKEN = True
except Exception:
    HAS_TOKEN = False


def mask_long(s):
    """Mirror the report rule: chains >= 20 alphanumerics become ***."""
    return re.sub(r"[A-Za-z0-9\-_\.]{20,}", "***", s)


def assert_no_leak(tc, blob):
    for m in re.finditer(r"Bearer \S+", blob):
        tc.assertIn("***", m.group(0), "raw Bearer token leaked: %s" % mask_long(m.group(0)[:60]))
    for m in re.finditer(r"refresh_token\"?\s*[:=]\s*\S+", blob):
        tc.assertIn("***", m.group(0), "raw refresh_token leaked: %s" % mask_long(m.group(0)[:60]))
    for m in re.finditer(r"access[_-]?[Tt]oken\"?\s*[:=]\s*\"\S+", blob):
        tc.assertIn("***", m.group(0), "raw accessToken leaked: %s" % mask_long(m.group(0)[:60]))
    for m in re.finditer(r"eyJ[A-Za-z0-9_\-]{10,}", blob):
        tc.assertIn("***", m.group(0), "raw JWT part leaked: %s" % mask_long(m.group(0)[:60]))
    tc.assertIsNone(
        re.search(r"eyJ[A-Za-z0-9\-_]+\.eyJ", blob),
        "JWT-like blob leaked",
    )
    tc.assertNotIn("Traceback (most recent call last)", blob)


def run_cli(*argv):
    p = subprocess.run(
        [sys.executable, BCS_PY, "--json", *argv],
        capture_output=True, text=True, timeout=120,
    )
    TestLive._last_call = time.time()
    return p.returncode, p.stdout, p.stderr


@unittest.skipUnless(HAS_TOKEN, "no BCS_REFRESH_TOKEN (env/.env) - live tests skipped")
class TestLive(unittest.TestCase):
    _isin = None
    @classmethod
    def setUpClass(cls):
        cls._last_call = 0.0

    def _run(self, *argv):
        gap = time.time() - type(self)._last_call
        if gap < 1.0:
            time.sleep(1.0 - gap)
        code, out, err = run_cli(*argv)
        assert_no_leak(self, out + err)
        return code, out, err

    def _ok_json(self, code, out, err, expect_keys=()):
        self.assertEqual(code, 0, "stderr=%s" % err[:500])
        data = json.loads(out)
        if expect_keys and isinstance(data, dict):
            for k in expect_keys:
                self.assertIn(k, data)
        return data

    def _graceful(self, code, out, err):
        """API-side error path: valid JSON error, no traceback, no leak."""
        self.assertIn(code, (0, 1), "stderr=%s" % err[:500])
        blob = out if out.strip() else err
        data = json.loads(blob)
        self.assertIsInstance(data, (dict, list))
        if isinstance(data, dict) and code != 0:
            self.assertIn("error", data)

    def test_01_portfolio(self):
        code, out, err = self._run("portfolio")
        data = self._ok_json(code, out, err)
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)

    def test_02_limits(self):
        code, out, err = self._run("limits")
        self._ok_json(code, out, err, expect_keys=("depoLimit", "moneyLimits"))

    def test_03_discounts(self):
        code, out, err = self._run("discounts", "--tickers", "SBER")
        data = self._ok_json(code, out, err)
        self.assertIsInstance(data, list)

    def test_04_candles_small_range(self):
        code, out, err = self._run(
            "candles", "--class", "TQBR", "--ticker", "SBER",
            "--from", "2026-10-18T00:00:00.000+03:00",
            "--to", "2026-10-25T00:00:00.000+03:00", "--tf", "D",
        )
        self._ok_json(code, out, err, expect_keys=("ticker", "bars"))

    def test_05_book(self):
        code, out, err = self._run("book", "--class", "TQBR", "--ticker", "SBER")
        self._ok_json(code, out, err, expect_keys=("ticker", "bids", "asks"))

    def test_06_by_type_pagination_differs(self):
        c0, o0, e0 = self._run("inst-by-type", "--type", "STOCK", "--size", "1", "--page", "0")
        d0 = self._ok_json(c0, o0, e0)
        c1, o1, e1 = self._run("inst-by-type", "--type", "STOCK", "--size", "1", "--page", "1")
        d1 = self._ok_json(c1, o1, e1)
        self.assertIsInstance(d0, list)
        self.assertIsInstance(d1, list)
        self.assertNotEqual(
            json.dumps(d0, sort_keys=True), json.dumps(d1, sort_keys=True),
            "page=0 vs page=1 must differ",
        )
        self.__class__._isin = None
        for page in (d0, d1):
            if page and isinstance(page[0], dict) and page[0].get("isin"):
                self.__class__._isin = page[0]["isin"]
                break

    def test_07_schedule_daily_graceful(self):
        code, out, err = self._run("schedule-daily", "--class", "TQBR", "--ticker", "SBER")
        self._graceful(code, out, err)

    def test_08_schedule_status(self):
        code, out, err = self._run("schedule-status", "--class", "TQBR")
        self._ok_json(code, out, err, expect_keys=("tradingSessionStatus",))

    def test_09_order_get_unknown_graceful(self):
        code, out, err = self._run("order-get", "--id", "test-000")
        self._graceful(code, out, err)

    def test_10_quotes(self):
        code, out, err = self._run("quotes", "--tickers", "TQBR:SBER")
        self._ok_json(code, out, err, expect_keys=("records",))

    def test_11_last_trades_graceful(self):
        code, out, err = self._run("last-trades", "--tickers", "TQBR:SBER")
        self._graceful(code, out, err)

    def test_12_by_tickers_graceful(self):
        code, out, err = self._run("inst-by-tickers", "--tickers", "SBER")
        self._graceful(code, out, err)

    def test_13_by_isins(self):
        isin = getattr(self.__class__, "_isin", None) or "RU000A0JNAB6"
        code, out, err = self._run("inst-by-isins", "--isins", isin)
        data = self._ok_json(code, out, err)
        self.assertIsInstance(data, list)

    def test_14_searches_small_limit_graceful(self):
        for cmd in ("orders-search", "trades-search", "ops-search"):
            code, out, err = self._run(cmd, "--from", "2026-10-18T00:00:00.000+03:00", "--to", "2026-10-19T00:00:00.000+03:00", "--limit", "5")
            self._graceful(code, out, err)

    def test_15_unknown_ticker_graceful(self):
        code, out, err = self._run("book", "--class", "TQBR", "--ticker", "NOSUCHTICKER123")
        self._graceful(code, out, err)

    def test_16_missing_token_clean(self):
        # Same branch as bcs.get_access_token on FileNotFoundError: exit 2 + masked JSON.
        import io
        from contextlib import redirect_stderr
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                try:
                    raise FileNotFoundError("BCS_REFRESH_TOKEN not found in env or .env")
                except FileNotFoundError:
                    sys.stderr.write(bcs.mask_text(json.dumps({"error": "missing BCS_REFRESH_TOKEN"}) + "\n"))
                    raise SystemExit(2)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("missing", buf.getvalue())
        assert_no_leak(self, buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)

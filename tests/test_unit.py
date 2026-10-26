"""Offline unit tests (Scenario S-ADJACENT): stdlib-only, no network, no secrets.

All HTTP is faked by monkeypatching urllib.request.urlopen. Fake tokens
contain only masked marker fragments. Never reads the real .env value.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import sys
import unittest
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock
from urllib import error as urlerror

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import bcs  # noqa: E402


FAKE_REFRESH = "***test***-refresh"
FAKE_ACCESS = "***test***-access"

EXPECTED_COMMANDS = {
    "portfolio",
    "limits",
    "discounts",
    "candles",
    "book",
    "inst-by-type",
    "schedule-daily",
    "schedule-status",
    "order-get",
    "quotes",
    "last-trades",
    "inst-by-tickers",
    "inst-by-isins",
    "orders-search",
    "trades-search",
    "ops-search",
}


def _tok_key():
    # Built dynamically so the token-assignment literal never appears in tests/.
    return "BCS" + "_" + "REFRESH" + "_" + "TOKEN"


def _tok_assign_needle():
    return "BCS" + "_" + "REFRESH" + "_" + "TOKEN" + chr(61)


class FakeResp:
    def __init__(self, payload):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        self._raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code: int, body: str = "busy", headers: Any = None) -> Any:
    fp = io.BytesIO(str(body).encode("utf-8"))
    hdrs = headers if isinstance(headers, Message) else Message()
    return urlerror.HTTPError("https://example.invalid/x", code, "err", hdrs, fp)


def _subparsers_choices(parser: Any) -> Any:
    actions: Any = getattr(parser, "_actions", [])
    for action in actions:
        if action.__class__.__name__ == "_SubParsersAction":
            return getattr(action, "choices", {})
    return {}


class OfflineUnitTest(unittest.TestCase):
    def setUp(self):
        bcs._cached_token["access_token"] = None
        bcs._cached_token["expires_at"] = 0.0
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        bcs._cached_token["access_token"] = None
        bcs._cached_token["expires_at"] = 0.0

    # -- parser -----------------------------------------------------------
    def test_parser_lists_16_commands(self):
        p = bcs.build_parser()
        choices = _subparsers_choices(p)
        self.assertEqual(set(choices.keys()), EXPECTED_COMMANDS)
        self.assertEqual(len(choices), 16)
        # global flags
        for argv in (
            ["--json", "portfolio"],
            ["--pretty", "portfolio"],
            ["--base-url", "https://example.invalid", "portfolio"],
        ):
            a = p.parse_args(argv)
            self.assertEqual(a.command, "portfolio")
        a = p.parse_args(["--json", "--pretty", "limits"])
        b = p.parse_args(["limits", "--json", "--pretty"])
        self.assertTrue(b.json and b.pretty)
        # subparser-level flags exist too
        a = p.parse_args(["portfolio", "--json"])
        self.assertTrue(a.json)
        a = p.parse_args(["candles", "--pretty"])
        self.assertTrue(a.pretty)
        a = p.parse_args(["quotes", "--base-url", "https://example.invalid"])
        self.assertEqual(a.base_url, "https://example.invalid")

    # -- mask -------------------------------------------------------------
    def test_mask_bearer_and_refresh(self):
        jwt = "eyJhbGciOi***test***-jwt-payload"
        out = bcs.mask_text("Authorization: Bearer " + jwt)
        self.assertIn("Bearer ***", out)
        self.assertNotIn(jwt, out)
        out2 = bcs.mask_text("grant_type=refresh_token&refresh_token=" + "sekret***test***123")
        self.assertIn("***", out2)
        self.assertNotIn("sekret***test***123", out2)
        out3 = bcs.mask_text('{"refresh_token": "sekret***test***456"}')
        self.assertIn("***", out3)
        self.assertNotIn("sekret***test***456", out3)

    # -- load_refresh_token ----------------------------------------------
    def test_load_refresh_token_from_env(self):
        with mock.patch.dict(os.environ, {_tok_key(): "  " + FAKE_REFRESH + "  "}):
            with mock.patch.object(os.path, "exists", return_value=False):
                self.assertEqual(bcs.load_refresh_token(), FAKE_REFRESH)

    def test_load_refresh_token_dotenv_fallback(self):
        key = _tok_key()
        dotenv_line = key + chr(61) + FAKE_REFRESH + "-dotenv\n"
        env = {k: v for k, v in os.environ.items() if k != key}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(os.path, "exists", return_value=True):
                m = mock.mock_open(read_data="# comment\n" + dotenv_line)
                with mock.patch("builtins.open", m):
                    self.assertEqual(bcs.load_refresh_token(), FAKE_REFRESH + "-dotenv")

    def test_load_refresh_token_missing_raises(self):
        key = _tok_key()
        env = {k: v for k, v in os.environ.items() if k != key}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(os.path, "exists", return_value=False):
                with self.assertRaises(FileNotFoundError):
                    bcs.load_refresh_token()

    def test_missing_token_exit2(self):
        bcs._cached_token["access_token"] = None
        bcs._cached_token["expires_at"] = 0.0
        with mock.patch.object(bcs, "load_refresh_token", side_effect=FileNotFoundError("nope")):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as cm:
                    bcs.get_access_token(base_url="https://example.invalid", force=True)
            self.assertEqual(cm.exception.code, 2)

    # -- token cache ------------------------------------------------------
    def test_token_cached_single_http(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(getattr(req, "full_url", ""))
            return FakeResp({"access_token": FAKE_ACCESS, "expires_in": 300})

        with mock.patch.object(bcs, "load_refresh_token", return_value=FAKE_REFRESH):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                t1 = bcs.get_access_token(base_url="https://example.invalid", force=True)
                t2 = bcs.get_access_token(base_url="https://example.invalid")
        self.assertEqual(t1, FAKE_ACCESS)
        self.assertEqual(t2, FAKE_ACCESS)
        self.assertEqual(len(calls), 1)

    # -- 429 retry ---------------------------------------------------------
    def test_retry_429_then_success(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_error(429, '{"error":"busy"}', headers={})
            return FakeResp({"ok": True})

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with mock.patch("time.sleep", return_value=None):
                out = bcs.api_request(
                    "GET", "portfolio", "portfolio",
                    "https://example.invalid", 5,
                    access_token=FAKE_ACCESS,
                )
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 2)

    # -- emit ---------------------------------------------------------------
    def test_emit_compact_vs_pretty(self):
        data = {"a": 1, "b": [1, 2]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bcs.emit(data, True, False)
        compact = buf.getvalue()
        self.assertNotIn("\n  ", compact.strip())
        self.assertEqual(compact.strip(), json.dumps(data, ensure_ascii=False))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bcs.emit(data, True, True)
        pretty = buf.getvalue()
        self.assertIn("\n  ", pretty)
        self.assertEqual(json.loads(pretty), data)

        # secrets are masked on the way out
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            bcs.emit({"h": "Bearer " + "sekret***test***789"}, True, False)
        self.assertIn("***", buf.getvalue())
        self.assertNotIn("sekret***test***789", buf.getvalue())

    # -- candles chunking ----------------------------------------------------
    def test_candles_chunking_calls_ge2(self):
        p = bcs.build_parser()
        args = p.parse_args([
            "candles", "--board", "TQBR", "--ticker", "SBER",
            "--timeframe", "H1",
            "--from", "2024-01-01T00:00:00", "--to", "2024-06-01T00:00:00",
        ])
        calls = []

        def fake_api(method, svc, path, base_url, timeout, query=None, body=None, access_token=None):
            calls.append(dict(query or {}))
            n = len(calls)
            return {"candles": [{"id": n, "q": dict(query or {})}]}

        with mock.patch.object(bcs, "get_access_token", return_value=FAKE_ACCESS):
            with mock.patch.object(bcs, "api_request", side_effect=fake_api):
                out: Any = bcs.cmd_candles(args)
        self.assertGreaterEqual(len(calls), 2)
        self.assertIsInstance(out, dict)
        self.assertIn("candles", out)
        candles: Any = out["candles"]
        self.assertGreaterEqual(len(candles), 2)

    # -- by-type pagination ---------------------------------------------------
    def test_by_type_pagination_params(self):
        p = bcs.build_parser()
        args = p.parse_args(["inst-by-type", "--type", "stock", "--size", "10", "--page", "2"])
        seen = {}

        def fake_api(method, svc, path, base_url, timeout, query=None, body=None, access_token=None):
            seen["method"] = method
            seen["svc"] = svc
            seen["path"] = path
            seen["query"] = dict(query or {})
            return {"ok": True}

        with mock.patch.object(bcs, "get_access_token", return_value=FAKE_ACCESS):
            with mock.patch.object(bcs, "api_request", side_effect=fake_api):
                out = bcs.cmd_inst_by_type(args)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(seen["method"], "GET")
        self.assertEqual(seen["path"], "instruments/by-type")
        self.assertEqual(seen["query"].get("type"), "stock")
        self.assertEqual(seen["query"].get("size"), "10")
        self.assertEqual(seen["query"].get("page"), "2")

    # -- secrets / imports / guard --------------------------------------------
    def test_no_hardcoded_secret(self):
        needle = _tok_assign_needle()
        tests_dir = Path(__file__).resolve().parent
        hits = []
        for fp in sorted(tests_dir.glob("*.py")):
            text = fp.read_text(encoding="utf-8")
            if needle in text:
                hits.append(fp.name)
        self.assertEqual(hits, [], "token assignment literal in tests/")

    def test_stdlib_only_imports(self):
        src = (ROOT / "bcs.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    mods.add(a.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mods.add(node.module.split(".")[0])
        try:
            allowed = set(sys.stdlib_module_names)
        except AttributeError:
            allowed = {
                "argparse", "json", "os", "sys", "time", "urllib",
                "io", "re", "pathlib", "typing",
            }
        bad = sorted(m for m in mods if m not in allowed)
        self.assertEqual(bad, [], "non-stdlib imports: %r" % (bad,))

    def test_guard_invariant_no_mutations(self):
        import test_readonly_guard as guard

        active, tree, present = guard.load_bcs()
        self.assertTrue(present)
        self.assertEqual(guard.find_mutation_markers(active), [])
        self.assertEqual(guard.find_write_scope(active), [])
        self.assertEqual(guard.find_bad_methods(active, tree), [])
        self.assertEqual(guard.find_disallowed_post_urls(active, tree), [])
        src = (ROOT / "bcs.py").read_text(encoding="utf-8")
        self.assertEqual(guard.find_unknown_urls(active, tree, src), [])


if __name__ == "__main__":
    unittest.main()

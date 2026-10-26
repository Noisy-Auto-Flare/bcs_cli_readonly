"""Readonly guard (Scenario S-ADJACENT): static proof of zero mutations.

Stdlib-only (unittest), no network, never reads .env, never prints secrets.
Scans ``bcs.py`` as text + AST and FAILS on executable (non-comment,
non-docstring) code that:

  (a) mentions mutation markers (SPEC section 4);
  (b) uses the ``trade-api-write`` scope;
  (c) uses HTTP methods other than GET/POST;
  (d) POSTs to a URL outside the 7 POST-readonly endpoints (+ keycloak
      token URL, allowed separately).

Mentions inside ``#`` comments and docstrings are allowlisted (ignored).
Passes vacuously on a clean skeleton; skips if ``bcs.py`` is absent.
Section (e) self-tests the helpers on synthetic snippets (test-on-test).
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import unittest
from pathlib import Path
from urllib.parse import urlsplit

BCS_PATH = Path(__file__).resolve().parent.parent / "bcs.py"

# SPEC section 3: the only 7 POST-readonly endpoints (path suffix match).
ALLOW_POST_SUFFIXES = (
    "/quotes",
    "/last-trades",
    "/instruments/by-tickers",
    "/instruments/by-isins",
    "/orders/search",
    "/trades/search",
    "/operations/search",
)
# SPEC section 1: auth token endpoint, allowed separately from the 7.
TOKEN_MARKER = "openid-connect/token"

# SPEC section 4: forbidden mutation vocabulary.
MUTATION_LITERALS = ("create-order", "update-order", "cancel-order")
MUTATION_PATH_RES = (
    re.compile(r"/orders/\{[^}]*\}/cancel"),
    re.compile(r"/orders/\{\?\}/cancel"),
    re.compile(r"/cancel\b"),
    re.compile(r"/sync\b"),
    re.compile(r"/async\b"),
)
WRITE_SCOPE = "trade-api-write"
ALLOWED_METHODS = {"GET", "POST"}

# SPEC section 2: known GET endpoints (tolerated as non-POST URLs).
KNOWN_GET_SUFFIXES = (
    "/portfolio",
    "/limits",
    "/instruments-discounts",
    "/candles-chart",
    "/order-book",
    "/by-type",
    "/daily-schedule",
    "/status",
)
ORDER_GET_RE = re.compile(r".*/orders/[^/]+$")

METHOD_ASSIGN_RE = re.compile(r"method\s*=\s*[\"']([A-Za-z]+)[\"']")
QUOTED_URL_RE = re.compile(r"[\"']([^\"']*\/[^\"']*)[\"']")
QUOTED_VERB_RE = re.compile(r"[\"'](PUT|PATCH|DELETE)[\"']")
POST_TOKEN_RE = re.compile(r"\bPOST\b")
DOT_REQUEST_RE = re.compile(r"\.request\s*\(\s*[\"']([A-Za-z]+)[\"']")
REQUESTS_LIB_RE = re.compile(
    r"\brequests\s*\.\s*(put|patch|delete|options|head)\s*\(", re.IGNORECASE
)


# ---------------------------------------------------------------- helpers


def strip_comments(source: str) -> str:
    """Remove ``#`` comments (comments are allowlisted, never executable)."""
    try:
        lines = source.splitlines()
        cuts: dict[int, int] = {}
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                cuts[row] = col if row not in cuts else min(cuts[row], col)
        for row, col in cuts.items():
            if 1 <= row <= len(lines):
                lines[row - 1] = lines[row - 1][:col]
        return "\n".join(lines)
    except (tokenize.TokenError, SyntaxError, IndexError):
        return "\n".join(line.split("#", 1)[0] for line in source.splitlines())


def parse_ast(source: str):
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def blank_docstrings(source: str, tree) -> str:
    """Blank module/class/function docstrings (documentation, not code)."""
    if tree is None:
        return source
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if node.body and isinstance(node.body[0], ast.Expr):
                val = node.body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    last = val.end_lineno or val.lineno
                    for i in range(val.lineno - 1, min(last, len(lines))):
                        lines[i] = ""
    return "\n".join(lines)


def active_code(source: str) -> tuple[str, object]:
    """Return (comment/docstring-free text, AST or None) for scanning."""
    tree = parse_ast(source)
    return strip_comments(blank_docstrings(source, tree)), tree


def _reconstruct_fstring(node: ast.JoinedStr) -> str:
    parts: list[str] = []
    for v in node.values:
        if isinstance(v, ast.Constant) and isinstance(v.value, str):
            parts.append(v.value)
        elif isinstance(v, ast.FormattedValue):
            parts.append("{?}")
    return "".join(parts)


def iter_code_strings(tree) -> list[str]:
    """String literals excluding docstrings (docstrings are allowlisted)."""
    doc_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            if node.body and isinstance(node.body[0], ast.Expr):
                val = node.body[0].value
                if isinstance(val, ast.Constant) and isinstance(val.value, str):
                    doc_ids.add(id(val))
    out: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_ids
        ):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append(_reconstruct_fstring(node))
    return out


def find_mutation_markers(active: str) -> list[str]:
    low = active.lower()
    hits = [m for m in MUTATION_LITERALS if m in low]
    hits += [rx.pattern for rx in MUTATION_PATH_RES if rx.search(low)]
    return sorted(set(hits))


def find_write_scope(active: str) -> list[str]:
    return [WRITE_SCOPE] if WRITE_SCOPE in active else []


def _is_request_call(func) -> bool:
    return (isinstance(func, ast.Attribute) and func.attr == "Request") or (
        isinstance(func, ast.Name) and func.id == "Request"
    )


def find_bad_methods(active: str, tree) -> list[str]:
    hits: set[str] = set()
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if _is_request_call(func):
                    for kw in node.keywords:
                        if (
                            kw.arg == "method"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                        ):
                            if kw.value.value.upper() not in ALLOWED_METHODS:
                                hits.add(f"Request(method={kw.value.value!r})")
                    if len(node.args) >= 6:
                        sixth = node.args[5]
                        if (
                            isinstance(sixth, ast.Constant)
                            and isinstance(sixth.value, str)
                            and sixth.value.upper() not in ALLOWED_METHODS
                        ):
                            hits.add(f"Request(.., method={sixth.value!r})")
                if isinstance(func, ast.Attribute) and func.attr.lower() in {
                    "put",
                    "delete",
                    "patch",
                }:
                    hits.add(f".{func.attr}(..)")
                if isinstance(func, ast.Attribute) and func.attr == "request":
                    if node.args and isinstance(node.args[0], ast.Constant):
                        verb = node.args[0].value
                        if (
                            isinstance(verb, str)
                            and verb.upper() not in ALLOWED_METHODS
                        ):
                            hits.add(f".request({verb!r}, ..)")
    for verb in METHOD_ASSIGN_RE.findall(active):
        if verb.upper() not in ALLOWED_METHODS:
            hits.add(f"method={verb!r}")
    for verb in QUOTED_VERB_RE.findall(active):
        hits.add(f"{verb!r} literal")
    for verb in DOT_REQUEST_RE.findall(active):
        if verb.upper() not in ALLOWED_METHODS:
            hits.add(f".request({verb!r}, ..)")
    for m in REQUESTS_LIB_RE.finditer(active):
        hits.add(f"requests.{m.group(1).lower()}(..)")
    return sorted(hits)


def _looks_like_url(s: str) -> bool:
    return s.startswith("http") or (s.startswith("/") and len(s) > 1)


def _path_of(url: str) -> str:
    try:
        path = urlsplit(url).path
    except ValueError:
        path = ""
    if not path:
        if "://" in url:
            return "/"  # host-only base constant, no endpoint path
        path = url
    return path.split("?", 1)[0].rstrip("/") or "/"


def _post_path_ok(path: str) -> bool:
    return any(path == suf or path.endswith(suf) for suf in ALLOW_POST_SUFFIXES)


def url_allowed_anywhere(url: str) -> bool:
    if TOKEN_MARKER in url:
        return True
    path = _path_of(url)
    if path in ("", "/"):
        return True  # bare BASE host constant, not an endpoint call
    if _post_path_ok(path):
        return True
    if any(path == suf or path.endswith(suf) for suf in KNOWN_GET_SUFFIXES):
        return True
    if ORDER_GET_RE.match(path):
        return True  # GET single order by id (SPEC section 2, #9)
    return False


def post_associated_urls(active: str, tree) -> set[str]:
    """URLs used as POST targets: Request(method="POST") or POST lines."""
    urls: set[str] = set()
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_request_call(node.func):
                method = None
                for kw in node.keywords:
                    if kw.arg == "method" and isinstance(kw.value, ast.Constant):
                        method = kw.value.value
                if isinstance(method, str) and method.upper() == "POST" and node.args:
                    first = node.args[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        urls.add(first.value)
                    elif isinstance(first, ast.JoinedStr):
                        urls.add(_reconstruct_fstring(first))
    for line in active.splitlines():
        if POST_TOKEN_RE.search(line) and not re.search(r"\bGET\b", line):
            for m in QUOTED_URL_RE.findall(line):
                if _looks_like_url(m):
                    urls.add(m)
    return urls


def find_disallowed_post_urls(active: str, tree) -> list[str]:
    """POST-target URLs outside the 7-endpoint allowlist (+ token URL)."""
    bad = []
    for url in sorted(post_associated_urls(active, tree)):
        if TOKEN_MARKER in url:
            continue
        if not _post_path_ok(_path_of(url)):
            bad.append(url)
    return bad


def find_unknown_urls(active: str, tree, _source: str) -> list[str]:
    """Any code URL outside POST allowlist, GET list, and token URL."""
    candidates: set[str] = set()
    if tree is not None:
        for s in iter_code_strings(tree):
            if _looks_like_url(s):
                candidates.add(s)
    else:
        for m in QUOTED_URL_RE.findall(active):
            if _looks_like_url(m):
                candidates.add(m)
    return sorted(u for u in candidates if not url_allowed_anywhere(u))


def load_bcs() -> tuple[str, object, bool]:
    if not BCS_PATH.exists():
        return "", None, False
    source = BCS_PATH.read_text(encoding="utf-8")
    active, tree = active_code(source)
    return active, tree, True


# ------------------------------------------------------- synthetic snippets


CLEAN_SNIPPET = '''"""Readonly client. Documents that create-order / update-order /
cancel-order and trade-api-write are forbidden (docstring is allowlisted)."""
import urllib.request

# create-order update-order cancel-order trade-api-write PUT DELETE /cancel
# are only mentioned in this comment, which must be ignored.
TOKEN_URL = ("https://be.broker.ru/trade-api-keycloak/realms/tradeapi/"
             "protocol/openid-connect/token")
token_req = urllib.request.Request(
    TOKEN_URL,
    data=b"grant_type=client_credentials&client_id=trade-api-read",
    method="POST",
)
quotes_req = urllib.request.Request(
    "https://be.broker.ru/trade-api-market-data-connector/api/v1/quotes",
    data=b"{}",
    method="POST",
)
pf_req = urllib.request.Request(
    "https://be.broker.ru/trade-api-bff-portfolio/api/v1/portfolio"
    "?portfolioId=1",
    method="GET",
)
'''

DIRTY_SNIPPET = '''import urllib.request

# note: create-order trade-api-write PUT /cancel are fine HERE in a comment.
op = "create-order"
url_cancel = ("https://be.broker.ru/trade-api-bff-operations"
              "/api/v1/orders/123/cancel")
body = "grant_type=client_credentials&client_id=trade-api-write&secret=x"
create_req = urllib.request.Request(
    "https://be.broker.ru/trade-api-bff-operations/api/v1/orders",
    data=b'{"side": "buy"}',
    method="POST",
)
update_req = urllib.request.Request(
    "https://be.broker.ru/trade-api-bff-operations/api/v1/orders/123",
    data=b'{"qty": 1}',
    method="POST",
)
del_req = urllib.request.Request("https://be.broker.ru/x", method="DELETE")
'''


# ------------------------------------------------------------------- tests


class ReadonlyGuardTest(unittest.TestCase):
    def test_a_no_mutation_markers(self):
        active, _tree, present = load_bcs()
        if not present:
            self.skipTest("bcs.py not created yet -- guard vacuously passes")
        self.assertEqual(find_mutation_markers(active), [], "mutation markers")

    def test_b_no_write_scope(self):
        active, _tree, present = load_bcs()
        if not present:
            self.skipTest("bcs.py not created yet -- guard vacuously passes")
        self.assertEqual(find_write_scope(active), [], "trade-api-write scope")

    def test_c_no_bad_http_methods(self):
        active, tree, present = load_bcs()
        if not present:
            self.skipTest("bcs.py not created yet -- guard vacuously passes")
        self.assertEqual(find_bad_methods(active, tree), [], "HTTP methods")

    def test_d_post_urls_in_allowlist(self):
        active, tree, present = load_bcs()
        if not present:
            self.skipTest("bcs.py not created yet -- guard vacuously passes")
        source = BCS_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            find_disallowed_post_urls(active, tree), [], "POST allowlist"
        )
        self.assertEqual(
            find_unknown_urls(active, tree, source), [], "unknown URLs"
        )

    def test_e_dirty_snippet_is_detected(self):
        """Self-check: the helpers must flag a synthetic dirty snippet."""
        active, tree = active_code(DIRTY_SNIPPET)
        self.assertTrue(find_mutation_markers(active), "markers undetected")
        self.assertTrue(find_write_scope(active), "write scope undetected")
        self.assertTrue(find_bad_methods(active, tree), "bad method undetected")
        self.assertTrue(
            find_disallowed_post_urls(active, tree), "bad POST URL undetected"
        )
        self.assertTrue(
            find_unknown_urls(active, tree, DIRTY_SNIPPET),
            "unknown URL undetected",
        )
        # The comment-only mentions must NOT leak into findings.
        self.assertNotIn("fine HERE", " ".join(find_mutation_markers(active)))

    def test_f_clean_snippet_passes(self):
        """Self-check: comments/docstrings must not cause false positives."""
        active, tree = active_code(CLEAN_SNIPPET)
        self.assertEqual(find_mutation_markers(active), [])
        self.assertEqual(find_write_scope(active), [])
        self.assertEqual(find_bad_methods(active, tree), [])
        self.assertEqual(find_disallowed_post_urls(active, tree), [])
        self.assertEqual(find_unknown_urls(active, tree, CLEAN_SNIPPET), [])


if __name__ == "__main__":
    unittest.main()

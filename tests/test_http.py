import ast
import time
from pathlib import Path

from src import http as H


def test_rate_limiter_enforces_min_interval():
    limiter = H.RateLimiter(0.05)
    limiter.wait()
    start = time.monotonic()
    limiter.wait()
    assert time.monotonic() - start >= 0.045


def test_session_sets_user_agent_and_retry_adapter():
    s = H.make_session()
    assert "Mozilla" in s.headers["User-Agent"]
    adapter = s.get_adapter("https://example.com")
    assert adapter.max_retries.total == 3
    assert 429 in adapter.max_retries.status_forcelist


class _FakeResponse:
    def __init__(self, payload=None, text="", status=200):
        self._payload = payload
        self.text = text
        self.status_code = status
        self.encoding = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_json_calls_limiter_then_returns_payload(monkeypatch):
    calls = []

    class _S:
        def get(self, url, **kw):
            calls.append(url)
            return _FakeResponse(payload={"ok": 1})

    limiter = H.RateLimiter(0.0)
    seen = []
    monkeypatch.setattr(limiter, "wait", lambda: seen.append(True))

    out = H.fetch_json(_S(), "https://x/y", limiter=limiter)
    assert out == {"ok": 1}
    assert seen == [True]
    assert calls == ["https://x/y"]


def test_fetch_text_applies_explicit_encoding():
    """spec 9.3：TDCC 需以 utf-8-sig 解碼。"""
    resp = _FakeResponse(text="資料日期,證券代號")

    class _S:
        def get(self, url, **kw):
            return resp

    out = H.fetch_text(_S(), "https://x", encoding="utf-8-sig")
    assert resp.encoding == "utf-8-sig"
    assert out.startswith("資料日期")


# --- 守門測試 -----------------------------------------------------------
# 以 AST 判斷實際的 import／呼叫，而非字串掃描。
# 字串掃描會把註解裡對這條規則的說明也判成違規，逼得程式碼不能自我說明。


def _find_urllib_request_users(root: Path) -> list[Path]:
    offenders = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                a.name.startswith("urllib.request") for a in node.names
            ):
                offenders.append(path)
                break
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "urllib.request"
            ):
                offenders.append(path)
                break
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "request"
                and isinstance(node.value, ast.Name)
                and node.value.id == "urllib"
            ):
                offenders.append(path)
                break
    return offenders


def _find_verify_false_users(root: Path) -> list[Path]:
    offenders = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if any(
                kw.arg == "verify"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in node.keywords
            ):
                offenders.append(path)
                break
    return offenders


def test_no_module_uses_urllib_request():
    """Global Constraint：TWSE 憑證會讓標準庫的 urllib 取用器失敗（spec 9.3）。"""
    assert _find_urllib_request_users(Path("src")) == []


def test_no_module_disables_tls_verification():
    """Global Constraint：禁止關閉 TLS 驗證（spec 9.3）。"""
    assert _find_verify_false_users(Path("src")) == []


def test_guard_detectors_catch_real_violations(tmp_path):
    """守門測試本身要能抓到真違規，否則可能一直是綠的卻沒在守。"""
    bad = tmp_path / "bad.py"
    bad.write_text(
        "import urllib.request\n"
        "import requests\n"
        "def go():\n"
        "    return requests.get('https://x', verify=False)\n",
        encoding="utf-8",
    )
    assert _find_urllib_request_users(tmp_path) == [bad]
    assert _find_verify_false_users(tmp_path) == [bad]


def test_guard_detectors_ignore_mentions_in_comments(tmp_path):
    """註解或字串裡提到規則名稱不算違規。"""
    ok = tmp_path / "ok.py"
    ok.write_text(
        '"""不可使用 urllib.request，也不可傳 verify=False。"""\n'
        "MSG = 'urllib.request is banned'\n",
        encoding="utf-8",
    )
    assert _find_urllib_request_users(tmp_path) == []
    assert _find_verify_false_users(tmp_path) == []

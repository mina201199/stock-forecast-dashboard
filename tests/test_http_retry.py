"""傳輸層暫時性錯誤的重試。

實測背景：TPEx 在 Cloudflare 後面，連續請求 8 次有 6 次失敗 ——
一種是 520 Server Error（不在標準 5xx 重試清單），
一種是 ChunkedEncodingError（回應中途截斷，urllib3 Retry 攔不到，
因為它發生在 requests 讀取內容的階段）。
"""
import pytest
import requests

from src import http as H


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text
        self.encoding = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(H.time, "sleep", lambda s: None)


def test_cloudflare_520_is_in_retry_list():
    assert 520 in H.RETRY_STATUS
    assert 521 in H.RETRY_STATUS and 522 in H.RETRY_STATUS and 524 in H.RETRY_STATUS


def test_retries_on_chunked_encoding_error_then_succeeds():
    session = _Session([
        requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
        requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
        _Resp(payload={"ok": 1}),
    ])
    assert H.fetch_json(session, "https://x") == {"ok": 1}
    assert session.calls == 3


def test_retries_on_520_then_succeeds():
    session = _Session([_Resp(status=520), _Resp(payload={"ok": 2})])
    assert H.fetch_json(session, "https://x") == {"ok": 2}
    assert session.calls == 2


def test_does_not_retry_on_404():
    """客戶端錯誤重試沒有意義，必須立刻拋出。"""
    session = _Session([_Resp(status=404)])
    with pytest.raises(requests.HTTPError):
        H.fetch_json(session, "https://x")
    assert session.calls == 1


def test_gives_up_after_configured_attempts_and_raises_last_error():
    err = requests.exceptions.ChunkedEncodingError("boom")
    session = _Session([err] * 10)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        H.fetch_json(session, "https://x")
    assert session.calls == H.config.TRANSIENT_ATTEMPTS


def test_limiter_is_applied_on_every_attempt():
    seen = []
    limiter = H.RateLimiter(0.0)
    limiter.wait = lambda: seen.append(1)
    session = _Session([
        requests.exceptions.ConnectionError("x"), _Resp(payload={"ok": 3}),
    ])
    H.fetch_json(session, "https://x", limiter=limiter)
    assert len(seen) == 2


def test_fetch_text_retries_and_applies_encoding():
    resp = _Resp(text="資料日期")
    session = _Session([requests.exceptions.Timeout("t"), resp])
    out = H.fetch_text(session, "https://x", encoding="utf-8-sig")
    assert out == "資料日期"
    assert resp.encoding == "utf-8-sig"

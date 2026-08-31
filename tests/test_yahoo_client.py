import pytest

from src.http import YahooClient


class _Resp:
    def __init__(self, text="", payload=None, status=200):
        self.text = text
        self.status_code = status
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    """記錄呼叫順序，並依 URL 回傳預設回應。"""

    def __init__(self, crumb="abc123", summary_status=200):
        self.calls = []
        self.crumb_value = crumb
        self.summary_status = summary_status

    def get(self, url, **kw):
        self.calls.append(url)
        if "fc.yahoo.com" in url:
            # spec 2.4：實際會回 404，但 cookie 已種下，不得視為失敗
            return _Resp(status=404)
        if "getcrumb" in url:
            return _Resp(text=self.crumb_value)
        if "quoteSummary" in url:
            if self.summary_status == 401:
                self.summary_status = 200  # 第二次握手後成功
                return _Resp(status=401)
            return _Resp(payload={"quoteSummary": {"result": [{"summaryDetail": {"beta": 1.1}}]}})
        raise AssertionError(f"未預期的 URL: {url}")


def test_handshake_tolerates_404_from_fc_yahoo():
    """spec 2.4：fc.yahoo.com 回 404 屬正常，不得中斷流程。"""
    s = _Session()
    client = YahooClient(s)
    assert client.crumb() == "abc123"
    assert any("fc.yahoo.com" in u for u in s.calls)
    assert any("getcrumb" in u for u in s.calls)


def test_crumb_is_cached_across_calls():
    s = _Session()
    client = YahooClient(s)
    client.crumb()
    client.crumb()
    assert sum("getcrumb" in u for u in s.calls) == 1


def test_quote_summary_returns_first_result():
    client = YahooClient(_Session())
    out = client.quote_summary("AAPL", ["summaryDetail"])
    assert out["summaryDetail"]["beta"] == 1.1


def test_quote_summary_rehandshakes_once_on_401():
    """spec 9.2：401 -> 重新握手 1 次。"""
    s = _Session(summary_status=401)
    client = YahooClient(s)
    out = client.quote_summary("AAPL", ["summaryDetail"])
    assert out["summaryDetail"]["beta"] == 1.1
    assert sum("getcrumb" in u for u in s.calls) == 2


def test_empty_crumb_raises():
    class _Bad(_Session):
        def get(self, url, **kw):
            if "getcrumb" in url:
                return _Resp(text="<html>error</html>")
            return super().get(url, **kw)

    with pytest.raises(RuntimeError, match="crumb"):
        YahooClient(_Bad()).crumb()


def test_no_results_raises():
    class _Empty(_Session):
        def get(self, url, **kw):
            if "quoteSummary" in url:
                return _Resp(payload={"quoteSummary": {"result": []}})
            return super().get(url, **kw)

    with pytest.raises(RuntimeError, match="無結果"):
        YahooClient(_Empty()).quote_summary("X", ["summaryDetail"])

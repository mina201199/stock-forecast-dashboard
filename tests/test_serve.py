"""本地即時報價代理。

存在的理由是 spec 2.7 實測到的 CORS 限制：三個來源都不回傳
Access-Control-Allow-Origin，瀏覽器無法直連。
"""
import json
import urllib.error
import urllib.request

import pytest

from src import serve


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


# --- 快取 -------------------------------------------------------------


def test_cache_returns_stored_value_within_ttl():
    clock = _Clock()
    cache = serve.TTLCache(5.0, clock)
    calls = []
    producer = lambda: (calls.append(1), "v")[1]  # noqa: E731

    first, cached_first = cache.get_or_call("k", producer)
    clock.advance(3.0)
    second, cached_second = cache.get_or_call("k", producer)

    assert first == second == "v"
    assert cached_first is False and cached_second is True
    assert len(calls) == 1


def test_cache_refetches_after_ttl():
    clock = _Clock()
    cache = serve.TTLCache(5.0, clock)
    calls = []
    producer = lambda: (calls.append(1), len(calls))[1]  # noqa: E731

    cache.get_or_call("k", producer)
    clock.advance(6.0)
    value, cached = cache.get_or_call("k", producer)

    assert value == 2 and cached is False


def test_cache_keys_are_independent():
    cache = serve.TTLCache(5.0, _Clock())
    cache.get_or_call("a", lambda: 1)
    value, cached = cache.get_or_call("b", lambda: 2)
    assert value == 2 and cached is False


# --- 服務層 -----------------------------------------------------------


def _service(fetcher, ttl=5.0):
    return serve.LiveService(session=None, cache=serve.TTLCache(ttl, _Clock()),
                             fetcher=fetcher)


def test_quotes_returns_rows():
    svc = _service(lambda session, symbols: [{"code": "2330", "price": 2380.0}])
    out = svc.quotes(["2330.TW"])
    assert out["status"] == "ok"
    assert out["quotes"][0]["price"] == 2380.0


def test_empty_symbol_list_short_circuits():
    called = []
    svc = _service(lambda s, sym: called.append(1) or [])
    assert svc.quotes([])["status"] == "empty"
    assert called == []


def test_market_closed_is_not_an_error():
    """spec 9.2：非交易時段 MIS 回空，須視為休市而非失敗。"""
    out = _service(lambda s, sym: []).quotes(["2330.TW"])
    assert out["status"] == "closed"
    assert out["quotes"] == []


def test_upstream_failure_returns_last_good_snapshot_not_stale_close():
    """spec 9.2：不得以日線收盤價冒充即時價，只能回上次成功的快照並標示。"""
    state = {"fail": False}

    def fetcher(session, symbols):
        if state["fail"]:
            raise RuntimeError("MIS timeout")
        return [{"code": "2330", "price": 2380.0}]

    svc = _service(fetcher, ttl=0.0)
    svc.quotes(["2330.TW"])
    state["fail"] = True
    out = svc.quotes(["2330.TW"])

    assert out["status"] == "stale"
    assert out["quotes"][0]["price"] == 2380.0
    assert "MIS timeout" in out["error"]


def test_upstream_failure_without_prior_success_reports_error():
    def boom(session, symbols):
        raise RuntimeError("no network")

    out = _service(boom, ttl=0.0).quotes(["2330.TW"])
    assert out["status"] == "error"
    assert out["quotes"] == []


def test_symbol_count_is_capped():
    seen = {}

    def fetcher(session, symbols):
        seen["n"] = len(symbols)
        return []

    _service(fetcher).quotes([f"{i}.TW" for i in range(500)])
    assert seen["n"] == serve.MAX_SYMBOLS_PER_REQUEST


def test_repeated_requests_hit_cache_not_upstream():
    calls = []

    def fetcher(session, symbols):
        calls.append(1)
        return [{"code": "2330", "price": 1.0}]

    svc = _service(fetcher, ttl=60.0)
    svc.quotes(["2330.TW"])
    svc.quotes(["2330.TW"])
    svc.quotes(["2330.TW"])
    assert len(calls) == 1


# --- 綁定位址 ---------------------------------------------------------


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", ""])
def test_refuses_non_loopback_bind(host):
    """對外開放即時報價代理沒有必要，也擴大攻擊面。"""
    with pytest.raises(ValueError, match="本機"):
        serve.serve(host=host, port=0)


# --- HTTP 端到端 ------------------------------------------------------


@pytest.fixture()
def running(tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<html><body>頁面</body></html>", encoding="utf-8")
    service = serve.LiveService(
        session=None, cache=serve.TTLCache(5.0, _Clock()),
        fetcher=lambda s, sym: [{"code": sym[0].split(".")[0], "price": 123.0}],
    )
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.build_handler(html, service))
    import threading
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 (本機測試)
        return response.status, response.read().decode("utf-8")


def test_serves_index_html(running):
    status, body = _get(running + "/")
    assert status == 200
    assert "頁面" in body


def test_api_live_returns_json(running):
    status, body = _get(running + "/api/live?symbols=2330.TW")
    assert status == 200
    data = json.loads(body)
    assert data["status"] == "ok"
    assert data["quotes"][0]["price"] == 123.0


def test_unknown_path_is_404(running):
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(running + "/secret")
    assert exc.value.code == 404


def test_missing_html_reports_clearly(tmp_path):
    service = serve.LiveService(session=None, cache=serve.TTLCache(1.0, _Clock()),
                                fetcher=lambda s, sym: [])
    import threading
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), serve.build_handler(tmp_path / "missing.html", service)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(f"http://127.0.0.1:{server.server_address[1]}/")
        assert exc.value.code == 404
        assert "index.html 不存在" in exc.value.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()

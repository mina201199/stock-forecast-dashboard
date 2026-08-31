"""台灣交易所主機的 TLS 設定。

背景：TWSE／TPEx 的憑證由 TWCA 簽發並在多個後端輪替，部分憑證缺少
Subject Key Identifier，OpenSSL 3.x 的 VERIFY_X509_STRICT 會拒絕。
實測為間歇性 —— 同一主機有時整批成功、有時連續三次重試全失敗。
"""
import ssl

from src.http import TW_EXCHANGE_HOSTS, RelaxedX509Adapter, make_session


def test_relaxed_context_clears_only_the_strict_extension_flag():
    ctx = RelaxedX509Adapter._context()
    assert not (ctx.verify_flags & ssl.VERIFY_X509_STRICT)


def test_relaxed_context_still_verifies_chain_and_hostname():
    """關鍵安全性質：這不等於 verify=False。"""
    ctx = RelaxedX509Adapter._context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_default_context_does_have_the_strict_flag():
    """證明我們確實改變了行為，而不是設了一個沒有作用的旗標。"""
    assert ssl.create_default_context().verify_flags & ssl.VERIFY_X509_STRICT


def test_taiwan_hosts_get_the_relaxed_adapter():
    session = make_session()
    for host in TW_EXCHANGE_HOSTS:
        adapter = session.get_adapter(f"{host}/some/path")
        assert isinstance(adapter, RelaxedX509Adapter), host


def test_other_hosts_keep_strict_verification():
    """Yahoo／Google／SEC／Wikipedia 不得被放寬。"""
    session = make_session()
    for url in [
        "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
        "https://news.google.com/rss/search",
        "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    ]:
        assert not isinstance(session.get_adapter(url), RelaxedX509Adapter), url


def test_relaxed_adapter_keeps_retry_policy():
    session = make_session()
    adapter = session.get_adapter("https://www.tpex.org.tw/openapi/v1/x")
    assert adapter.max_retries.total == 3
    assert 429 in adapter.max_retries.status_forcelist

"""共用 HTTP 層。

所有對外請求都必須經過本模組，理由：
1. TWSE 憑證缺 Subject Key Identifier，urllib.request 會失敗，requests 可以（spec 9.3）
2. 集中限速與重試，避免各 fetch 模組各寫一套
3. fetch_json / fetch_text 是唯一的網路接縫，測試以 monkeypatch 打樁即可離線
"""
from __future__ import annotations

import ssl
import threading
import time
from typing import Any

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.util.ssl_ import create_urllib3_context

import config

# 台灣交易所主機。這些站台的憑證由 TWCA 簽發，且會在多個後端間輪替；
# 其中部分憑證缺少 Subject Key Identifier 擴充欄位，OpenSSL 3.x 的
# VERIFY_X509_STRICT 會拒絕。實測是間歇性的：同一主機重測數次，
# 有時整批成功、有時連續三次重試全失敗。
TW_EXCHANGE_HOSTS = (
    "https://openapi.twse.com.tw",
    "https://www.twse.com.tw",
    "https://mis.twse.com.tw",
    "https://www.tpex.org.tw",
    "https://opendata.tdcc.com.tw",
)


class RelaxedX509Adapter(HTTPAdapter):
    """僅關閉 RFC 5280 嚴格擴充欄位檢查的 TLS 介面卡。

    憑證鏈驗證、主機名比對、有效期檢查全部保留
    （verify_mode 仍為 CERT_REQUIRED、check_hostname 仍為 True）。
    只放寬 VERIFY_X509_STRICT 這一條，因為 TWCA 憑證缺少 SKI 欄位。

    這與 verify=False 有本質差異：後者關閉全部驗證，本專案禁用。
    """

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context()
        return super().proxy_manager_for(*args, **kwargs)

    @staticmethod
    def _context() -> ssl.SSLContext:
        ctx = create_urllib3_context()
        ctx.load_verify_locations(certifi.where())
        ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        return ctx


# 需要重試的狀態碼。除了標準 5xx，還要含 Cloudflare 專屬碼 ——
# TPEx 在 Cloudflare 後面，連續請求時實測會回 520。
RETRY_STATUS = (429, 500, 502, 503, 504, 520, 521, 522, 524)

# 傳輸層的暫時性錯誤。ChunkedEncodingError（回應被截斷）不是狀態碼錯誤，
# urllib3 的 Retry 抓不到，必須在應用層重試；實測 TPEx 會出現。
TRANSIENT_ERRORS = (
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


class RateLimiter:
    """執行緒安全的最小間隔限速器。"""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self.min_interval:
                time.sleep(self.min_interval - delta)
            self._last = time.monotonic()


def make_session(user_agent: str = config.USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
    )
    retry = Retry(
        total=config.HTTP_RETRIES,
        connect=config.HTTP_RETRIES,
        read=config.HTTP_RETRIES,
        status=config.HTTP_RETRIES,
        status_forcelist=RETRY_STATUS,
        allowed_methods=frozenset(["GET"]),
        backoff_factor=config.HTTP_BACKOFF,
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    session.mount("http://", HTTPAdapter(max_retries=retry, pool_maxsize=16))
    # 只有台灣交易所主機用放寬版；Yahoo／Google／SEC／Wikipedia 維持完整嚴格驗證
    for host in TW_EXCHANGE_HOSTS:
        session.mount(host, RelaxedX509Adapter(max_retries=retry, pool_maxsize=16))
    return session


def get_with_retry(
    session: requests.Session,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    params: dict | None = None,
    timeout: int | None = None,
    attempts: int = config.TRANSIENT_ATTEMPTS,
    backoff: float = config.TRANSIENT_BACKOFF,
) -> requests.Response:
    """在 urllib3 Retry 之上再補一層應用層重試。

    需要這一層的原因是 ChunkedEncodingError（回應中途被截斷）不會被
    urllib3 的 Retry 攔到 —— 它發生在 requests 讀取內容的階段。
    實測 TPEx 連續請求時 8 次有 6 次因此失敗。
    """
    last: Exception | None = None
    for attempt in range(attempts):
        if limiter is not None:
            limiter.wait()
        try:
            response = session.get(url, params=params, timeout=timeout or config.HTTP_TIMEOUT)
            response.raise_for_status()
            return response
        except TRANSIENT_ERRORS as exc:
            last = exc
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status not in RETRY_STATUS:
                raise
            last = exc
        if attempt < attempts - 1:
            time.sleep(backoff * (2**attempt))
    raise last  # type: ignore[misc]


def fetch_json(
    session: requests.Session,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    params: dict | None = None,
    timeout: int | None = None,
) -> Any:
    return get_with_retry(
        session, url, limiter=limiter, params=params, timeout=timeout
    ).json()


def fetch_text(
    session: requests.Session,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    params: dict | None = None,
    timeout: int | None = None,
    encoding: str | None = None,
) -> str:
    response = get_with_retry(session, url, limiter=limiter, params=params, timeout=timeout)
    if encoding is not None:
        response.encoding = encoding
    return response.text


class YahooClient:
    """Yahoo Finance 存取。

    quoteSummary 需要 cookie + crumb 握手（spec 2.4）：
      1. GET fc.yahoo.com          -> 回 404，但 cookie 會種下，不得視為失敗
      2. GET v1/test/getcrumb      -> 取得 crumb
      3. quoteSummary 附加 &crumb=
    v7/finance/quote 已全面封鎖，不使用。
    """

    COOKIE_URL = "https://fc.yahoo.com"
    CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
    SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}"

    def __init__(self, session: requests.Session, limiter: RateLimiter | None = None) -> None:
        self._session = session
        self._limiter = limiter
        self._crumb: str | None = None

    def _handshake(self) -> str:
        try:
            self._session.get(self.COOKIE_URL, timeout=20)
        except requests.RequestException:
            # cookie 端點不穩定；即使連線失敗也先試著取 crumb
            pass
        response = self._session.get(self.CRUMB_URL, timeout=20)
        response.raise_for_status()
        crumb = response.text.strip()
        if not crumb or "<" in crumb:
            raise RuntimeError(f"Yahoo crumb 取得失敗：{crumb[:60]!r}")
        self._crumb = crumb
        return crumb

    def crumb(self) -> str:
        return self._crumb or self._handshake()

    def quote_summary(self, symbol: str, modules: list[str]) -> dict:
        for attempt in (1, 2):
            if self._limiter is not None:
                self._limiter.wait()
            response = self._session.get(
                self.SUMMARY_URL.format(symbol=symbol),
                params={"modules": ",".join(modules), "crumb": self.crumb()},
                timeout=config.HTTP_TIMEOUT,
            )
            if response.status_code == 401 and attempt == 1:
                self._crumb = None  # crumb 過期 -> 重新握手一次（spec 9.2）
                continue
            response.raise_for_status()
            payload = response.json()
            results = (payload.get("quoteSummary") or {}).get("result") or []
            if not results:
                raise RuntimeError(f"{symbol} quoteSummary 無結果")
            return results[0]
        raise RuntimeError(f"{symbol} quoteSummary 重新握手後仍失敗")

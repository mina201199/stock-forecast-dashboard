"""共用 HTTP 層。

所有對外請求都必須經過本模組，理由：
1. TWSE 憑證缺 Subject Key Identifier，urllib.request 會失敗，requests 可以（spec 9.3）
2. 集中限速與重試，避免各 fetch 模組各寫一套
3. fetch_json / fetch_text 是唯一的網路接縫，測試以 monkeypatch 打樁即可離線
"""
from __future__ import annotations

import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config


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
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        backoff_factor=config.HTTP_BACKOFF,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_json(
    session: requests.Session,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    params: dict | None = None,
    timeout: int | None = None,
) -> Any:
    if limiter is not None:
        limiter.wait()
    response = session.get(url, params=params, timeout=timeout or config.HTTP_TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_text(
    session: requests.Session,
    url: str,
    *,
    limiter: RateLimiter | None = None,
    params: dict | None = None,
    timeout: int | None = None,
    encoding: str | None = None,
) -> str:
    if limiter is not None:
        limiter.wait()
    response = session.get(url, params=params, timeout=timeout or config.HTTP_TIMEOUT)
    response.raise_for_status()
    if encoding is not None:
        response.encoding = encoding
    return response.text

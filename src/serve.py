"""本地即時報價代理（spec 8.6）。

為什麼需要這一層：TWSE MIS、Yahoo、TWSE OpenAPI 都不回傳
Access-Control-Allow-Origin，瀏覽器裡的靜態頁面無法直接取得即時報價
（spec 2.7）。伺服器端沒有 CORS 限制，因此由 Python 代打再回傳。

安全性：僅綁定 127.0.0.1，不對外開放。上游結果快取數秒，
避免多個分頁重整時打爆來源。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import config
from src.fetch import live
from src.http import make_session

MAX_SYMBOLS_PER_REQUEST = 60


class TTLCache:
    """極簡的時間快取。用注入的 clock 讓測試不需要真的等待。"""

    def __init__(self, ttl: float, clock=time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, object]] = {}

    def get_or_call(self, key: str, producer):
        with self._lock:
            hit = self._entries.get(key)
            if hit and self._clock() - hit[0] < self.ttl:
                return hit[1], True
        value = producer()
        with self._lock:
            self._entries[key] = (self._clock(), value)
        return value, False


class LiveService:
    """把即時報價包成可測試的單元，與 HTTP 層分離。"""

    def __init__(self, session, cache: TTLCache | None = None, fetcher=None) -> None:
        self.session = session
        self.cache = cache or TTLCache(config.LIVE_CACHE_SECONDS)
        self.fetcher = fetcher or live.fetch_mis
        self.last_good: dict[str, object] | None = None

    def quotes(self, symbols: list[str]) -> dict:
        symbols = [s for s in symbols if s][:MAX_SYMBOLS_PER_REQUEST]
        if not symbols:
            return {"quotes": [], "status": "empty", "cached": False}

        key = "|".join(sorted(symbols))
        try:
            rows, cached = self.cache.get_or_call(key, lambda: self.fetcher(self.session, symbols))
        except Exception as exc:
            # spec 9.2：上游失效時回最後一次成功的快照，並標示時間。
            # 絕不以日線收盤價冒充即時價。
            if self.last_good:
                return {**self.last_good, "status": "stale", "error": str(exc)[:120]}
            return {"quotes": [], "status": "error", "error": str(exc)[:120], "cached": False}

        if not rows:
            return {"quotes": [], "status": "closed", "cached": cached}

        payload = {
            "quotes": rows,
            "status": "ok",
            "cached": cached,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.last_good = payload
        return payload


def build_handler(html_path: Path, service: LiveService):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 靜音預設的存取日誌
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802  (BaseHTTPRequestHandler 的介面)
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                if not html_path.exists():
                    self._send(404, "index.html 不存在，請先執行 python run.py".encode(),
                               "text/plain; charset=utf-8")
                    return
                self._send(200, html_path.read_bytes(), "text/html; charset=utf-8")
                return

            if parsed.path == "/api/live":
                params = parse_qs(parsed.query)
                symbols = [s for s in (params.get("symbols", [""])[0]).split(",") if s]
                body = json.dumps(service.quotes(symbols), ensure_ascii=False).encode()
                self._send(200, body, "application/json; charset=utf-8")
                return

            self._send(404, b"not found", "text/plain; charset=utf-8")

    return Handler


def serve(
    host: str = config.SERVE_HOST,
    port: int = config.SERVE_PORT,
    html_path: Path | None = None,
    session=None,
) -> ThreadingHTTPServer:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError(f"僅允許綁定本機位址，收到：{host}")
    target = Path(html_path or config.OUT_HTML)
    service = LiveService(session or make_session())
    return ThreadingHTTPServer((host, port), build_handler(target, service))

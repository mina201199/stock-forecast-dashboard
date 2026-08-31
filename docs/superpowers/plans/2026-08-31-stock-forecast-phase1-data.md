# 股票預測平台 — 第一階段：資料層 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可增量更新的資料管線，把台股 300 檔與美股 200 檔的價量、基本面、籌碼、股利、新聞、即時報價抓進 SQLite，並可由 `python run.py --fetch-only` 端到端執行。

**Architecture:** 所有 HTTP 呼叫集中於 `src/http.py` 的單一接縫（`fetch_json` / `fetch_text`），各資料源在 `src/fetch/` 下各自成模組、只負責「抓取 + 正規化成 dict list」，寫入由 `src/store.py` 統一處理。抓取與儲存分離，使得每個 fetch 模組都能用 fixture 離線測試。

**Tech Stack:** Python 3.13、requests、pandas、SQLite（stdlib）、pytest 8.4.2（HTTP 以 `monkeypatch` 打樁，不引入 `responses`／`requests-mock`）

**Spec:** `docs/superpowers/specs/2026-08-31-stock-forecast-design.md`

## Global Constraints

以下為專案全域規則，每個 Task 的要求都隱含包含本節。數值與字串**逐字**取自 spec。

- **禁用 `urllib.request`**。TWSE 憑證（TWCA 簽發）缺少 Subject Key Identifier，Python 3.13 的 `ssl.create_default_context()` 預設啟用 `VERIFY_X509_STRICT` 會拒絕。一律使用 `requests`（走 urllib3，不套該旗標）。（spec §9.3）
- **禁用 `verify=False`**。憑證仍須驗證。（spec §9.3）
- **Yahoo chart 必須帶 `&events=div,split`**，否則不回傳 `indicators.adjclose`。（spec §2.3）
- **所有報酬率與技術指標一律用 `adjclose`**；`close` 僅用於顯示現價。（spec §2.3）
- **`fc.yahoo.com` 回 404 視為正常**，只檢查 cookie 是否種下。（spec §2.4、§9.2）
- **TWSE OpenAPI 使用中文欄位鍵**，須集中定義於 `src/constants.py`，不得散落於各模組。（spec §9.3）
- **TDCC 為 CSV + UTF-8 BOM**，以 `utf-8-sig` 解碼。（spec §9.3）
- **TPEx 三大法人欄位名含不一致空格**，讀取前須正規化。（spec §9.3）
- **MIS 成交價 `z` 可能為字串 `"-"`**，fallback 序列 `z` → 前次成交價 → `y`（昨收）。（spec §9.3）
- **`SEC_CONTACT_EMAIL` 預設留空，留空時自動停用 SEC 備援**。不得填入假信箱。（spec §2.6）
- 台股基準 `0050.TW`、美股基準 `SPY`，皆取 `adjclose`。（spec §6.1）
- 台股 300 檔（成交值排序）、美股 200 檔（S&P 500 市值排序）、歷史 3 年、預測期間 20 交易日。（spec §1.2）
- 測試**不得打真實網路**，一律用 fixture + `monkeypatch`。（spec §10）

---

## File Structure

| 檔案 | 責任 |
|---|---|
| `config.py` | 全域設定值（檔數、路徑、限速、基準、SEC 信箱） |
| `src/constants.py` | TWSE／TPEx 的中文與英文欄位名常數 |
| `src/http.py` | `RateLimiter`、`make_session`、`fetch_json`、`fetch_text`、`YahooClient` |
| `src/store.py` | SQLite schema、`upsert_*`、`get_last_date` |
| `src/fetch/twse.py` | 上市：價量、估值、月營收、季報、EPS、除權息、融資融券、借券、三大法人、公司資料 |
| `src/fetch/tpex.py` | 上櫃：同上（含欄位正規化） |
| `src/fetch/tdcc.py` | 集保大戶持股（CSV/BOM） |
| `src/fetch/yahoo.py` | 日線歷史（adjclose）、美股 quoteSummary |
| `src/fetch/sec.py` | 美股 XBRL 備援 |
| `src/fetch/news.py` | Google News RSS |
| `src/fetch/live.py` | TWSE MIS 即時報價、Yahoo 分鐘線 |
| `src/fetch/universe.py` | 選股池決定 |
| `run.py` | CLI 進入點 |

---

### Task 1: 專案骨架、設定與欄位常數

**Files:**
- Create: `config.py`
- Create: `src/__init__.py`, `src/fetch/__init__.py`
- Create: `src/constants.py`
- Create: `pyproject.toml`
- Create: `tests/__init__.py`, `tests/conftest.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 無（第一個 task）
- Produces: `config` 模組的 `DB_PATH: Path`、`TW_COUNT: int = 300`、`US_COUNT: int = 200`、`HISTORY_YEARS: int = 3`、`HORIZON_DAYS: int = 20`、`TW_BENCHMARK: str = "0050.TW"`、`US_BENCHMARK: str = "SPY"`、`SEC_CONTACT_EMAIL: str = ""`、`USER_AGENT: str`、`YAHOO_MIN_INTERVAL: float = 0.6`、`SERVE_HOST`、`SERVE_PORT`；`src.constants` 的各欄位名常數

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_config.py
from pathlib import Path

import config
from src import constants


def test_config_has_required_values():
    assert config.TW_COUNT == 300
    assert config.US_COUNT == 200
    assert config.HISTORY_YEARS == 3
    assert config.HORIZON_DAYS == 20
    assert config.TW_BENCHMARK == "0050.TW"
    assert config.US_BENCHMARK == "SPY"
    assert isinstance(config.DB_PATH, Path)


def test_sec_email_defaults_empty_to_disable_fallback():
    """spec §2.6：留空時自動停用 SEC 備援，不得填假信箱。"""
    assert config.SEC_CONTACT_EMAIL == ""


def test_twse_chinese_field_names_are_exact():
    """spec §9.3：TWSE 中文欄位鍵須集中定義。"""
    assert constants.REV_PUBLISH_DATE == "出表日期"
    assert constants.REV_YM == "資料年月"
    assert constants.REV_CODE == "公司代號"
    assert constants.REV_MONTH_REVENUE == "營業收入-當月營收"
    assert constants.REV_YOY == "營業收入-去年同月增減(%)"
    assert constants.MARGIN_CODE == "股票代號"
    assert constants.MARGIN_BALANCE_TODAY == "融資今日餘額"
    assert constants.SHORT_BALANCE_TODAY == "融券今日餘額"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'config'`

- [ ] **Step 3: 建立設定與常數**

```python
# config.py
"""全域設定。所有可調參數集中於此。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "cache.db"
OUT_HTML = ROOT / "index.html"

# 選股池（spec §1.2）
TW_COUNT = 300
US_COUNT = 200

# 模型（spec §1.2、§6.1）
HISTORY_YEARS = 3
HORIZON_DAYS = 20
TW_BENCHMARK = "0050.TW"
US_BENCHMARK = "SPY"

# HTTP
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
YAHOO_MIN_INTERVAL = 0.6
TWSE_MIN_INTERVAL = 0.3
HTTP_TIMEOUT = 45
HTTP_RETRIES = 3
HTTP_BACKOFF = 2.0

# SEC：留空即停用備援（spec §2.6）。不得填假信箱。
SEC_CONTACT_EMAIL = ""

# --serve 模式（spec §8.6）
SERVE_HOST = "127.0.0.1"
SERVE_PORT = 8848
LIVE_CACHE_SECONDS = 5
```

```python
# src/constants.py
"""TWSE／TPEx 欄位名常數。

TWSE OpenAPI 使用中文欄位鍵，字串必須逐字正確（spec §9.3）。
所有取值一律引用本模組，不得在各 fetch 模組內寫死字面值。
"""

# --- t187ap05_L 月營收 ---
REV_PUBLISH_DATE = "出表日期"          # 公布日，point-in-time 對齊用（spec §6.1 R3）
REV_YM = "資料年月"
REV_CODE = "公司代號"
REV_NAME = "公司名稱"
REV_INDUSTRY = "產業別"
REV_MONTH_REVENUE = "營業收入-當月營收"
REV_LAST_MONTH = "營業收入-上月營收"
REV_LAST_YEAR_MONTH = "營業收入-去年當月營收"
REV_MOM = "營業收入-上月比較增減(%)"
REV_YOY = "營業收入-去年同月增減(%)"

# --- t187ap14_L 各年度 EPS ---
EPS_YEAR = "年度"
EPS_QUARTER = "季別"
EPS_CODE = "公司代號"
EPS_INDUSTRY = "產業別"
EPS_BASIC = "基本每股盈餘(元)"
EPS_REVENUE = "營業收入"
EPS_OP_INCOME = "營業利益"
EPS_NET_INCOME = "稅後淨利"

# --- t187ap06_L_ci 綜合損益表 ---
CI_YEAR = "年度"
CI_QUARTER = "季別"
CI_CODE = "公司代號"
CI_REVENUE = "營業收入"
CI_COST = "營業成本"
CI_OP_INCOME = "營業利益"

# --- t187ap03_L 公司基本資料 ---
PROFILE_CODE = "公司代號"
PROFILE_SHORT_NAME = "公司簡稱"
PROFILE_INDUSTRY = "產業別"
PROFILE_CAPITAL = "實收資本額"

# --- t187ap45_L 股利分派 ---
DIV_CODE = "公司代號"
DIV_CASH = "股東配發-盈餘分配之現金股利(元/股)"
DIV_STOCK = "股東配發-盈餘轉增資配股(元/股)"
DIV_MEETING_DATE = "股東會日期"

# --- MI_MARGN 融資融券 ---
MARGIN_CODE = "股票代號"
MARGIN_BALANCE_TODAY = "融資今日餘額"
MARGIN_BALANCE_PREV = "融資前日餘額"
SHORT_BALANCE_TODAY = "融券今日餘額"
SHORT_BALANCE_PREV = "融券前日餘額"

# --- 英文欄位（TWSE exchangeReport / TPEx openapi）---
TWSE_DAY_CODE = "Code"
TWSE_DAY_CLOSE = "ClosingPrice"
TWSE_DAY_VALUE = "TradeValue"
TWSE_BWIBBU_PE = "PEratio"
TWSE_BWIBBU_YIELD = "DividendYield"
TWSE_BWIBBU_PB = "PBratio"
TWSE_EXRIGHT_DATE = "Date"
TWSE_EXRIGHT_TYPE = "Exdividend"
TWSE_EXRIGHT_CASH = "CashDividend"
TWSE_EXRIGHT_STOCK = "StockDividendRatio"

TPEX_CODE = "SecuritiesCompanyCode"
TPEX_CLOSE = "Close"
TPEX_AMOUNT = "TransactionAmount"
TPEX_EXRIGHT_DATE = "ExRrightsExDividendDate"
TPEX_EXRIGHT_TYPE = "ExRrightsExDividend"

# --- TDCC 集保股權分散（CSV，spec §9.3）---
TDCC_DATE = "資料日期"
TDCC_CODE = "證券代號"
TDCC_LEVEL = "持股分級"
TDCC_PCT = "占集保庫存數比例%"
TDCC_BIG_HOLDER_LEVEL = "15"  # 400 張以上
```

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
line-length = 100
target-version = "py313"
```

建立空的 `src/__init__.py`、`src/fetch/__init__.py`、`tests/__init__.py`、`tests/conftest.py`。

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add config.py src/ tests/ pyproject.toml
git commit -m "feat: 專案骨架、全域設定與 TWSE 欄位名常數"
```

---

### Task 2: HTTP 共用層（Session、限速、重試）

**Files:**
- Create: `src/http.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: `config.USER_AGENT`、`config.HTTP_TIMEOUT`、`config.HTTP_RETRIES`、`config.HTTP_BACKOFF`
- Produces:
  - `RateLimiter(min_interval: float)` → `.wait() -> None`
  - `make_session(user_agent: str = config.USER_AGENT) -> requests.Session`
  - `fetch_json(session, url: str, *, limiter=None, params=None, timeout=None) -> Any`
  - `fetch_text(session, url: str, *, limiter=None, params=None, timeout=None, encoding: str | None = None) -> str`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_http.py
import time
from pathlib import Path

import pytest

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
    """spec §9.3：TDCC 需以 utf-8-sig 解碼。"""
    resp = _FakeResponse(text="資料日期,證券代號")

    class _S:
        def get(self, url, **kw):
            return resp

    out = H.fetch_text(_S(), "https://x", encoding="utf-8-sig")
    assert resp.encoding == "utf-8-sig"
    assert out.startswith("資料日期")


def test_no_module_uses_urllib_request():
    """Global Constraint：TWSE 憑證會讓 urllib.request 失敗（spec §9.3）。"""
    offenders = [
        p for p in Path("src").rglob("*.py")
        if "urllib.request" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"這些檔案使用了 urllib.request：{offenders}"


def test_no_module_disables_tls_verification():
    """Global Constraint：禁用 verify=False。"""
    offenders = [
        p for p in Path("src").rglob("*.py")
        if "verify=False" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"這些檔案關閉了 TLS 驗證：{offenders}"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.http'`

- [ ] **Step 3: 實作 HTTP 層**

```python
# src/http.py
"""共用 HTTP 層。

所有對外請求都必須經過本模組，理由：
1. TWSE 憑證缺 Subject Key Identifier，urllib.request 會失敗，requests 可以（spec §9.3）
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
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_http.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/http.py tests/test_http.py
git commit -m "feat: HTTP 共用層（限速、重試、禁用 urllib.request 的守門測試）"
```

---

### Task 3: Yahoo crumb 握手

**Files:**
- Modify: `src/http.py`（新增 `YahooClient`）
- Test: `tests/test_yahoo_client.py`

**Interfaces:**
- Consumes: `make_session`、`RateLimiter`
- Produces: `YahooClient(session, limiter=None)`，方法 `.crumb() -> str`、`.quote_summary(symbol: str, modules: list[str]) -> dict`（回傳 `quoteSummary.result[0]`）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_yahoo_client.py
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
            # spec §2.4：實際會回 404，但 cookie 已種下，不得視為失敗
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
    """spec §2.4：fc.yahoo.com 回 404 屬正常，不得中斷流程。"""
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
    """spec §9.2：401 → 重新握手 1 次。"""
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
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_yahoo_client.py -v`
Expected: FAIL — `ImportError: cannot import name 'YahooClient'`

- [ ] **Step 3: 實作 YahooClient**

在 `src/http.py` 末端追加：

```python
class YahooClient:
    """Yahoo Finance 存取。

    quoteSummary 需要 cookie + crumb 握手（spec §2.4）：
      1. GET fc.yahoo.com          → 回 404，但 cookie 會種下，不得視為失敗
      2. GET v1/test/getcrumb      → 取得 crumb
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
                self._crumb = None  # crumb 過期 → 重新握手一次（spec §9.2）
                continue
            response.raise_for_status()
            payload = response.json()
            results = (payload.get("quoteSummary") or {}).get("result") or []
            if not results:
                raise RuntimeError(f"{symbol} quoteSummary 無結果")
            return results[0]
        raise RuntimeError(f"{symbol} quoteSummary 重新握手後仍失敗")
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_yahoo_client.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/http.py tests/test_yahoo_client.py
git commit -m "feat: Yahoo cookie+crumb 握手（容忍 fc.yahoo.com 404、401 重試一次）"
```

---

### Task 4: SQLite Schema

**Files:**
- Create: `src/store.py`
- Test: `tests/test_store_schema.py`

**Interfaces:**
- Consumes: `config.DB_PATH`
- Produces: `connect(path: Path | str) -> sqlite3.Connection`、`init_schema(conn) -> None`、模組常數 `TABLES: tuple[str, ...]`（11 張表名）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_store_schema.py
import sqlite3

import pytest

from src import store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def test_creates_all_eleven_tables(conn):
    """spec §4：11 張表。"""
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r["name"] for r in rows}
    expected = {
        "stocks", "prices", "fundamentals", "analyst", "institutional",
        "margin", "dividends", "holders", "news", "predictions", "meta",
    }
    assert expected <= names
    assert set(store.TABLES) == expected


def test_init_schema_is_idempotent(conn):
    store.init_schema(conn)
    store.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) c FROM stocks").fetchone()["c"] == 0


def test_prices_has_adjclose_and_composite_key(conn):
    """spec §2.3：報酬一律用 adjclose，故必須存。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(prices)")}
    assert {"symbol", "date", "open", "high", "low", "close", "adjclose", "volume"} <= cols

    conn.execute(
        "INSERT INTO prices (symbol,date,open,high,low,close,adjclose,volume)"
        " VALUES ('2330.TW','2026-06-11',2250,2260,2240,2250,2250,30000)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO prices (symbol,date,open,high,low,close,adjclose,volume)"
            " VALUES ('2330.TW','2026-06-11',1,1,1,1,1,1)"
        )


def test_rows_are_dict_like(conn):
    conn.execute("INSERT INTO meta (key,value) VALUES ('k','v')")
    row = conn.execute("SELECT * FROM meta").fetchone()
    assert row["key"] == "k" and row["value"] == "v"
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_store_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.store'`

- [ ] **Step 3: 實作 schema**

```python
# src/store.py
"""SQLite 快取層（spec §4）。

增量更新是本專案選擇分層架構的主因：改模型參數時不必重抓資料。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import config

TABLES = (
    "stocks", "prices", "fundamentals", "analyst", "institutional",
    "margin", "dividends", "holders", "news", "predictions", "meta",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stocks (
    symbol TEXT PRIMARY KEY, market TEXT NOT NULL, code TEXT NOT NULL,
    name TEXT, name_en TEXT, sector TEXT, industry TEXT,
    currency TEXT, listed_date TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, adjclose REAL, volume INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices(date);
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol TEXT NOT NULL, asof TEXT NOT NULL,
    pe REAL, forward_pe REAL, pb REAL, dividend_yield REAL, market_cap REAL,
    eps_ttm REAL, eps_forward REAL, revenue_yoy REAL, revenue_mom REAL,
    gross_margin REAL, op_margin REAL, net_margin REAL, roe REAL, roa REAL,
    debt_to_equity REAL, current_ratio REAL, free_cashflow REAL,
    source TEXT, PRIMARY KEY (symbol, asof)
);
CREATE TABLE IF NOT EXISTS analyst (
    symbol TEXT NOT NULL, asof TEXT NOT NULL,
    target_mean REAL, target_high REAL, target_low REAL,
    recommendation TEXT, n_analysts INTEGER, PRIMARY KEY (symbol, asof)
);
CREATE TABLE IF NOT EXISTS institutional (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    foreign_net INTEGER, trust_net INTEGER, dealer_net INTEGER,
    PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS margin (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    margin_balance INTEGER, margin_balance_prev INTEGER,
    short_balance INTEGER, short_balance_prev INTEGER,
    margin_util REAL, PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS dividends (
    symbol TEXT NOT NULL, ex_date TEXT NOT NULL,
    type TEXT, cash_dividend REAL, stock_dividend REAL, pay_date TEXT,
    PRIMARY KEY (symbol, ex_date)
);
CREATE TABLE IF NOT EXISTS holders (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    big_holder_pct REAL, PRIMARY KEY (symbol, date)
);
CREATE TABLE IF NOT EXISTS news (
    link_hash TEXT PRIMARY KEY, symbol TEXT NOT NULL, published_at TEXT,
    title TEXT, link TEXT, source TEXT, sentiment REAL
);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol, published_at);
CREATE TABLE IF NOT EXISTS predictions (
    symbol TEXT NOT NULL, run_date TEXT NOT NULL,
    close REAL, target_low REAL, target_mid REAL, target_high REAL,
    upside_pct REAL, confidence REAL, trend TEXT,
    tech_q10 REAL, tech_q50 REAL, tech_q90 REAL,
    valuation_fair REAL, sentiment REAL,
    PRIMARY KEY (symbol, run_date)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(path: Path | str = config.DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_store_schema.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_store_schema.py
git commit -m "feat: SQLite schema 11 張表（prices 含 adjclose）"
```

---

### Task 5: 儲存讀寫與增量判斷

**Files:**
- Modify: `src/store.py`
- Test: `tests/test_store_io.py`

**Interfaces:**
- Consumes: Task 4 的 `connect`、`init_schema`
- Produces:
  - `upsert(conn, table: str, rows: list[dict]) -> int`（回傳寫入筆數）
  - `get_last_date(conn, table: str, symbol: str, column: str = "date") -> str | None`
  - `set_meta(conn, key: str, value: str) -> None` / `get_meta(conn, key: str) -> str | None`
  - `read_prices(conn, symbol: str) -> pandas.DataFrame`（欄位 `date, open, high, low, close, adjclose, volume`，依 `date` 遞增）

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_store_io.py
import pandas as pd
import pytest

from src import store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def _price(symbol, date, close):
    return {
        "symbol": symbol, "date": date, "open": close, "high": close,
        "low": close, "close": close, "adjclose": close, "volume": 100,
    }


def test_upsert_inserts_and_replaces(conn):
    assert store.upsert(conn, "prices", [_price("2330.TW", "2026-08-28", 2380)]) == 1
    store.upsert(conn, "prices", [_price("2330.TW", "2026-08-28", 2400)])
    rows = conn.execute("SELECT close FROM prices").fetchall()
    assert len(rows) == 1 and rows[0]["close"] == 2400


def test_upsert_empty_list_is_noop(conn):
    assert store.upsert(conn, "prices", []) == 0


def test_get_last_date_returns_none_when_empty(conn):
    assert store.get_last_date(conn, "prices", "2330.TW") is None


def test_get_last_date_returns_max_for_symbol(conn):
    store.upsert(conn, "prices", [
        _price("2330.TW", "2026-08-26", 1), _price("2330.TW", "2026-08-28", 2),
        _price("AAPL", "2026-08-29", 3),
    ])
    assert store.get_last_date(conn, "prices", "2330.TW") == "2026-08-28"
    assert store.get_last_date(conn, "prices", "AAPL") == "2026-08-29"


def test_get_last_date_supports_alternate_column(conn):
    store.upsert(conn, "dividends", [
        {"symbol": "2330.TW", "ex_date": "2026-06-11", "type": "息",
         "cash_dividend": 6.0, "stock_dividend": 0.0, "pay_date": None},
    ])
    assert store.get_last_date(conn, "dividends", "2330.TW", column="ex_date") == "2026-06-11"


def test_meta_roundtrip(conn):
    assert store.get_meta(conn, "missing") is None
    store.set_meta(conn, "last_run", "2026-08-31")
    store.set_meta(conn, "last_run", "2026-09-01")
    assert store.get_meta(conn, "last_run") == "2026-09-01"


def test_read_prices_returns_sorted_dataframe(conn):
    store.upsert(conn, "prices", [
        _price("2330.TW", "2026-08-28", 2380), _price("2330.TW", "2026-08-26", 2350),
    ])
    df = store.read_prices(conn, "2330.TW")
    assert isinstance(df, pd.DataFrame)
    assert list(df["date"]) == ["2026-08-26", "2026-08-28"]
    assert list(df["adjclose"]) == [2350.0, 2380.0]


def test_upsert_rejects_unknown_table(conn):
    with pytest.raises(ValueError, match="未知的表"):
        store.upsert(conn, "drop_me", [{"a": 1}])
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_store_io.py -v`
Expected: FAIL — `AttributeError: module 'src.store' has no attribute 'upsert'`

- [ ] **Step 3: 實作讀寫**

在 `src/store.py` 末端追加：

```python
import pandas as pd


def upsert(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """以 INSERT OR REPLACE 寫入。table 需為 TABLES 白名單成員（避免 SQL 注入）。"""
    if table not in TABLES:
        raise ValueError(f"未知的表：{table}")
    if not rows:
        return 0
    columns = list(rows[0].keys())
    placeholders = ",".join("?" for _ in columns)
    sql = (
        f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    )
    conn.executemany(sql, [tuple(row[c] for c in columns) for row in rows])
    conn.commit()
    return len(rows)


def get_last_date(
    conn: sqlite3.Connection, table: str, symbol: str, column: str = "date"
) -> str | None:
    """增量更新用：回傳該 symbol 已有的最大日期。"""
    if table not in TABLES:
        raise ValueError(f"未知的表：{table}")
    row = conn.execute(
        f"SELECT MAX({column}) AS d FROM {table} WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["d"] if row and row["d"] else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES (?,?)", (key, value))
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def read_prices(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT date, open, high, low, close, adjclose, volume"
        " FROM prices WHERE symbol = ? ORDER BY date ASC",
        conn,
        params=(symbol,),
    )
```

`column` 參數同樣需防注入 —— 在 `get_last_date` 開頭加入：

```python
    if column not in {"date", "ex_date", "asof", "run_date", "published_at"}:
        raise ValueError(f"未知的日期欄位：{column}")
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_store_io.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/store.py tests/test_store_io.py
git commit -m "feat: store 讀寫、增量日期查詢與 meta"
```

---

### Task 6: TWSE 價量與估值

**Files:**
- Create: `src/fetch/twse.py`
- Create: `tests/fixtures/twse_stock_day_all.json`
- Create: `tests/fixtures/twse_bwibbu_all.json`
- Test: `tests/test_fetch_twse_quotes.py`

**Interfaces:**
- Consumes: `src.http.fetch_json`、`src.constants`
- Produces:
  - `fetch_day_all(session, limiter=None) -> list[dict]`，每筆 `{"code","name","close","trade_value","open","high","low","volume"}`
  - `fetch_valuation(session, limiter=None) -> list[dict]`，每筆 `{"code","pe","dividend_yield","pb"}`
  - `to_symbol(code: str) -> str`（`"2330"` → `"2330.TW"`）
  - 模組常數 `BASE = "https://openapi.twse.com.tw/v1"`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/twse_stock_day_all.json`（取自實測回應，保留原欄位名）：

```json
[
  {"Date":"1150828","Code":"2330","Name":"台積電","TradeVolume":"31234567","TradeValue":"74000000000","OpeningPrice":"2370.00","HighestPrice":"2395.00","LowestPrice":"2360.00","ClosingPrice":"2380.00","Change":"40.0000","Transaction":"52341"},
  {"Date":"1150828","Code":"1101","Name":"台泥","TradeVolume":"12000000","TradeValue":"400000000","OpeningPrice":"33.10","HighestPrice":"33.50","LowestPrice":"33.00","ClosingPrice":"33.40","Change":"0.3000","Transaction":"4210"},
  {"Date":"1150828","Code":"00400A","Name":"主動國泰動能高息","TradeVolume":"41461478","TradeValue":"621924604","OpeningPrice":"14.96","HighestPrice":"15.05","LowestPrice":"14.91","ClosingPrice":"15.00","Change":"0.1900","Transaction":"7417"}
]
```

`tests/fixtures/twse_bwibbu_all.json`：

```json
[
  {"Date":"1150828","Code":"2330","Name":"台積電","PEratio":"21.40","DividendYield":"1.51","PBratio":"5.80"},
  {"Date":"1150828","Code":"1101","Name":"台泥","PEratio":"","DividendYield":"3.29","PBratio":"0.79"}
]
```

```python
# tests/test_fetch_twse_quotes.py
import json
from pathlib import Path

import pytest

from src.fetch import twse

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def patched(monkeypatch):
    """把 fetch_json 換成依 URL 回 fixture 的假函式（不打真網路）。"""
    def fake(session, url, **kw):
        if "STOCK_DAY_ALL" in url:
            return _load("twse_stock_day_all.json")
        if "BWIBBU_ALL" in url:
            return _load("twse_bwibbu_all.json")
        raise AssertionError(f"未預期的 URL: {url}")

    monkeypatch.setattr(twse, "fetch_json", fake)


def test_to_symbol_appends_tw_suffix():
    assert twse.to_symbol("2330") == "2330.TW"


def test_fetch_day_all_parses_numbers(patched):
    rows = twse.fetch_day_all(session=None)
    tsmc = next(r for r in rows if r["code"] == "2330")
    assert tsmc["close"] == 2380.0
    assert tsmc["trade_value"] == 74_000_000_000.0
    assert tsmc["volume"] == 31_234_567
    assert tsmc["name"] == "台積電"


def test_fetch_valuation_blank_pe_becomes_none(patched):
    """台泥 PEratio 為空字串（虧損時 TWSE 就是給空字串），必須成為 None 而非 0。"""
    rows = {r["code"]: r for r in twse.fetch_valuation(session=None)}
    assert rows["2330"]["pe"] == 21.40
    assert rows["1101"]["pe"] is None
    assert rows["1101"]["dividend_yield"] == 3.29


def test_fetch_day_all_keeps_etf_codes(patched):
    """ETF（00400A）不在此層過濾，交由 universe 決定。"""
    codes = {r["code"] for r in twse.fetch_day_all(session=None)}
    assert "00400A" in codes
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_twse_quotes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fetch.twse'`

- [ ] **Step 3: 實作**

```python
# src/fetch/twse.py
"""TWSE（上市）資料擷取。

注意：TWSE 憑證缺 Subject Key Identifier，必須經 src.http（requests）取得，
不可使用 urllib.request（spec §9.3）。
"""
from __future__ import annotations

from src import constants as C
from src.http import fetch_json

BASE = "https://openapi.twse.com.tw/v1"


def to_symbol(code: str) -> str:
    return f"{code}.TW"


def _num(value, cast=float):
    """TWSE 以空字串表示無資料（例如虧損公司的 PE），須轉為 None 而非 0。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "--"}:
        return None
    try:
        return cast(text)
    except ValueError:
        return None


def fetch_day_all(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/STOCK_DAY_ALL", limiter=limiter)
    return [
        {
            "code": row[C.TWSE_DAY_CODE],
            "name": row["Name"],
            "open": _num(row["OpeningPrice"]),
            "high": _num(row["HighestPrice"]),
            "low": _num(row["LowestPrice"]),
            "close": _num(row[C.TWSE_DAY_CLOSE]),
            "volume": _num(row["TradeVolume"], int),
            "trade_value": _num(row[C.TWSE_DAY_VALUE]),
        }
        for row in raw
    ]


def fetch_valuation(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/BWIBBU_ALL", limiter=limiter)
    return [
        {
            "code": row[C.TWSE_DAY_CODE],
            "pe": _num(row[C.TWSE_BWIBBU_PE]),
            "dividend_yield": _num(row[C.TWSE_BWIBBU_YIELD]),
            "pb": _num(row[C.TWSE_BWIBBU_PB]),
        }
        for row in raw
    ]
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_twse_quotes.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/twse.py tests/fixtures/ tests/test_fetch_twse_quotes.py
git commit -m "feat: TWSE 全市場價量與估值擷取（空字串轉 None）"
```

---

### Task 7: TWSE 月營收（公布日對齊）、除權息、融資融券

**Files:**
- Modify: `src/fetch/twse.py`
- Create: `tests/fixtures/twse_revenue.json`, `tests/fixtures/twse_exright.json`, `tests/fixtures/twse_margin.json`
- Test: `tests/test_fetch_twse_fundamentals.py`

**Interfaces:**
- Consumes: Task 6 的 `_num`、`BASE`
- Produces:
  - `fetch_monthly_revenue(session, limiter=None) -> list[dict]`，每筆 `{"code","publish_date","period","revenue","yoy","mom","industry"}`，`publish_date` 為 ISO `YYYY-MM-DD`
  - `fetch_exright(session, limiter=None) -> list[dict]`，每筆 `{"code","ex_date","type","cash_dividend","stock_dividend"}`
  - `fetch_margin(session, limiter=None) -> list[dict]`，每筆 `{"code","margin_balance","margin_balance_prev","short_balance","short_balance_prev"}`
  - `roc_to_iso(value: str) -> str`（`"1150817"` → `"2026-08-17"`）

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/twse_revenue.json`：

```json
[
  {"出表日期":"1150817","資料年月":"11507","公司代號":"1101","公司名稱":"台泥","產業別":"水泥工業","營業收入-當月營收":"13744103","營業收入-上月營收":"13382706","營業收入-去年當月營收":"13535929","營業收入-上月比較增減(%)":"2.70047776585692","營業收入-去年同月增減(%)":"1.5379365538929763"},
  {"出表日期":"1150810","資料年月":"11507","公司代號":"2330","公司名稱":"台積電","產業別":"半導體業","營業收入-當月營收":"300000000","營業收入-上月營收":"280000000","營業收入-去年當月營收":"217000000","營業收入-上月比較增減(%)":"7.14","營業收入-去年同月增減(%)":"38.25"}
]
```

`tests/fixtures/twse_exright.json`：

```json
[
  {"Date":"1150911","Code":"2330","Name":"台積電","Exdividend":"息","StockDividendRatio":"","SubscriptionRatio":"","SubscriptionPricePerShare":"","CashDividend":"6.0","SharesOffered":"","SharesEmpOwner":"","SharesholderOwner":"","StockHoldingRatio":""},
  {"Date":"1150907","Code":"00400A","Name":"主動國泰動能高息","Exdividend":"息","StockDividendRatio":"","SubscriptionRatio":"","SubscriptionPricePerShare":"","CashDividend":"","SharesOffered":"","SharesEmpOwner":"","SharesholderOwner":"","StockHoldingRatio":""}
]
```

`tests/fixtures/twse_margin.json`：

```json
[
  {"股票代號":"2330","股票名稱":"台積電","融資買進":"1200","融資賣出":"900","融資現金償還":"50","融資前日餘額":"45000","融資今日餘額":"45250","融資限額":"999999","融券買進":"30","融券賣出":"45","融券現券償還":"5","融券前日餘額":"1200","融券今日餘額":"1210","融券限額":"999999","資券互抵":"12","註記":" "}
]
```

```python
# tests/test_fetch_twse_fundamentals.py
import json
from pathlib import Path

import pytest

from src.fetch import twse

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def patched(monkeypatch):
    def fake(session, url, **kw):
        if "t187ap05_L" in url:
            return _load("twse_revenue.json")
        if "TWT48U_ALL" in url:
            return _load("twse_exright.json")
        if "MI_MARGN" in url:
            return _load("twse_margin.json")
        raise AssertionError(f"未預期的 URL: {url}")

    monkeypatch.setattr(twse, "fetch_json", fake)


def test_roc_to_iso_converts_roc_year():
    assert twse.roc_to_iso("1150817") == "2026-08-17"
    assert twse.roc_to_iso("1150911") == "2026-09-11"


def test_roc_to_iso_returns_none_for_blank():
    assert twse.roc_to_iso("") is None


def test_monthly_revenue_uses_publish_date_not_period(patched):
    """spec §6.1 R3：必須以公布日對齊，7 月營收是 8 月才公布。"""
    rows = {r["code"]: r for r in twse.fetch_monthly_revenue(session=None)}
    tsmc = rows["2330"]
    assert tsmc["period"] == "2026-07"       # 所屬期間
    assert tsmc["publish_date"] == "2026-08-10"  # 公布日，晚於所屬期間
    assert tsmc["publish_date"] > tsmc["period"]
    assert tsmc["yoy"] == 38.25
    assert tsmc["industry"] == "半導體業"


def test_exright_parses_ex_date_and_cash(patched):
    rows = {r["code"]: r for r in twse.fetch_exright(session=None)}
    assert rows["2330"]["ex_date"] == "2026-09-11"
    assert rows["2330"]["type"] == "息"
    assert rows["2330"]["cash_dividend"] == 6.0
    assert rows["00400A"]["cash_dividend"] is None  # 空字串不得變成 0


def test_margin_parses_balances(patched):
    row = twse.fetch_margin(session=None)[0]
    assert row["code"] == "2330"
    assert row["margin_balance"] == 45250
    assert row["margin_balance_prev"] == 45000
    assert row["short_balance"] == 1210
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_twse_fundamentals.py -v`
Expected: FAIL — `AttributeError: module 'src.fetch.twse' has no attribute 'roc_to_iso'`

- [ ] **Step 3: 實作**

在 `src/fetch/twse.py` 追加：

```python
def roc_to_iso(value: str | None) -> str | None:
    """民國日期字串 → ISO。`"1150817"` → `"2026-08-17"`。"""
    text = (value or "").strip()
    if len(text) != 7 or not text.isdigit():
        return None
    year = int(text[:3]) + 1911
    return f"{year:04d}-{text[3:5]}-{text[5:7]}"


def _roc_ym_to_iso(value: str | None) -> str | None:
    """民國年月 → `YYYY-MM`。`"11507"` → `"2026-07"`。"""
    text = (value or "").strip()
    if len(text) != 5 or not text.isdigit():
        return None
    return f"{int(text[:3]) + 1911:04d}-{text[3:5]}"


def fetch_monthly_revenue(session, limiter=None) -> list[dict]:
    """月營收。

    publish_date 取自「出表日期」，即實際公布日。特徵工程必須以此對齊，
    不可用「資料年月」，否則會用到當時尚未公布的資訊（spec §6.1 R3）。
    """
    raw = fetch_json(session, f"{BASE}/opendata/t187ap05_L", limiter=limiter)
    return [
        {
            "code": row[C.REV_CODE],
            "publish_date": roc_to_iso(row[C.REV_PUBLISH_DATE]),
            "period": _roc_ym_to_iso(row[C.REV_YM]),
            "industry": row.get(C.REV_INDUSTRY),
            "revenue": _num(row[C.REV_MONTH_REVENUE]),
            "yoy": _num(row[C.REV_YOY]),
            "mom": _num(row[C.REV_MOM]),
        }
        for row in raw
    ]


def fetch_exright(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/TWT48U_ALL", limiter=limiter)
    return [
        {
            "code": row[C.TWSE_DAY_CODE],
            "ex_date": roc_to_iso(row[C.TWSE_EXRIGHT_DATE]),
            "type": (row.get(C.TWSE_EXRIGHT_TYPE) or "").strip() or None,
            "cash_dividend": _num(row.get(C.TWSE_EXRIGHT_CASH)),
            "stock_dividend": _num(row.get(C.TWSE_EXRIGHT_STOCK)),
        }
        for row in raw
    ]


def fetch_margin(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/exchangeReport/MI_MARGN", limiter=limiter)
    return [
        {
            "code": row[C.MARGIN_CODE],
            "margin_balance": _num(row[C.MARGIN_BALANCE_TODAY], int),
            "margin_balance_prev": _num(row[C.MARGIN_BALANCE_PREV], int),
            "short_balance": _num(row[C.SHORT_BALANCE_TODAY], int),
            "short_balance_prev": _num(row[C.SHORT_BALANCE_PREV], int),
        }
        for row in raw
    ]
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_twse_fundamentals.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/twse.py tests/fixtures/ tests/test_fetch_twse_fundamentals.py
git commit -m "feat: TWSE 月營收（公布日對齊）、除權息、融資融券"
```

---

### Task 8: TPEx 上櫃（含欄位名正規化）

**Files:**
- Create: `src/fetch/tpex.py`
- Create: `tests/fixtures/tpex_quotes.json`, `tests/fixtures/tpex_3insti.json`
- Test: `tests/test_fetch_tpex.py`

**Interfaces:**
- Consumes: `src.http.fetch_json`
- Produces:
  - `to_symbol(code: str) -> str`（`"6488"` → `"6488.TWO"`）
  - `normalize_key(key: str) -> str`（去除所有空白並轉小寫）
  - `fetch_quotes(session, limiter=None) -> list[dict]`
  - `fetch_institutional(session, limiter=None) -> list[dict]`，每筆 `{"code","foreign_net","trust_net","dealer_net"}`
  - 模組常數 `BASE = "https://www.tpex.org.tw/openapi/v1"`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/tpex_quotes.json`：

```json
[
  {"Date":"1150828","SecuritiesCompanyCode":"6488","CompanyName":"環球晶","Close":"620.00","Change":"+5.00","Open":"615.00","High":"625.00","Low":"612.00","TradingShares":"3200000","TransactionAmount":"1980000000","TransactionNumber":"5120","LatestBidPrice":"619.00","LatesAskPrice":"620.00","Capitals":"4350000","NextLimitUp":"682.00","NextLimitDown":"558.00"}
]
```

`tests/fixtures/tpex_3insti.json` —— **刻意保留實測到的不一致空格**：

```json
[
  {"Date":"1150828","SecuritiesCompanyCode":"6488","CompanyName":"環球晶",
   "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Buy":"410000",
   " Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell":"239000",
   "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference":"171000",
   "SecuritiesInvestmentTrustCompanies-Difference":"52000",
   "Dealers-Difference":"-8000",
   "Dealers -TotalSell":"3000",
   "TotalDifference":"215000"}
]
```

```python
# tests/test_fetch_tpex.py
import json
from pathlib import Path

import pytest

from src.fetch import tpex

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def patched(monkeypatch):
    def fake(session, url, **kw):
        if "daily_close_quotes" in url:
            return _load("tpex_quotes.json")
        if "3insti" in url:
            return _load("tpex_3insti.json")
        raise AssertionError(f"未預期的 URL: {url}")

    monkeypatch.setattr(tpex, "fetch_json", fake)


def test_to_symbol_uses_two_suffix():
    assert tpex.to_symbol("6488") == "6488.TWO"


def test_normalize_key_strips_all_whitespace():
    """spec §9.3：TPEx 欄位名含不一致空格。"""
    a = tpex.normalize_key(" Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell")
    b = tpex.normalize_key("Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Total Sell")
    assert a == b
    assert tpex.normalize_key("Dealers -TotalSell") == tpex.normalize_key("Dealers-TotalSell")


def test_fetch_quotes_parses(patched):
    row = tpex.fetch_quotes(session=None)[0]
    assert row["code"] == "6488"
    assert row["close"] == 620.0
    assert row["trade_value"] == 1_980_000_000.0


def test_fetch_institutional_survives_inconsistent_spacing(patched):
    """欄位名帶前導/中間空格時仍須正確取值，不得 KeyError。"""
    row = tpex.fetch_institutional(session=None)[0]
    assert row["code"] == "6488"
    assert row["foreign_net"] == 171000
    assert row["trust_net"] == 52000
    assert row["dealer_net"] == -8000
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_tpex.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fetch.tpex'`

- [ ] **Step 3: 實作**

```python
# src/fetch/tpex.py
"""TPEx（上櫃）資料擷取。

TPEx 三大法人端點的欄位名含不一致空格（前導空格、中間空格），
直接以字面 key 取值會 KeyError，故一律先正規化欄位名（spec §9.3）。
"""
from __future__ import annotations

import re

from src.fetch.twse import _num
from src.http import fetch_json

BASE = "https://www.tpex.org.tw/openapi/v1"

_WS = re.compile(r"\s+")


def to_symbol(code: str) -> str:
    return f"{code}.TWO"


def normalize_key(key: str) -> str:
    """去除所有空白並轉小寫，讓帶空格的變體對應到同一個鍵。"""
    return _WS.sub("", key).lower()


def _normalized(row: dict) -> dict:
    return {normalize_key(k): v for k, v in row.items()}


def fetch_quotes(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/tpex_mainboard_daily_close_quotes", limiter=limiter)
    out = []
    for row in raw:
        r = _normalized(row)
        out.append(
            {
                "code": r["securitiescompanycode"],
                "name": r.get("companyname"),
                "open": _num(r.get("open")),
                "high": _num(r.get("high")),
                "low": _num(r.get("low")),
                "close": _num(r.get("close")),
                "volume": _num(r.get("tradingshares"), int),
                "trade_value": _num(r.get("transactionamount")),
            }
        )
    return out


_FOREIGN_DIFF = normalize_key(
    "Foreign Investors include Mainland Area Investors (Foreign Dealers excluded)-Difference"
)
_TRUST_DIFF = normalize_key("SecuritiesInvestmentTrustCompanies-Difference")
_DEALER_DIFF = normalize_key("Dealers-Difference")


def fetch_institutional(session, limiter=None) -> list[dict]:
    raw = fetch_json(session, f"{BASE}/tpex_3insti_daily_trading", limiter=limiter)
    out = []
    for row in raw:
        r = _normalized(row)
        out.append(
            {
                "code": r["securitiescompanycode"],
                "foreign_net": _num(r.get(_FOREIGN_DIFF), int),
                "trust_net": _num(r.get(_TRUST_DIFF), int),
                "dealer_net": _num(r.get(_DEALER_DIFF), int),
            }
        )
    return out
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_tpex.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/tpex.py tests/fixtures/ tests/test_fetch_tpex.py
git commit -m "feat: TPEx 上櫃擷取，欄位名正規化解決空格不一致"
```

---

### Task 9: Yahoo 日線歷史（adjclose）

**Files:**
- Create: `src/fetch/yahoo.py`
- Create: `tests/fixtures/yahoo_chart_2330.json`
- Test: `tests/test_fetch_yahoo_chart.py`

**Interfaces:**
- Consumes: `src.http.fetch_json`、`src.http.YahooClient`
- Produces:
  - `CHART_URL: str`
  - `fetch_chart(session, symbol: str, *, range_: str = "3y", interval: str = "1d", limiter=None) -> list[dict]`，每筆 `{"symbol","date","open","high","low","close","adjclose","volume"}`
  - `fetch_dividend_events(session, symbol: str, limiter=None) -> list[dict]`，每筆 `{"symbol","ex_date","cash_dividend"}`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/yahoo_chart_2330.json` —— 用實測驗證過的除息數字（2026-06-11 配息 6.0，前一日 close 2255 / adjclose 2249）。時間戳為台北時間當日 13:30 對應的 epoch：

```json
{"chart":{"result":[{"meta":{"currency":"TWD","symbol":"2330.TW","longName":"Taiwan Semiconductor Manufacturing Company Limited","regularMarketPrice":2380.0,"fiftyTwoWeekHigh":2535.0,"fiftyTwoWeekLow":1145.0},
"timestamp":[1781053200,1781139600,1781226000],
"events":{"dividends":{"1781139600":{"amount":6.000036,"date":1781139600}}},
"indicators":{"quote":[{"open":[2260.0,2245.0,2255.0],"high":[2270.0,2265.0,2315.0],"low":[2250.0,2240.0,2250.0],"close":[2255.0,2250.0,2310.0],"volume":[28000000,31000000,35000000]}],
"adjclose":[{"adjclose":[2249.0,2250.0,2310.0]}]}}],"error":null}}
```

```python
# tests/test_fetch_yahoo_chart.py
import json
from pathlib import Path

import pytest

from src.fetch import yahoo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def patched(monkeypatch):
    payload = json.loads((FIXTURES / "yahoo_chart_2330.json").read_text(encoding="utf-8"))
    captured = {}

    def fake(session, url, params=None, **kw):
        captured["url"] = url
        captured["params"] = params or {}
        return payload

    monkeypatch.setattr(yahoo, "fetch_json", fake)
    return captured


def test_chart_request_includes_events_param(patched):
    """spec §2.3：不帶 events 就不會回傳 adjclose。"""
    yahoo.fetch_chart(session=None, symbol="2330.TW")
    assert patched["params"].get("events") == "div,split"
    assert patched["params"].get("interval") == "1d"


def test_chart_rows_include_adjclose_distinct_from_close(patched):
    rows = yahoo.fetch_chart(session=None, symbol="2330.TW")
    assert len(rows) == 3
    first = rows[0]
    assert first["close"] == 2255.0
    assert first["adjclose"] == 2249.0  # 除息還原後低 6 元
    assert first["symbol"] == "2330.TW"


def test_chart_dates_are_iso(patched):
    rows = yahoo.fetch_chart(session=None, symbol="2330.TW")
    assert all(len(r["date"]) == 10 and r["date"][4] == "-" for r in rows)
    assert rows[0]["date"] < rows[-1]["date"]


def test_dividend_events_extracted(patched):
    events = yahoo.fetch_dividend_events(session=None, symbol="2330.TW")
    assert len(events) == 1
    assert round(events[0]["cash_dividend"], 2) == 6.0


def test_missing_bars_are_skipped(monkeypatch):
    """Yahoo 對停牌日會回傳 null，該列必須跳過而非寫入 None 價格。"""
    payload = {"chart": {"result": [{"meta": {"symbol": "X"}, "timestamp": [1781053200, 1781139600],
        "indicators": {"quote": [{"open": [1.0, None], "high": [1.0, None], "low": [1.0, None],
        "close": [1.0, None], "volume": [10, None]}], "adjclose": [{"adjclose": [1.0, None]}]}}]}}
    monkeypatch.setattr(yahoo, "fetch_json", lambda *a, **k: payload)
    rows = yahoo.fetch_chart(session=None, symbol="X")
    assert len(rows) == 1
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_yahoo_chart.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fetch.yahoo'`

- [ ] **Step 3: 實作**

```python
# src/fetch/yahoo.py
"""Yahoo Finance：日線歷史（台美股共用）與美股基本面。"""
from __future__ import annotations

from datetime import datetime, timezone

from src.http import fetch_json

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def _epoch_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _chart_payload(session, symbol: str, range_: str, interval: str, limiter):
    payload = fetch_json(
        session,
        CHART_URL.format(symbol=symbol),
        params={"range": range_, "interval": interval, "events": "div,split"},
        limiter=limiter,
    )
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        raise RuntimeError(f"{symbol} chart 無結果")
    return results[0]


def fetch_chart(
    session, symbol: str, *, range_: str = "3y", interval: str = "1d", limiter=None
) -> list[dict]:
    """日線。必須帶 events=div,split 才會有 adjclose（spec §2.3）。"""
    result = _chart_payload(session, symbol, range_, interval, limiter)
    timestamps = result.get("timestamp") or []
    quote = (result["indicators"]["quote"] or [{}])[0]
    adj_block = (result["indicators"].get("adjclose") or [{}])[0]
    adjclose = adj_block.get("adjclose") or [None] * len(timestamps)

    rows = []
    for i, ts in enumerate(timestamps):
        close = quote["close"][i]
        adj = adjclose[i]
        if close is None or adj is None:
            continue  # 停牌日 Yahoo 回 null
        rows.append(
            {
                "symbol": symbol,
                "date": _epoch_to_iso(ts),
                "open": quote["open"][i],
                "high": quote["high"][i],
                "low": quote["low"][i],
                "close": close,
                "adjclose": adj,
                "volume": quote["volume"][i],
            }
        )
    return rows


def fetch_dividend_events(session, symbol: str, limiter=None) -> list[dict]:
    result = _chart_payload(session, symbol, "3y", "1d", limiter)
    dividends = (result.get("events") or {}).get("dividends") or {}
    return [
        {
            "symbol": symbol,
            "ex_date": _epoch_to_iso(item["date"]),
            "cash_dividend": item["amount"],
        }
        for item in sorted(dividends.values(), key=lambda d: d["date"])
    ]
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_yahoo_chart.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/yahoo.py tests/fixtures/ tests/test_fetch_yahoo_chart.py
git commit -m "feat: Yahoo 日線擷取（強制 events 參數以取得 adjclose）"
```

---

### Task 10: Yahoo 美股基本面與分析師目標價

**Files:**
- Modify: `src/fetch/yahoo.py`
- Create: `tests/fixtures/yahoo_summary_aapl.json`
- Test: `tests/test_fetch_yahoo_summary.py`

**Interfaces:**
- Consumes: Task 3 的 `YahooClient`
- Produces:
  - `SUMMARY_MODULES: list[str]`
  - `fetch_us_fundamentals(client, symbol: str) -> dict`，鍵含 `pe, forward_pe, pb, dividend_yield, market_cap, revenue_yoy, gross_margin, op_margin, net_margin, roe, roa, debt_to_equity, current_ratio, free_cashflow, eps_forward, sector, industry, ex_dividend_date, earnings_date, short_percent_float, held_percent_institutions, beta`
  - `fetch_us_analyst(client, symbol: str) -> dict`，鍵 `target_mean, target_high, target_low, recommendation, n_analysts`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/yahoo_summary_aapl.json`（結構取自實測回應，數值精簡）：

```json
{"summaryDetail":{"trailingPE":{"raw":36.62},"forwardPE":{"raw":33.52},"dividendYield":{"raw":0.0034},"exDividendDate":{"raw":1786320000,"fmt":"2026-08-10"},"payoutRatio":{"raw":0.1204},"beta":{"raw":1.086},"marketCap":{"raw":4665759498240}},
"defaultKeyStatistics":{"priceToBook":{"raw":43.4375},"shortPercentOfFloat":{"raw":0.008},"heldPercentInstitutions":{"raw":0.66398},"earningsQuarterlyGrowth":{"raw":0.271}},
"financialData":{"currentPrice":{"raw":319.7},"targetHighPrice":{"raw":400.0},"targetLowPrice":{"raw":215.0},"targetMeanPrice":{"raw":324.45},"recommendationKey":"buy","numberOfAnalystOpinions":{"raw":39},"revenueGrowth":{"raw":0.164},"grossMargins":{"raw":0.468},"operatingMargins":{"raw":0.321},"profitMargins":{"raw":0.27618},"returnOnEquity":{"raw":1.48751},"returnOnAssets":{"raw":0.245},"debtToEquity":{"raw":78.445},"currentRatio":{"raw":0.92},"freeCashflow":{"raw":98000000000}},
"summaryProfile":{"sector":"Technology","industry":"Consumer Electronics"},
"calendarEvents":{"earnings":{"earningsDate":[{"raw":1793304000,"fmt":"2026-10-29"}]},"exDividendDate":{"raw":1786320000}},
"earningsTrend":{"trend":[{"period":"0q","earningsEstimate":{"avg":{"raw":1.97656}}},{"period":"+1y","earningsEstimate":{"avg":{"raw":9.42}}}]}}
```

```python
# tests/test_fetch_yahoo_summary.py
import json
from pathlib import Path

import pytest

from src.fetch import yahoo

FIXTURES = Path(__file__).parent / "fixtures"


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.modules_seen = None

    def quote_summary(self, symbol, modules):
        self.modules_seen = modules
        return self.payload


@pytest.fixture()
def client():
    return _Client(json.loads((FIXTURES / "yahoo_summary_aapl.json").read_text(encoding="utf-8")))


def test_requests_all_required_modules(client):
    yahoo.fetch_us_fundamentals(client, "AAPL")
    assert set(client.modules_seen) >= {
        "summaryDetail", "defaultKeyStatistics", "financialData",
        "summaryProfile", "calendarEvents", "earningsTrend",
    }


def test_fundamentals_unwrap_raw_values(client):
    out = yahoo.fetch_us_fundamentals(client, "AAPL")
    assert out["pe"] == 36.62
    assert out["forward_pe"] == 33.52
    assert out["pb"] == 43.4375
    assert out["net_margin"] == 0.27618
    assert out["sector"] == "Technology"
    assert out["industry"] == "Consumer Electronics"


def test_forward_eps_taken_from_plus_one_year(client):
    """spec §6.2：前瞻 EPS 取 earningsTrend 的 +1y。"""
    assert yahoo.fetch_us_fundamentals(client, "AAPL")["eps_forward"] == 9.42


def test_event_dates_converted_to_iso(client):
    out = yahoo.fetch_us_fundamentals(client, "AAPL")
    assert out["ex_dividend_date"] == "2026-08-10"
    assert out["earnings_date"] == "2026-10-29"


def test_analyst_targets(client):
    out = yahoo.fetch_us_analyst(client, "AAPL")
    assert out["target_mean"] == 324.45
    assert out["target_high"] == 400.0
    assert out["n_analysts"] == 39
    assert out["recommendation"] == "buy"


def test_missing_fields_become_none():
    out = yahoo.fetch_us_fundamentals(_Client({"summaryDetail": {}}), "X")
    assert out["pe"] is None
    assert out["sector"] is None
    assert out["eps_forward"] is None
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_yahoo_summary.py -v`
Expected: FAIL — `AttributeError: module 'src.fetch.yahoo' has no attribute 'fetch_us_fundamentals'`

- [ ] **Step 3: 實作**

在 `src/fetch/yahoo.py` 追加：

```python
SUMMARY_MODULES = [
    "summaryDetail",
    "defaultKeyStatistics",
    "financialData",
    "summaryProfile",
    "calendarEvents",
    "earningsTrend",
]


def _raw(block: dict | None, key: str):
    """Yahoo 以 {"raw":..., "fmt":...} 包裝數值，取 raw；純量則原樣回傳。"""
    if not block:
        return None
    value = block.get(key)
    if isinstance(value, dict):
        return value.get("raw")
    return value


def _iso_from_epoch_field(block: dict | None, key: str) -> str | None:
    value = _raw(block, key)
    return _epoch_to_iso(value) if isinstance(value, (int, float)) else None


def _forward_eps(summary: dict) -> float | None:
    for item in (summary.get("earningsTrend") or {}).get("trend") or []:
        if item.get("period") == "+1y":
            return _raw(item.get("earningsEstimate"), "avg")
    return None


def _earnings_date(summary: dict) -> str | None:
    dates = ((summary.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
    if not dates:
        return None
    first = dates[0]
    raw = first.get("raw") if isinstance(first, dict) else first
    return _epoch_to_iso(raw) if isinstance(raw, (int, float)) else None


def fetch_us_fundamentals(client, symbol: str) -> dict:
    summary = client.quote_summary(symbol, SUMMARY_MODULES)
    detail = summary.get("summaryDetail") or {}
    stats = summary.get("defaultKeyStatistics") or {}
    fin = summary.get("financialData") or {}
    profile = summary.get("summaryProfile") or {}
    return {
        "symbol": symbol,
        "pe": _raw(detail, "trailingPE"),
        "forward_pe": _raw(detail, "forwardPE"),
        "pb": _raw(stats, "priceToBook"),
        "dividend_yield": _raw(detail, "dividendYield"),
        "market_cap": _raw(detail, "marketCap"),
        "beta": _raw(detail, "beta"),
        "revenue_yoy": _raw(fin, "revenueGrowth"),
        "gross_margin": _raw(fin, "grossMargins"),
        "op_margin": _raw(fin, "operatingMargins"),
        "net_margin": _raw(fin, "profitMargins"),
        "roe": _raw(fin, "returnOnEquity"),
        "roa": _raw(fin, "returnOnAssets"),
        "debt_to_equity": _raw(fin, "debtToEquity"),
        "current_ratio": _raw(fin, "currentRatio"),
        "free_cashflow": _raw(fin, "freeCashflow"),
        "eps_forward": _forward_eps(summary),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "ex_dividend_date": _iso_from_epoch_field(detail, "exDividendDate"),
        "earnings_date": _earnings_date(summary),
        "short_percent_float": _raw(stats, "shortPercentOfFloat"),
        "held_percent_institutions": _raw(stats, "heldPercentInstitutions"),
    }


def fetch_us_analyst(client, symbol: str) -> dict:
    fin = client.quote_summary(symbol, ["financialData"]).get("financialData") or {}
    return {
        "symbol": symbol,
        "target_mean": _raw(fin, "targetMeanPrice"),
        "target_high": _raw(fin, "targetHighPrice"),
        "target_low": _raw(fin, "targetLowPrice"),
        "recommendation": fin.get("recommendationKey"),
        "n_analysts": _raw(fin, "numberOfAnalystOpinions"),
    }
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_yahoo_summary.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/yahoo.py tests/fixtures/ tests/test_fetch_yahoo_summary.py
git commit -m "feat: Yahoo 美股基本面、前瞻 EPS 與分析師目標價"
```

---

### Task 11: TDCC 大戶持股（CSV + BOM）

**Files:**
- Create: `src/fetch/tdcc.py`
- Create: `tests/fixtures/tdcc_sample.csv`
- Test: `tests/test_fetch_tdcc.py`

**Interfaces:**
- Consumes: `src.http.fetch_text`
- Produces: `URL: str`、`parse_big_holders(csv_text: str) -> list[dict]`（每筆 `{"code","date","big_holder_pct"}`）、`fetch_big_holders(session, limiter=None) -> list[dict]`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/tdcc_sample.csv` —— **檔案開頭必須含 UTF-8 BOM**（用支援 BOM 的方式寫入，或以 Python 產生：`Path(...).write_text(text, encoding="utf-8-sig")`）：

```csv
資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%
20260828,2330,1,120000,50000000,0.19
20260828,2330,14,35,900000000,3.47
20260828,2330,15,28,18500000000,71.34
20260828,1101,15,12,3000000000,42.10
```

```python
# tests/test_fetch_tdcc.py
from pathlib import Path

import pytest

from src.fetch import tdcc

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def csv_text():
    return (FIXTURES / "tdcc_sample.csv").read_text(encoding="utf-8-sig")


def test_fixture_actually_has_bom():
    """spec §9.3：TDCC 回傳帶 BOM，測資必須忠實重現。"""
    assert (FIXTURES / "tdcc_sample.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_parse_keeps_only_level_15(csv_text):
    """持股分級 15 = 400 張以上，即大戶。"""
    rows = {r["code"]: r for r in tdcc.parse_big_holders(csv_text)}
    assert set(rows) == {"2330", "1101"}
    assert rows["2330"]["big_holder_pct"] == 71.34
    assert rows["1101"]["big_holder_pct"] == 42.10


def test_parse_converts_date(csv_text):
    assert tdcc.parse_big_holders(csv_text)[0]["date"] == "2026-08-28"


def test_fetch_requests_utf8_sig(monkeypatch, csv_text):
    seen = {}

    def fake(session, url, **kw):
        seen.update(kw)
        return csv_text

    monkeypatch.setattr(tdcc, "fetch_text", fake)
    rows = tdcc.fetch_big_holders(session=None)
    assert seen.get("encoding") == "utf-8-sig"
    assert len(rows) == 2
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_tdcc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fetch.tdcc'`

- [ ] **Step 3: 實作**

```python
# src/fetch/tdcc.py
"""集保戶股權分散表（大戶持股）。

回傳為 CSV 而非 JSON，且帶 UTF-8 BOM，必須以 utf-8-sig 解碼（spec §9.3）。
持股分級 15 代表 400 張以上，即一般所稱「大戶」。資料為週更。
"""
from __future__ import annotations

import csv
import io

from src import constants as C
from src.http import fetch_text

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"


def _iso(compact: str) -> str | None:
    text = (compact or "").strip()
    if len(text) != 8 or not text.isdigit():
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def parse_big_holders(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    out = []
    for row in reader:
        if (row.get(C.TDCC_LEVEL) or "").strip() != C.TDCC_BIG_HOLDER_LEVEL:
            continue
        try:
            pct = float(row[C.TDCC_PCT])
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            {
                "code": (row[C.TDCC_CODE] or "").strip(),
                "date": _iso(row[C.TDCC_DATE]),
                "big_holder_pct": pct,
            }
        )
    return out


def fetch_big_holders(session, limiter=None) -> list[dict]:
    text = fetch_text(session, URL, limiter=limiter, encoding="utf-8-sig", timeout=120)
    return parse_big_holders(text)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_tdcc.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/tdcc.py tests/fixtures/tdcc_sample.csv tests/test_fetch_tdcc.py
git commit -m "feat: TDCC 大戶持股擷取（CSV + UTF-8 BOM）"
```

---

### Task 12: Google News RSS 與去重

**Files:**
- Create: `src/fetch/news.py`
- Create: `tests/fixtures/google_news.xml`
- Test: `tests/test_fetch_news.py`

**Interfaces:**
- Consumes: `src.http.fetch_text`、`lxml`
- Produces:
  - `build_query_url(name: str, market: str) -> str`
  - `parse_rss(xml_text: str, symbol: str) -> list[dict]`（每筆 `{"link_hash","symbol","published_at","title","link","source","sentiment"}`，`sentiment` 先填 `None`）
  - `fetch_news(session, symbol: str, name: str, market: str, limiter=None) -> list[dict]`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/google_news.xml`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>台積電 - Google News</title>
<item>
  <title>法人上調台積電目標價至 2800 元</title>
  <link>https://news.example.com/a1</link>
  <pubDate>Sun, 30 Aug 2026 09:12:00 GMT</pubDate>
  <source url="https://news.example.com">經濟日報</source>
</item>
<item>
  <title>先進封裝產能滿載至明年</title>
  <link>https://news.example.com/a2</link>
  <pubDate>Sat, 29 Aug 2026 01:00:00 GMT</pubDate>
  <source url="https://news.example.com">工商時報</source>
</item>
</channel></rss>
```

```python
# tests/test_fetch_news.py
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from src.fetch import news

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def xml_text():
    return (FIXTURES / "google_news.xml").read_text(encoding="utf-8")


def test_build_query_url_uses_traditional_chinese_for_tw():
    url = news.build_query_url("台積電", "TW")
    qs = parse_qs(urlparse(url).query)
    assert qs["hl"] == ["zh-TW"]
    assert qs["ceid"] == ["TW:zh-Hant"]
    assert qs["q"] == ["台積電"]


def test_build_query_url_uses_english_for_us():
    qs = parse_qs(urlparse(news.build_query_url("Apple Inc", "US")).query)
    assert qs["hl"] == ["en-US"]
    assert qs["ceid"] == ["US:en"]


def test_parse_rss_extracts_items(xml_text):
    items = news.parse_rss(xml_text, "2330.TW")
    assert len(items) == 2
    first = items[0]
    assert first["title"] == "法人上調台積電目標價至 2800 元"
    assert first["source"] == "經濟日報"
    assert first["symbol"] == "2330.TW"
    assert first["published_at"] == "2026-08-30T09:12:00+00:00"
    assert first["sentiment"] is None


def test_link_hash_is_stable_and_unique(xml_text):
    items = news.parse_rss(xml_text, "2330.TW")
    again = news.parse_rss(xml_text, "2330.TW")
    assert items[0]["link_hash"] == again[0]["link_hash"]
    assert items[0]["link_hash"] != items[1]["link_hash"]
    assert len(items[0]["link_hash"]) == 40  # SHA1 hex


def test_malformed_xml_returns_empty_list():
    assert news.parse_rss("<not-xml", "X") == []
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_news.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fetch.news'`

- [ ] **Step 3: 實作**

```python
# src/fetch/news.py
"""Google News RSS 擷取。

去重以 link 的 SHA1 為主鍵（spec §4），因此同一則新聞重複抓取不會產生重複列。
情緒分數在此階段留空，由第二階段的 models/sentiment.py 回填。
"""
from __future__ import annotations

import hashlib
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode

from lxml import etree

from src.http import fetch_text

BASE = "https://news.google.com/rss/search"

_LOCALE = {
    "TW": {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"},
    "US": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}


def build_query_url(name: str, market: str) -> str:
    locale = _LOCALE.get(market.upper(), _LOCALE["TW"])
    return f"{BASE}?{urlencode({'q': name, **locale})}"


def _iso(pub_date: str | None) -> str | None:
    if not pub_date:
        return None
    try:
        return parsedate_to_datetime(pub_date).isoformat()
    except (TypeError, ValueError):
        return None


def parse_rss(xml_text: str, symbol: str) -> list[dict]:
    try:
        root = etree.fromstring(xml_text.encode("utf-8"))
    except etree.XMLSyntaxError:
        return []

    out = []
    for item in root.iterfind(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        out.append(
            {
                "link_hash": hashlib.sha1(link.encode("utf-8")).hexdigest(),
                "symbol": symbol,
                "published_at": _iso(item.findtext("pubDate")),
                "title": (item.findtext("title") or "").strip(),
                "link": link,
                "source": (item.findtext("source") or "").strip() or None,
                "sentiment": None,
            }
        )
    return out


def fetch_news(session, symbol: str, name: str, market: str, limiter=None) -> list[dict]:
    xml_text = fetch_text(session, build_query_url(name, market), limiter=limiter)
    return parse_rss(xml_text, symbol)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_news.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/news.py tests/fixtures/google_news.xml tests/test_fetch_news.py
git commit -m "feat: Google News RSS 擷取與 SHA1 去重"
```

---

### Task 13: 即時報價（TWSE MIS 與 Yahoo 分鐘線）

**Files:**
- Create: `src/fetch/live.py`
- Create: `tests/fixtures/mis_response.json`
- Test: `tests/test_fetch_live.py`

**Interfaces:**
- Consumes: `src.http.fetch_json`
- Produces:
  - `MIS_SESSION_URL: str`, `MIS_API_URL: str`
  - `build_ex_ch(symbols: list[str]) -> str`（`["2330.TW","6488.TWO"]` → `"tse_2330.tw|otc_6488.tw"`）
  - `parse_mis(payload: dict) -> list[dict]`（每筆 `{"code","name","price","prev_close","open","high","low","volume","time","is_stale"}`）
  - `fetch_mis(session, symbols: list[str], limiter=None) -> list[dict]`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/mis_response.json` —— **第一筆刻意使用實測到的 `z="-"`**：

```json
{"msgArray":[
 {"c":"2330","n":"台積電","z":"-","tv":"-","v":"10219","o":"2395.0000","h":"2405.0000","l":"2375.0000","y":"2420.0000","tlong":"1788148438000","t":"11:53:58","a":"2390.0000_2395.0000_","b":"2385.0000_2380.0000_"},
 {"c":"2317","n":"鴻海","z":"238.5000","tv":"12","v":"88000","o":"239.0000","h":"240.0000","l":"237.5000","y":"239.5000","tlong":"1788148438000","t":"11:53:58","a":"238.5000_","b":"238.0000_"}
],"rtcode":"0000","rtmessage":"OK"}
```

```python
# tests/test_fetch_live.py
import json
from pathlib import Path

import pytest

from src.fetch import live

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def payload():
    return json.loads((FIXTURES / "mis_response.json").read_text(encoding="utf-8"))


def test_build_ex_ch_maps_market_prefix():
    assert live.build_ex_ch(["2330.TW", "6488.TWO"]) == "tse_2330.tw|otc_6488.tw"


def test_parse_handles_dash_price_by_falling_back_to_prev_close(payload):
    """spec §9.3：無成交時 z 為字串 "-"，直接 float() 會 ValueError。"""
    rows = {r["code"]: r for r in live.parse_mis(payload)}
    tsmc = rows["2330"]
    assert tsmc["price"] == 2420.0       # fallback 至昨收 y
    assert tsmc["prev_close"] == 2420.0
    assert tsmc["is_stale"] is True       # 標記為非成交價


def test_parse_uses_actual_price_when_available(payload):
    rows = {r["code"]: r for r in live.parse_mis(payload)}
    assert rows["2317"]["price"] == 238.5
    assert rows["2317"]["is_stale"] is False


def test_parse_returns_empty_when_market_closed():
    """spec §9.2：rtcode 非 0000 或 msgArray 空 → 視為休市。"""
    assert live.parse_mis({"rtcode": "5001", "msgArray": []}) == []
    assert live.parse_mis({"rtcode": "0000", "msgArray": []}) == []


def test_fetch_mis_primes_session_cookie_first(monkeypatch, payload):
    """spec §9.3：須先 GET index.jsp 建立 session，否則 API 被拒。"""
    calls = []

    class _S:
        def get(self, url, **kw):
            calls.append(url)
            class _R:
                status_code = 200
                def raise_for_status(self): pass
            return _R()

    monkeypatch.setattr(live, "fetch_json", lambda s, url, **kw: payload)
    live.fetch_mis(_S(), ["2330.TW"])
    assert any("index.jsp" in u for u in calls)
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_fetch_live.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fetch.live'`

- [ ] **Step 3: 實作**

```python
# src/fetch/live.py
"""即時報價（spec §2.7）。

台股走 TWSE MIS（delay=0，真即時）；美股走 Yahoo 分鐘線（延遲約 15 分）。
兩個關鍵限制：
1. 必須先 GET index.jsp 建立 session，直接打 API 會被拒（spec §9.3）
2. 成交價 z 在無成交時為字串 "-"，直接 float() 會 ValueError（spec §9.3）
"""
from __future__ import annotations

import requests

from src.http import fetch_json

MIS_SESSION_URL = "https://mis.twse.com.tw/stock/index.jsp"
MIS_API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"


def build_ex_ch(symbols: list[str]) -> str:
    parts = []
    for symbol in symbols:
        code, _, suffix = symbol.partition(".")
        prefix = "otc" if suffix.upper() == "TWO" else "tse"
        parts.append(f"{prefix}_{code}.tw")
    return "|".join(parts)


def _price(value) -> float | None:
    """MIS 以 "-" 表示無成交，不可直接轉型。"""
    text = str(value or "").strip()
    if text in {"", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_mis(payload: dict) -> list[dict]:
    if str(payload.get("rtcode")) != "0000":
        return []
    out = []
    for row in payload.get("msgArray") or []:
        traded = _price(row.get("z"))
        prev_close = _price(row.get("y"))
        price = traded if traded is not None else prev_close
        if price is None:
            continue
        out.append(
            {
                "code": row.get("c"),
                "name": row.get("n"),
                "price": price,
                "prev_close": prev_close,
                "open": _price(row.get("o")),
                "high": _price(row.get("h")),
                "low": _price(row.get("l")),
                "volume": _price(row.get("v")),
                "time": row.get("t"),
                "is_stale": traded is None,
            }
        )
    return out


def fetch_mis(session, symbols: list[str], limiter=None) -> list[dict]:
    try:
        session.get(MIS_SESSION_URL, timeout=20)
    except requests.RequestException:
        pass  # cookie 端點失敗時仍嘗試 API
    payload = fetch_json(
        session,
        MIS_API_URL,
        params={"ex_ch": build_ex_ch(symbols), "json": "1", "delay": "0"},
        limiter=limiter,
    )
    return parse_mis(payload)
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_fetch_live.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/live.py tests/fixtures/mis_response.json tests/test_fetch_live.py
git commit -m "feat: TWSE MIS 即時報價（處理 z=\"-\" 與 session cookie）"
```

---

### Task 14: 選股池

**Files:**
- Create: `src/fetch/universe.py`
- Create: `tests/fixtures/sp500_wiki.html`
- Test: `tests/test_universe.py`

**Interfaces:**
- Consumes: `src.fetch.twse.fetch_day_all`、`src.fetch.tpex.fetch_quotes`、`src.http.fetch_text`、`config.TW_COUNT`、`config.US_COUNT`
- Produces:
  - `is_common_stock(code: str) -> bool`（排除 ETF／權證／特別股等非普通股代號）
  - `select_taiwan(twse_rows: list[dict], tpex_rows: list[dict], limit: int) -> list[dict]`（依 `trade_value` 遞減，回傳 `{"symbol","code","market","name"}`）
  - `parse_sp500(html: str) -> list[dict]`（`{"code","name","sector"}`）
  - `select_us(rows: list[dict], limit: int) -> list[dict]`

- [ ] **Step 1: 建立 fixture 與失敗測試**

`tests/fixtures/sp500_wiki.html`：

```html
<table class="wikitable" id="constituents"><tbody>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
<tr><td><a>AAPL</a></td><td><a>Apple Inc.</a></td><td>Information Technology</td></tr>
<tr><td><a>MSFT</a></td><td><a>Microsoft</a></td><td>Information Technology</td></tr>
<tr><td><a>BRK.B</a></td><td><a>Berkshire Hathaway</a></td><td>Financials</td></tr>
</tbody></table>
```

```python
# tests/test_universe.py
from pathlib import Path

from src.fetch import universe

FIXTURES = Path(__file__).parent / "fixtures"


def test_excludes_non_common_stock_codes():
    """ETF（0050、00400A）、權證（6 碼英數）不進選股池。"""
    assert universe.is_common_stock("2330") is True
    assert universe.is_common_stock("6488") is True
    assert universe.is_common_stock("0050") is False
    assert universe.is_common_stock("00400A") is False
    assert universe.is_common_stock("03001P") is False


def test_select_taiwan_ranks_by_trade_value_and_tags_market():
    twse_rows = [
        {"code": "2330", "name": "台積電", "trade_value": 74_000_000_000.0},
        {"code": "1101", "name": "台泥", "trade_value": 400_000_000.0},
        {"code": "0050", "name": "元大台灣50", "trade_value": 99_000_000_000.0},
    ]
    tpex_rows = [{"code": "6488", "name": "環球晶", "trade_value": 1_980_000_000.0}]

    picked = universe.select_taiwan(twse_rows, tpex_rows, limit=2)
    assert [p["symbol"] for p in picked] == ["2330.TW", "6488.TWO"]
    assert picked[0]["market"] == "TW"
    assert picked[1]["market"] == "TW"


def test_select_taiwan_respects_limit():
    rows = [{"code": f"1{i:03d}", "name": str(i), "trade_value": float(i)} for i in range(1, 20)]
    assert len(universe.select_taiwan(rows, [], limit=5)) == 5


def test_parse_sp500_extracts_symbols_and_normalizes_dots():
    html = (FIXTURES / "sp500_wiki.html").read_text(encoding="utf-8")
    rows = universe.parse_sp500(html)
    codes = [r["code"] for r in rows]
    assert codes[:2] == ["AAPL", "MSFT"]
    assert "BRK-B" in codes  # Yahoo 用 - 而非 .
    assert rows[0]["sector"] == "Information Technology"


def test_select_us_applies_limit_and_symbol():
    rows = [{"code": "AAPL", "name": "Apple", "sector": "IT"},
            {"code": "MSFT", "name": "Microsoft", "sector": "IT"}]
    picked = universe.select_us(rows, limit=1)
    assert picked == [{"symbol": "AAPL", "code": "AAPL", "market": "US",
                       "name": "Apple", "sector": "IT"}]
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_universe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.fetch.universe'`

- [ ] **Step 3: 實作**

```python
# src/fetch/universe.py
"""選股池決定（spec §1.2）。

台股 300 檔以成交值排序、美股 200 檔取 S&P 500 前段。
ETF／權證／特別股不納入 —— 它們沒有基本面可估值，會汙染模型。
"""
from __future__ import annotations

import re

from lxml import html as lxml_html

from src.fetch import tpex, twse

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

_COMMON_STOCK = re.compile(r"^[1-9]\d{3}$")  # 台股普通股為 4 碼數字且不以 0 開頭


def is_common_stock(code: str) -> bool:
    return bool(_COMMON_STOCK.match((code or "").strip()))


def select_taiwan(twse_rows: list[dict], tpex_rows: list[dict], limit: int) -> list[dict]:
    candidates = []
    for row in twse_rows:
        if is_common_stock(row["code"]):
            candidates.append((row, twse.to_symbol(row["code"])))
    for row in tpex_rows:
        if is_common_stock(row["code"]):
            candidates.append((row, tpex.to_symbol(row["code"])))

    candidates.sort(key=lambda pair: pair[0].get("trade_value") or 0.0, reverse=True)
    return [
        {"symbol": symbol, "code": row["code"], "market": "TW", "name": row.get("name")}
        for row, symbol in candidates[:limit]
    ]


def parse_sp500(html_text: str) -> list[dict]:
    tree = lxml_html.fromstring(html_text)
    out = []
    for tr in tree.xpath("//table[contains(@class,'wikitable')]//tr"):
        cells = tr.xpath("./td")
        if len(cells) < 3:
            continue
        code = cells[0].text_content().strip().replace(".", "-")  # Yahoo 用 BRK-B
        if not code:
            continue
        out.append(
            {
                "code": code,
                "name": cells[1].text_content().strip(),
                "sector": cells[2].text_content().strip(),
            }
        )
    return out


def select_us(rows: list[dict], limit: int) -> list[dict]:
    return [
        {
            "symbol": row["code"], "code": row["code"], "market": "US",
            "name": row.get("name"), "sector": row.get("sector"),
        }
        for row in rows[:limit]
    ]
```

> **註**：spec §1.2 要求美股「市值前 200」。Wikipedia 表格未提供市值，故此處先取表格順序前 200 作為候選，待 Task 15 抓到 `market_cap` 後於 `run.py` 內重新排序取前 `US_COUNT`。此順序在 Task 15 的整合測試中驗證。

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_universe.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/fetch/universe.py tests/fixtures/sp500_wiki.html tests/test_universe.py
git commit -m "feat: 選股池（台股成交值前 N、S&P 500 解析、排除 ETF/權證）"
```

---

### Task 15: `run.py --fetch-only` 端到端整合

**Files:**
- Create: `run.py`
- Create: `src/pipeline.py`
- Test: `tests/test_pipeline_fetch.py`

**Interfaces:**
- Consumes: 前述所有 fetch 模組、`src.store`
- Produces:
  - `src.pipeline.fetch_all(conn, session, *, tw_limit: int, us_limit: int, yahoo_client=None, progress=None) -> dict`（回傳各表寫入筆數統計）
  - `run.py` CLI：`--full`、`--no-fetch`、`--only SYMBOL`、`--fetch-only`、`--serve`、`--backtest`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/test_pipeline_fetch.py
import pytest

from src import pipeline, store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


@pytest.fixture()
def stubs(monkeypatch):
    """把所有 fetch 函式換成固定回傳，驗證 pipeline 的組裝與寫入。"""
    monkeypatch.setattr(pipeline.twse, "fetch_day_all", lambda *a, **k: [
        {"code": "2330", "name": "台積電", "open": 2370.0, "high": 2395.0, "low": 2360.0,
         "close": 2380.0, "volume": 31234567, "trade_value": 74_000_000_000.0}])
    monkeypatch.setattr(pipeline.tpex, "fetch_quotes", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.twse, "fetch_valuation", lambda *a, **k: [
        {"code": "2330", "pe": 21.4, "dividend_yield": 1.51, "pb": 5.8}])
    monkeypatch.setattr(pipeline.twse, "fetch_monthly_revenue", lambda *a, **k: [
        {"code": "2330", "publish_date": "2026-08-10", "period": "2026-07",
         "industry": "半導體業", "revenue": 3.0e8, "yoy": 38.25, "mom": 7.14}])
    monkeypatch.setattr(pipeline.twse, "fetch_exright", lambda *a, **k: [
        {"code": "2330", "ex_date": "2026-09-11", "type": "息",
         "cash_dividend": 6.0, "stock_dividend": None}])
    monkeypatch.setattr(pipeline.twse, "fetch_margin", lambda *a, **k: [
        {"code": "2330", "margin_balance": 45250, "margin_balance_prev": 45000,
         "short_balance": 1210, "short_balance_prev": 1200}])
    monkeypatch.setattr(pipeline.tpex, "fetch_institutional", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.tdcc, "fetch_big_holders", lambda *a, **k: [
        {"code": "2330", "date": "2026-08-28", "big_holder_pct": 71.34}])
    monkeypatch.setattr(pipeline.universe, "parse_sp500", lambda html: [
        {"code": "AAPL", "name": "Apple Inc.", "sector": "Information Technology"}])
    monkeypatch.setattr(pipeline, "fetch_text", lambda *a, **k: "<html></html>")
    monkeypatch.setattr(pipeline.yahoo, "fetch_chart", lambda session, symbol, **k: [
        {"symbol": symbol, "date": "2026-08-28", "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "adjclose": 1.0, "volume": 10}])
    monkeypatch.setattr(pipeline.yahoo, "fetch_us_fundamentals", lambda client, symbol: {
        "symbol": symbol, "pe": 36.6, "forward_pe": 33.5, "pb": 43.4, "dividend_yield": 0.0034,
        "market_cap": 4.6e12, "beta": 1.09, "revenue_yoy": 0.164, "gross_margin": 0.468,
        "op_margin": 0.321, "net_margin": 0.276, "roe": 1.49, "roa": 0.245,
        "debt_to_equity": 78.4, "current_ratio": 0.92, "free_cashflow": 9.8e10,
        "eps_forward": 9.42, "sector": "Technology", "industry": "Consumer Electronics",
        "ex_dividend_date": "2026-08-10", "earnings_date": "2026-10-29",
        "short_percent_float": 0.008, "held_percent_institutions": 0.664})
    monkeypatch.setattr(pipeline.yahoo, "fetch_us_analyst", lambda client, symbol: {
        "symbol": symbol, "target_mean": 324.45, "target_high": 400.0,
        "target_low": 215.0, "recommendation": "buy", "n_analysts": 39})
    monkeypatch.setattr(pipeline.news, "fetch_news", lambda *a, **k: [])


def test_fetch_all_populates_stocks_for_both_markets(conn, stubs):
    pipeline.fetch_all(conn, session=None, tw_limit=1, us_limit=1, yahoo_client=object())
    rows = conn.execute("SELECT symbol, market FROM stocks ORDER BY symbol").fetchall()
    assert [(r["symbol"], r["market"]) for r in rows] == [("2330.TW", "TW"), ("AAPL", "US")]


def test_fetch_all_writes_prices_with_adjclose(conn, stubs):
    pipeline.fetch_all(conn, session=None, tw_limit=1, us_limit=1, yahoo_client=object())
    row = conn.execute("SELECT adjclose FROM prices WHERE symbol='2330.TW'").fetchone()
    assert row["adjclose"] == 1.0


def test_fetch_all_writes_dividends_and_margin(conn, stubs):
    pipeline.fetch_all(conn, session=None, tw_limit=1, us_limit=1, yahoo_client=object())
    div = conn.execute("SELECT * FROM dividends WHERE symbol='2330.TW'").fetchone()
    assert div["ex_date"] == "2026-09-11" and div["cash_dividend"] == 6.0
    mgn = conn.execute("SELECT * FROM margin WHERE symbol='2330.TW'").fetchone()
    assert mgn["margin_balance"] == 45250


def test_fetch_all_writes_us_analyst_targets(conn, stubs):
    pipeline.fetch_all(conn, session=None, tw_limit=1, us_limit=1, yahoo_client=object())
    row = conn.execute("SELECT * FROM analyst WHERE symbol='AAPL'").fetchone()
    assert row["target_mean"] == 324.45 and row["n_analysts"] == 39


def test_fetch_all_records_run_timestamp(conn, stubs):
    pipeline.fetch_all(conn, session=None, tw_limit=1, us_limit=1, yahoo_client=object())
    assert store.get_meta(conn, "last_fetch") is not None


def test_single_symbol_failure_does_not_abort_run(conn, stubs, monkeypatch):
    """spec §9.1：任何單一來源失效都不得讓整份報表產不出來。"""
    def boom(session, symbol, **kw):
        if symbol == "AAPL":
            raise RuntimeError("Yahoo 429")
        return [{"symbol": symbol, "date": "2026-08-28", "open": 1.0, "high": 1.0,
                 "low": 1.0, "close": 1.0, "adjclose": 1.0, "volume": 10}]

    monkeypatch.setattr(pipeline.yahoo, "fetch_chart", boom)
    stats = pipeline.fetch_all(conn, session=None, tw_limit=1, us_limit=1, yahoo_client=object())
    assert conn.execute("SELECT COUNT(*) c FROM prices WHERE symbol='2330.TW'").fetchone()["c"] == 1
    assert "AAPL" in stats["failed"]
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `python -m pytest tests/test_pipeline_fetch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.pipeline'`

- [ ] **Step 3: 實作 pipeline 與 CLI**

```python
# src/pipeline.py
"""抓取流程組裝（spec §9.1：單一來源失效不得中斷整體流程）。"""
from __future__ import annotations

from datetime import datetime, timezone

import config
from src import store
from src.fetch import news, tdcc, tpex, twse, universe, yahoo
from src.http import fetch_text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _index(rows: list[dict], key: str = "code") -> dict[str, dict]:
    return {row[key]: row for row in rows if row.get(key)}


def fetch_all(
    conn, session, *, tw_limit: int, us_limit: int, yahoo_client=None, progress=None
) -> dict:
    stats: dict = {"failed": []}

    def log(message: str) -> None:
        if progress:
            progress(message)

    # --- 選股池 ---
    log("抓取台股全市場報價…")
    twse_rows = twse.fetch_day_all(session)
    tpex_rows = tpex.fetch_quotes(session)
    tw_picks = universe.select_taiwan(twse_rows, tpex_rows, tw_limit)

    log("抓取 S&P 500 成分股…")
    sp500 = universe.parse_sp500(fetch_text(session, universe.SP500_URL))
    us_picks = universe.select_us(sp500, us_limit)

    store.upsert(conn, "stocks", [
        {"symbol": p["symbol"], "market": p["market"], "code": p["code"],
         "name": p.get("name"), "name_en": p.get("name") if p["market"] == "US" else None,
         "sector": p.get("sector"), "industry": None,
         "currency": "TWD" if p["market"] == "TW" else "USD",
         "listed_date": None, "updated_at": _now()}
        for p in tw_picks + us_picks
    ])
    stats["universe"] = len(tw_picks) + len(us_picks)

    # --- 台股全市場批次（不逐檔請求，spec §4）---
    log("抓取台股估值／營收／除權息／融資融券／大戶持股…")
    valuation = _index(twse.fetch_valuation(session))
    revenue = _index(twse.fetch_monthly_revenue(session))
    exright = _index(twse.fetch_exright(session))
    margin = _index(twse.fetch_margin(session))
    holders = _index(tdcc.fetch_big_holders(session))
    institutional = _index(tpex.fetch_institutional(session))

    tw_fundamentals, tw_dividends, tw_margin, tw_holders, tw_insti = [], [], [], [], []
    for pick in tw_picks:
        code, symbol = pick["code"], pick["symbol"]
        val, rev = valuation.get(code, {}), revenue.get(code, {})
        asof = rev.get("publish_date") or _now()[:10]
        tw_fundamentals.append({
            "symbol": symbol, "asof": asof, "pe": val.get("pe"), "forward_pe": None,
            "pb": val.get("pb"), "dividend_yield": val.get("dividend_yield"),
            "market_cap": None, "eps_ttm": None, "eps_forward": None,
            "revenue_yoy": rev.get("yoy"), "revenue_mom": rev.get("mom"),
            "gross_margin": None, "op_margin": None, "net_margin": None,
            "roe": None, "roa": None, "debt_to_equity": None,
            "current_ratio": None, "free_cashflow": None, "source": "twse",
        })
        if code in exright and exright[code].get("ex_date"):
            e = exright[code]
            tw_dividends.append({"symbol": symbol, "ex_date": e["ex_date"], "type": e.get("type"),
                                 "cash_dividend": e.get("cash_dividend"),
                                 "stock_dividend": e.get("stock_dividend"), "pay_date": None})
        if code in margin:
            m = margin[code]
            tw_margin.append({"symbol": symbol, "date": _now()[:10],
                              "margin_balance": m.get("margin_balance"),
                              "margin_balance_prev": m.get("margin_balance_prev"),
                              "short_balance": m.get("short_balance"),
                              "short_balance_prev": m.get("short_balance_prev"),
                              "margin_util": None})
        if code in holders:
            h = holders[code]
            tw_holders.append({"symbol": symbol, "date": h["date"],
                               "big_holder_pct": h["big_holder_pct"]})
        if code in institutional:
            i = institutional[code]
            tw_insti.append({"symbol": symbol, "date": _now()[:10],
                             "foreign_net": i.get("foreign_net"),
                             "trust_net": i.get("trust_net"),
                             "dealer_net": i.get("dealer_net")})

    store.upsert(conn, "fundamentals", tw_fundamentals)
    store.upsert(conn, "dividends", tw_dividends)
    store.upsert(conn, "margin", tw_margin)
    store.upsert(conn, "holders", tw_holders)
    store.upsert(conn, "institutional", tw_insti)

    # --- 逐檔：日線（台美股共用）---
    for pick in tw_picks + us_picks:
        symbol = pick["symbol"]
        try:
            rows = yahoo.fetch_chart(session, symbol, range_=f"{config.HISTORY_YEARS}y")
            store.upsert(conn, "prices", rows)
        except Exception as exc:  # 單檔失敗不得中斷整體（spec §9.1）
            stats["failed"].append(symbol)
            log(f"  ✗ {symbol} 日線失敗：{exc}")

    # --- 逐檔：美股基本面與分析師 ---
    if yahoo_client is not None:
        us_fundamentals, us_analyst = [], []
        for pick in us_picks:
            symbol = pick["symbol"]
            try:
                f = yahoo.fetch_us_fundamentals(yahoo_client, symbol)
                us_fundamentals.append({
                    "symbol": symbol, "asof": _now()[:10], "pe": f["pe"],
                    "forward_pe": f["forward_pe"], "pb": f["pb"],
                    "dividend_yield": f["dividend_yield"], "market_cap": f["market_cap"],
                    "eps_ttm": None, "eps_forward": f["eps_forward"],
                    "revenue_yoy": f["revenue_yoy"], "revenue_mom": None,
                    "gross_margin": f["gross_margin"], "op_margin": f["op_margin"],
                    "net_margin": f["net_margin"], "roe": f["roe"], "roa": f["roa"],
                    "debt_to_equity": f["debt_to_equity"],
                    "current_ratio": f["current_ratio"],
                    "free_cashflow": f["free_cashflow"], "source": "yahoo",
                })
                a = yahoo.fetch_us_analyst(yahoo_client, symbol)
                us_analyst.append({"symbol": symbol, "asof": _now()[:10],
                                   "target_mean": a["target_mean"], "target_high": a["target_high"],
                                   "target_low": a["target_low"],
                                   "recommendation": a["recommendation"],
                                   "n_analysts": a["n_analysts"]})
            except Exception as exc:
                stats["failed"].append(symbol)
                log(f"  ✗ {symbol} 基本面失敗：{exc}")
        store.upsert(conn, "fundamentals", us_fundamentals)
        store.upsert(conn, "analyst", us_analyst)

    # --- 逐檔：新聞 ---
    for pick in tw_picks + us_picks:
        try:
            items = news.fetch_news(session, pick["symbol"], pick.get("name") or pick["code"],
                                    pick["market"])
            store.upsert(conn, "news", items)
        except Exception as exc:
            log(f"  ✗ {pick['symbol']} 新聞失敗：{exc}")

    store.set_meta(conn, "last_fetch", _now())
    return stats
```

```python
# run.py
"""進入點（spec §12）。"""
from __future__ import annotations

import argparse
import sys

import config
from src import store
from src.fetch import yahoo  # noqa: F401  （確保模組可載入）
from src.http import RateLimiter, YahooClient, make_session
from src.pipeline import fetch_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="台股／美股預測分析平台")
    parser.add_argument("--full", action="store_true", help="強制全量重抓")
    parser.add_argument("--no-fetch", action="store_true", help="只重算模型與頁面")
    parser.add_argument("--fetch-only", action="store_true", help="只抓資料，不算模型")
    parser.add_argument("--only", metavar="SYMBOL", help="單檔除錯")
    parser.add_argument("--serve", action="store_true", help="啟動本地即時模式")
    parser.add_argument("--backtest", action="store_true", help="產出回測驗證報告")
    args = parser.parse_args(argv)

    conn = store.connect(config.DB_PATH)
    store.init_schema(conn)

    if not args.no_fetch:
        session = make_session()
        client = YahooClient(session, RateLimiter(config.YAHOO_MIN_INTERVAL))
        stats = fetch_all(
            conn, session,
            tw_limit=config.TW_COUNT, us_limit=config.US_COUNT,
            yahoo_client=client, progress=lambda m: print(m, flush=True),
        )
        print(f"完成：{stats['universe']} 檔，失敗 {len(stats['failed'])} 檔")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 執行測試確認通過**

Run: `python -m pytest tests/test_pipeline_fetch.py -v`
Expected: 6 passed

- [ ] **Step 5: 執行全部測試與 lint**

```bash
python -m pytest -q
python -m ruff check src tests run.py config.py
```

Expected: 全數 passed；ruff 無錯誤

- [ ] **Step 6: 真實網路煙霧測試（手動，唯一允許打真網路的一步）**

```bash
python -c "import config; config.TW_COUNT=3; config.US_COUNT=2; import run; run.main([])"
```

Expected: 印出進度並在 `data/cache.db` 產生資料。以下列指令確認：

```bash
python -c "from src import store; c=store.connect(); print([(t, c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]) for t in ('stocks','prices','fundamentals','analyst','dividends')])"
```

- [ ] **Step 7: Commit**

```bash
git add run.py src/pipeline.py tests/test_pipeline_fetch.py
git commit -m "feat: 抓取流程整合與 CLI，單檔失敗不中斷整體"
```

---

## Self-Review

**1. Spec 覆蓋度**

| Spec 章節 | 對應 Task |
|---|---|
| §1.2 選股池 300/200 | Task 1（設定）、Task 14（選股） |
| §2.1 台股端點 | Task 6、7、8、11 |
| §2.2 美股端點 | Task 9、10、14 |
| §2.3 adjclose + events | Task 9 |
| §2.4 crumb 握手 | Task 3 |
| §2.5 新聞 RSS | Task 12 |
| §2.6 SEC 信箱留空停用 | Task 1（設定與測試）；SEC 備援模組本身延後至第二階段實作（見下方缺口） |
| §2.7 即時報價 + CORS | Task 13（`serve.py` 屬第三階段） |
| §4 11 張表 + 增量 | Task 4、5 |
| §9.1 單一來源失效不中斷 | Task 15 |
| §9.3 五個實作陷阱 | Task 2（urllib/verify）、8（空格）、9（events）、11（BOM）、13（`z="-"`） |
| §10 測試策略 | 全 Task；fixture 全離線 |
| §12 CLI | Task 15 |

**已知缺口（刻意延後，非遺漏）**

- `src/fetch/sec.py`（§2.2 美股 XBRL 備援）：`SEC_CONTACT_EMAIL` 預設留空即停用，第一階段無此模組不影響任何測試或流程。移至第二階段計畫的首個 Task，屆時可與美股基本面缺值處理一併驗證。
- `src/fetch/twse.py` 的季報 `t187ap06_L_ci` 與 EPS `t187ap14_L`：三率與 EPS 屬特徵工程輸入，移至第二階段計畫 Task 1，與 point-in-time 對齊測試一起做較能驗證正確性。
- Task 14 的美股「市值前 200」排序需 `market_cap`，已在該 Task 註記由 `run.py` 於取得基本面後重排；此重排邏輯移至第二階段。

**2. 佔位符掃描**：全部 Step 均含可執行的實際程式碼與指令，無 TBD／TODO／「類似 Task N」。

**3. 型別一致性核對**

| 名稱 | 定義處 | 使用處 | 一致 |
|---|---|---|---|
| `fetch_json(session, url, *, limiter, params, timeout)` | Task 2 | Task 6–10、13 | ✓ |
| `fetch_text(..., encoding=)` | Task 2 | Task 11（`utf-8-sig`）、12、15 | ✓ |
| `YahooClient.quote_summary(symbol, modules)` | Task 3 | Task 10 | ✓ |
| `store.upsert(conn, table, rows) -> int` | Task 5 | Task 15 | ✓ |
| `store.get_last_date(conn, table, symbol, column)` | Task 5 | 第二階段增量使用 | ✓ |
| `twse._num(value, cast)` | Task 6 | Task 7、8（`tpex` 匯入） | ✓ |
| `twse.to_symbol` / `tpex.to_symbol` | Task 6 / 8 | Task 14 | ✓ |
| `yahoo._epoch_to_iso` | Task 9 | Task 10 | ✓ |
| `universe.select_taiwan/select_us` | Task 14 | Task 15 | ✓ |

---

## 下一階段

第二階段（特徵與模型）計畫：`docs/superpowers/plans/2026-08-31-stock-forecast-phase2-models.md`
第三階段（合成與前端）計畫：`docs/superpowers/plans/2026-08-31-stock-forecast-phase3-frontend.md`

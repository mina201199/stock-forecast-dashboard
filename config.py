"""全域設定。所有可調參數集中於此。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "cache.db"
OUT_HTML = ROOT / "index.html"

# 選股池（spec 1.2）
TW_COUNT = 300
US_COUNT = 200

# 模型（spec 1.2、6.1）
HISTORY_YEARS = 3
HORIZON_DAYS = 20
TW_BENCHMARK = "0050.TW"
US_BENCHMARK = "SPY"

# 時區：台股資料的營業日以台北時間為準，不可用 UTC 日期（會早 8 小時跨日）
TAIPEI_TZ = "Asia/Taipei"
NY_TZ = "America/New_York"

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
# 傳輸層暫時性錯誤（ChunkedEncodingError、Cloudflare 520）的應用層重試
TRANSIENT_ATTEMPTS = 4
TRANSIENT_BACKOFF = 1.5

# SEC：留空即停用備援（spec 2.6）。不得填假信箱。
SEC_CONTACT_EMAIL = ""

# --serve 模式（spec 8.6）
SERVE_HOST = "127.0.0.1"
SERVE_PORT = 8848
LIVE_CACHE_SECONDS = 5

"""TWSE／TPEx 欄位名常數。

TWSE OpenAPI 使用中文欄位鍵，字串必須逐字正確（spec 9.3）。
所有取值一律引用本模組，不得在各 fetch 模組內寫死字面值。
"""

# --- t187ap05_L 月營收 ---
REV_PUBLISH_DATE = "出表日期"          # 公布日，point-in-time 對齊用（spec 6.1 R3）
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

# --- TDCC 集保股權分散（CSV，spec 9.3）---
TDCC_DATE = "資料日期"
TDCC_CODE = "證券代號"
TDCC_LEVEL = "持股分級"
TDCC_PCT = "占集保庫存數比例%"
TDCC_BIG_HOLDER_LEVEL = "15"  # 400 張以上

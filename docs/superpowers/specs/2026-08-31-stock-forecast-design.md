# 台股／美股 預測分析平台 — 設計規格書

- **日期**：2026-08-31
- **狀態**：已與需求方確認，待實作
- **產出物**：單一 `index.html`（可離線開啟、可搜尋、可瀏覽最新股勢）

---

## 1. 目標與範圍

### 1.1 目標

建立一條「抓資料 → 算特徵 → 跑模型 → 合成結論 → 產生網頁」的離線管線，每檔股票產出：

- 未來 20 交易日的**預測報酬區間**（非單點）
- **目標價區間**（技術面／基本面／分析師共識三軌合成）
- **趨勢分級**（5 類）與 4 個主題榜單
- 完整**基本面、籌碼、股利、事件**欄位

### 1.2 範圍

| 項目 | 決定 |
|---|---|
| 市場 | 台股（TWSE 上市 + TPEx 上櫃）＋ 美股 |
| 檔數 | 台股 300（成交值排序）＋ 美股 200（S&P 500 市值排序）= 500 |
| 預測期間 | **20 個交易日**（約 1 個月） |
| 執行方式 | Python 批次產生靜態 HTML；**另提供 `--serve` 即時模式**（見 §2.7、§8.6） |
| 預測方法 | 混合：LightGBM ＋ 基本面估值 ＋ 新聞情緒 |
| 頁面 | 首頁＝榜單＋表格；個股＝**全頁**詳情 |
| **即時報價** | 台股 TWSE MIS（真即時）／美股 Yahoo（延遲約 15 分）；靜態模式為產出當下快照 |
| **風險指標** | 波動度、Beta、最大回撤、流動性分級等客觀量化欄位（見 §7.6） |
| **自訂篩選器** | 使用者自行設定條件過濾（見 §8.7） |

### 1.3 非目標（明確不做）

- 下單、回報、模擬倉位
- 外部 LLM API 呼叫（新聞情緒用詞典法）
- **個人化投資建議**（見 §1.4）

### 1.4 定位聲明

本專案輸出為**模型統計結果，非投資建議**。此聲明須固定顯示於頁首，且不得移除。

**不產生個人化投資建議。** 系統不輸出「你應該買進 X」「建議配置 Y%」這類針對特定使用者處境的指示。取而代之的是三類**客觀、描述性**的功能，判斷權留在使用者手上：

| 提供 | 不提供 |
|---|---|
| 客觀風險指標（波動度、Beta、回撤、流動性）§7.6 | 「這檔適不適合你」 |
| 使用者自訂條件的篩選器 §8.7 | 系統預設的推薦名單當成建議 |
| 持股組合的**描述性**分析（產業集中度、加權 Beta）§8.8 | 建議加碼／減碼／調整比例 |

差別在於：**呈現事實讓使用者自行判斷** vs **替使用者做決定**。所有榜單與模型輸出均標示為統計結果，且維持 §7.5 的 sanity check 與信心度揭露。

---

## 2. 資料來源

所有端點皆已於 2026-08-31 實測回傳 200 並確認欄位。

### 2.1 台股

| 用途 | 端點 | 關鍵欄位 |
|---|---|---|
| 全市場日收盤 | `openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` | `Code, Name, TradeVolume, TradeValue, OpeningPrice, HighestPrice, LowestPrice, ClosingPrice, Change` |
| 估值 | `.../v1/exchangeReport/BWIBBU_ALL` | `Code, PEratio, DividendYield, PBratio` |
| 公司基本資料 | `.../v1/opendata/t187ap03_L` | 中文鍵：`公司代號, 公司簡稱, 產業別, 實收資本額` |
| 月營收 | `.../v1/opendata/t187ap05_L` | 中文鍵：`出表日期(公布日), 資料年月, 營業收入-當月營收, 營業收入-去年同月增減(%), 營業收入-上月比較增減(%)` |
| 季度損益 | `.../v1/opendata/t187ap06_L_ci` | 中文鍵：`年度, 季別, 營業收入, 營業成本, 營業利益` |
| 各年度 EPS | `.../v1/opendata/t187ap14_L` | 中文鍵：`年度, 季別, 產業別, 基本每股盈餘(元), 營業收入, 營業利益, 稅後淨利` |
| **除權息預告** | `.../v1/exchangeReport/TWT48U_ALL` | `Date(除權息日), Code, Exdividend(權/息), CashDividend, StockDividendRatio` |
| **股利分派** | `.../v1/opendata/t187ap45_L` | 中文鍵：`股東配發-盈餘分配之現金股利(元/股), 股東配發-盈餘轉增資配股(元/股), 股東會日期` |
| **融資融券** | `.../v1/exchangeReport/MI_MARGN` | 中文鍵：`股票代號, 融資今日餘額, 融資前日餘額, 融券今日餘額, 融券前日餘額, 資券互抵` |
| 借券賣出 | `.../v1/SBL/TWT96U` | `TWSECode, TWSEAvailableVolume` |
| 三大法人 | `www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=json` | `fields` + `data` 二維陣列 |
| 上櫃報價 | `www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes` | `SecuritiesCompanyCode, Close, Open, High, Low, TradingShares, TransactionAmount` |
| 上櫃估值 | `.../v1/tpex_mainboard_peratio_analysis` | `PriceEarningRatio, DividendPerShare, YieldRatio, PriceBookRatio` |
| 上櫃除權息 | `.../v1/tpex_exright_prepost` | `ExRrightsExDividendDate, ExRrightsExDividend, CashDividend, StockDividendRatio` |
| 上櫃融資融券 | `.../v1/tpex_mainboard_margin_balance` | `MarginPurchaseBalance, ShortSaleBalance, MarginPurchaseUtilizationRate` |
| 上櫃三大法人 | `.../v1/tpex_3insti_daily_trading` | ⚠ 欄位名含不一致空格，見 §9.3 |
| 上櫃週轉率 | `.../v1/tpex_daily_turnover` | `TurnoverRatio, Capitalization` |
| **大戶持股** | `opendata.tdcc.com.tw/getOD.ashx?id=1-5` | ⚠ **CSV + UTF-8 BOM**：`資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%`。分級 15 = 400 張以上 |
| 日線歷史 | `query1.finance.yahoo.com/v8/finance/chart/{code}.TW` 或 `.TWO` | 見 §2.3 |

### 2.2 美股

| 用途 | 端點 | 關鍵欄位 |
|---|---|---|
| 成分股清單 | `en.wikipedia.org/wiki/List_of_S%26P_500_companies` | 表格解析 |
| 代號→CIK | `www.sec.gov/files/company_tickers.json` | 需 UA 帶聯絡信箱，見 §2.6 |
| 日線歷史 | Yahoo chart（同 §2.3） | |
| 基本面 | Yahoo `quoteSummary`（需 crumb，見 §2.4） | 模組見下表 |
| 財報備援 | `data.sec.gov/api/xbrl/companyfacts/CIK{10碼}.json` | XBRL tags |

**quoteSummary 模組與欄位**

| 模組 | 取用欄位 |
|---|---|
| `summaryDetail` | `trailingPE, forwardPE, dividendRate, dividendYield, exDividendDate, payoutRatio, fiveYearAvgDividendYield, beta, marketCap` |
| `defaultKeyStatistics` | `priceToBook, enterpriseValue, sharesShort, shortRatio, shortPercentOfFloat, heldPercentInsiders, heldPercentInstitutions, earningsQuarterlyGrowth` |
| `financialData` | `currentPrice, targetHighPrice, targetLowPrice, targetMeanPrice, recommendationKey, numberOfAnalystOpinions, revenueGrowth, earningsGrowth, grossMargins, operatingMargins, profitMargins, returnOnEquity, returnOnAssets, debtToEquity, currentRatio, quickRatio, freeCashflow` |
| `summaryProfile` | `sector, industry, longBusinessSummary` |
| `calendarEvents` | `earnings.earningsDate`（下次財報日）, `exDividendDate`, `dividendDate` |
| `earningsTrend` | `trend[period='+1y'].earningsEstimate.avg` → **前瞻 EPS** |

### 2.3 Yahoo chart：必須帶 events 參數

```
https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=3y&interval=1d&events=div%2Csplit
```

**不帶 `events` 就不會回傳 `indicators.adjclose`。**

驗證測資（2330.TW，2026-06-11 除息 6.0 元）：

| 日期 | close | adjclose |
|---|---|---|
| 2026-06-10 | 2255.0 | 2249.0 |
| 2026-06-11 | 2250.0 | 2250.0 |

**規則：所有報酬率與技術指標一律用 `adjclose`；`close` 僅用於顯示現價。**

### 2.4 Yahoo crumb 握手流程

```
1. GET https://fc.yahoo.com          → 回 404，但 cookie 會種下（勿視為失敗）
2. GET https://query1.finance.yahoo.com/v1/test/getcrumb  （帶上 cookie）
3. quoteSummary 請求附加 &crumb={crumb}
```

未帶 crumb 的 `quoteSummary` 回 401 `Invalid Crumb`；`v7/finance/quote` 已全面封鎖，不使用。

### 2.5 新聞

`news.google.com/rss/search?q={查詢}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant`（美股用 `hl=en-US&gl=US&ceid=US:en`）。查詢字串用公司名，抓近 7 日。

### 2.6 SEC 存取政策

SEC 要求所有自動化請求的 `User-Agent` 帶**真實聯絡信箱**，否則回 403（實測未帶時確為 403）。此值須置於 `config.py` 的 `SEC_CONTACT_EMAIL`，由使用者填入自己的信箱。

**預設留空，且留空時自動停用 SEC 備援**（美股基本面僅靠 Yahoo，失敗即標 `fundamentals_stale`）。不得填入假信箱規避 —— 那違反 SEC 存取政策且可能導致 IP 被封。

### 2.7 即時／盤中報價

**TWSE MIS（台股，真即時）**

```
1. GET https://mis.twse.com.tw/stock/index.jsp     ← 先取 session cookie，否則後續被拒
2. GET https://mis.twse.com.tw/stock/api/getStockInfo.jsp
       ?ex_ch=tse_2330.tw|tse_2317.tw|otc_6488.tw&json=1&delay=0
```

單次可查多檔（`|` 分隔），上市前綴 `tse_`、上櫃 `otc_`。回傳 `rtcode=0000` 表成功。

| 欄位 | 意義 | 欄位 | 意義 |
|---|---|---|---|
| `c` | 股票代號 | `n` | 股票名稱 |
| `z` | **成交價** | `tv` | 當盤成交量 |
| `o` / `h` / `l` | 開／高／低 | `v` | 累計成交量 |
| `y` | 昨收 | `t` | 撮合時間 `HH:MM:SS` |
| `a` / `b` | 五檔賣／買價（`_` 分隔） | `tlong` | epoch 毫秒 |

⚠ **`z` 在無成交時回傳 `"-"`**（實測 2330 於 11:53:58 即為 `"-"`）。必須 fallback：`z` → 最近成交價 → `y`（昨收），不可直接數值轉換，否則 `ValueError`。

**Yahoo 分鐘線（美股 + 台股補充）**

```
https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1m&range=1d
```

實測：`AAPL` 391 根、`2330.TW` 155 根、`5m/5d` 248 根。用於盤中走勢圖。

**延遲實測（2026-08-31）**：TWSE MIS 撮合時間 `11:53:58`，Yahoo 台股最後一根 `11:33` → **Yahoo 台股延遲約 20 分鐘**。故台股即時價一律以 TWSE MIS 為準，Yahoo 僅用於分鐘走勢圖。美股 Yahoo 延遲約 15 分鐘（免費源普遍限制，無免費替代）。

**CORS 限制（決定架構）**：TWSE MIS、Yahoo、TWSE OpenAPI **皆未回傳 `Access-Control-Allow-Origin`**（實測三者均為 `None`）。因此瀏覽器中的靜態 `file://` 頁面**無法直接取得即時報價**，必須經由本地代理，見 §8.6。

### 2.8 資料落差（不可掩飾）

| 落差 | 影響 | 處理 |
|---|---|---|
| 台股無免費分析師目標價 | 台股少一軌 | 權重回補技術面／基本面；頁面標示 |
| 台股無前瞻 EPS 預估 | 估值較弱 | 用近四季 EPS × 月營收 YoY 動能外推；信心度自動打折 |
| 台股無財報公布日預告 | 事件曆不精確 | 依法規期限推算（Q1→5/15、Q2→8/14、Q3→11/14、年報→3/31），標示「預估」 |
| 美股無月營收 | 少一個高頻成長訊號 | 以季度 `revenueGrowth` 替代 |

---

## 3. 系統架構

```
股票/
├── run.py                進入點：--full | --quick | --only SYMBOL | --no-fetch | --serve
├── config.py             檔數、市場配比、模型超參、路徑
├── data/cache.db         SQLite
├── index.html            ← 最終產出
├── docs/superpowers/specs/
└── src/
    ├── http.py           共用 Session：重試、限速、Yahoo crumb
    ├── fetch/
    │   ├── universe.py   選股池
    │   ├── twse.py  tpex.py  yahoo.py  sec.py  news.py
    │   └── live.py       即時報價：TWSE MIS + Yahoo 分鐘線
    ├── serve.py          --serve 模式的本地代理（http.server，繞過 CORS）
    ├── risk.py           客觀風險指標（波動度／Beta／回撤／流動性分級）
    ├── store.py          SQLite schema + 讀寫 + 增量判斷
    ├── features.py       技術指標 → 台美股統一 schema
    ├── models/
    │   ├── technical.py  LightGBM quantile
    │   ├── valuation.py  三種估值法
    │   └── sentiment.py  詞典法情緒
    ├── compose.py        三軌合成 + sanity check
    ├── backtest.py       走前驗證報告
    ├── render.py         Jinja2 → index.html
    └── templates/
        ├── index.html.j2
        ├── app.js        搜尋／排序／篩選／SVG 圖表
        └── style.css
```

**資料流**：`fetch/* → store.db → features → models/* → compose → render → index.html`

**設計原則**：每個模型模組輸入 DataFrame、輸出 DataFrame，可單獨測試、單獨停用。台美股差異只擋在 `fetch/` 層，`features` 之後完全共用。

---

## 4. 資料庫 Schema（SQLite，11 張表）

| 表 | 主鍵 | 欄位重點 |
|---|---|---|
| `stocks` | `symbol` | `market, code, name, name_en, sector, industry, currency, listed_date` |
| `prices` | `(symbol, date)` | `open, high, low, close, adjclose, volume` |
| `fundamentals` | `(symbol, asof)` | `pe, forward_pe, pb, dividend_yield, market_cap, eps_ttm, eps_forward, revenue_yoy, gross_margin, op_margin, net_margin, roe, roa, debt_to_equity, source` |
| `analyst` | `(symbol, asof)` | `target_mean, target_high, target_low, recommendation, n_analysts`（美股） |
| `institutional` | `(symbol, date)` | `foreign_net, trust_net, dealer_net`（台股） |
| `margin` | `(symbol, date)` | `margin_balance, short_balance, margin_util, short_margin_ratio`（台股） |
| `dividends` | `(symbol, ex_date)` | `type(權/息), cash_dividend, stock_dividend, pay_date` |
| `holders` | `(symbol, date)` | `big_holder_pct`（400 張以上，週更，台股） |
| `news` | `link_hash` | `symbol, published_at, title, source, sentiment` |
| `predictions` | `(symbol, run_date)` | 每次執行的完整輸出 → 供事後檢驗模型實際準確度 |
| `meta` | `key` | 各資料源最後更新時間、crumb 快取 |

### 增量更新策略

| 資料 | 策略 |
|---|---|
| `prices` | 只抓 `max(date)` 之後；首次 3 年 |
| 台股 `fundamentals` | 全市場批次 API，**不逐檔請求** |
| 美股 `fundamentals` | 逐檔 quoteSummary，限速 0.6 秒 |
| `news` | 近 7 日，`link` SHA1 去重 |
| `holders` | 每週一次 |

**預期耗時**：首跑 ~12 分鐘｜日更 ~2 分鐘｜`--no-fetch` 只重算模型 ~20 秒。

---

## 5. 特徵工程

統一表，台美股共用；缺值保留 `NaN`（**LightGBM 原生支援缺值，禁止盲目填 0 或均值**）。

| 類別 | 特徵 |
|---|---|
| 報酬 | `ret_1d, ret_5d, ret_20d, ret_60d, ret_120d`（全部用 adjclose） |
| 均線 | `ma5/20/60/120`、`price/ma20-1`、`ma5/ma20-1`、`ma20/ma60-1`、多頭排列布林 |
| 動能 | `RSI(14)`、`MACD(12,26,9)` 的 macd/signal/hist、`KD(9,3,3)` |
| 波動 | `ATR(14)/close`、20 日年化波動、布林帶位置 |
| 量能 | `vol_ma5/vol_ma20`、成交值 20 日均、量價背離 |
| 位階 | 52 週位階百分位、`close/high_252-1`、`close/low_252-1` |
| 估值 | `pe, pb, dividend_yield`（美股另有 `forward_pe`, `ps`） |
| 成長 | 台：`rev_yoy_1m, rev_yoy_3m_avg, rev_mom`／美：`revenue_growth, earnings_growth` |
| 獲利 | `gross_margin, op_margin, net_margin, roe` 及其 QoQ 變化 |
| 籌碼（台） | 外資／投信 5 日淨買超佔成交量比、`short_margin_ratio`、大戶持股週變化 |
| 籌碼（美） | `short_percent_of_float, held_percent_institutions` |
| 新聞 | `news_count_7d, sentiment_mean_7d, sentiment_trend` |

---

## 6. 模型設計

### 6.1 軌道一：LightGBM 技術面

**標籤**

```
y = 個股未來 20 交易日報酬 − 基準同期報酬
    台股基準 0050.TW；美股基準 SPY（皆取 adjclose）
```

理由：絕對報酬由大盤 beta 主導，模型會退化成「猜大盤」。改用超額報酬讓模型專注於橫斷面相對強弱。

**基準為何用 ETF 而非指數**：個股報酬取自 `adjclose`，屬**含息總報酬**。`^TWII`／`^GSPC` 是**價格指數（不含息）**，相減會產生約等於殖利率的系統性正偏差（台股年化約 3.5%，20 日約 0.28%），並直接汙染 §7.1 的目標價換算。`0050.TW` 與 `SPY` 的 `adjclose` 同為含息總報酬，定義一致。

代價：`0050.TW` 為市值前 50 大，對中小型股是大型股偏誤的基準。此處選擇**報酬定義一致性**優先於**universe 覆蓋度**，因為前者是系統性偏差、後者只影響基準的代表性。此限制列入 §11。

**輸出區間**：三個模型，`objective='quantile'`，`alpha = 0.1 / 0.5 / 0.9` → 悲觀／中位／樂觀。禁止只輸出單點。

**防資料洩漏（強制）**

| 規則 | 內容 |
|---|---|
| R1 | **嚴禁隨機切分**。時序切分：前 70% 訓練／15% 驗證／最後 15% 測試 |
| R2 | **Purge 20 天**：訓練集尾端砍 20 天，避免其未來窗口伸入驗證期 |
| R3 | **基本面 point-in-time**：以**公布日**對齊，非所屬期間。`t187ap05_L` 的 `出表日期` 即公布日 |
| R4 | 標準化統計量只能取自訓練集 |

R3 最易犯錯且錯了回測會失真至不可信，須有專屬測試（見 §10）。

**驗證**：走前驗證 5 窗；指標為 IC（Spearman）與分組報酬單調性（預測分 5 組看報酬遞增）。

**誠實基準**：與「全猜 0」及「純 20 日動能」對比。**若無法勝過動能基準，頁面必須標示模型無顯著優勢**，不得粉飾。

### 6.2 軌道二：基本面合理價（規則式）

| 方法 | 算法 | 適用 |
|---|---|---|
| PE 帶狀 | 過去 3 年 PE 的 P20/P50/P80 × 前瞻 EPS | `eps > 0` 且非金融業（主力） |
| PB 帶狀 | 歷史 PB 百分位 × 每股淨值 | 金融業、資產股、**`eps ≤ 0` 時的替代** |
| 殖利率法 | 現金股利 ÷ 歷史平均殖利率 | 殖利率 > 4% 且連續 5 年配息 |

前瞻 EPS：美股取 `earningsTrend['+1y']`；台股 = 近四季 EPS × (1 + 月營收 YoY 動能調整)。

輸出 `valuation_low / fair / high / method / upside_pct`；頁面三法並列。

### 6.3 軌道三：新聞情緒（詞典法）

- 中文：自建約 400 詞財經詞典（正：擴產、接單暢旺、調升目標價、獲利創高；負：下修、減產、認列虧損、跳票）
- 英文：**Loughran-McDonald 金融情緒詞典**精簡版（通用情緒詞典會誤判 `liability`、`cost`，LM 專為財報文本設計）
- 標題權重 > 摘要；7 日指數衰減；「不如預期／未達／無法」等否定前綴翻轉極性
- 輸出 `sentiment ∈ [-1, 1]` + 3 則代表性新聞

**限制**：詞典法準確率約 65-70%，明顯不如 LLM 語意理解。頁面標示為「關鍵詞情緒」。`sentiment.py` 介面須設計成可替換為 API 實作。

---

## 7. 合成層

### 7.1 目標價

| 來源 | 產出 | 基礎權重 | 降權條件 |
|---|---|:-:|---|
| LightGBM | q10/q50/q90 → 價格 | 0.40 | 資料 < 250 交易日 → 0.15 |
| 基本面估值 | low/fair/high | 0.40 | EPS 缺失 → 0.15；台股無前瞻 EPS → ×0.8 |
| 分析師共識 | targetLow/Mean/High | 0.20 | 分析師 < 5 人 → 降權；台股為 0，權重回補前兩項 |
| 新聞情緒 | **不直接給價** | — | 僅 **±5% 封頂**微調 |

技術面換算價格時，大盤預期報酬取 **0（假設大盤持平）**，此前提須顯示於頁面。

新聞不直接產生目標價，因情緒分數無價格尺度，換算即為杜撰。

### 7.2 信心度（0-100）

| 因子 | 算法 | 權重 |
|---|---|:-:|
| 三軌一致性 | 三來源目標價變異係數，越小越高 | **30%** |
| 資料完整度 | 有效特徵數 / 總特徵數 | 25% |
| 模型歷史準確度 | 該產業回測期 IC 與命中率 | 25% |
| 預測區間寬度 | (q90−q10) / 該檔波動度 | 20% |

一致性權重最高：三軌分歧時取平均會產生「看似合理、實則無意義」的數字，正確做法是顯示分歧並壓低信心。頁面須攤開四因子，不只給總分。

**信心 < 40 不給預測**，僅顯示「資料不足／訊號分歧」。

### 7.3 趨勢分級

| 分級 | 條件 |
|---|---|
| 🔥 強勢突破 | MA5>MA20>MA60、量能 >1.5 倍、52 週位階 >80% |
| 📈 穩步上升 | 多頭排列未爆量、位階 50-80% |
| ➡️ 區間整理 | 價格在 MA20 ±5%、ATR 收斂 |
| 📉 轉弱 | 跌破 MA20、MA5<MA20、量增 |
| ❄️ 弱勢築底 | 位階 <20%，RSI 背離或量縮 |

### 7.4 四個榜單

1. **模型看多榜**：預測超額報酬前 30 且信心 ≥60
2. **價值低估榜**：現價 < 合理價下緣，且 EPS>0、營收 YoY>0
3. **籌碼異動榜**：台股＝外資連買 + 融資減少；美股＝放空比高 + 法人加碼
4. **事件行事曆**：近 14 日除權息、近 14 日財報

### 7.5 Sanity check（出貨前強制）

- 目標價偏離現價 **>50%** → 標記異常、壓低信心、頁面警示
- 三軌方向完全相反 → 信心度**強制 ≤40**
- 上市未滿 120 日或停牌 → **不預測**
- 價格序列必須為 `adjclose`

### 7.6 客觀風險指標（`risk.py`）

純量化描述，**不含任何適合性判斷**。每檔輸出：

| 指標 | 算法 |
|---|---|
| 年化波動度 | 日報酬標準差 × √252（60 日與 252 日兩種窗口） |
| Beta | 對基準（0050.TW／SPY）的 252 日迴歸斜率 |
| 最大回撤 | 252 日內 `min(price/cummax(price) − 1)` |
| 下檔波動度 | 僅取負報酬計算的標準差 |
| 流動性分級 | 20 日均成交值分五級（極高／高／中／低／極低） |
| 波動分級 | 年化波動度對全體 universe 的百分位，分五級 |

用途：作為表格欄位與篩選條件供使用者自行判斷，**不用於產生建議**。頁面呈現時為中性描述（「年化波動度 42%，位於全體第 88 百分位」），不加價值判斷用語。

---

## 8. 前端頁面

### 8.1 形態

單一 `index.html`，資料以 `<script type="application/json">` 內嵌。預估 ~3.2 MB（摘要 200KB／年線 2.0MB／近 60 日 OHLC 850KB／新聞 150KB）。價格取 2 位小數、成交量以千股計可再壓 ~20%。

備案（若過肥）：拆為 `index.html` + `data.js`；`<script src>` 於 `file://` 不受 CORS 限制。

### 8.2 版面

- **首頁**：頂部搜尋 + 市場切換 → 四個榜單卡片 → 完整可排序表格
- **個股**：**全頁切換**（含「← 返回列表」）。內容為大圖表、目標價區間帶、三軌拆解、信心度四因子、基本面／籌碼／股利、相關新聞

### 8.3 圖表：自繪 SVG

不使用任何外部函式庫（離線要求排除 CDN）。需求為：還原收盤線 + 3 條均線 + 成交量柱 + **目標價區間橫帶** + 除權息／財報日標記。後兩者為通用圖表庫所無，自繪約 150 行 JS。

### 8.4 互動

- **搜尋**：同時比對代號、中文名、英文名、產業（`2330`／`台積`／`TSMC`／`半導體` 皆可命中）
- **篩選**：市場、產業、趨勢分級、信心度下限
- **排序**：表格 header 點擊，全欄位
- 深色／淺色跟隨系統；支援手機版面

### 8.5 誠實標示（強制）

| 位置 | 內容 |
|---|---|
| 頁首固定 | 「本頁為模型統計輸出，非投資建議」 |
| 目標價旁 | 資料日期 + 「假設大盤持平」前提 |
| 信心度 | 四因子拆解 |
| 台股估值 | 「無分析師前瞻 EPS，以營收動能外推」 |
| 新聞情緒 | 「關鍵詞情緒，非語意理解」 |
| 回測區塊 | IC、分組報酬、**與動能基準對比** |
| 即時報價旁 | 資料來源與延遲（台股「即時」／美股「延遲約 15 分」／靜態模式「產出當下快照 HH:MM」） |

### 8.6 即時模式（`--serve`）

因 §2.7 的 CORS 限制，靜態 `file://` 頁面無法直接取得即時報價。設計兩種模式：

| 模式 | 指令 | 報價行為 |
|---|---|---|
| **靜態（預設）** | `python run.py` | 內嵌**產出當下**的報價快照，標示時間戳。離線可看、單檔可分享 |
| **即時** | `python run.py --serve` | 啟動本地 `http.server`（預設 `127.0.0.1:8848`），頁面每 10 秒 `fetch('/api/live')` |

`serve.py` 職責：

- 提供 `index.html` 與 `/api/live?symbols=...`
- 伺服器端代打 TWSE MIS／Yahoo（**伺服器端無 CORS 限制**），回傳正規化 JSON
- **上游結果快取 5 秒**，避免多分頁重整打爆來源
- **僅綁定 `127.0.0.1`**，不對外開放
- 非交易時段自動降頻為 60 秒；收盤後停止輪詢並顯示「已收盤」

頁面在兩種模式下**共用同一份 HTML**：載入時偵測 `/api/live` 是否可用，可用則啟動輪詢、不可用則維持快照並標示。如此靜態檔案的可分享性不受影響。

### 8.7 自訂篩選器

使用者自行設定條件，系統只負責過濾與呈現：

- 數值範圍：PE、PB、殖利率、市值、營收 YoY、ROE、年化波動度、Beta、最大回撤、信心度
- 類別多選：市場、產業、趨勢分級、流動性分級
- 條件以 AND 組合，可存成具名組合（`localStorage`）
- 顯示符合檔數與**被排除的原因分佈**（例如「142 檔因殖利率不足被排除」），避免使用者誤以為市場上沒有標的

**系統不預設任何「推薦條件組合」** —— 條件全由使用者輸入。

### 8.8 持股組合描述性分析

使用者可輸入持股（代號 + 股數，存於 `localStorage`，不上傳）。系統輸出**描述性統計**：

| 輸出 | 說明 |
|---|---|
| 產業集中度 | 各產業市值佔比與 HHI 集中度指數 |
| 加權 Beta | 依市值加權的組合 Beta |
| 加權波動度 | 組合年化波動度（含個股相關係數矩陣） |
| 市場曝險 | 台股／美股比重、匯率曝險 |
| 事件行事曆 | 持股在未來 30 日的除權息與財報日 |

**明確不輸出**：加碼／減碼建議、目標配置比例、再平衡指示、「你的組合是否健康」這類評價。輸出僅為事實陳述，判斷權在使用者。

---

## 9. 錯誤處理

### 9.1 核心原則

**任何單一資料源失效都不得導致整份報表產不出來。** 最壞情況為部分欄位標示為過期。

### 9.2 各情境處理

| 情境 | 處理 |
|---|---|
| Yahoo crumb 過期／401 | 重新握手 1 次；仍失敗 → 標記 `fundamentals_stale`，改用 SEC XBRL |
| `fc.yahoo.com` 回 404 | **視為正常**，只檢查 cookie 是否種下 |
| Yahoo 429 限流 | 指數退避 2→4→8 秒，最多 3 次；仍失敗則跳過該檔並記錄 |
| 個股缺資料（新股、停牌） | 照常進頁面，標「資料不足」且**不給預測**，不讓 NaN 汙染模型 |
| 資料源整體失效 | 以 cache 舊資料續跑，頁面標示「此區塊為 X 日前資料」 |
| **即時報價來源失效** | `--serve` 回傳最後一次成功的快照 + 其時間戳，頁面標示「即時報價中斷，顯示 HH:MM 資料」。**不得以日線收盤價冒充即時價** |
| **非交易時段呼叫 MIS** | `rtcode` 非 `0000` 或 `msgArray` 為空 → 視為休市，改顯示最近收盤，輪詢降頻 |

### 9.3 已知實作陷阱

| 陷阱 | 處理 |
|---|---|
| **TWSE TLS 憑證缺 Subject Key Identifier** | Python 3.13 `ssl.create_default_context()` 預設啟用 `VERIFY_X509_STRICT` 會拒絕。**一律使用 `requests`（走 urllib3，不套該旗標），禁用 `urllib.request`**。憑證仍有驗證，**不得使用 `verify=False`** |
| **TDCC 為 CSV + UTF-8 BOM** | 以 `utf-8-sig` 解碼，非 JSON |
| **TPEx 三大法人欄位名含不一致空格** | 如 `' Foreign Investors ...-Total Sell'`（前導空格）、`'Dealers -TotalSell'`（中間空格）。讀取前須正規化欄位名（去空格、統一大小寫），否則 KeyError |
| **Yahoo chart 預設不回 adjclose** | 必須帶 `&events=div,split` |
| **TWSE OpenAPI 使用中文欄位鍵** | 程式須以精確中文字串取值，集中定義於常數避免散落 |
| **MIS 成交價 `z` 可能為 `"-"`** | 無成交時回傳字串 `"-"`（實測 2330 於 11:53:58 即為此值）。直接 `float()` 會 `ValueError`。fallback 序列：`z` → 前次成交價 → `y`（昨收） |
| **MIS 需先取 session cookie** | 直接打 `getStockInfo.jsp` 會被拒。須先 GET `mis.twse.com.tw/stock/index.jsp` 建立 session |
| **免費源皆無 CORS 標頭** | 三個端點實測 `Access-Control-Allow-Origin` 均為 `None`。即時報價必須經 `serve.py` 代理，不可期待瀏覽器直連 |

---

## 10. 測試策略

pytest。重點在**會靜默出錯**之處。資料層測試使用 fixture（存真實 API 回應樣本），**不打真網路**，確保離線可跑且不受對方改版影響。

| 測試 | 內容 |
|---|---|
| **point-in-time 對齊** | 給定日期，斷言取不到該日之後才公布的基本面。對應 §6.1 R3 |
| **除權息還原** | 固定測資：2330.TW 2026-06-10 `close=2255` / `adjclose=2249`（配息 6.0） |
| 技術指標 | RSI／MACD／KD 以已知數列驗算 |
| 估值法切換 | `eps ≤ 0` 時必須跳過 PE 法、改用 PB 法 |
| sanity check | 偏離 >50%、三軌反向 → 必須觸發降信心 |
| 欄位正規化 | TPEx 帶空格欄位名可正確解析 |
| 時序切分 | 斷言訓練集最大日期 + purge 天數 < 驗證集最小日期 |
| **MIS `"-"` 處理** | 餵入 `z="-"` 的 fixture，斷言回傳昨收而非拋出 `ValueError` |
| **即時價不得冒充** | 上游失效時，斷言回傳的是帶時間戳的舊快照，而非日線收盤價 |
| **風險指標** | 波動度／Beta／回撤以已知數列驗算 |
| **組合分析** | HHI 集中度、加權 Beta 以手算結果驗證 |
| **無建議性輸出** | 掃描 render 產物，斷言不含「建議買進／賣出／加碼／減碼／應配置」等指示性字串 |

---

## 11. 已知限制

1. **預測本質不確定**：股價受不可預測事件影響，模型僅捕捉歷史統計規律。頁面以區間而非單點呈現即為反映此點。
2. **台股弱於美股**：缺分析師前瞻 EPS 與目標價，估值軌道較弱，信心度自動打折。
3. **新聞情緒為詞典法**：準確率約 65-70%。
4. **無盤中資料**：日頻收盤。
5. **模型可能無效**：若回測勝不過動能基準，須據實標示。
6. **基準有大型股偏誤**：`0050.TW` 僅涵蓋市值前 50 大，用於中小型股的超額報酬時基準代表性較弱。此為 §6.1 選擇報酬定義一致性所付的代價。
7. **美股即時報價有延遲**：Yahoo 約 15 分鐘，免費源無替代方案。台股經 TWSE MIS 為真即時。
8. **靜態模式非即時**：預設產出的 `index.html` 內嵌的是**產出當下**的報價快照。需要即時跳動必須用 `--serve`。此為 CORS 限制的必然結果（§2.7），非實作取捨。
9. **模型與即時價不同步**：所有預測、目標價、趨勢分級皆基於**日線收盤**計算，盤中不重算。即時報價僅更新現價與漲跌幅顯示，目標價維持前一收盤的計算結果。

---

## 12. 執行方式

```bash
python run.py              # 增量更新 + 重算 + 產生 index.html
python run.py --full       # 強制全量重抓（首次或修復用）
python run.py --no-fetch   # 只重算模型與頁面（調參用，~20 秒）
python run.py --only 2330.TW  # 單檔除錯
python run.py --backtest   # 產出回測驗證報告
python run.py --serve      # 啟動本地即時模式 http://127.0.0.1:8848
```

`--serve` 會先確認 `index.html` 存在（不存在則先產生），再啟動代理。僅綁定 `127.0.0.1`，`Ctrl+C` 結束。

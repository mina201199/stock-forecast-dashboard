/* 單一 HTML 的前端邏輯。無外部依賴：圖表自繪 SVG。 */
(function () {
  "use strict";

  var DATA = JSON.parse(document.getElementById("payload").textContent);
  var STOCKS = DATA.stocks || [];
  var BOARDS = DATA.boards || {};
  var BY_SYMBOL = {};
  STOCKS.forEach(function (s) { BY_SYMBOL[s.symbol] = s; });

  var state = {
    query: "",
    market: "ALL",
    industry: "ALL",
    trend: "ALL",
    minConfidence: 0,
    sortKey: "turnover",
    sortDir: -1,
  };

  // --- 格式化 ---------------------------------------------------------

  function num(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toLocaleString("zh-TW", {
      minimumFractionDigits: digits === undefined ? 2 : digits,
      maximumFractionDigits: digits === undefined ? 2 : digits,
    });
  }
  function pct(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var d = digits === undefined ? 1 : digits;
    return (v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "%";
  }
  function plain(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    return Number(v).toFixed(digits === undefined ? 2 : digits);
  }
  function big(v) {
    if (v === null || v === undefined || isNaN(v)) return "—";
    var a = Math.abs(v);
    if (a >= 1e12) return (v / 1e12).toFixed(2) + " 兆";
    if (a >= 1e8) return (v / 1e8).toFixed(2) + " 億";
    if (a >= 1e4) return (v / 1e4).toFixed(1) + " 萬";
    return num(v, 0);
  }
  function signClass(v) {
    if (v === null || v === undefined || isNaN(v)) return "";
    return v > 0 ? "up" : (v < 0 ? "dn" : "");
  }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // --- 篩選與排序 -----------------------------------------------------

  function matchesQuery(s, q) {
    if (!q) return true;
    var hay = [s.code, s.symbol, s.name, s.name_en, s.industry]
      .filter(Boolean).join(" ").toLowerCase();
    return hay.indexOf(q.toLowerCase()) >= 0;
  }

  function sortValue(s, key) {
    switch (key) {
      case "code": return s.code || "";
      case "name": return s.name || "";
      case "industry": return s.industry || "";
      case "price": return s.price;
      case "change": return s.change_pct;
      case "trend": return s.trend_label || "";
      case "target": return s.prediction && s.prediction.target_mid;
      case "upside": return s.prediction && s.prediction.upside_pct;
      case "confidence": return s.prediction && s.prediction.confidence;
      case "pe": return s.fundamentals && s.fundamentals.pe;
      case "yield": return s.fundamentals && s.fundamentals.dividend_yield;
      case "revenue": return s.fundamentals && s.fundamentals.revenue_yoy;
      case "volatility": return s.risk && s.risk.volatility_252d;
      case "turnover": return s.risk && s.risk.turnover_ma20;
      default: return null;
    }
  }

  function filtered() {
    return STOCKS.filter(function (s) {
      if (!matchesQuery(s, state.query)) return false;
      if (state.market !== "ALL" && s.market !== state.market) return false;
      if (state.industry !== "ALL" && s.industry !== state.industry) return false;
      if (state.trend !== "ALL" && s.trend !== state.trend) return false;
      var c = (s.prediction && s.prediction.confidence) || 0;
      if (c < state.minConfidence) return false;
      return true;
    });
  }

  function sorted(rows) {
    var key = state.sortKey, dir = state.sortDir;
    return rows.slice().sort(function (a, b) {
      var va = sortValue(a, key), vb = sortValue(b, key);
      var na = va === null || va === undefined || (typeof va === "number" && isNaN(va));
      var nb = vb === null || vb === undefined || (typeof vb === "number" && isNaN(vb));
      if (na && nb) return 0;
      if (na) return 1;   // 缺值一律排最後，不因排序方向而躍居前列
      if (nb) return -1;
      if (typeof va === "string") return va.localeCompare(vb, "zh-Hant") * dir;
      return (va - vb) * dir;
    });
  }

  // --- 首頁：榜單 -----------------------------------------------------

  var BOARD_META = [
    { key: "bullish", title: "🚀 模型看多", why: "預測超額報酬為正且信心度 ≥ 60",
      value: function (r) { return pct(r.upside_pct); },
      cls: function (r) { return signClass(r.upside_pct); } },
    { key: "undervalued", title: "💎 低於合理價下緣", why: "現價低於估值區間下緣，且 EPS > 0、營收年增為正",
      value: function (r) { return pct(r.upside_pct); },
      cls: function () { return "dim"; } },
    { key: "chips", title: "🔥 籌碼異動", why: "台股：法人買超且融資減少｜美股：放空比偏高",
      value: function (r) {
        return r.foreign_net !== null && r.foreign_net !== undefined
          ? big(r.foreign_net) : pct(r.change_pct);
      },
      cls: function (r) { return signClass(r.foreign_net); } },
    { key: "events", title: "📅 近 14 日除權息", why: "依交易所公告的除權息日期排序",
      value: function (r) { return (r.event_date || "").slice(5) + " " + (r.event_type || ""); },
      cls: function () { return "dim"; } },
  ];

  function renderBoards() {
    document.getElementById("boards").innerHTML = BOARD_META.map(function (meta) {
      var rows = BOARDS[meta.key] || [];
      var body = rows.length
        ? rows.slice(0, 6).map(function (r) {
            return '<div class="board-row" data-symbol="' + esc(r.symbol) + '">' +
              '<span class="nm">' + esc(r.name) + ' <span class="faint">' +
              esc(r.code || r.symbol) + "</span></span>" +
              '<span class="vv ' + meta.cls(r) + '">' + meta.value(r) + "</span></div>";
          }).join("")
        : '<div class="board-empty">目前沒有符合條件的標的</div>';
      return '<div class="board"><h3>' + meta.title + "</h3>" +
        '<div class="why">' + esc(meta.why) + "</div>" + body + "</div>";
    }).join("");
  }

  // --- 首頁：表格 -----------------------------------------------------

  var COLUMNS = [
    { key: "code", label: "代號" },
    { key: "name", label: "名稱" },
    { key: "industry", label: "產業" },
    { key: "price", label: "現價", right: true },
    { key: "change", label: "漲跌", right: true },
    { key: "trend", label: "趨勢" },
    { key: "target", label: "目標價", right: true },
    { key: "upside", label: "空間", right: true },
    { key: "confidence", label: "信心", right: true },
    { key: "pe", label: "本益比", right: true },
    { key: "yield", label: "殖利率", right: true },
    { key: "revenue", label: "營收YoY", right: true },
    { key: "volatility", label: "年化波動", right: true },
  ];

  function renderTable() {
    var rows = sorted(filtered());
    document.getElementById("count").textContent =
      "符合 " + rows.length + " / " + STOCKS.length + " 檔";

    var head = "<tr>" + COLUMNS.map(function (c) {
      var arrow = state.sortKey === c.key ? (state.sortDir > 0 ? "▲" : "▼") : "";
      return '<th class="' + (c.right ? "r" : "") + '" data-key="' + c.key + '">' +
        esc(c.label) + '<span class="arrow">' + arrow + "</span></th>";
    }).join("") + "</tr>";

    var body = rows.length ? rows.map(function (s) {
      var p = s.prediction || {};
      var f = s.fundamentals || {};
      var predictable = p.predictable;
      return '<tr data-symbol="' + esc(s.symbol) + '">' +
        "<td>" + esc(s.code) + "</td>" +
        "<td>" + esc(s.name) + "</td>" +
        '<td class="dim">' + esc(s.industry || "—") + "</td>" +
        '<td class="r num">' + num(s.price) + "</td>" +
        '<td class="r num ' + signClass(s.change_pct) + '">' + pct(s.change_pct, 2) + "</td>" +
        '<td><span class="chip t-' + esc(s.trend) + '">' + esc(s.trend_label) + "</span></td>" +
        '<td class="r num">' + (predictable ? num(p.target_mid) : "—") + "</td>" +
        '<td class="r num ' + (predictable ? signClass(p.upside_pct) : "") + '">' +
          (predictable ? pct(p.upside_pct) : "—") + "</td>" +
        '<td class="r">' + confBar(p.confidence, predictable) + "</td>" +
        '<td class="r num">' + plain(f.pe, 1) + "</td>" +
        '<td class="r num">' + (f.dividend_yield !== null && f.dividend_yield !== undefined
          ? plain(f.dividend_yield, 2) + "%" : "—") + "</td>" +
        '<td class="r num ' + signClass(f.revenue_yoy) + '">' +
          (f.revenue_yoy !== null && f.revenue_yoy !== undefined
            ? plain(f.revenue_yoy, 1) + "%" : "—") + "</td>" +
        '<td class="r num">' + (s.risk && s.risk.volatility_252d !== null
          ? pct(s.risk.volatility_252d, 0) .replace("+", "") : "—") + "</td>" +
        "</tr>";
    }).join("")
      : '<tr><td class="empty-row" colspan="' + COLUMNS.length +
        '">沒有符合條件的股票。試著放寬篩選條件。</td></tr>';

    document.getElementById("thead").innerHTML = head;
    document.getElementById("tbody").innerHTML = body;
  }

  function confBar(v, predictable) {
    if (!predictable || v === null || v === undefined || isNaN(v)) {
      return '<span class="faint">—</span>';
    }
    return '<span class="conf-bar"><i style="width:' + Math.max(0, Math.min(100, v)) +
      '%"></i></span> <span class="num faint">' + Math.round(v) + "</span>";
  }

  // --- SVG 圖表 -------------------------------------------------------

  function lineChart(stock) {
    var c = stock.chart || {};
    var vals = c.adjclose || [];
    var dates = c.dates || [];
    if (vals.length < 2) return '<div class="faint">價格資料不足，無法繪圖</div>';

    var W = 900, H = 260, padL = 52, padR = 92, padT = 14, padB = 40, volH = 44;
    var plotH = H - padT - padB - volH;
    var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);

    var p = stock.prediction || {};
    var showTarget = p.predictable && p.target_low && p.target_high;
    if (showTarget) { lo = Math.min(lo, p.target_low); hi = Math.max(hi, p.target_high); }

    var span = (hi - lo) || 1;
    lo -= span * 0.06; hi += span * 0.06; span = hi - lo;

    var x = function (i) { return padL + (i / (vals.length - 1)) * (W - padL - padR); };
    var y = function (v) { return padT + (1 - (v - lo) / span) * plotH; };

    var parts = [];

    // 目標價區間帶（通用圖表庫沒有的東西，正是自繪的理由）
    if (showTarget) {
      var yTop = y(p.target_high), yBot = y(p.target_low);
      parts.push('<rect x="' + padL + '" y="' + yTop + '" width="' + (W - padL - padR) +
        '" height="' + Math.max(1, yBot - yTop) +
        '" fill="var(--accent)" opacity="0.10"/>');
      parts.push('<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + y(p.target_mid) +
        '" y2="' + y(p.target_mid) +
        '" stroke="var(--accent)" stroke-width="1.5" stroke-dasharray="5 4"/>');
      parts.push('<text x="' + (W - padR + 6) + '" y="' + (y(p.target_mid) + 4) +
        '" font-size="11" fill="var(--accent)">目標 ' + num(p.target_mid) + "</text>");
      parts.push('<text x="' + (W - padR + 6) + '" y="' + (yTop + 4) +
        '" font-size="10" fill="var(--text-faint)">' + num(p.target_high) + "</text>");
      parts.push('<text x="' + (W - padR + 6) + '" y="' + (yBot + 4) +
        '" font-size="10" fill="var(--text-faint)">' + num(p.target_low) + "</text>");
    }

    // 水平刻度
    for (var g = 0; g <= 4; g++) {
      var gv = lo + (span * g) / 4, gy = y(gv);
      parts.push('<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + gy + '" y2="' + gy +
        '" stroke="var(--line)" stroke-width="1"/>');
      parts.push('<text x="' + (padL - 7) + '" y="' + (gy + 4) +
        '" font-size="10.5" text-anchor="end" fill="var(--text-faint)">' +
        num(gv, gv > 500 ? 0 : 1) + "</text>");
    }

    // 均線
    [[5, "var(--text-faint)"], [20, "#eab308"], [60, "#8b5cf6"]].forEach(function (cfg) {
      var win = cfg[0], color = cfg[1];
      if (vals.length <= win) return;
      var pts = [];
      for (var i = win - 1; i < vals.length; i++) {
        var sum = 0;
        for (var k = i - win + 1; k <= i; k++) sum += vals[k];
        pts.push(x(i) + "," + y(sum / win));
      }
      parts.push('<polyline points="' + pts.join(" ") + '" fill="none" stroke="' + color +
        '" stroke-width="1" opacity="0.75"/>');
    });

    // 收盤線
    var line = vals.map(function (v, i) { return x(i) + "," + y(v); }).join(" ");
    parts.push('<polygon points="' + padL + "," + (padT + plotH) + " " + line + " " +
      (W - padR) + "," + (padT + plotH) +
      '" fill="var(--accent)" opacity="0.08"/>');
    parts.push('<polyline points="' + line +
      '" fill="none" stroke="var(--accent)" stroke-width="1.6"/>');

    // 成交量
    var vols = c.volume || [];
    var vmax = Math.max.apply(null, vols) || 1;
    var vTop = padT + plotH + 14;
    var bw = Math.max(1, (W - padL - padR) / vals.length - 0.5);
    vols.forEach(function (v, i) {
      var h = (v / vmax) * volH;
      parts.push('<rect x="' + (x(i) - bw / 2) + '" y="' + (vTop + volH - h) +
        '" width="' + bw + '" height="' + h +
        '" fill="var(--text-faint)" opacity="0.35"/>');
    });

    // 事件標記：除權息
    (stock.dividends || []).forEach(function (d) {
      var idx = dates.indexOf(d.ex_date);
      if (idx < 0) return;
      parts.push('<line x1="' + x(idx) + '" x2="' + x(idx) + '" y1="' + padT +
        '" y2="' + (padT + plotH) +
        '" stroke="var(--warn)" stroke-width="1" stroke-dasharray="3 3"/>');
    });

    // 日期軸
    [0, Math.floor(vals.length / 2), vals.length - 1].forEach(function (i, n) {
      parts.push('<text x="' + x(i) + '" y="' + (H - 8) + '" font-size="10.5" fill="var(--text-faint)" text-anchor="' +
        (n === 0 ? "start" : n === 2 ? "end" : "middle") + '">' +
        esc((dates[i] || "").slice(2)) + "</text>");
    });

    return '<svg class="chart" viewBox="0 0 ' + W + " " + H +
      '" preserveAspectRatio="xMidYMid meet" role="img" aria-label="價格走勢與目標價區間">' +
      parts.join("") + "</svg>";
  }

  // --- 詳情頁 ---------------------------------------------------------

  function kv(k, v, cls) {
    return '<div class="kv"><span class="k">' + esc(k) + '</span><span class="v ' +
      (cls || "") + '">' + v + "</span></div>";
  }

  function renderDetail(symbol) {
    var s = BY_SYMBOL[symbol];
    if (!s) return;
    var p = s.prediction || {};
    var f = s.fundamentals || {};
    var r = s.risk || {};
    var ch = s.chips || {};
    var cur = s.currency === "USD" ? "US$" : "NT$";

    var html = '<button class="back" id="back">← 返回列表</button>';

    html += '<div class="d-head"><div>' +
      '<div class="d-name">' + esc(s.name) + ' <span class="faint" style="font-size:15px">' +
      esc(s.symbol) + "</span></div>" +
      '<div class="d-sub">' + esc(s.industry || "產業未分類") + " ｜ " +
      '<span class="chip t-' + esc(s.trend) + '">' + esc(s.trend_label) + "</span>" +
      " ｜ 資料日 " + esc(s.as_of) + " ｜ 可用日線 " + s.bars + " 根" +
      (s.listed_date ? " ｜ 上市 " + esc(s.listed_date) : "") + "</div></div>" +
      '<div><div class="d-price">' + cur + num(s.price) + "</div>" +
      '<div class="d-chg ' + signClass(s.change_pct) + '" style="text-align:right">' +
      pct(s.change_pct, 2) + "</div></div></div>";

    (p.alerts || []).forEach(function (a) {
      html += '<div class="alert">⚠ ' + esc(a) + "</div>";
    });

    html += '<div class="grid">';

    // 圖表
    html += '<div class="card wide"><h4>價格走勢（還原權值）與目標價區間</h4>' +
      lineChart(s) +
      '<div class="faint" style="margin-top:6px;font-size:11px">' +
      "藍線為還原後收盤，黃/紫線為 20/60 日均線，橘色虛線為除權息日。" +
      "所有指標均以還原價計算，避免把除息當成下跌。</div></div>";

    // 目標價與三軌
    if (p.predictable) {
      var lo = p.target_low, hi = p.target_high, mid = p.target_mid;
      var span = (hi - lo) || 1;
      var posPrice = Math.max(0, Math.min(100, ((s.price - lo) / span) * 100));
      var posMid = Math.max(0, Math.min(100, ((mid - lo) / span) * 100));
      html += '<div class="card"><h4>目標價區間（未來約 ' + DATA.horizon_days + ' 個交易日）</h4>' +
        '<div class="target-bar">' +
        '<div class="mark" style="left:' + posPrice + '%" data-label="現價"></div>' +
        '<div class="mark" style="left:' + posMid + '%;background:var(--accent)" data-label="目標"></div>' +
        "</div>" +
        '<div class="target-ends"><span>' + num(lo) + "</span><span>" + num(hi) + "</span></div>" +
        kv("合成目標價", cur + num(mid), "") +
        kv("潛在空間", pct(p.upside_pct), signClass(p.upside_pct)) +
        '<div class="faint" style="font-size:11px;margin-top:8px">' +
        "技術面軌道假設大盤同期持平（大盤走勢無法預測，故取 0）。</div></div>";

      var tracks = p.tracks || {};
      var trackNames = { tech: "技術面模型", valuation: "基本面估值", analyst: "分析師共識" };
      var trackRows = Object.keys(tracks).map(function (k) {
        var w = (p.weights || {})[k];
        return kv(trackNames[k] + (w ? "（權重 " + Math.round(w * 100) + "%）" : ""),
          cur + num(tracks[k].fair) + ' <span class="' + signClass(tracks[k].fair - s.price) +
          '">' + pct(tracks[k].fair / s.price - 1) + "</span>");
      }).join("");
      html += '<div class="card"><h4>三軌拆解</h4>' + trackRows +
        (p.sentiment_adjustment
          ? kv("新聞情緒微調", pct(p.sentiment_adjustment, 2) + "（上限 ±5%）")
          : "") +
        (Object.keys(tracks).length < 2
          ? '<div class="faint" style="font-size:11px;margin-top:8px">' +
            "僅有單一軌道可用，無法交叉驗證，信心度已因此折價。</div>"
          : "") +
        "</div>";
    } else {
      html += '<div class="card"><h4>目標價</h4>' +
        '<div class="dim">不提供預測值。</div>' +
        '<div class="faint" style="font-size:11.5px;margin-top:6px">' +
        "資料或訊號品質未達門檻時，本頁選擇不給數字，而不是給一個沒有依據的數字。" +
        "下方的原始數據仍可供參考。</div></div>";
    }

    // 信心度拆解
    var factors = p.confidence_factors || {};
    var weights = p.confidence_weights || {};
    var factorRows = Object.keys(factors).map(function (k) {
      var v = factors[k];
      var w = weights[k] ? "（" + Math.round(weights[k] * 100) + "%）" : "";
      return '<div class="factor-row"><span class="fname">' + esc(k) + esc(w) + "</span>" +
        '<span class="fbar"><i style="width:' + (v === null ? 0 : Math.max(0, Math.min(100, v))) +
        '%"></i></span>' +
        '<span class="fval">' + (v === null ? "無法計算" : Math.round(v)) + "</span></div>";
    }).join("");
    html += '<div class="card"><h4>信心度 ' +
      (p.confidence !== undefined && p.confidence !== null ? Math.round(p.confidence) : "—") +
      " / 100</h4>" + factorRows +
      '<div class="faint" style="font-size:11px;margin-top:8px">' +
      "四個因子攤開顯示，因為只給一個總分等於要求你盲信。" +
      "可計算的因子越少，總分折價越多。</div></div>";

    // 基本面
    html += '<div class="card"><h4>基本面</h4>' +
      kv("本益比", plain(f.pe, 2)) +
      (f.forward_pe ? kv("預估本益比", plain(f.forward_pe, 2)) : "") +
      kv("股價淨值比", plain(f.pb, 2)) +
      kv("殖利率", f.dividend_yield !== null && f.dividend_yield !== undefined
        ? plain(f.dividend_yield, 2) + "%" : "—") +
      kv("月營收年增率", f.revenue_yoy !== null && f.revenue_yoy !== undefined
        ? plain(f.revenue_yoy, 2) + "%" : "—", signClass(f.revenue_yoy)) +
      (f.roe !== null && f.roe !== undefined ? kv("股東權益報酬率", pct(f.roe, 1)) : "") +
      (f.net_margin !== null && f.net_margin !== undefined ? kv("淨利率", pct(f.net_margin, 1)) : "") +
      (f.gross_margin !== null && f.gross_margin !== undefined ? kv("毛利率", pct(f.gross_margin, 1)) : "") +
      (f.market_cap ? kv("市值", big(f.market_cap)) : "") +
      (s.market === "TW"
        ? '<div class="faint" style="font-size:11px;margin-top:8px">' +
          "台股無免費的分析師前瞻 EPS，估值以營收動能外推，本質上弱於美股。</div>"
        : "") +
      "</div>";

    // 估值
    if (s.valuation) {
      html += '<div class="card"><h4>合理價估算（同業相對）</h4>' +
        kv("下緣", cur + num(s.valuation.low)) +
        kv("合理價", cur + num(s.valuation.fair)) +
        kv("上緣", cur + num(s.valuation.high)) +
        kv("採用方法", esc((s.valuation.methods || []).join("、") || "—")) +
        '<div class="faint" style="font-size:11px;margin-top:8px">' +
        "以同產業其他個股的估值分佈為基準。PE 法在 EPS ≤ 0 時自動改用 PB 法。</div></div>";
    }

    // 分析師
    if (s.analyst && s.analyst.target_mean) {
      html += '<div class="card"><h4>分析師共識</h4>' +
        kv("目標價（均）", cur + num(s.analyst.target_mean)) +
        kv("區間", num(s.analyst.target_low) + " – " + num(s.analyst.target_high)) +
        kv("評級", esc(s.analyst.recommendation || "—")) +
        kv("分析師人數", num(s.analyst.n_analysts, 0)) + "</div>";
    }

    // 籌碼
    html += '<div class="card"><h4>籌碼</h4>' +
      (ch.chip_date ? kv("資料日", esc(ch.chip_date)) : "") +
      (ch.foreign_net !== null && ch.foreign_net !== undefined
        ? kv("外資買賣超", big(ch.foreign_net) + " 股", signClass(ch.foreign_net)) : "") +
      (ch.trust_net !== null && ch.trust_net !== undefined
        ? kv("投信買賣超", big(ch.trust_net) + " 股", signClass(ch.trust_net)) : "") +
      (ch.dealer_net !== null && ch.dealer_net !== undefined
        ? kv("自營商買賣超", big(ch.dealer_net) + " 股", signClass(ch.dealer_net)) : "") +
      (ch.margin_balance !== null && ch.margin_balance !== undefined
        ? kv("融資餘額", big(ch.margin_balance) + " 張") : "") +
      (ch.margin_change !== null && ch.margin_change !== undefined
        ? kv("融資增減", big(ch.margin_change) + " 張", signClass(-ch.margin_change)) : "") +
      (ch.short_balance !== null && ch.short_balance !== undefined
        ? kv("融券餘額", big(ch.short_balance) + " 張") : "") +
      (ch.big_holder_pct !== null && ch.big_holder_pct !== undefined
        ? kv("400 張以上大戶持股", plain(ch.big_holder_pct, 2) + "%") : "") +
      (ch.short_percent_float !== null && ch.short_percent_float !== undefined
        ? kv("放空比（流通股）", pct(ch.short_percent_float, 2)) : "") +
      "</div>";

    // 風險
    html += '<div class="card"><h4>風險指標（客觀量化，非適合性判斷）</h4>' +
      kv("年化波動度", pct(r.volatility_252d, 1).replace("+", "") +
        (r.volatility_percentile !== null && r.volatility_percentile !== undefined
          ? '  <span class="faint">全體第 ' + Math.round(r.volatility_percentile) + " 百分位</span>"
          : "")) +
      kv("下檔波動度", pct(r.downside_volatility, 1).replace("+", "")) +
      kv("Beta", plain(r.beta, 2)) +
      kv("近一年最大回撤", pct(r.max_drawdown, 1)) +
      kv("波動分級", esc(r.volatility_level || "—")) +
      kv("流動性分級", esc(r.liquidity_level || "—")) +
      "</div>";

    // 事件
    if ((s.dividends || []).length) {
      html += '<div class="card"><h4>即將到來的除權息</h4>' +
        s.dividends.map(function (d) {
          return kv(esc(d.ex_date) + "　除" + esc(d.type || "息"),
            d.cash_dividend ? "現金 " + plain(d.cash_dividend, 2) : "—");
        }).join("") + "</div>";
    }

    // 新聞
    var nw = s.news || {};
    var items = nw.top || [];
    html += '<div class="card wide"><h4>相關新聞與關鍵詞情緒</h4>' +
      kv("情緒分數", nw.sentiment === null || nw.sentiment === undefined
        ? "無近期新聞" : plain(nw.sentiment, 2), signClass(nw.sentiment)) +
      kv("近 7 日則數", num(nw.news_count, 0)) +
      (items.length
        ? items.map(function (n) {
            var cls = n.sentiment > 0.1 ? "pos" : (n.sentiment < -0.1 ? "neg" : "");
            return '<div class="news-item ' + cls + '">' +
              (n.link ? '<a href="' + esc(n.link) + '" target="_blank" rel="noopener">' +
                esc(n.title) + "</a>" : esc(n.title)) +
              '<div class="nm">' + esc(n.source || "") + " ｜ " +
              esc((n.published_at || "").slice(0, 16).replace("T", " ")) +
              " ｜ 情緒 " + plain(n.sentiment, 2) + "</div></div>";
          }).join("")
        : '<div class="faint" style="margin-top:8px">近 7 日沒有抓到相關新聞。</div>') +
      '<div class="faint" style="font-size:11px;margin-top:8px">' +
      "情緒為財經詞典比對結果（準確率約 65-70%），非語意理解，僅供參考。" +
      "英文採用 Loughran-McDonald 金融詞典，避免把 liability、cost 等財務中性詞誤判為負面。" +
      "</div></div>";

    html += "</div>";
    document.getElementById("detail").innerHTML = html;
    document.getElementById("back").addEventListener("click", showHome);
    document.body.classList.add("detail-mode");
    window.scrollTo(0, 0);
    location.hash = "s/" + symbol;
  }

  function showHome() {
    document.body.classList.remove("detail-mode");
    if (location.hash) { location.hash = ""; }
    window.scrollTo(0, 0);
  }

  // --- 事件綁定 -------------------------------------------------------

  function initFilters() {
    var industries = {};
    STOCKS.forEach(function (s) { if (s.industry) industries[s.industry] = 1; });
    var sel = document.getElementById("f-industry");
    Object.keys(industries).sort(function (a, b) {
      return a.localeCompare(b, "zh-Hant");
    }).forEach(function (name) {
      var o = document.createElement("option");
      o.value = name; o.textContent = name;
      sel.appendChild(o);
    });
  }

  function bind() {
    document.getElementById("search").addEventListener("input", function (e) {
      state.query = e.target.value.trim();
      renderTable();
    });
    document.querySelectorAll("[data-market]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.querySelectorAll("[data-market]").forEach(function (b) {
          b.classList.remove("on");
        });
        btn.classList.add("on");
        state.market = btn.getAttribute("data-market");
        renderTable();
      });
    });
    document.getElementById("f-industry").addEventListener("change", function (e) {
      state.industry = e.target.value; renderTable();
    });
    document.getElementById("f-trend").addEventListener("change", function (e) {
      state.trend = e.target.value; renderTable();
    });
    document.getElementById("f-conf").addEventListener("input", function (e) {
      state.minConfidence = Number(e.target.value);
      document.getElementById("f-conf-val").textContent = e.target.value;
      renderTable();
    });
    document.getElementById("thead").addEventListener("click", function (e) {
      var th = e.target.closest("th");
      if (!th) return;
      var key = th.getAttribute("data-key");
      if (state.sortKey === key) { state.sortDir *= -1; }
      else { state.sortKey = key; state.sortDir = -1; }
      renderTable();
    });
    document.getElementById("tbody").addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-symbol]");
      if (tr) renderDetail(tr.getAttribute("data-symbol"));
    });
    document.getElementById("boards").addEventListener("click", function (e) {
      var row = e.target.closest("[data-symbol]");
      if (row) renderDetail(row.getAttribute("data-symbol"));
    });
    window.addEventListener("hashchange", function () {
      if (!location.hash) { document.body.classList.remove("detail-mode"); }
    });
  }

  initFilters();
  renderBoards();
  renderTable();
  bind();

  if (location.hash.indexOf("#s/") === 0) {
    renderDetail(location.hash.slice(3));
  }
})();

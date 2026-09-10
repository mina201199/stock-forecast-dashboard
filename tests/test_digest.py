"""每日日報測試（spec §7）。

日報的內容順序是刻意的：準度計分卡放最上面，因為它決定讀者該多信
下面的東西。測試把這個順序當成契約。
"""
import json

import numpy as np
import pandas as pd
import pytest

from src import baserate, digest, store
from src.signals.base import Signal


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(tmp_path / "t.db")
    store.init_schema(c)
    yield c
    c.close()


def _frame(symbol, market, closes):
    n = len(closes)
    return baserate.SymbolFrame(symbol=symbol, market=market, df=pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B").strftime("%Y-%m-%d"),
        "adjclose": np.array(closes, dtype=float),
        "close": np.array(closes, dtype=float),
        "volume": np.ones(n) * 1000,
    }))


def _always(key="always"):
    return Signal(key=key, name=f"永遠觸發-{key}", family="technical",
                  markets=("TW", "US"),
                  rule=lambda df: pd.Series(True, index=df.index), min_bars=0)


def _never(key="never"):
    return Signal(key=key, name="不觸發", family="technical", markets=("TW", "US"),
                  rule=lambda df: pd.Series(False, index=df.index), min_bars=0)


def _rate(key, market="TW", horizon=20, verdict="edge", **kw):
    base = dict(
        signal_key=key, market=market, horizon=horizon, n_fires=500, n_effective=40,
        hit_rate=0.55, baseline_hit_rate=0.47, median_excess=0.02,
        ci_low=0.005, ci_high=0.04, p_value=0.01, p_adjusted=0.03,
        first_half_excess=0.01, second_half_excess=0.02,
        first_half_median=0.01, second_half_median=0.02, verdict=verdict,
    )
    return baserate.BaseRate(**{**base, **kw})


# --- 今日掃描 -----------------------------------------------------------


def test_scan_latest_reports_the_last_bar_only():
    frames = [_frame("2330.TW", "TW", list(range(100, 200)))]
    fires = baserate.scan_latest([_always()], frames)
    assert len(fires) == 1
    assert fires[0].symbol == "2330.TW"
    assert fires[0].date == frames[0].df["date"].iloc[-1]
    assert fires[0].price == pytest.approx(199.0)


def test_scan_latest_skips_signals_that_did_not_fire():
    frames = [_frame("2330.TW", "TW", list(range(100, 200)))]
    assert baserate.scan_latest([_never()], frames) == []


def test_scan_latest_respects_market_applicability():
    tw_only = Signal(key="tw_only", name="僅台股", family="flow", markets=("TW",),
                     rule=lambda df: pd.Series(True, index=df.index), min_bars=0)
    frames = [_frame("2330.TW", "TW", list(range(100, 200))),
              _frame("AAPL", "US", list(range(100, 200)))]
    fires = baserate.scan_latest([tw_only], frames)
    assert [f.symbol for f in fires] == ["2330.TW"]


# --- signal_stats 持久化 ------------------------------------------------


def test_save_and_load_signal_stats_round_trip(conn):
    rates = [_rate("a"), _rate("b", verdict="no_edge")]
    assert baserate.save_stats(conn, rates) == 2

    loaded = {(r.signal_key, r.market, r.horizon): r for r in baserate.load_stats(conn)}
    assert len(loaded) == 2
    got = loaded[("a", "TW", 20)]
    assert got.verdict == "edge"
    assert got.hit_rate == pytest.approx(0.55)
    assert got.n_effective == 40


def test_save_stats_replaces_previous_run(conn):
    baserate.save_stats(conn, [_rate("a", verdict="edge")])
    baserate.save_stats(conn, [_rate("a", verdict="no_edge")])
    loaded = baserate.load_stats(conn)
    assert len(loaded) == 1
    assert loaded[0].verdict == "no_edge"


# --- 日報結構 -----------------------------------------------------------


def _payload(**kw):
    base = dict(
        as_of="2026-09-10",
        last_success="2026-09-10",
        accuracy_lines=["方向命中率 48.9%（p=0.915）與隨機無法區分"],
        fires=[],
        rates=[],
        bands=[],
        plan=None,
        degraded=[],
    )
    return {**base, **kw}


def test_accuracy_scorecard_comes_first():
    """比的是章節標題的位置，不是任意子字串 —— 標題「每日訊號日報」
    本身就含「訊號」，用子字串比會永遠失敗。"""
    md = digest.render_markdown(digest.build(_payload()))
    assert md.index("## 1. 準度計分卡") < md.index("## 2. 今日觸發的訊號")


def test_edge_section_states_emptiness_explicitly():
    """沒有任何 edge 訊號時，必須明說，不能只是空白。

    空白區塊會被讀成「今天沒觸發」，而實際意思是「沒有任何訊號有
    統計優勢」—— 完全不同的兩件事。
    """
    md = digest.render_markdown(digest.build(_payload()))
    assert "沒有任何訊號" in md


def test_edge_fires_show_win_rate_with_baseline_and_effective_sample():
    data = digest.build(_payload(
        fires=[baserate.Fire("2330.TW", "TW", "a", "2026-09-10", 1000.0)],
        rates=[_rate("a")],
    ))
    md = digest.render_markdown(data)
    assert "2330.TW" in md
    assert "55.0%" in md          # 勝率
    assert "47.0%" in md          # 基準對照，必須並列
    assert "40" in md             # 有效樣本


def test_non_edge_fires_are_labelled_not_hidden():
    data = digest.build(_payload(
        fires=[baserate.Fire("2317.TW", "TW", "b", "2026-09-10", 200.0)],
        rates=[_rate("b", verdict="no_edge")],
    ))
    md = digest.render_markdown(data)
    assert "2317.TW" in md
    assert "無統計優勢" in md


def test_unstable_fires_say_which_half_failed():
    data = digest.build(_payload(
        fires=[baserate.Fire("2454.TW", "TW", "c", "2026-09-10", 900.0)],
        rates=[_rate("c", verdict="unstable", first_half_median=-0.014,
                     second_half_median=0.053)],
    ))
    md = digest.render_markdown(data)
    assert "前半" in md and "-1.4%" in md


def test_bands_are_not_called_target_prices():
    data = digest.build(_payload(
        bands=[{"symbol": "2330.TW", "price": 1000.0, "low": 900.0, "high": 1100.0}],
    ))
    md = digest.render_markdown(data)
    assert "波動區間" in md
    assert "目標價" not in md


def test_stale_fetch_raises_a_warning_in_the_header():
    md = digest.render_markdown(digest.build(
        _payload(as_of="2026-09-10", last_success="2026-09-01")))
    assert "抓取" in md
    assert "9" in md   # 落後天數


def test_fresh_fetch_has_no_warning():
    md = digest.render_markdown(digest.build(
        _payload(as_of="2026-09-10", last_success="2026-09-10")))
    assert "資料已過期" not in md


# --- 輸出 ---------------------------------------------------------------


def test_write_produces_markdown_and_parseable_json(tmp_path):
    data = digest.build(_payload(
        fires=[baserate.Fire("2330.TW", "TW", "a", "2026-09-10", 1000.0)],
        rates=[_rate("a")],
    ))
    files = digest.write(data, tmp_path)
    md_path, json_path = files.markdown, files.json

    assert md_path.exists() and json_path.exists()
    parsed = json.loads(json_path.read_text(encoding="utf-8"))
    assert parsed["as_of"] == "2026-09-10"
    assert parsed["signals"]["edge"][0]["symbols"][0]["symbol"] == "2330.TW"


def test_digest_stays_within_size_budget(tmp_path):
    """1.7 MB 的 index.html 貼不進外部工具，日報必須小。"""
    fires = [baserate.Fire(f"S{i}.TW", "TW", "a", "2026-09-10", 100.0) for i in range(120)]
    data = digest.build(_payload(fires=fires, rates=[_rate("a")]))
    md_path = digest.write(data, tmp_path).markdown
    assert md_path.stat().st_size < digest.SIZE_BUDGET_BYTES


# --- 分組（2026-09-10 實跑 500 檔後補上）------------------------------


def test_non_edge_section_prints_stats_once_per_signal_not_per_symbol():
    """基準率掛在訊號上，不是掛在個股上 —— 逐檔重複是冗余。

    實跑 500 檔時日報出現 12 行 near_high_strength，每行的勝率、
    中位超額、有效樣本數字完全相同。181 筆觸發讓日報從 8.3 KB 漲到
    29.6 KB，而 50 KB 上限大約在 300 筆就會撞破。
    """
    fires = [
        baserate.Fire(f"S{i}.TW", "TW", "b", "2026-09-10", 100.0) for i in range(12)
    ]
    md = digest.render_markdown(digest.build(
        _payload(fires=fires, rates=[_rate("b", verdict="no_edge")])))

    # 勝率只能出現一次，不是 12 次
    assert md.count("55.0%") == 1
    # 但 12 檔標的都要列出來
    for i in range(12):
        assert f"S{i}.TW" in md


def test_grouped_section_lists_every_symbol():
    fires = [
        baserate.Fire("2330.TW", "TW", "b", "2026-09-10", 1000.0),
        baserate.Fire("2317.TW", "TW", "b", "2026-09-10", 200.0),
    ]
    md = digest.render_markdown(digest.build(
        _payload(fires=fires, rates=[_rate("b", verdict="no_edge")])))
    assert "2330.TW" in md and "2317.TW" in md


def test_different_signals_stay_in_separate_groups():
    fires = [
        baserate.Fire("2330.TW", "TW", "b", "2026-09-10", 1000.0),
        baserate.Fire("2317.TW", "TW", "c", "2026-09-10", 200.0),
    ]
    md = digest.render_markdown(digest.build(_payload(
        fires=fires,
        rates=[_rate("b", verdict="no_edge"),
               _rate("c", verdict="no_edge", hit_rate=0.41)],
    )))
    assert "55.0%" in md and "41.0%" in md


def test_digest_stays_within_budget_at_production_scale(tmp_path):
    """實跑 500 檔選股池：單日 181 筆觸發、155 行波動區間，共 29.2 KB。

    這裡用 3 倍的量（600 筆觸發 + 500 檔區間）當上界。未分組的版本在
    這個規模下約 121 bytes/筆，訊號區單獨就會超過 70 KB —— 所以這個
    測試同時是分組沒被改回去的護欄。測試的規模必須跟得上生產環境，
    不然預算等於沒守。
    """
    fires = [
        baserate.Fire(f"S{i}.TW", "TW", f"sig{i % 10}", "2026-09-10", 100.0)
        for i in range(600)
    ]
    rates = [_rate(f"sig{k}", verdict="no_edge") for k in range(10)]
    bands = [
        {"symbol": f"S{i}.TW", "price": 100.0, "low": 90.0, "high": 110.0}
        for i in range(500)
    ]
    data = digest.build(_payload(fires=fires, rates=rates, bands=bands))
    md_path = digest.write(data, tmp_path).markdown
    assert md_path.stat().st_size < digest.SIZE_BUDGET_BYTES


# --- HTML 版日報 -------------------------------------------------------


def _html(**kw):
    return digest.render_html(digest.build(_payload(**kw)))


def test_html_is_self_contained():
    """不得引用任何外部資源。

    可列印、可存檔、可寄給別人的前提就是這個 —— 一個依賴 CDN 的頁面
    存到硬碟三年後打開就是壞的。
    """
    import re
    html = _html()
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?:', html)
    assert "<script" not in html          # 完全不需要 JS
    assert "<link" not in html


def test_html_has_no_forbidden_phrases():
    """與 index.html 共用同一份禁語清單（render.FORBIDDEN_PHRASES）。"""
    from src import render
    html = _html(
        fires=[baserate.Fire("2330.TW", "TW", "a", "2026-09-10", 1000.0)],
        rates=[_rate("a")],
        bands=[{"symbol": "2330.TW", "price": 1000.0, "low": 900.0, "high": 1100.0}],
    )
    for phrase in render.FORBIDDEN_PHRASES:
        assert phrase not in html, f"產出含禁語：{phrase}"


def test_html_is_well_formed():
    """所有標籤成對關閉。破損的 HTML 在不同瀏覽器會長得不一樣。"""
    from html.parser import HTMLParser

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "source", "track", "wbr"}

    class Checker(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.errors = []

        def handle_starttag(self, tag, attrs):
            if tag not in VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if not self.stack:
                self.errors.append(f"多餘的 </{tag}>")
            elif self.stack[-1] != tag:
                self.errors.append(f"預期 </{self.stack[-1]}> 卻遇到 </{tag}>")
            else:
                self.stack.pop()

    checker = Checker()
    checker.feed(_html(bands=[
        {"symbol": "2330.TW", "price": 1000.0, "low": 900.0, "high": 1100.0}]))
    assert checker.errors == []
    assert checker.stack == []


def test_html_accuracy_scorecard_comes_first():
    html = _html()
    assert html.index("準度計分卡") < html.index("今日觸發的訊號")


def test_html_states_edge_emptiness_explicitly():
    assert "沒有任何訊號" in _html()


def test_html_labels_non_edge_groups():
    html = _html(
        fires=[baserate.Fire("2317.TW", "TW", "b", "2026-09-10", 200.0)],
        rates=[_rate("b", verdict="no_edge")],
    )
    assert "2317.TW" in html
    assert "無統計優勢" in html


def test_html_bands_are_not_called_target_prices():
    html = _html(bands=[
        {"symbol": "2330.TW", "price": 1000.0, "low": 900.0, "high": 1100.0}])
    assert "波動區間" in html
    assert "目標價" not in html


# --- 無障礙：極性不得只靠顏色（dataviz 規則）--------------------------


def test_polarity_is_encoded_structurally_not_only_by_colour():
    """前後半超額的正負，必須靠位置與正負號傳達，不能只靠紅綠。

    實測本專案配色 #10893e（綠）↔ #d92b3a（紅）的 deutan ΔE 只有 5.0，
    低於 6–8 的最低容許帶 —— 色盲讀者分不出來。紅漲綠跌是台股慣例，
    在單一頁面裡改成相反慣例會更誤導，所以顏色保留、但降為冗余資訊：
    方向由 data-sign 與正負號各自獨立表達。
    """
    html = _html(
        fires=[baserate.Fire("2454.TW", "TW", "c", "2026-09-10", 900.0)],
        rates=[_rate("c", verdict="unstable", first_half_median=-0.014,
                     second_half_median=0.053)],
    )
    assert 'data-sign="neg"' in html
    assert 'data-sign="pos"' in html
    assert "-1.4%" in html          # 負號存在於文字
    assert "+5.3%" in html          # 正號也明確標出


def test_hit_rate_bar_does_not_use_the_red_green_pair():
    """勝率對照不需要極性色。單一色長條 + 中性基準刻度即可，
    「低於基準」靠位置判讀。"""
    html = _html(
        fires=[baserate.Fire("2317.TW", "TW", "b", "2026-09-10", 200.0)],
        rates=[_rate("b", verdict="no_edge", hit_rate=0.41,
                     baseline_hit_rate=0.47)],
    )
    import re
    bar = re.search(r'<svg[^>]*class="hit-bar".*?</svg>', html, re.S)
    assert bar, "找不到勝率長條"
    assert "#d92b3a" not in bar.group(0)
    assert "#10893e" not in bar.group(0)


def test_light_palette_is_defined_on_bare_root():
    """深色只在 media query 裡定義會讓淺色模式沒有值可用。"""
    html = _html()
    assert ":root" in html
    assert "--bg:" in html


# --- 尺寸與輸出 -------------------------------------------------------


def test_html_stays_within_its_own_budget(tmp_path):
    """HTML 比 markdown 大是預期的（內嵌樣式 + 155 個區間條圖），
    但仍要有上限。貼外部工具用的是 markdown，不是這個。"""
    fires = [
        baserate.Fire(f"S{i}.TW", "TW", f"sig{i % 10}", "2026-09-10", 100.0)
        for i in range(600)
    ]
    rates = [_rate(f"sig{k}", verdict="no_edge") for k in range(10)]
    bands = [
        {"symbol": f"S{i}.TW", "price": 100.0, "low": 90.0, "high": 110.0}
        for i in range(500)
    ]
    data = digest.build(_payload(fires=fires, rates=rates, bands=bands))
    paths = digest.write(data, tmp_path)
    html_path = next(p for p in paths if p.suffix == ".html")
    assert html_path.stat().st_size < digest.HTML_SIZE_BUDGET_BYTES


def test_write_produces_three_files(tmp_path):
    """markdown 與 JSON 的角色不變，HTML 是新增的第三個。"""
    data = digest.build(_payload())
    paths = digest.write(data, tmp_path)
    assert {p.suffix for p in paths} == {".md", ".json", ".html"}
    for p in paths:
        assert p.exists()

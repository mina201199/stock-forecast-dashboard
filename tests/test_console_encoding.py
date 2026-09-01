"""主控台輸出的可編碼性。

實測：`python run.py --accuracy` 在 Windows cp950 主控台因為輸出
U+26A0（⚠）而 UnicodeEncodeError 崩潰。HTML 是 UTF-8 沒問題，
但走 print() 的路徑必須能在使用者的實際主控台編碼下存活。
"""
import ast
from pathlib import Path

import pytest

from src import accuracy

# Windows 繁中預設主控台編碼
CONSOLE_ENCODING = "cp950"


def _unencodable(text: str) -> set[str]:
    bad = set()
    for ch in text:
        try:
            ch.encode(CONSOLE_ENCODING)
        except UnicodeEncodeError:
            bad.add(ch)
    return bad


def test_the_original_crash_character_really_is_unencodable():
    """先證明這個問題是真的。"""
    assert _unencodable("⚠") == {"⚠"}
    with pytest.raises(UnicodeEncodeError):
        "⚠".encode(CONSOLE_ENCODING)


def test_traditional_chinese_survives_cp950():
    """cp950 本來就能表示繁體中文，問題只在少數符號。"""
    assert _unencodable("方向命中率　與隨機無法區分　預測看多組") == set()


def _accuracy_summary_lines():
    frame_rows = [
        {"symbol": f"S{i}", "market": "TW", "upside_pct": 0.05 * (1 if i % 2 else -1),
         "actual_excess": 0.01 * (1 if i % 3 else -1),
         "matured": i % 2 == 0, "elapsed_days": 3}
        for i in range(20)
    ]
    import pandas as pd
    scored = accuracy.Scored(pd.DataFrame(frame_rows), 20)
    return accuracy.format_summary(accuracy.summarise(scored))


def test_accuracy_report_lines_are_console_safe():
    """這是原始崩潰的直接迴歸測試。"""
    for line in _accuracy_summary_lines():
        assert _unencodable(line) == set(), f"無法在主控台輸出：{line!r}"


def test_accuracy_report_still_warns_about_immature_predictions():
    """換掉符號之後，警告本身不能消失。"""
    assert any("尚未走滿預測期間" in line for line in _accuracy_summary_lines())


def test_accuracy_report_still_flags_insignificance():
    assert any("與隨機無法區分" in line or "統計顯著" in line
               for line in _accuracy_summary_lines())


def test_run_py_reconfigures_stdout_error_handling():
    """安全網：即使漏了某個字元也不該讓程式崩潰。"""
    tree = ast.parse(Path("run.py").read_text(encoding="utf-8"))
    found = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reconfigure"
        and any(kw.arg == "errors" for kw in node.keywords)
        for node in ast.walk(tree)
    )
    assert found, "run.py 應在啟動時設定 stdout/stderr 的 errors 處理"

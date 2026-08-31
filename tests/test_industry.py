"""產業代碼對照。

TWSE t187ap03_L 的「產業別」是代碼（"01"），t187ap05_L 的「產業別」是
中文（"水泥工業"），兩者皆以公司代號為鍵。把上市的兩份資料 join 起來
即可得到權威對照表，再套用到只給代碼的上櫃資料 ——
比硬寫一張可能過時的對照表安全。
"""
from src.industry import build_code_map, resolve


def test_derives_code_to_name_from_listed_companies():
    profiles = [{"code": "1101", "industry_code": "01"},
                {"code": "2330", "industry_code": "24"}]
    revenue = [{"code": "1101", "industry": "水泥工業"},
               {"code": "2330", "industry": "半導體業"}]
    assert build_code_map(profiles, revenue) == {"01": "水泥工業", "24": "半導體業"}


def test_majority_wins_when_a_code_maps_to_several_names():
    """個別公司分類可能有雜訊，取多數決而非最後一筆。"""
    profiles = [{"code": f"a{i}", "industry_code": "24"} for i in range(4)]
    revenue = [{"code": "a0", "industry": "半導體業"}, {"code": "a1", "industry": "半導體業"},
               {"code": "a2", "industry": "半導體業"}, {"code": "a3", "industry": "其他業"}]
    assert build_code_map(profiles, revenue)["24"] == "半導體業"


def test_ignores_entries_missing_either_side():
    profiles = [{"code": "1101", "industry_code": "01"}, {"code": "9999", "industry_code": None}]
    revenue = [{"code": "1101", "industry": "水泥工業"}, {"code": "8888", "industry": "食品工業"}]
    assert build_code_map(profiles, revenue) == {"01": "水泥工業"}


def test_resolve_prefers_direct_text_over_code_lookup():
    """上市有中文可直接用；上櫃只有代碼才需要查表。"""
    code_map = {"24": "半導體業"}
    assert resolve("電子零組件業", "24", code_map) == "電子零組件業"
    assert resolve(None, "24", code_map) == "半導體業"
    assert resolve(None, "99", code_map) is None
    assert resolve(None, None, code_map) is None


def test_resolve_treats_blank_text_as_absent():
    assert resolve("  ", "24", {"24": "半導體業"}) == "半導體業"

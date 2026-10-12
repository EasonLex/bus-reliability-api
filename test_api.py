"""
端到端測試。跑法：

    MYSQL_HOST=127.0.0.1 MYSQL_PORT=3399 MYSQL_USER=bus_ro \
    MYSQL_PASSWORD=testpw MYSQL_DATABASE=bus python -m pytest test_api.py -q

前三個測項對應三種「錯了也不會報錯」的情況，是這支服務最該被守住的地方。
"""
import math
import os

from fastapi.testclient import TestClient

import main

c = TestClient(main.app)


def test_meta_and_routes():
    m = c.get("/api/meta").json()
    assert {x["value"] for x in m["day_types"]} == {"weekday"}
    assert [x["value"] for x in m["bands"]] == ["am_peak"]
    # 使用者要知道「早尖峰」是幾點到幾點，不然選了也不知道選的是什麼
    assert m["bands"][0]["label"] == "早尖峰（06:00–10:00）"

    r = c.get("/api/routes").json()
    assert r["total"] == 4
    tpe = [x for x in r["routes"] if x["sub_route_uid"] == "TPE11881"][0]
    assert tpe["route_name"] == "287"
    assert tpe["n_stops"] == 17          # 18 個站序編號，缺 14
    assert tpe["first_stop"] == "陽明山"
    assert tpe["last_stop"] == "劍潭站"  # 第 17 站（seq 18）
    # 使用者認得的是號碼，標籤要以它開頭
    assert tpe["label"].startswith("287　")


def test_search_by_route_number_ranks_exact_first():
    """
    ★ 打「287」時，287 必須排在 287區間車 和 2871 前面。

    純子字串比對會讓這三條隨機排列，使用者得在一堆長得很像的選項裡
    挑出他要的那個——而他多半直接選第一個，然後看到別條路線的時間。
    """
    d = c.get("/api/routes", params={"q": "287"}).json()
    names = [r["route_name"] for r in d["routes"]]
    assert names[0] == "287", f"287 沒有排第一，實際順序：{names}"
    assert set(names) == {"287", "287區間車", "2871"}

    # 中文路線名也要找得到
    assert [r["route_name"] for r in
            c.get("/api/routes", params={"q": "藍22"}).json()["routes"]] == ["藍22"]

    # 舊的查法不能壞掉：代碼與站名
    assert c.get("/api/routes", params={"q": "TPE11881"}).json()["total"] == 1
    assert c.get("/api/routes", params={"q": "陽明山"}).json()["total"] >= 1


def test_route_list_is_ordered_like_a_human_expects():
    """
    ★ 路線清單要按號碼的「數值」排，不是字典序。

    字典序會排成 1, 12, 2, 287, 3——使用者捲到一半就放棄了。
    數字開頭的排前面，藍22、幹線這類排後面。

    這支測試順便守住一個引擎差異：REGEXP_SUBSTR 沒配到時
    MySQL 8 回傳 NULL、MariaDB 回傳空字串。用「NULL 判斷」寫排序的話，
    在本機（MariaDB）跟正式環境（MySQL 8.4）會排出不同結果，
    而本機測試永遠是綠的。CI 跑的是真的 mysql:8.4，所以這支測試
    是在正式環境的引擎上驗的。
    """
    names = [r["route_name"] for r in c.get("/api/routes").json()["routes"]]
    assert names == ["287", "287區間車", "2871", "藍22"], names


def test_gap_in_seq_is_not_skipped():
    """
    ★ seq 13 的下一站是 15。

    用 b.seq = a.seq + 1 接的話，13→15 這一段會整個消失：
    段數變少、總時間偏低，而且完全不會報錯。
    """
    d = c.get("/api/travel-time", params=dict(
        sub_route_uid="TPE11881", direction=0, from_seq=12, to_seq=16,
        day_type="weekday", band="am_peak")).json()
    pairs = [(s["from_seq"], s["to_seq"]) for s in d["segments"]]
    assert pairs == [(12, 13), (13, 15), (15, 16)]
    assert d["coverage"]["n_segments"] == 3


def test_missing_baseline_is_reported_not_dropped():
    """
    ★ seq 8→9 沒有基準列。

    INNER JOIN 會讓這一列消失，總時間少一段而看起來正常。
    這裡要求它留下來、標成無資料，而且 complete 必須是 False。
    """
    d = c.get("/api/travel-time", params=dict(
        sub_route_uid="TPE11881", direction=0, from_seq=7, to_seq=10)).json()
    seg = {s["from_seq"]: s for s in d["segments"]}
    assert set(seg) == {7, 8, 9}
    assert seg[8]["has_data"] is False and seg[8]["p50_sec"] is None
    assert d["coverage"]["n_missing"] == 1
    assert d["coverage"]["complete"] is False
    assert d["coverage"]["n_with_data"] == 2


def test_weak_p90_is_flagged():
    d = c.get("/api/travel-time", params=dict(
        sub_route_uid="TPE11881", direction=0, from_seq=10, to_seq=12)).json()
    assert d["coverage"]["n_weak_p90"] == 1
    assert [s["p90_reliable"] for s in d["segments"]] == [False, True]


def test_composition_beats_naive_sum():
    """
    ★ 分位數不可加。

    Σp90 必須嚴格大於 Σmean + 1.28·√Σsd²，而且合成後的不可靠指數
    要低於任何單一路段——段數越多整趟越好預測。
    """
    d = c.get("/api/travel-time", params=dict(
        sub_route_uid="TPE11881", direction=0, from_seq=1, to_seq=8)).json()
    T, segs = d["totals"], d["segments"]
    assert d["coverage"]["complete"] is True

    exp_mean = sum(s["mean_sec"] for s in segs)
    exp_p90 = exp_mean + 1.2816 * math.sqrt(sum(s["sd_sec"] ** 2 for s in segs))
    assert abs(T["mean_sec"] - exp_mean) < 0.5
    assert abs(T["composed_p90_sec"] - exp_p90) < 0.5

    assert T["naive_p90_sum_sec"] > T["composed_p90_sec"]
    assert T["overestimate_pct"] > 0
    assert T["unreliability"] < min(s["unreliability"] for s in segs)


def test_bad_input():
    assert c.get("/api/travel-time", params=dict(
        sub_route_uid="TPE11881", direction=0, from_seq=9, to_seq=9)).status_code == 400
    assert c.get("/api/travel-time", params=dict(
        sub_route_uid="NOPE", direction=0, from_seq=1, to_seq=2)).status_code == 404
    assert c.get("/api/routes/NOPE/0/stops").status_code == 404


def test_every_band_has_name_and_hours():
    """
    BAND_NAME、BAND_HOURS、BAND_ORDER 是三份清單，少一個鍵不會報錯，
    只會讓畫面上某個時段變回英文代碼、或沒有時間區間。
    """
    assert set(main.BAND_NAME) == set(main.BAND_HOURS) == set(main.BAND_ORDER)
    for b in main.BAND_ORDER:
        assert main.BAND_HOURS[b] in main.BAND_LABEL[b]

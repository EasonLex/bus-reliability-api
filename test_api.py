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

    r = c.get("/api/routes").json()
    assert r["total"] == 2
    tpe = [x for x in r["routes"] if x["sub_route_uid"] == "TPE11881"][0]
    assert tpe["n_stops"] == 17          # 18 個站序編號，缺 14
    assert tpe["first_stop"] == "陽明山"
    assert tpe["last_stop"] == "劍潭站"  # 第 17 站（seq 18）


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

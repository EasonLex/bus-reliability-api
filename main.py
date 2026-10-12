"""
公車服務可靠度 —— 查詢 API

    「TPE11881 這條線，第 4 站到第 12 站，平日早上尖峰要多久？」

執行（本機）：
    uvicorn main:app --reload --port 8000

端點：
    GET /api/meta                        城市、日型、時段的可選值
    GET /api/routes?city=&q=             路線方向清單（含起訖站名）
    GET /api/routes/{uid}/{dir}/stops    某路線方向的站序
    GET /api/travel-time?...             ★ 主查詢：多路段行車時間
    GET /healthz
"""

import contextlib
import math
import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db

# ---------------------------------------------------------------------
# 時段標籤
#
# ★ 這裡只是「給人看的名字」，時段的實際定義在 sql/baseline_build.sql 的
#   CASE WHEN 裡。兩邊改了一邊沒改另一邊，畫面上會出現對不上的說明，
#   而且不會有任何錯誤訊息。
#
#   下拉選單的選項不是寫死在這裡，是啟動時從資料庫問出來的（見 /api/meta）——
#   資料庫裡沒有的時段，畫面上就不該出現。
# ---------------------------------------------------------------------
BAND_NAME = {
    "am_peak": "早尖峰",
    "midday":  "離峰",              # 時間區間已經說明是白天，不用再加（白天）
    "pm_peak": "晚尖峰",
    "evening": "晚間",
    "night":   "深夜",
}
# 時間區間，照抄 baseline_build.sql 的 CASE WHEN（台北時間，以起站的到站時刻判定）：
#   hr BETWEEN  6 AND  9 → am_peak      hr BETWEEN 16 AND 19 → pm_peak
#   hr BETWEEN 10 AND 15 → midday       hr BETWEEN 20 AND 22 → evening
#   其餘（23–5 點）      → night
# 寫成「起–迄」的半開區間：06:00–10:00 代表 6:00 到 9:59。
BAND_HOURS = {
    "am_peak": "06:00–10:00",
    "midday":  "10:00–16:00",
    "pm_peak": "16:00–20:00",
    "evening": "20:00–23:00",
    "night":   "23:00–06:00",
}
BAND_LABEL = {b: f"{BAND_NAME[b]}（{BAND_HOURS[b]}）" for b in BAND_NAME}
BAND_ORDER = ["am_peak", "midday", "pm_peak", "evening", "night"]

DAYTYPE_LABEL = {"weekday": "平日", "weekend": "假日"}
CITY_LABEL = {"Taipei": "臺北市", "NewTaipei": "新北市"}

# 常態分布 90 百分位對應的 z 值。合成用。
Z90 = 1.2816

@contextlib.asynccontextmanager
async def lifespan(_app):
    # 連線在啟動時就建好。要因為密碼錯／MySQL 沒起來而失敗，
    # 就在這裡失敗——不要等第一個使用者按下查詢才發現。
    db.init_pool()
    yield


app = FastAPI(title="公車服務可靠度查詢", version="1.0", lifespan=lifespan)


# =====================================================================
# 中繼資料
# =====================================================================
_meta_cache = {"at": 0.0, "data": None}


@app.get("/api/meta")
def meta():
    """
    可選值一律從資料庫問，不寫死。

    寫死的話，基準表哪天多了一個時段（或某個城市整個沒資料），
    畫面上還是會出現那個選項，使用者選了得到空結果，
    看起來像「這條路線沒資料」而不是「這個選項根本不該存在」。
    """
    if _meta_cache["data"] and time.time() - _meta_cache["at"] < 600:
        return _meta_cache["data"]

    with db.cursor() as cur:
        cur.execute("SELECT DISTINCT city FROM baseline_segment")
        cities = sorted(r["city"] for r in cur.fetchall())
        cur.execute("SELECT DISTINCT band FROM baseline_segment")
        bands = [r["band"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT day_type FROM baseline_segment")
        day_types = [r["day_type"] for r in cur.fetchall()]
        cur.execute("""SELECT MIN(date_from) AS d0, MAX(date_to) AS d1,
                              MAX(n_days) AS n_days, MAX(baseline_version) AS ver,
                              MAX(rule_version) AS rule, MAX(computed_at) AS computed_at
                       FROM baseline_segment""")
        cover = cur.fetchone()

    data = {
        "cities": [{"value": c, "label": CITY_LABEL.get(c, c)} for c in cities],
        "day_types": [{"value": d, "label": DAYTYPE_LABEL.get(d, d)}
                      for d in sorted(day_types)],
        # 依 BAND_ORDER 排；資料庫裡有但清單沒有的排在最後，不要默默消失
        "bands": [{"value": b, "label": BAND_LABEL.get(b, b)}
                  for b in sorted(bands, key=lambda x: (BAND_ORDER.index(x)
                                                        if x in BAND_ORDER else 99, x))],
        "baseline": {
            "date_from": str(cover["d0"]), "date_to": str(cover["d1"]),
            "n_days": cover["n_days"],
            "baseline_version": cover["ver"], "rule_version": cover["rule"],
            "computed_at": str(cover["computed_at"]),
        },
    }
    _meta_cache.update(at=time.time(), data=data)
    return data


# =====================================================================
# 路線清單
# =====================================================================
_routes_cache = {"at": 0.0, "data": None}

# 起訖站名用 ROW_NUMBER 取，不用 GROUP_CONCAT + SUBSTRING_INDEX。
# GROUP_CONCAT 預設上限 1024 bytes，中文一個字 3 bytes，
# 一條 90 站的路線大約 1,300 bytes ——會被「靜靜地截斷」，
# 於是最長的那幾條路線的終點站名會是錯的，而且完全不會報錯。
SQL_ROUTES = """
SELECT city, route_name, sub_route_uid, direction,
       COUNT(*)                                        AS n_stops,
       MIN(CASE WHEN rn_asc  = 1 THEN stop_name END)   AS first_stop,
       MIN(CASE WHEN rn_desc = 1 THEN stop_name END)   AS last_stop
FROM (
  SELECT city, route_name, sub_route_uid, direction, stop_name,
         ROW_NUMBER() OVER (PARTITION BY sub_route_uid, direction ORDER BY seq)      AS rn_asc,
         ROW_NUMBER() OVER (PARTITION BY sub_route_uid, direction ORDER BY seq DESC) AS rn_desc
  FROM route_stop
) t
GROUP BY city, route_name, sub_route_uid, direction
ORDER BY
  -- 「287」要排在「30」後面而不是「3」和「4」中間——字典序會給出
  -- 1, 12, 2, 287, 3 這種順序，使用者捲到一半就放棄了。
  --
  -- 第一鍵：數字開頭的排前面（藍22、幹線這類排後面）。
  --   寫成 `REGEXP '^[0-9]' = 0` 而不是判斷 REGEXP_SUBSTR 是不是 NULL，
  --   因為那兩件事在 MySQL 和 MariaDB 上答案不一樣：沒配到時
  --   MySQL 8 回傳 NULL、MariaDB 回傳空字串，而 CAST('' AS UNSIGNED) 是 0，
  --   於是同一句 SQL 在兩個引擎上排出完全不同的結果。
  --   `REGEXP` 回傳 1/0 兩邊一致。
  route_name REGEXP '^[0-9]' = 0,
  -- 第二鍵：開頭那串數字的數值
  CAST(REGEXP_SUBSTR(route_name, '^[0-9]+') AS UNSIGNED),
  route_name, sub_route_uid, direction
"""


def _all_routes():
    if _routes_cache["data"] and time.time() - _routes_cache["at"] < 600:
        return _routes_cache["data"]
    with db.cursor() as cur:
        cur.execute(SQL_ROUTES)
        rows = cur.fetchall()
    for r in rows:
        r["direction"] = int(r["direction"])
        # 使用者認得的是「287」，不是「TPE11881」。
        # uid 留在標籤尾巴：使用者會忽略它，但出問題時要查得回來。
        r["label"] = (f"{r['route_name']}　{r['first_stop']} → {r['last_stop']}"
                      f"　·　{r['sub_route_uid']}")
    _routes_cache.update(at=time.time(), data=rows)
    return rows


@app.get("/api/routes")
def routes(city: Optional[str] = None, q: Optional[str] = None,
           limit: int = Query(200, le=2000)):
    """
    2,570 筆整包在記憶體裡篩。

    這是刻意的：全部撈出來也只有幾百 KB，一天最多變一次。
    丟給資料庫做 LIKE '%關鍵字%' 反而每次打字都要掃一次表，
    而且前綴萬用字元本來就用不到索引。
    """
    rows = _all_routes()
    if city:
        rows = [r for r in rows if r["city"] == city]

    if q:
        k = q.strip().upper()

        def rank(r):
            """
            打「287」時，287 要排在 287區間車 和 「2870」之前。

            純粹用子字串比對的話，287 會跟 2871、1287 混在一起隨機出現，
            使用者得在一堆長得很像的選項裡找他要的那一個——
            而他很可能就直接選了第一個。
            排序讓「完全相同」永遠在最前面。
            """
            name = (r["route_name"] or "").upper()
            if name == k:
                return 0                      # 完全相同
            if name.startswith(k):
                return 1                      # 287區間車、287副
            if k in name:
                return 2                      # 名稱裡有
            if k in r["sub_route_uid"].upper():
                return 3                      # 用代碼找的
            return 4                          # 只有站名對上

        rows = [r for r in rows
                if k in (r["route_name"] or "").upper()
                or k in r["sub_route_uid"].upper()
                or k in (r["first_stop"] or "")
                or k in (r["last_stop"] or "")]
        # stable sort：同一個 rank 之內保持原本的路線號碼順序
        rows = sorted(rows, key=rank)

    return {"total": len(rows), "routes": rows[:limit]}


@app.get("/api/routes/{sub_route_uid}/{direction}/stops")
def stops(sub_route_uid: str, direction: int):
    with db.cursor() as cur:
        cur.execute(
            """SELECT seq, station_id, stop_name
               FROM route_stop
               WHERE sub_route_uid = %s AND direction = %s
               ORDER BY seq""",
            (sub_route_uid, direction))
        rows = cur.fetchall()
    if not rows:
        raise HTTPException(404, f"查無 {sub_route_uid} 方向 {direction}")
    return {"sub_route_uid": sub_route_uid, "direction": direction,
            "n_stops": len(rows), "stops": rows}


# =====================================================================
# 主查詢
# =====================================================================
# 相鄰兩站用 LEAD() 取，不是 b.seq = a.seq + 1。
#
# 站序有跳號——實際資料裡就有路線缺 seq 14。用 +1 去接的話，
# 那一段會整段消失，總時間少算一段，而且結果看起來完全正常。
# 這正是這個專題最想避免的那種錯：不會報錯，只是答案是錯的。
#
# LEFT JOIN 不是 JOIN：某個站牌對沒有基準時，那一列要留下來標成「無資料」。
# 用 INNER JOIN 的話缺的段會被默默跳過，總時間偏低，一樣不會有任何徵兆。
SQL_SEGMENTS = """
WITH pairs AS (
  SELECT city, seq, station_id, stop_name,
         LEAD(seq)        OVER w AS to_seq,
         LEAD(station_id) OVER w AS to_station_id,
         LEAD(stop_name)  OVER w AS to_stop_name
  FROM route_stop
  WHERE sub_route_uid = %s AND direction = %s
  WINDOW w AS (ORDER BY seq)
)
SELECT p.seq            AS from_seq,
       p.station_id     AS from_station_id,
       p.stop_name      AS from_stop_name,
       p.to_seq, p.to_station_id, p.to_stop_name,
       s.p50_sec, s.p90_sec, s.mean_sec, s.sd_sec,
       s.n, s.n_route, s.level, s.p90_reliable, s.unreliability
FROM pairs p
LEFT JOIN baseline_segment s
       ON s.city            = p.city
      AND s.from_station_id = p.station_id
      AND s.to_station_id   = p.to_station_id
      AND s.day_type        = %s
      AND s.band            = %s
WHERE p.seq >= %s AND p.seq < %s AND p.to_seq IS NOT NULL
ORDER BY p.seq
"""

LEVEL_LABEL = {
    "band":    "時段",
    "daytype": "整個平日／假日",
    "pair":    "不分日型時段",
}


def _fmt_mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60} 分 {sec % 60:02d} 秒" if sec >= 60 else f"{sec} 秒"


@app.get("/api/travel-time")
def travel_time(
    sub_route_uid: str,
    direction: int,
    from_seq: int,
    to_seq: int,
    day_type: str = "weekday",
    band: str = "am_peak",
):
    if to_seq <= from_seq:
        raise HTTPException(400, "迄站的站序必須大於起站")
    if day_type not in DAYTYPE_LABEL:
        raise HTTPException(400, f"day_type 只能是 {list(DAYTYPE_LABEL)}")

    with db.cursor() as cur:
        cur.execute(SQL_SEGMENTS,
                    (sub_route_uid, direction, day_type, band, from_seq, to_seq))
        rows = cur.fetchall()

    if not rows:
        raise HTTPException(404, "這個區間查不到任何路段，請確認路線與站序")

    segments, sum_mean, sum_var, sum_p50, sum_p90 = [], 0.0, 0.0, 0.0, 0.0
    n_missing = n_weak = 0

    for r in rows:
        has = r["p50_sec"] is not None
        if has:
            mean, sd = float(r["mean_sec"]), float(r["sd_sec"])
            sum_mean += mean
            sum_var  += sd * sd          # ★ 變異數可加，標準差不可加
            sum_p50  += float(r["p50_sec"])
            sum_p90  += float(r["p90_sec"])
            if not r["p90_reliable"]:
                n_weak += 1
        else:
            n_missing += 1

        segments.append({
            "from_seq": r["from_seq"], "from_stop_name": r["from_stop_name"],
            "to_seq": r["to_seq"],     "to_stop_name": r["to_stop_name"],
            "has_data": has,
            "p50_sec": int(r["p50_sec"]) if has else None,
            "p90_sec": int(r["p90_sec"]) if has else None,
            "mean_sec": float(r["mean_sec"]) if has else None,
            "sd_sec": float(r["sd_sec"]) if has else None,
            "n": int(r["n"]) if has else None,
            "n_route": int(r["n_route"]) if has else None,
            "level": r["level"] if has else None,
            "level_label": LEVEL_LABEL.get(r["level"], r["level"]) if has else None,
            "p90_reliable": bool(r["p90_reliable"]) if has else None,
            "unreliability": float(r["unreliability"]) if has else None,
        })

    n_seg = len(segments)
    n_have = n_seg - n_missing

    # -----------------------------------------------------------------
    # 合成
    #
    #   分位數不可加。把八段的 p90 直接相加，等於假設八段同時遇到最糟的情況，
    #   那個機率是 0.1 的八次方。
    #
    #   可加的是平均與變異數，所以走：
    #       Σmean + 1.28 · √(Σsd²)
    #   CLT 在「和」上成立，段數越多這個估計越穩。
    # -----------------------------------------------------------------
    composed_p90 = sum_mean + Z90 * math.sqrt(sum_var) if n_have else None
    naive_p90 = sum_p90 if n_have else None
    over_pct = ((naive_p90 - composed_p90) / composed_p90 * 100
                if composed_p90 else None)

    return {
        "query": {
            "sub_route_uid": sub_route_uid, "direction": direction,
            "from_seq": from_seq, "to_seq": to_seq,
            "day_type": day_type, "day_type_label": DAYTYPE_LABEL.get(day_type, day_type),
            "band": band, "band_label": BAND_LABEL.get(band, band),
        },
        "coverage": {
            "n_segments": n_seg,
            "n_with_data": n_have,
            "n_missing": n_missing,
            # 有缺段時總時間一定偏低。這個旗標讓畫面有辦法講實話，
            # 而不是端出一個看起來很正常、實際上少算了幾段的數字。
            "complete": n_missing == 0,
            "n_weak_p90": n_weak,
        },
        "totals": None if not n_have else {
            "mean_sec": round(sum_mean, 1),
            "mean_text": _fmt_mmss(sum_mean),
            "p50_sum_sec": round(sum_p50, 1),
            "composed_p90_sec": round(composed_p90, 1),
            "composed_p90_text": _fmt_mmss(composed_p90),
            "naive_p90_sum_sec": round(naive_p90, 1),
            "naive_p90_text": _fmt_mmss(naive_p90),
            "overestimate_pct": round(over_pct, 1),
            "overestimate_sec": round(naive_p90 - composed_p90, 1),
            # 整趟的不可靠指數。段數越多這個值越接近 1——
            # 變異數相加，但不等比例放大。
            "unreliability": round(composed_p90 / sum_mean, 2) if sum_mean else None,
        },
        "segments": segments,
    }


@app.get("/healthz")
def healthz():
    with db.cursor() as cur:
        cur.execute("SELECT 1 AS ok")
        cur.fetchone()
    return {"ok": True}


# 靜態頁面掛在最後，讓 /api/* 先比對到
STATIC = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))

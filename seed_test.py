"""
測試用假資料。刻意做出三種「會讓錯誤程式看起來正常」的情況：

  1. 站序跳號        seq 跳過 14  → 用 b.seq = a.seq + 1 接的話會少一段
  2. 缺基準          seq 8→9 那對站牌沒有基準列 → INNER JOIN 會默默跳過
  3. 樣本不足        seq 10→11 的 n < 100，p90_reliable = 0
  4. 路線號碼撞名    287 / 287區間車 / 2871 三條都含「287」
                     → 排序沒做的話，使用者打 287 會在三個長得很像的
                       選項裡隨便挑一個，而他多半直接選第一個

灌哪個資料庫由環境變數決定（跟 API 用同一組 MYSQL_*），
不要在這裡寫死主機或 socket 路徑——寫死的話 CI 上就得再改一次。

    MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root \
    MYSQL_PASSWORD=... MYSQL_DATABASE=bus python seed_test.py
"""
import random

import db

random.seed(11881)
conn = db._connect()          # 需要寫入權限，不是 API 用的唯讀帳號
cur = conn.cursor()
cur.execute("DELETE FROM route_stop")
cur.execute("DELETE FROM baseline_segment")

NAMES = ["陽明山", "山仔后", "文化大學", "永明派出所", "榮總", "綜合市場",
         "捷運石牌站", "石牌國小", "明德站", "德行東路", "天母圓環", "士東市場",
         "芝山岩", "福林橋", "士林站", "銘傳大學", "劍潭站", "圓山站"]

# 站序：1..13, 15..18（缺 14）
seqs = [s for s in range(1, 19) if s != 14]
stops = []
for i, s in enumerate(seqs):
    stops.append((s, f"S{1000+s}", NAMES[i % len(NAMES)]))

INS_STOP = ("INSERT INTO route_stop (city, route_name, sub_route_uid, direction,"
            " seq, station_id, stop_name, boarding, version_id, loaded_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,'2026-08-30',NOW())")

# 主要那條：287，站序有跳號，基準資料都掛在它身上
cur.executemany(INS_STOP, [("Taipei", "287", "TPE11881", 0, s, sid, nm)
                           for s, sid, nm in stops])

# 名稱容易混淆的鄰居。它們沒有基準資料，只用來測搜尋排序。
# 「2871」不是真的路線，是刻意造出來咬住「純子字串比對」的：
# 打 287 的人絕對不想看到它排在 287 前面。
for city, name, uid, dirn, n_stop, base in [
    ("Taipei",    "287區間車", "TPE11882", 0, 5, 3000),
    ("Taipei",    "2871",      "TPE11883", 0, 4, 4000),
    ("NewTaipei", "藍22",      "NWT10001", 1, 4, 2000),
]:
    cur.executemany(INS_STOP, [
        (city, name, uid, dirn, i + 1, f"X{base+i}", NAMES[i])
        for i in range(n_stop)])

rows = []
for (fs, fid, _), (ts, tid, _) in zip(stops, stops[1:]):
    if fs == 8:                       # ← 刻意不建這一段的基準
        continue
    mean = random.uniform(45, 160)
    sd = mean * random.uniform(0.18, 0.42)
    p50 = mean * 0.94
    p90 = mean + 1.2816 * sd
    n = 40 if fs == 10 else random.randint(300, 4000)   # ← 刻意做一段樣本不足
    rows.append(("Taipei", fid, tid, "weekday", "am_peak",
                 "band", n, random.randint(1, 5),
                 int(p50 * 0.8), int(p50), int(p50 * 1.15), int(p90),
                 round(p90 / p50, 2), 1 if n >= 100 else 0,
                 round(mean, 2), round(sd, 2),
                 "2026-08-04", "2026-08-30", 27, "v2", "b1", "2026-09-01 00:00:00"))

cur.executemany(
    "INSERT INTO baseline_segment (city, from_station_id, to_station_id, day_type,"
    " band, level, n, n_route, p25_sec, p50_sec, p75_sec, p90_sec, unreliability,"
    " p90_reliable, mean_sec, sd_sec, date_from, date_to, n_days, rule_version,"
    " baseline_version, computed_at) VALUES (" + ",".join(["%s"] * 22) + ")", rows)

# db._connect() 用的是 DictCursor，取值要用欄位名——所以這裡一定要 AS
cur.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT sub_route_uid) AS n_route"
            " FROM route_stop")
c = cur.fetchone()
print(f"route_stop {c['n']} 列 / {c['n_route']} 條子路線"
      f" · baseline_segment {len(rows)} 列")
print("287 的站序：", [s for s, _, _ in stops], "（缺 14）")

"""
契約測試：這支 API 對資料庫的假設，還成不成立。

為什麼需要這個
------------------------------------------------------------
  schema 的真相在主專案（TKR102/sql/schema.sql），這個 repo 只是讀它。
  兩個 repo 各自演進，遲早會有一邊改了欄位而另一邊不知道。

  症狀還特別難查：少一欄會是 pymysql 的 Unknown column，還算明顯；
  但型別悄悄改掉（例如 mean_sec 從 DECIMAL 變成 VARCHAR）不會報錯，
  只會讓 float() 在某些值上炸掉，或更糟——算出一個錯的總時間。

  所以這裡不比對 DDL 文字，而是去問 information_schema：
  API 真正會讀、會拿去算的那些欄位，在不在、能不能算。

  這支測試對著正式資料庫跑也應該全綠。
  跟 test_api.py 不同，它不需要任何假資料。
"""
import pytest

import db

# API 會 SELECT 的欄位。少一個就是 Unknown column。
REQUIRED = {
    "route_stop": {
        "city", "sub_route_uid", "direction", "seq", "station_id", "stop_name",
    },
    "baseline_segment": {
        "city", "from_station_id", "to_station_id", "day_type", "band",
        "level", "n", "n_route",
        "p50_sec", "p90_sec", "mean_sec", "sd_sec",
        "unreliability", "p90_reliable",
        "date_from", "date_to", "n_days",
        "rule_version", "baseline_version", "computed_at",
    },
}

# 會被拿去做算術的欄位。型別悄悄變成字串的話，
# 合成出來的總時間會是錯的，而且不一定會有例外。
MUST_BE_NUMERIC = {
    "baseline_segment": {"n", "n_route", "p50_sec", "p90_sec",
                         "mean_sec", "sd_sec", "unreliability", "p90_reliable"},
    "route_stop": {"direction", "seq"},
}
NUMERIC_TYPES = {"tinyint", "smallint", "mediumint", "int", "bigint",
                 "decimal", "float", "double"}


@pytest.fixture(scope="module")
def columns():
    with db.cursor() as cur:
        cur.execute("""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME IN ('route_stop', 'baseline_segment')""")
        rows = cur.fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["TABLE_NAME"], {})[r["COLUMN_NAME"]] = r
    return out


@pytest.mark.parametrize("table", sorted(REQUIRED))
def test_tables_exist(columns, table):
    assert table in columns, f"資料庫裡沒有 {table}——schema 還沒建，或連錯資料庫"


@pytest.mark.parametrize("table", sorted(REQUIRED))
def test_required_columns_present(columns, table):
    missing = REQUIRED[table] - set(columns.get(table, {}))
    assert not missing, (
        f"{table} 少了 API 會讀的欄位：{sorted(missing)}。"
        f"主專案的 schema 改過了，這邊要跟上。")


@pytest.mark.parametrize("table", sorted(MUST_BE_NUMERIC))
def test_arithmetic_columns_are_numeric(columns, table):
    bad = {c: columns[table][c]["DATA_TYPE"]
           for c in MUST_BE_NUMERIC[table]
           if c in columns.get(table, {})
           and columns[table][c]["DATA_TYPE"].lower() not in NUMERIC_TYPES}
    assert not bad, (
        f"{table} 這些欄位要拿去算術，但型別不是數值：{bad}。"
        f"字串相加不會報錯，只會算出錯的答案。")


def test_station_ids_are_same_type(columns):
    """
    JOIN 的兩邊型別要一致。

    route_stop.station_id 跟 baseline_segment.from_station_id 若一邊是
    VARCHAR 一邊是 INT，MySQL 會做隱式轉型：JOIN 仍然會成立，
    但索引用不上，而且 '007' 跟 7 會被判定相等。
    查得出來，只是慢、而且偶爾錯。
    """
    rs = columns["route_stop"]["station_id"]["DATA_TYPE"]
    for c in ("from_station_id", "to_station_id"):
        bs = columns["baseline_segment"][c]["DATA_TYPE"]
        assert rs == bs, f"route_stop.station_id 是 {rs}，baseline_segment.{c} 是 {bs}"

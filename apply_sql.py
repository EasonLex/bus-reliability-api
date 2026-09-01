"""
把一個 .sql 檔灌進 MYSQL_* 指到的資料庫。

    python apply_sql.py sql/test_fixture_schema.sql

為什麼不直接用 mysql CLI
------------------------------------------------------------
  CI 的 runner 上有沒有 mysql client 是「碰運氣」——
  它不在 workflow 裡宣告，是 runner 映像檔碰巧帶著的東西。
  哪天 GitHub 換一版映像檔把它拿掉，這裡就會壞，
  而錯誤訊息會是 `mysql: command not found`，
  看起來跟這個專案完全無關。

  pymysql 是 requirements.txt 裡寫明的相依，本來就一定在。
  少一個沒宣告的相依，就少一個哪天會無聲消失的東西。

  順帶解決另一個坑：`mysql -p < file` 的密碼提示會從被導向的檔案讀，
  於是第一行 SQL 被當成密碼吃掉，而且不會有錯誤訊息。
"""
import pathlib
import re
import sys

import db


def statements(sql_text: str):
    """
    去掉 -- 註解，再依分號切開。

    這是「夠用」而不是「通用」的切法：字串常值裡如果有分號會切錯。
    這個 repo 的 .sql 只有 DDL，沒有那種東西——
    真的需要通用剖析時，該換的是工具，不是把這個函式愈補愈複雜。
    """
    no_comments = re.sub(r"--[^\n]*", "", sql_text)
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def main(path: str):
    sql_text = pathlib.Path(path).read_text(encoding="utf-8")
    stmts = statements(sql_text)

    conn = db._connect()          # 需要 DDL 權限，不是 API 用的唯讀帳號
    try:
        with conn.cursor() as cur:
            for i, s in enumerate(stmts, 1):
                cur.execute(s)
                head = " ".join(s.split())[:60]
                print(f"  [{i}/{len(stmts)}] {head}…")
        conn.commit()
    finally:
        conn.close()
    print(f"{path}：{len(stmts)} 句執行完畢")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python apply_sql.py <檔案.sql>")
    main(sys.argv[1])

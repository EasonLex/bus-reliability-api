"""
MySQL 連線池。

為什麼要池，不是每次請求開一條
------------------------------------------------------------
  MySQL 那邊 --max-connections=32。每個請求開一條、用完關掉，
  在瀏覽器連續點選（選路線會連打三四個請求）時很容易衝上去，
  而且每次都要重新做一次 caching_sha2_password 的握手。

  這裡用最小的做法：一個 LifoQueue 裝著固定數量的連線，
  借出時 ping(reconnect=True)，還回去時不關。
  沒有引進 SQLAlchemy 或 DBUtils——這支服務只有三個查詢，
  多一個相依就多一個要跟著升級的東西。

  Lifo 不是 Fifo：閒置時重複用最近用過的那幾條，
  剩下的連線會自然老死，不會全部一直保持在 open。
"""

import os
import queue
import contextlib

import pymysql

POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))

_pool: "queue.LifoQueue[pymysql.connections.Connection]" = queue.LifoQueue(POOL_SIZE)


def _connect():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "mysql"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        charset="utf8mb4",              # 站名是中文。少了這個查出來全是問號，
                                        # 而且那是「顯示壞了」不是「資料壞了」，很難查
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,                # 這支服務只讀不寫，不需要交易
        connect_timeout=5,
        read_timeout=10,
    )


def init_pool():
    """啟動時就把連線建好。要炸就在啟動時炸，不要等第一個使用者來才炸。"""
    for _ in range(POOL_SIZE):
        _pool.put(_connect())


@contextlib.contextmanager
def cursor():
    try:
        conn = _pool.get_nowait()
    except queue.Empty:
        conn = _connect()           # 尖峰時多開，用完就丟（下面 put 滿了會走 except）

    # MySQL 預設 8 小時沒動就砍連線，池裡的連線放久了會是死的。
    # ping(reconnect=True) 已被 PyMySQL 標為 deprecated，改成自己判斷：
    # ping 不過就換一條新的。
    try:
        conn.ping(reconnect=False)
    except Exception:
        with contextlib.suppress(Exception):
            conn.close()
        conn = _connect()

    try:
        with conn.cursor() as cur:
            yield cur
    except Exception:
        # 連線可能已經壞了，不要放回池裡繼續害下一個人
        with contextlib.suppress(Exception):
            conn.close()
        raise
    else:
        try:
            _pool.put_nowait(conn)
        except queue.Full:
            conn.close()

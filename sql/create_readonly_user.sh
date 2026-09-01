#!/usr/bin/env bash
#
# 建立 API 用的唯讀資料庫帳號，並把憑證寫進 .env.api。
#
#   ./sql/create_readonly_user.sh
#
# 為什麼是腳本，不是一份 .sql 讓人自己填密碼
# ---------------------------------------------------------------------
#   原本這裡是 grant_readonly.sql，裡面寫著
#       IDENTIFIED BY '<把這裡換成一組新密碼>'
#   而這個專題已經有三次「佔位符被原封不動貼上去」的紀錄。
#
#   最糟的是這種佔位符**不會失敗**：資料庫會很開心地把那串中文設成密碼，
#   然後 .env.api 裡填的是別的東西，症狀變成 Access denied，
#   看起來像打錯字，實際上是兩邊各自成功地做了不同的事。
#
#   所以密碼不給人打。這裡產生、這裡寫進 .env.api，
#   兩邊必然一致，因為它們來自同一個變數。
#
# 密碼用 hex 不用 base64
# ---------------------------------------------------------------------
#   base64 會產出 / + = 這些字元。它們會出現在 shell 引號裡、
#   .env 檔裡、連線字串裡、以及某天有人想用 sed 改這個檔案的時候——
#   而 / 和 | 正好是最常見的 sed 分隔字元。
#   hex 只有 0-9a-f，走到哪裡都不需要跳脫。
#   48 個 hex 字元 = 192 bits，夠了。

set -euo pipefail

CONTAINER="${MYSQL_CONTAINER:-mysql}"
DB="${MYSQL_DATABASE:-bus}"
DBUSER="bus_api"

# 只允許從 docker 內部網段連進來。用 '%' 的話這個帳號從任何地方都能連，
# 而 3306 有綁在 Tailscale IP 上。172.16.0.0/12 涵蓋 docker 會用到的網段。
HOSTPAT='172.16.0.0/255.240.0.0'

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env.api"

# 用 if 不用 `[ -e ... ] && { ...; }`。
# 後者在 set -e 底下，當檔案「不存在」時整個 && 運算式回傳 1，
# 腳本會就地結束——條件成立時什麼都不做，條件不成立時直接退出，
# 剛好跟意圖相反，而且不會有任何訊息。
if [ -e "$ENV_FILE" ]; then
  echo "已經有 $ENV_FILE，不覆蓋。"
  echo "要重建的話先把它改名（裡面的密碼是目前 MySQL 上生效的那組）。"
  exit 1
fi

PW="$(openssl rand -hex 24)"

# CREATE ... IF NOT EXISTS 加 ALTER：兩句一起下，這支腳本就可以重跑。
# 只有 CREATE IF NOT EXISTS 的話，帳號已存在時密碼不會更新，
# 但 .env.api 會寫入新產生的那組——於是連不上，而原因完全看不出來。
docker exec -i "$CONTAINER" sh -c \
  "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysql -u root --default-character-set=utf8mb4 $DB" <<SQL
CREATE USER IF NOT EXISTS '$DBUSER'@'$HOSTPAT' IDENTIFIED BY '$PW';
ALTER  USER               '$DBUSER'@'$HOSTPAT' IDENTIFIED BY '$PW';

GRANT SELECT ON \`$DB\`.baseline_segment TO '$DBUSER'@'$HOSTPAT';
GRANT SELECT ON \`$DB\`.route_stop       TO '$DBUSER'@'$HOSTPAT';

FLUSH PRIVILEGES;
SELECT '--- 這個帳號能做的事 ---' AS '';
SHOW GRANTS FOR '$DBUSER'@'$HOSTPAT';
SQL

# 先建檔再限權限，不要先寫內容再 chmod——
# 中間那一瞬間檔案是 644 的。
umask 077
cat > "$ENV_FILE" <<ENV
# 由 sql/create_readonly_user.sh 產生於 $(date -u '+%Y-%m-%d %H:%M:%SZ')
# 這個檔案有密碼，不要進 git（.gitignore 已蓋掉 .env.*）

# 服務名，不是 IP。api 跟 mysql 在同一個 docker network 上。
MYSQL_HOST=mysql
MYSQL_PORT=3306

MYSQL_USER=$DBUSER
MYSQL_PASSWORD=$PW
MYSQL_DATABASE=$DB

# 每個 uvicorn worker 各自持有這麼多條連線。
# 2 workers × 5 = 10，離 MySQL 的 --max-connections=32 還很遠。
DB_POOL_SIZE=5
ENV

echo
echo "已寫入 $ENV_FILE（權限 $(stat -c '%a' "$ENV_FILE" 2>/dev/null || stat -f '%Lp' "$ENV_FILE")）"
echo "密碼沒有印在畫面上，也不需要你手動抄——.env.api 裡的那組就是資料庫上生效的那組。"
echo
echo "驗證：起 api 之後 curl -s localhost:8000/healthz 應該回 {\"ok\":true}"

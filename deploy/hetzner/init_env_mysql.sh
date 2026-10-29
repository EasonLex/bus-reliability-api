#!/usr/bin/env bash
#
# 產生 repo 根目錄的 .env.mysql（MySQL root 密碼）。
#
#   ./deploy/hetzner/init_env_mysql.sh
#
# 跟 sql/create_readonly_user.sh 同一個原則：密碼不給人打。
# 用 hex 不用 base64——不會出現 / + = 這些要跳脫的字元。
#
# ★ 只在第一次啟動 MySQL 之前有意義。★
#   MySQL 映像檔只在「資料目錄是空的」時候讀這個密碼。
#   volume 已經初始化過之後再改這個檔，資料庫裡的密碼不會跟著變，
#   而 healthcheck 會開始用新密碼去連——然後一直 unhealthy。
#   所以已經有這個檔就不覆蓋。

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="$REPO_DIR/.env.mysql"

if [ -e "$ENV_FILE" ]; then
  echo "已經有 $ENV_FILE，不覆蓋。"
  exit 1
fi

# 寫之前先確認會被 git 忽略。寫完才檢查的話，密碼已經躺在一個會被 commit 的檔案裡了。
if ! git -C "$REPO_DIR" check-ignore -q .env.mysql; then
  echo "★ .env.mysql 不會被 git 忽略，先修 .gitignore 再跑 ★" >&2
  exit 1
fi

umask 077
cat > "$ENV_FILE" <<ENV
# 由 deploy/hetzner/init_env_mysql.sh 產生於 $(date -u '+%Y-%m-%d %H:%M:%SZ')
# 這個檔案有密碼，不要進 git（.gitignore 已蓋掉 .env.*）
MYSQL_ROOT_PASSWORD=$(openssl rand -hex 24)
MYSQL_DATABASE=bus
ENV

echo "寫好了：$ENV_FILE（權限 600，已確認被 git 忽略）"

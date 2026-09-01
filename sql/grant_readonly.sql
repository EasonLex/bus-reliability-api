-- =====================================================================
-- 給 API 的唯讀帳號
--
-- 執行（在 GCE 上）：
--   docker exec -i mysql sh -c \
--     'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -u root --default-character-set=utf8mb4 bus' \
--     < sql/grant_readonly.sql
--
--   ※ 不要用 mysql -p < file ——密碼提示會把檔案的第一行吃掉當密碼，
--     然後第一行 SQL 就消失了，而且不會有錯誤訊息。
--
-- 為什麼不共用匯出腳本的帳號
--   匯出腳本要 CREATE / DROP / RENAME（原子交換用），權限很大。
--   API 是唯一會被瀏覽器連到的東西，只需要 SELECT。
--   兩者共用一個帳號的話，Web 層的任何一個洞都直接通到可以刪表的權限。
-- =====================================================================

-- 只允許從 docker 內部網段連進來。
-- 172.16.0.0/12 是 docker 預設的 bridge 網段；用 '%' 的話，
-- 這個帳號從任何地方都能連，而 3306 有綁 Tailscale IP。
CREATE USER IF NOT EXISTS 'bus_api'@'172.16.0.0/255.240.0.0'
  IDENTIFIED BY '<把這裡換成一組新密碼，不要跟 root 一樣>';

GRANT SELECT ON bus.baseline_segment TO 'bus_api'@'172.16.0.0/255.240.0.0';
GRANT SELECT ON bus.route_stop       TO 'bus_api'@'172.16.0.0/255.240.0.0';

FLUSH PRIVILEGES;

-- 驗證：應該只看到兩行 SELECT，沒有 INSERT / DROP / CREATE
SHOW GRANTS FOR 'bus_api'@'172.16.0.0/255.240.0.0';

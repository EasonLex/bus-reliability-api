-- =====================================================================
-- ★ 這不是正式的 schema。★
--
-- 正式版本在主專案 TKR102 的 sql/schema.sql，那裡才是真相。
-- 這份只有一個用途：讓這個 repo 的測試可以獨立跑起來，
-- 不必先架好整套資料管線。
--
-- 檔名刻意叫 test_fixture_schema.sql 而不是 schema.sql——
-- 兩個 repo 裡各有一份同名檔案，遲早會有人在錯的那份上改東西。
--
-- 那「兩份 DDL 遲早會長歪」怎麼辦？
--   靠 test_schema_contract.py。那支測試不看這個檔案，
--   它去問資料庫的 information_schema：API 讀的那些欄位在不在、
--   型別能不能算。對著正式資料庫跑，它就會抓到漂移。
--
--   所以這份檔案就算過時了也不會騙人——它只是測試的腳手架，
--   真正的把關在契約測試那邊。
-- =====================================================================

CREATE TABLE IF NOT EXISTS baseline_segment (
  city              VARCHAR(16)  NOT NULL,
  from_station_id   VARCHAR(32)  NOT NULL,
  to_station_id     VARCHAR(32)  NOT NULL,
  day_type          ENUM('weekday','weekend') NOT NULL,
  band              VARCHAR(16)  NOT NULL,

  level             ENUM('band','daytype','pair') NOT NULL,
  n                 INT UNSIGNED NOT NULL,
  n_route           SMALLINT UNSIGNED NOT NULL,

  p25_sec           SMALLINT UNSIGNED NOT NULL,
  p50_sec           SMALLINT UNSIGNED NOT NULL,
  p75_sec           SMALLINT UNSIGNED NOT NULL,
  p90_sec           SMALLINT UNSIGNED NOT NULL,
  unreliability     DECIMAL(6,2) NOT NULL,
  p90_reliable      TINYINT(1)   NOT NULL,

  mean_sec          DECIMAL(7,2) NOT NULL,
  sd_sec            DECIMAL(7,2) NOT NULL,

  date_from         DATE NOT NULL,
  date_to           DATE NOT NULL,
  n_days            SMALLINT UNSIGNED NOT NULL,
  rule_version      VARCHAR(8)  NOT NULL,
  baseline_version  VARCHAR(16) NOT NULL,
  computed_at       DATETIME    NOT NULL,

  PRIMARY KEY (city, from_station_id, to_station_id, day_type, band)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC;


CREATE TABLE IF NOT EXISTS route_stop (
  city           VARCHAR(16)  NOT NULL,
  sub_route_uid  VARCHAR(32)  NOT NULL,
  direction      TINYINT UNSIGNED NOT NULL,
  seq            SMALLINT UNSIGNED NOT NULL,
  station_id     VARCHAR(32)  NOT NULL,
  stop_name      VARCHAR(64)  NOT NULL,
  boarding       TINYINT      NULL,
  version_id     VARCHAR(64)  NOT NULL,
  loaded_at      DATETIME     NOT NULL,

  PRIMARY KEY (sub_route_uid, direction, seq),
  KEY idx_station (station_id),
  KEY idx_city_route (city, sub_route_uid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 ROW_FORMAT=DYNAMIC;

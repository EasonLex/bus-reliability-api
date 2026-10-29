# 部署：Hetzner（服務層 MySQL + API）

上游的收集管線（collector、Kafka、Airflow、BigQuery）在 GCP 試用期結束後停了，
這裡只搬服務層：**資料凍結在最後一次匯出的那一天**，API 和頁面照常可用。

主機本身的設定（Docker、Tailscale、多專案慣例、port 表）在 `hetzner-infra` 那個 repo，
這份只講這個專案。開始之前主機要已經跑過它的 `bootstrap.sh`。

```
/srv/apps/bus/                   ← 這個 repo
  ├── .env.mysql                 ← init_env_mysql.sh 產生
  ├── .env.api                   ← sql/create_readonly_user.sh 產生
  └── deploy/hetzner/
        ├── docker-compose.yml   ← 以下指令都在這個資料夾裡下
        └── init_env_mysql.sh
```

| 容器 | 對外 |
|---|---|
| `bus-mysql` | 不開 port |
| `bus-api` | `127.0.0.1:8000` → Funnel → `https://kafka-1.tail9b1e87.ts.net/` |

---

## 0. 從舊機器匯出（一次性）

在 GCE 的 kafka-1 上。先記下筆數，搬完要對：

```bash
docker exec mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot bus -e "
  SELECT COUNT(*) AS baseline_segment FROM baseline_segment;
  SELECT COUNT(*) AS route_stop FROM route_stop;"'

docker exec mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot \
  --single-transaction bus baseline_segment route_stop' | gzip > ~/bus_serving.sql.gz

ls -lh ~/bus_serving.sql.gz        # 不能是 0 bytes
```

拉回 Mac（Tailscale 已連）：

```bash
scp <帳號>@100.119.53.70:~/bus_serving.sql.gz ~/Downloads/
```

**這份檔案就是服務層的全部資料。** Mac 上留一份，另外再放一個地方（雲端硬碟）。
確認拉回來之前，不要動 Tailscale 後台的舊 `kafka-1`。

---

## 1. Clone

```bash
cd /srv/apps
git clone https://github.com/EasonLex/bus-reliability-api.git bus
cd bus
```

## 2. 起 MySQL

```bash
./deploy/hetzner/init_env_mysql.sh       # 產生 root 密碼，不經過你的鍵盤
cd deploy/hetzner
docker compose up -d mysql
docker compose ps                        # 等 bus-mysql 變成 (healthy)，約一分鐘
```

確認網路落在唯讀帳號允許的範圍（`172.16.0.0/12`，見 hetzner-infra 的 daemon.json）：

```bash
docker network inspect bus_default --format '{{(index .IPAM.Config 0).Subnet}}'   # 要是 172.x.x.x/24
```

## 3. 還原資料

從 Mac 把 dump 傳上去：

```bash
scp ~/Downloads/bus_serving.sql.gz ethan@kafka-1:~/
```

在 Hetzner 上：

```bash
gunzip -c ~/bus_serving.sql.gz | docker exec -i bus-mysql sh -c \
  'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --default-character-set=utf8mb4 bus'

docker exec bus-mysql sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot bus -e "
  SELECT COUNT(*) AS baseline_segment FROM baseline_segment;
  SELECT COUNT(*) AS route_stop FROM route_stop;"'
```

**兩個數字要跟第 0 步記下的一模一樣。** 差一筆都不要往下走。

## 4. 唯讀帳號

```bash
cd /srv/apps/bus
MYSQL_CONTAINER=bus-mysql ./sql/create_readonly_user.sh
git check-ignore -v .env.api             # 要有輸出
```

`MYSQL_CONTAINER` 不能省：腳本預設找的是 GCE 上的 `mysql`。
忘了的話會是 `No such container: mysql`，重跑時加上就好。

## 5. 起 API

```bash
cd deploy/hetzner
docker compose up -d --build api
docker compose ps                        # bus-api 要 (healthy)

curl -s 127.0.0.1:8000/healthz
curl -s '127.0.0.1:8000/api/travel-time?sub_route_uid=TPE11881&direction=0&from_seq=4&to_seq=12' \
  | python3 -m json.tool | head -30
docker stats --no-stream bus-mysql bus-api
```

## 6. 對外

```bash
sudo tailscale funnel --bg 8000
tailscale funnel status
```

用手機的**行動網路**（不要連 Tailscale）開 `https://kafka-1.tail9b1e87.ts.net/`，
或掃簡報封面的 QR code。

---

## 更新程式

```bash
cd /srv/apps/bus && git pull
cd deploy/hetzner && docker compose up -d --build api
```

MySQL 不用動。

## 備份

資料是凍結的，第 0 步那份 `bus_serving.sql.gz` 就是備份，不需要排程。
哪天上游重新跑、重新匯出了，再照第 0 步的指令（容器名改成 `bus-mysql`）dump 一份。

## 收掉

```bash
cd /srv/apps/bus/deploy/hetzner
sudo tailscale funnel --https=443 off   # 只關這個專案用的 443，不要用 reset（會清掉所有專案的設定）
docker compose down            # 保留資料
docker compose down -v         # 連資料一起刪（volume bus_mysql-data）
```

FROM python:3.12-slim

WORKDIR /app

# 先裝相依、後複製程式碼。反過來寫的話，每改一行程式都要重裝一次套件。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db.py main.py ./
COPY static/ ./static/

# 不用 root 跑。這支服務是這台機器上唯一會被瀏覽器連到的東西。
RUN useradd -u 10001 -m app
USER app

EXPOSE 8000

# --workers 2：夠用。這支服務只讀，重的活都在 MySQL 那邊，
# 開太多 worker 只是把 MySQL 的 32 條連線上限吃掉。
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

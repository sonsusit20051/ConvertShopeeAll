# Shopee Link Converter (Queue + Worker)

Web convert link Shopee theo mô hình YT-like:

`Web -> /?url=...&yt=1 -> queue -> worker poll -> worker submit -> trả affiliate link`

## Tính năng

- Frontend giống ConvertSP, convert theo sync API.
- Backend queue job:
  - `POST /api/convert` tạo job async
  - `GET /api/jobs/{id}` lấy trạng thái
  - `POST /worker/poll` / `POST /worker/submit` cho worker
- Sync API kiểu YT:
  - `GET /?url=<encoded>&yt=1`
- Worker local Python:
  - resolve shortlink Shopee
  - build link local theo rule cơ bản
- Extension worker (khuyến nghị):
  - lấy `affiliateLink` từ request campaign context
  - backend giữ nguyên link worker submit (không rebuild lại `an_redir`)
- Admin dashboard có login cookie-session (`/admin/login`).
- Lưu lịch sử convert/statistics vào SQLite (`data/app.db`).

## Cấu hình

Sao chép `.env.example` thành `.env`:

```bash
cp .env.example .env
```

Biến quan trọng:

- `WORKER_TOKEN=dev-worker-token`
- `FORCE_YT_MODE=0`

## Chạy local

### 1) Chạy backend

```bash
cd "/Users/sonmoi/Downloads/Convert FB"
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8790 --reload
```

### 2) Chạy worker local (terminal khác)

```bash
cd "/Users/sonmoi/Downloads/Convert FB"
source .venv/bin/activate
WORKER_TOKEN=dev-worker-token AFFILIATE_ID=17322940169 SUB_ID=cvweb SERVER_BASE=http://localhost:8790 python3 worker/local_worker.py
```

### 2b) Hoặc chạy Extension worker (khuyến nghị để lấy campaign context)

```text
chrome://extensions -> Developer mode -> Load unpacked -> chọn thư mục extension/
```

- Mở `Extension options`:
  - `Server Base URL`: `http://localhost:8790`
  - `Worker Token`: `dev-worker-token`
  - `Affiliate ID`: dùng làm fallback khi cần
  - `Sub ID`: để trống hoặc cấu hình fallback
- Đăng nhập `https://affiliate.shopee.vn/` trên cùng profile Chrome.
- Bật worker trong extension.

### 3) Mở web

- User: `http://localhost:8790/`
- Admin login: `http://localhost:8790/admin/login`

## Deploy VPS (Ubuntu 22.04/24.04)

### 1) Chuẩn bị

- Trỏ DNS `A record` của domain về IP VPS.
- Mở firewall cho `80` và `443`.
- SSH vào VPS bằng user có quyền `sudo`.

### 2) Cài đặt 1 lần bằng script

```bash
git clone https://github.com/sonsusit20051/ConvertShopeeAll.git
cd ConvertShopeeAll
sudo DOMAIN=convert.yourdomain.com \
  ADMIN_KEY='doi-mat-khau-admin-ngay' \
  WORKER_TOKEN='doi-worker-token-ngay' \
  bash deploy/scripts/setup_ubuntu.sh
```

Script sẽ tự:

- Cài `python/nginx/certbot`
- Clone app vào `/opt/convertshopee`
- Tạo virtualenv + cài dependencies
- Tạo `systemd` service `convertshopee`
- Cấu hình `nginx` reverse proxy

### 3) Bật HTTPS

```bash
sudo certbot --nginx -d convert.yourdomain.com
```

Sau đó truy cập:

- User: `https://convert.yourdomain.com/`
- Admin: `https://convert.yourdomain.com/admin/login`

### 4) Cập nhật app khi có code mới

```bash
sudo APP_DIR=/opt/convertshopee APP_USER=convertshopee bash /opt/convertshopee/deploy/scripts/update_app.sh
```

### 5) Lệnh quản trị nhanh

```bash
sudo systemctl status convertshopee
sudo journalctl -u convertshopee -f
sudo nginx -t
```

## API nhanh

### Sync convert

```bash
curl "http://localhost:8790/?url=https%3A%2F%2Fs.shopee.vn%2F60MYHRgR4W&yt=1"
```

### Async convert

```bash
curl -X POST "http://localhost:8790/api/convert" \
  -H "Content-Type: application/json" \
  -d '{"input":"https://s.shopee.vn/60MYHRgR4W"}'
```

### Worker poll

```bash
curl -X POST "http://localhost:8790/worker/poll" \
  -H "Content-Type: application/json" \
  -H "X-Worker-Token: dev-worker-token" \
  -d '{"workerId":"w1","workerName":"local","affiliateId":"17322940169","subId":"cvweb"}'
```

## CLI đổi aff id (không cần chạy backend/worker)

### 1) Đổi 1 link

```bash
cd "/Users/sonmoi/Downloads/ALL CV/FB"
python3 tools/affiliate_cli.py convert \
  --url "https://shopee.vn/product/123/456" \
  --affiliate-id "17322940169" \
  --sub-id "cvweb"
```

### 2) Đổi hàng loạt (mỗi dòng 1 link trong `urls.txt`)

```bash
cd "/Users/sonmoi/Downloads/ALL CV/FB"
python3 tools/affiliate_cli.py batch \
  --input urls.txt \
  --output converted.txt \
  --affiliate-id "17322940169" \
  --sub-id "cvweb"
```

### 3) Đổi aff id mặc định toàn hệ thống trong `.env`

```bash
cd "/Users/sonmoi/Downloads/ALL CV/FB"
python3 tools/affiliate_cli.py set-env \
  --env .env \
  --affiliate-id "17322940169" \
  --affiliate-id-yt "17391540096"
```

## Lưu ý

- Không có worker online thì sync/async convert sẽ không hoàn tất.
- Không dùng API chính thức Shopee, nên hành vi có thể thay đổi theo thời gian.
- Trước khi public internet: đổi `ADMIN_KEY`, `SESSION_SECRET`, `WORKER_TOKEN`.

# Shopee Link Converter (Local Backend)

Web convert link Shopee theo `affiliate_id` của bạn, có lưu lịch sử và thống kê admin.

## Tính năng

- Convert link bằng cách resolve link đầu vào và tạo link:
  - `https://s.shopee.vn/an_redir?origin_link=...&affiliate_id=...&sub_id=...`
- Lưu lịch sử convert vào SQLite (`data/app.db`)
- Dashboard admin (có khóa key)
- Giao diện người dùng theo layout yêu cầu

## Cấu hình

Sao chép `.env.example` thành `.env` nếu cần:

```bash
cp .env.example .env
```

Biến môi trường chính:

- `AFFILIATE_ID=17322940169`
- `SUB_ID=addlivetag----`
- `ADMIN_KEY=240905`
- `DB_PATH=data/app.db`
- `REQUEST_TIMEOUT_SECONDS=15`

## Chạy local

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8787 --reload
```

Mở:

- User page: `http://localhost:8787/`
- Admin page: `http://localhost:8787/admin?key=240905`

## API

### `POST /api/convert`

Request:

```json
{
  "input_url": "https://s.shopee.vn/60MYHRgR4W"
}
```

Response:

```json
{
  "success": true,
  "id": 1,
  "input_url": "...",
  "resolved_url": "...",
  "origin_link": "...",
  "affiliate_link": "https://s.shopee.vn/an_redir?..."
}
```

### `GET /api/admin/stats?key=...`

Trả về summary + 100 lượt gần nhất.

## Đưa lên Git

```bash
git init
git add .
git commit -m "Initial shopee converter app"
git branch -M main
git remote add origin <YOUR_GIT_REPO_URL>
git push -u origin main
```

## Lưu ý

- Cơ chế không dùng API chính thức của Shopee, nên có rủi ro thay đổi hành vi khi Shopee thay đổi redirect/rule.
- `ADMIN_KEY` hiện là key tĩnh, nên đổi key mạnh hơn trước khi public internet.

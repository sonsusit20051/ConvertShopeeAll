# Chrome Extension Worker (MVP)

Đây là extension chung cho cả 2 nguồn convert `FB` và `YT/Shopee` của web hợp nhất.

Extension này giúp máy bạn làm worker nhận job từ queue server và trả affiliate link.
Luồng bridge: background nhận job -> gọi request campaign context (tab affiliate hoặc API mapping) -> lấy `affiliateLink` trả về -> submit nguyên link đó về backend.
Backend giữ nguyên `affiliateLink` worker submit, không tự build lại `an_redir`.

## Cài đặt

1. Mở `chrome://extensions`
2. Bật `Developer mode`
3. Chọn `Load unpacked`
4. Trỏ tới thư mục `extension/`

## Cấu hình

Vào `Details` -> `Extension options`:

- `Server Base URL`: mặc định `http://localhost:8790`
  - Có thể dùng backend public dạng `https://<name>.trycloudflare.com`
- `Worker Token`: phải khớp với backend (`WORKER_TOKEN`)
- `Affiliate ID (fallback)`: để trống để extension tự detect theo tab/profile `affiliate.shopee.vn` đang đăng nhập.
- `Sub ID`: mặc định `cvweb`
- `Base Redirect`: `https://s.shopee.vn/an_redir`

## Cách chạy

- Đăng nhập tab `https://affiliate.shopee.vn/`
- Đảm bảo backend queue đang chạy
- Bật worker trong options
- Extension sẽ poll job và submit kết quả
- Nếu tab affiliate không phản hồi, extension sẽ fallback gọi API mapping trực tiếp (`https://yt.shpee.cc`).
- Khi worker bật, extension sẽ tự reload tab `https://affiliate.shopee.vn/dashboard` mỗi 5 phút để giữ session/tab ổn định.

## Popup trạng thái realtime

- Click icon extension để mở popup.
- Popup có:
  - Chấm màu trạng thái realtime (`xanh`: hoạt động, `vàng`: đang xử lý/chờ, `đỏ`: lỗi/offline).
  - Nút nhỏ `Kiểm tra ngay` để force poll và cập nhật tức thì.
  - Nút `Bật/Tắt worker`.
- Nếu vừa sửa code extension, nhớ bấm `Reload` trong `chrome://extensions`.
- Nếu đổi backend từ localhost sang domain public, cũng cần `Reload` extension để áp permission mới.

## Lưu ý

- Đây là skeleton MVP, phần auto detect affiliate id phụ thuộc DOM thực tế của trang affiliate.
- Nếu auto detect không ổn định, hãy nhập `Affiliate ID` thủ công trong options.

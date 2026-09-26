# net_stream

Tầng L3, mạng: khách TCP của luồng tiếng (KẾ HOẠCH §7.4). Board nối ra máy nhận, không mở cổng nghe nào.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `lwip` |
| Bật tắt | `NET_STREAM_ENABLE`: bật ở `dev`, `bench`, `ci`, **tắt ở `prod`** — tắt thì không file nguồn nào được dịch (§4.5.8, §7.5) |
| Hàm | `connect` (tra tên, connect không chặn có hạn `NET_STREAM_CONNECT_TIMEOUT_MS`), `send` (gửi trọn hoặc đóng kết nối, hạn `NET_STREAM_SEND_TIMEOUT_MS`), `close`, `stats` |
| Ai gọi | chỉ `luong_task` qua `svc_report`; khuôn khung do `svc_report` đóng theo `gen_stream.h` |
| Máy nhận | `host/src/srhost/stream_rx.py` |

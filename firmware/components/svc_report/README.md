# svc_report

Tầng L4, dịch vụ ghép: mọi thứ rời board về máy nhận. Hiện có nửa **luồng tiếng** (KẾ HOẠCH §7.4);
`telemetry` và `event` qua MQTT chuyển về đây ở E13.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `net_stream`; `net_mqtt` vào cùng `telemetry` |
| Luồng tiếng | `sach_task` gọi `svc_report_stream_push` mỗi bước: đóng khung SRST theo `gen_stream.h`, ghi `sb_stream` timeout 0, thiếu chỗ thì bỏ cả khung và đếm. `luong_task` gọi `svc_report_luong_step`: nối khi cần, gửi, mất kết nối thì giữ khung trong `sb_stream` và nối lại mỗi giây |
| Mở, đóng | `svc_report_stream_start(mode, host, port, giây)` từ console (`stream`) hoặc `SET_STREAM` (E13-T7); tự tắt sau `SVC_REPORT_STREAM_MAX_S` (600 s, §7.5) |
| Không làm | không tạo task, không tạo hàng đợi: `sb_stream` do `app_wiring` tạo và trao vào |
| Bật tắt | nửa luồng tiếng chỉ dịch khi `NET_STREAM_ENABLE`; `prod` không có symbol nào của nó |

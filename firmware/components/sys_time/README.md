# `sys_time` — L2

Giờ thật của board, chỉ lấy từ GPS (KẾ HOẠCH §3.19). Board không có pin RTC: mất nguồn là mất giờ, bật lại thì chờ GPS.

- `sys_time_offer`: `do_task` đưa giây UTC của mỗi câu `RMC` mới cùng lúc đọc. Lúc đọc trễ hơn câu, nên ứng viên đầu nhận ngay,
  sau đó mỗi `clock.window_s` nhận ứng viên lớn nhất của khoảng ấy, rồi `settimeofday`.
- `sys_time_utc_ms`: đổi giờ máy (`esp_timer`) của lượt chạy này ra giờ UTC, cho `capture.utcMs` và `up/sensors`.
- `sys_time_init` đặt múi giờ của ngày giờ file trên thẻ: `device/tz` của NVS, vắng thì `clock.tz` của `vision.yaml`.
- Phụ thuộc: `common`, `esp_timer`.

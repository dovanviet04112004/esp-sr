# `svc_sensors` — L4

Gom GPS, hồng ngoại, MPU-6500 và ADC của board C, đăng `up/sensors` khi đổi và mỗi 10 s (KẾ HOẠCH §3.19). `do_task` gọi nó mỗi giây;
component không tự tạo task.

- Một thiết bị hỏng lúc boot thì các thiết bị khác vẫn chạy, và khối của nó vắng khỏi payload.
- `svc_sensors_latest` chép lần đọc mới nhất, đã gửi hay chưa, dưới spinlock, cho mục nhật ký của `svc_journal` (KẾ HOẠCH §5.3).
- Mỗi câu `RMC` mới có giờ đi vào `sys_time_offer`; báo cáo mang `utcMs` khi board đã có giờ, và đăng ngay lần đầu có.
- Phụ thuộc: `common`, `drv_gps`, `drv_ir`, `drv_i2c`, `sys_time`, `net_mqtt`.
- Board không có các khối này thì `APP_SENSORS_ENABLE` tắt, component rỗng.

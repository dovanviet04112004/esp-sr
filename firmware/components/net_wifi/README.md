# net_wifi

Tầng L3: Wi-Fi chế độ station theo KẾ HOẠCH §7.2. `net_task` ở `main` gọi `net_wifi_init` rồi `net_wifi_apply`,
chờ bit `APP_BIT_WIFI_OK` của `eg_system` tới khi có, rồi tự xoá.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`; riêng tư `sys_storage`, `esp_wifi`, `esp_netif`, `esp_event`, `esp_timer` |
| Thông tin mạng | NVS `wifi/ssid`, `wifi/pass` qua `sys_storage`; driver khởi tạo với `nvs_enable = 0` nên không tự đọc ghi NVS |
| Nối lại | mỗi lần rớt chờ 1 s rồi gấp đôi tới 30 s, **không bỏ cuộc**; có địa chỉ thì về lại 1 s |
| Task | không có task riêng: nối lại chạy trong callback của `sys_evt` và `esp_timer`, cả hai ở nhân 0 (§5.2) |
| Tiết kiệm điện | `WIFI_PS_MIN_MODEM`; luồng tiếng (E5-T10) sẽ đổi sang `WIFI_PS_NONE` |
| Tên máy | DHCP host name là `deviceId` (`sr-` + MAC) |
| Kiểm | trên board: tắt điểm phát 5 phút rồi bật lại, máy tự về mà không khởi động lại (E5-T8) |

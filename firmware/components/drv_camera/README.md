# `drv_camera` — L2

OV5640 của board C: khung JPEG do sensor tự nén, sensor nghỉ giữa hai lần chụp (KẾ HOẠCH §3.19).

Xuất phát từ `drv_camera` của repo face attendance, bỏ vòng phơi sáng tay: vòng ấy đọc RGB565 để giữ sáng ổn định cho nhận
diện mặt, còn ở đây sensor tự phơi sáng và chỉ chạy lúc chụp.

- Phụ thuộc: `common`, `bsp_board`, `espressif/esp32-camera` 2.1.7.
- Số ở `contracts/vision.yaml`. Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md`.

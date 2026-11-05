# `svc_vision` — L4

`nhin_task` của board C: chụp, lưu thẻ, đẩy ảnh lên `up/image`, và với "nhận diện" thì chạy `detect` (KẾ HOẠCH §3.19).

- Phụ thuộc: `common`, `net_mqtt`, `bsp_board`, `drv_camera`, `drv_sd`, `ai_engine`, `espressif/esp_jpeg`.
- Mọi đệm cấp lúc khởi tạo, ở PSRAM.
- Thiếu đệm hay thiếu camera thì `nhin_task` vẫn chạy và từ chối từng yêu cầu bằng `CAMERA_FAIL`.
- Tên ảnh trên thẻ không bao giờ đè ảnh cũ: tên đã có thì lấy hậu tố `_<n>`.
- Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md`.

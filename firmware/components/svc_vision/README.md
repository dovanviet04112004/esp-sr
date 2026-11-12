# `svc_vision` — L4

Camera của `nhin_task` trên board C (KẾ HOẠCH §3.19): mỗi lần chụp đúng một ảnh đầy đủ 2560 × 1920; với "nhận diện" và "ghi nhật ký"
thì giải chính ảnh ấy về 512 × 384, chạy `detect` và gửi khung trước; rồi đẩy ảnh lên `up/image` và lưu thẻ. `svc_vision_step` trả mọi
yêu cầu chụp cho `nhin_task`, `svc_capture` gọi `svc_vision_shoot`, rồi `svc_vision_done` cho `q_vision` nhận yêu cầu kế. Xem trực tiếp
chạy ở đây, cùng cỡ khung.

- Phụ thuộc: `common`, `net_mqtt`, `bsp_board`, `drv_camera`, `drv_sd`, `ai_engine`, `espressif/esp_new_jpeg`.
- Mọi đệm cấp lúc khởi tạo, ở PSRAM.
- Thiếu đệm hay thiếu camera thì `nhin_task` vẫn chạy và từ chối từng yêu cầu bằng `CAMERA_FAIL`.
- Tên ảnh trên thẻ không bao giờ đè ảnh cũ: tên đã có thì lấy hậu tố `_<n>` (`drv_sd_name`).
- Ảnh và khung xem trực tiếp đi theo khúc của `net_mqtt_publish_chunks`.
- Không vẽ khung lên ảnh: ảnh đầy đủ đi sạch, server và host vẽ từ `up/detections` hay `up/record` (chủ repo chốt 11/11).
- Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md`.

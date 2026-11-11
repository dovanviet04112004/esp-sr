# `svc_vision` — L4

`nhin_task` của board C: chụp, lưu thẻ, đẩy ảnh lên `up/image`, và với "nhận diện" thì chạy `detect` (KẾ HOẠCH §3.19). Yêu cầu
`JOURNAL` không làm ở đây: `svc_vision_step` trả nó cho `nhin_task`, `svc_journal` gọi `svc_vision_detect` và `svc_vision_full`, rồi
`svc_vision_done` cho `q_vision` nhận yêu cầu kế.

- Phụ thuộc: `common`, `net_mqtt`, `bsp_board`, `drv_camera`, `drv_sd`, `ai_engine`, `espressif/esp_new_jpeg`.
- Mọi đệm cấp lúc khởi tạo, ở PSRAM.
- Thiếu đệm hay thiếu camera thì `nhin_task` vẫn chạy và từ chối từng yêu cầu bằng `CAMERA_FAIL`.
- Tên ảnh trên thẻ không bao giờ đè ảnh cũ: tên đã có thì lấy hậu tố `_<n>` (`drv_sd_name`).
- Ảnh và khung xem trực tiếp đi theo khúc của `net_mqtt_publish_chunks`.
- Không vẽ khung lên ảnh: ảnh đầy đủ đi sạch, server và host vẽ từ `up/detections` (chủ repo chốt 11/11).
- Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md`.

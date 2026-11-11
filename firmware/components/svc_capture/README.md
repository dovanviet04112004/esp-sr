# `svc_capture` — L5

Một lần chụp của board C, ở cả ba lệnh "chụp ảnh", "nhận diện", "ghi nhật ký" (KẾ HOẠCH §3.19): ảnh của `svc_vision`, lời ghi chú
của `svc_note` khi là nhật ký, rồi bản ghi `up/record` mà `svc_outbox` gửi và giữ trên thẻ. Nằm ở L5 vì nối các component L4.
`nhin_task` gọi `svc_capture_keep` khi `svc_vision_step` trả một yêu cầu chụp; component không tự tạo task.

- "Chụp ảnh": ảnh đầy đủ. "Nhận diện": khung `detect`, khung tìm được, rồi ảnh đầy đủ. "Ghi nhật ký": lời ghi chú và hai ảnh cùng lúc,
  `svc_note_start` đưa `seq` cho `ghi_task`, rồi hai ảnh, rồi `svc_note_wait` và `svc_note_send`.
- Bản ghi có ba khoá `capture`, `detections`, `sensors`, mỗi khoá đúng schema cùng tên; khoá nào không có thì vắng.
- Số đo cảm biến lấy qua hàm `main` cắm vào (`svc_sensors_latest`), `NULL` khi cảm biến tắt.
- Tiếng báo của hai lệnh ảnh do `svc_vision` phát ngay khi ảnh tới được một nơi; tiếng cuối của nhật ký ở đây: dài khi lời ghi chú
  và ảnh đầy đủ tới được ít nhất một nơi, trầm nếu không.
- Không ra được ảnh, khung hay lời ghi chú nào thì không có bản ghi.
- Board không vẽ khung lên ảnh; server và host vẽ từ `up/detections` hay `up/record`.
- Phụ thuộc: `common`, `net_mqtt`, `svc_vision`, `svc_note`, `svc_outbox`.
- Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md` §4.9.

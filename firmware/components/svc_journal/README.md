# `svc_journal` — L5

Một mục "ghi nhật ký" của board C (KẾ HOẠCH §3.19): lời ghi chú của `svc_note`, rồi khung `detect` và ảnh đầy đủ của `svc_vision`, rồi
`up/journal` và `/sdcard/IMG/B<boot>/<seq>.JSN`. Nằm ở L5 vì nối hai component L4. `nhin_task` gọi `svc_journal_keep` khi
`svc_vision_step` trả yêu cầu `JOURNAL`; component không tự tạo task.

- `.JSN` là một đối tượng JSON ba khoá `journal`, `detections`, `sensors`, mỗi khoá đúng schema của payload cùng tên; khoá nào không có
  thì vắng. Mất mạng thì thẻ vẫn giữ đủ một mục: ảnh, lời ghi chú, khung và số đo.
- Chữ của `.JSN` in ở vùng JSON của `nhin_task` trong `net_mqtt`, không có đệm riêng.
- Số đo cảm biến lấy qua hàm `main` cắm vào (`svc_sensors_latest`), `NULL` khi cảm biến tắt.
- Hai tiếng: ngắn khi ghi xong lời; cuối cùng dài khi lời ghi chú và ảnh đầy đủ tới được ít nhất một nơi, trầm nếu không.
- Board không vẽ khung lên ảnh; server và host vẽ từ `up/detections` hay `.JSN`.
- Phụ thuộc: `common`, `net_mqtt`, `drv_sd`, `svc_note`, `svc_vision`.
- Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md` §4.9.

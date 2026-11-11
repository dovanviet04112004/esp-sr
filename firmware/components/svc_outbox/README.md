# `svc_outbox` — L4

Mục trên thẻ của mỗi lần chụp và gửi bù khi có mạng lại (KẾ HOẠCH §3.19). Chạy trong `nhin_task`: `svc_capture` gọi
`svc_outbox_keep`, `nhin_task` gọi `svc_outbox_step` giữa các lần chụp. Component không tự tạo task.

- `svc_outbox_keep` gửi `up/record` khi có phiên MQTT, ghi bản ghi thành `/sdcard/IMG/B<boot>/<seq>.JSN` và dấu
  `/sdcard/IMG/PND/B<boot>_<seq>.PND`, file rỗng nghĩa là server chưa xác nhận. Tên `.JSN` bị chiếm bởi lượt chạy cũ thì không có dấu.
- `down/ack` tới qua `q_ack`; `svc_outbox_step` xoá dấu của nó.
- Có phiên và rảnh: đọc thư mục dấu lúc phiên mới nối và sau mỗi lượt `outbox.retry_s`, giữ `outbox.batch` mục cũ nhất; mỗi bước gửi
  một khúc ảnh đầy đủ, một khúc lời ghi chú hay bản ghi của mục cũ nhất, cờ `late`. Hộp thư esp-mqtt đầy thì bước sau gửi lại khúc ấy.
- Phần nào bản ghi khai mà thẻ không còn, hay khác cỡ, thì bản ghi gửi bù khai cỡ 0, để server không chờ mãi.
- `.JSN` hỏng hay mất thì bỏ dấu.
- Phụ thuộc: `common`, `drv_sd`, `net_mqtt`.
- Một bản ghi và chữ của nó ở PSRAM, cấp lúc khởi tạo.
- Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.

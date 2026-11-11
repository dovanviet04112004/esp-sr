# `svc_note` — L4

Lời ghi chú của một mục nhật ký trên board C: hop âm đã lọc từ `sb_note` thành `/sdcard/IMG/B<boot>/<seq>.WAV`, rồi đọc lại từ thẻ
gửi `up/note` (KẾ HOẠCH §3.19). Chạy trong `nhin_task`, do `svc_journal` gọi; component không tự tạo task.

- Ghi tới khi người nói im `note.end_silence_s`, bỏ khi `note.start_s` không ai nói, dài nhất `note.max_s` (`contracts/vision.yaml`).
  Hop điếc, lúc loa đang phát tiếng bíp, không tính là nói hay im.
- `APP_BIT_NOTE_ON` bật suốt lúc ghi: chỉ khi ấy `nhan_task` chép hop vào `sb_note`, và hop ấy không tới `svc_listen`.
- Ghi thẻ theo khối 8 KB, vá header WAV lúc xong. Không có thẻ thì không có lời ghi chú.
- Phụ thuộc: `common`, `bsp_board` (đèn), `drv_sd`, `net_mqtt`.
- Khối thẻ 8 KB và một bản ghi hop ở PSRAM, cấp lúc khởi tạo.
- Board không có camera thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md` §4.9.

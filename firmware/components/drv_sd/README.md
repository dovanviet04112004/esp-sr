# `drv_sd` — L2

Thẻ SD của board C qua SPI, FAT ở `/sdcard` (KẾ HOẠCH §3.19). Thẻ vắng thì `drv_sd_init` báo lỗi và sản phẩm chạy tiếp không
có thẻ.

- Ghi hỏng thì xoá file dở, nên trên thẻ không có file cụt; `drv_sd_exists` để nơi gọi khỏi đè file cũ.
- `drv_sd_name` đặt tên mới cho một file, thêm hậu tố `_<n>` khi tên đã có: ảnh, lời ghi chú và `.JSN` theo cùng một luật.
- `drv_sd_append`, `drv_sd_patch`, `drv_sd_read` cho lời ghi chú: ghi dần theo khối, vá header lúc xong, đọc lại để gửi. Nối thêm hỏng thì
  phần đã lên thẻ còn nguyên.
- `nhin_task` và `ghi_task` cùng dùng thẻ: mỗi lời gọi file giữ khoá `m_sd` từ lúc mở tới lúc đóng, chờ tới 10 s (KẾ HOẠCH §5.3).
- Phụ thuộc: `common`, `bsp_board`, driver SDSPI và FATFS của IDF.
- Board không có thẻ thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md`.

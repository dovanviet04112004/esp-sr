# `drv_sd` — L2

Thẻ SD của board C qua SPI, FAT ở `/sdcard` (KẾ HOẠCH §3.19). Thẻ vắng thì `drv_sd_init` báo lỗi và sản phẩm chạy tiếp không
có thẻ.

- Phụ thuộc: `common`, `bsp_board`, driver SDSPI và FATFS của IDF.
- Board không có thẻ thì `APP_CAMERA_ENABLE` tắt, component rỗng.
- Số đo ở `docs/measurements/board_c.md`.

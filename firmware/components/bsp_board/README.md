# bsp_board

Tầng L1: `include/app_config.h` là chỗ **duy nhất** khai chân GPIO của board B (KẾ HOẠCH §2.2), và
`bsp_board_init()` đặt mức an toàn cho chân ampli và LED lúc khởi động.

| Mục | Nội dung |
|---|---|
| Phụ thuộc | `common`, `esp_driver_gpio` |
| Chặn lúc biên dịch | bật console USB-Serial-JTAG (kể cả console phụ) trong khi I2S đang ở GPIO 19/20 là lỗi dịch |
| Đổi chân | sửa `app_config.h` và KẾ HOẠCH §2.2 trong **cùng một commit** (CLAUDE.md §1.3) |
| Giới hạn | chân ampli (17, 18) và LED (21) là đề xuất, xác nhận ở E2-T2 |

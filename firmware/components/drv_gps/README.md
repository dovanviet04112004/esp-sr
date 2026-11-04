# `drv_gps` — L2

GPS của board C: UART1 chỉ RX ở GPIO 20, 9600 baud, NMEA (KẾ HOẠCH §3.19).

- `nmea.h` là phần thuần: dựng câu, kiểm checksum, đọc `RMC`, `GGA`, `GSV`, `TXT`. Test chạy trên máy tính ở
  `test_apps/host`, có đối chứng âm.
- `drv_gps.h` đọc UART, không tạo task; `do_task` gọi nó.
- Phụ thuộc: `common`, `bsp_board`, driver UART của IDF.

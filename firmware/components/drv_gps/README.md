# `drv_gps` — L2

GPS của board C: UART1 chỉ RX ở GPIO 20, 9600 baud, NMEA (KẾ HOẠCH §3.19).

- `nmea.h` là phần thuần: dựng câu, kiểm checksum, đọc `RMC`, `GGA`, `GSV`, `TXT`. Giờ và ngày UTC lấy từ `RMC`, chỉ
  khi đủ chữ số, đúng khoảng và năm từ `clock.year_min` trở đi. Test chạy trên máy tính ở `test_apps/host`, có đối chứng âm.
- `drv_gps_poll` xoá `has_time` sau mỗi lần đọc, nên `do_task` không đưa một câu cũ cho `sys_time` hai lần.
- Số vệ tinh thấy được là tổng `GSV` mới nhất của từng hệ (GPS, BeiDou, …). HDOP, loại fix (`GGA`) và chế độ (ô 12 của `RMC`) chỉ
  được báo; vị trí chưa lọc, cách lọc chờ số đo có anten (TASKS E15-T40).
- `drv_gps.h` đọc UART, không tạo task; `do_task` gọi nó.
- Phụ thuộc: `common`, `bsp_board`, driver UART của IDF.

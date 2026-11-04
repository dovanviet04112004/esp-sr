# `drv_i2c` — L2

Bus I2C của board C ở GPIO 1 và 2, mỗi thiết bị một file (KẾ HOẠCH §3.19). Không tạo task; `do_task` gọi nó.

- `mpu6500.h`: gia tốc, con quay và nhiệt độ của MPU-6500 ở 0x68.
- `ads1115.h`: bốn đầu vào của ADC ở 0x48, mỗi bước đọc một kênh.
- Test ở `test_apps/host` chạy hai thiết bị trên một bus giả, có đối chứng âm.
- Phụ thuộc: `common`, `bsp_board`, driver I2C của IDF.
- Board không có các thiết bị này thì `APP_SENSORS_ENABLE` tắt, component rỗng.

# Board C: kiểm phần cứng

Kiểm ngày 04/11 trên board C (KẾ HOẠCH §2.1, bảng chân §2.2), E2-T12. Chip và eFuse đọc bằng esptool và espefuse. Các khối
còn lại chạy bằng một app thử không nằm trong repo, dựng trên IDF 6.0.2 và `esp32-camera` 2.1.7: máy tính gửi một ký tự qua
UART0, app chạy một khối rồi in kết quả.

## 1. Kết quả từng khối

| Khối | Chân | Kết quả | Trạng thái |
|---|---|---|---|
| Chip, flash, PSRAM | | ESP32-S3 QFN56 rev v0.2, flash 16 MB quad (mã 0x46 / 0x4018), PSRAM 8 MB AP_3v3, VDD_SPI 3,3 V; secure boot và mã hoá flash tắt | đạt |
| UART0, chế độ nạp | 43, 44, 0 | ROM in `boot:0x3 (DOWNLOAD(USB/UART0))`, trả lời SYNC ở 115 200 baud | đạt |
| Micro | 9, 3, 48 | khe trái: nền −67 dBFS, đỉnh −56 dBFS; khe phải: −16 tới −22 dBFS, đỉnh 0 dBFS, là dây thả nổi | đạt, **khe trái** |
| Loa | 9, 3, 14 | tone 1 kHz ở −20 dBFS: chủ repo nghe thấy; micro đo 1 kHz từ −113 lên −52,6 dBFS, tắt tone về −99,8 dBFS | đạt |
| Camera | 18, 38–41, 15, 4–8, 16, 17, 46 | OV5640 (PID 0x5640, SCCB 0x3C); QVGA RGB565, XCLK 20 MHz: 30/30 khung, 7,9 khung/s; dải màu thử cho OR 0xFF, AND 0x00, cả tám dây đổi mức; ảnh là cảnh thật, còn nhòe | đạt |
| Thẻ SD | 10–13 | `sdmmc_card_init` qua SPI 20 MHz: SDHC tên `SD16G`, 29 818 MB; chưa đọc ghi | đạt |
| I2C | 1, 2 | hai dây có kéo lên; thấy thiết bị ở 0x48 và 0x68 | đạt, chưa biết là gì 🔬 |
| GPS | 20 | NMEA 9600 baud, cả GPS lẫn BeiDou; báo `ANTENNA OPEN`, chưa có vị trí | đạt, **thiếu anten** |
| Hồng ngoại | 19 | yên thì ở mức cao; vẫy tay đổi mức 266 và 165 lần trong hai lượt 8 s, rung ở mức mili giây | đạt, cần lọc |
| Đèn | 42 | chớp ba lần, chủ repo thấy | đạt |

## 2. Vấn đề của board

- **Không reset được từ máy tính.** CP2102 là mạch rời, DTR và RTS không tới EN hay GPIO 0, và board có nguồn riêng nên rút
  cáp USB không khởi động lại chip. Nút duy nhất là GPIO 0. Cách xử lý ở KẾ HOẠCH §2.1.
- **Khe phải là rác.** Đọc hai khe sẽ trộn rác vào tiếng; phải đọc đúng khe trái.
- **Không có chân tắt ampli.** Ampli luôn thức và nhận xung của micro. Bản sản phẩm đầu tiên để GPIO 14 thả nổi và loa rè
  (chủ repo nghe thấy 04/11); `bsp_board` giữ chân ấy ở mức thấp.
- **Đèn riêng tư là đèn chiếu sáng.** Board không có LED trạng thái, nên GPIO 42 sáng khi luồng tiếng mở.
- **GPS thiếu anten**, nên chưa kiểm được vị trí.
- **Hồng ngoại rung**, khi dùng phải lọc trong firmware.
- **Ảnh nhòe** và chỉ 7,9 khung/s ở cấu hình thử. Chủ repo cho biết camera này có tự lấy nét, khác camera của repo face
  attendance. App thử không bật tự lấy nét, nên ảnh nhòe có thể do đó; `esp32-camera` 2.1.7 có sẵn phần tự lấy nét cho OV5640
  (`sensors/ov5640_af.c`), chưa thử trên board này 🔬. Repo face attendance chạy OV5640 ở XCLK 27 MHz và tự điều phơi sáng;
  khi làm E15-T15 thì đo lại với cấu hình ấy 🔬.
- **Hai thiết bị I2C** ở 0x48 và 0x68 chưa rõ là gì 🔬.
- **Micro chưa đo SNR và `pcm_shift`.** Board C tạm dùng `pcm_shift` 13 của board B 🔬.

## 3. Bản sản phẩm trên board

`make product BOARD=C` ngày 04/11, profile `bench`, 460 800 baud: `models.bin` 5,4 MB ghi trong 97 s, hash khớp mọi ảnh.

- Boot: `sr-30eda01e4630, 1 mic(s), 1 channel(s), speaker off`, thức ở 800‰, kiểm người nói tắt vì chưa có giọng mẫu.
- NVS đặt như board B: `pcm_shift` 13, `wake_th` 800, ngưỡng lệnh 300 / 10 / 65 535.
- Mạng `esp` như board B, broker ở PC, trạng thái `ONLINE`.
- Heartbeat đầu: RAM nội còn 48,5 KiB, thấp nhất 23,6 KiB, không rớt khung, DMA hay sự kiện.
- `WAKE` đầu tiên điểm 0,84 lúc 17:05:43, chưa rõ là người nói thật hay thức nhầm 🔬.

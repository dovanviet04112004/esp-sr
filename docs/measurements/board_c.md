# Board C: kiểm phần cứng

Kiểm ngày 04/11 trên board C (KẾ HOẠCH §2.1, bảng chân §2.2), E2-T12. Chip và eFuse đọc bằng esptool và espefuse. Các khối
còn lại chạy bằng app thử `firmware/test_apps/board_c` (`make board-c-check`), dựng trên IDF 6.0.2 và `esp32-camera` 2.1.7:
máy tính gửi một ký tự qua UART0, app chạy một khối rồi in kết quả.

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
- **Ống kính nét cố định, không có motor** (§4.3). Ảnh của vật gần nhòe vì vật nằm ngoài điểm nét đặt sẵn; đổi điểm nét bằng
  cách xoay ren ống kính, `make vision-live` hiện ảnh và cỡ JPEG.
- **Hai thiết bị I2C** ở 0x48 và 0x68 chưa rõ là gì 🔬.
- **Micro chưa đo SNR và `pcm_shift`.** Board C tạm dùng `pcm_shift` 13 của board B 🔬.

## 3. Bản sản phẩm trên board

`make product BOARD=C` ngày 04/11, profile `bench`, 460 800 baud: `models.bin` 5,4 MB ghi trong 97 s, hash khớp mọi ảnh.

- Boot: `sr-30eda01e4630, 1 mic(s), 1 channel(s), speaker off`, thức ở 800‰, kiểm người nói tắt vì chưa có giọng mẫu.
- NVS đặt như board B: `pcm_shift` 13, `wake_th` 800, ngưỡng lệnh 300 / 10 / 65 535.
- Mạng `esp` như board B, broker ở PC, trạng thái `ONLINE`.
- Heartbeat đầu: RAM nội còn 48,5 KiB, thấp nhất 23,6 KiB, không rớt khung, DMA hay sự kiện.
- `WAKE` đầu tiên điểm 0,84 lúc 17:05:43, chưa rõ là người nói thật hay thức nhầm 🔬.

## 4. Luồng ảnh và RAM nội (E15-T7, T15, T20, T21, 04/11)

Firmware sản phẩm, profile `bench`, một micro, b0, `detect` dựng lúc boot, broker ở PC, không ai nói. Cảnh cố định: một cái
cốc cách camera ~30 cm, khung hình xoay 90°.

### 4.1 DMA của camera

`CONFIG_CAMERA_PSRAM_DMA` bỏ gần hết khung JPEG (`NO-EOI`): ở chế độ này `esp32-camera` chỉ dò dấu kết thúc JPEG ở 1 KB cuối,
mà sensor đệm thêm sau dấu ấy. Không bật thì đệm DMA 16 KB ở RAM nội, dò cả khung, mọi khung dùng được.

### 4.2 RAM nội

Lúc đầu Wi-Fi không đủ RAM: phiên MQTT chết khi gửi ảnh, RAM nội còn ~2,5 KiB. Cột trước là bản có Wi-Fi và lwIP ưu tiên PSRAM
(`SPIRAM_TRY_ALLOCATE_WIFI_LWIP`), đệm tĩnh RX và TX 6. Cột sau thêm các khoản ở bảng dưới.

| Bước của boot, B trống / khối lớn nhất | Trước | Sau |
|---|---|---|
| `storage` | 177 951 / 118 784 | 242 499 / 212 992 |
| `front` | 84 683 / 40 960 | 149 223 / 131 072 |
| `detect` | 84 063 / 40 960 | 148 603 / 131 072 |
| `camera` | 62 299 / 31 744 | 126 835 / 110 592 |
| `vision` | 59 919 / 31 744 | 124 595 / 106 496 |
| Wi-Fi lên | 10 319 / 5 120 | 74 763 / 57 344 |
| Heartbeat: trống, thấp nhất | 12,6 KiB, 4,2 KiB | 75,6 KiB, 35,2 KiB |

| Khoản lấy lại, DIRAM tĩnh | Giảm |
|---|---|
| Mã Wi-Fi và PHY, ISR của SPI, log ra khỏi IRAM (`.text`) | 25 260 B |
| Bảng thanh ghi của 14 sensor không lắp (`.data`) | 7 956 B |
| `.bss` của Wi-Fi, lwIP ra PSRAM; ngăn xếp `gui`, `dieu`, `luong` ra PSRAM; pool `q_frame` một kênh khi không có loa (`.bss`) | 30 880 B |
| **DIRAM tĩnh** | **226 619 → 162 523 B** |

`SPIRAM_MALLOC_RESERVE_INTERNAL` từ 32 KB xuống 8 KB không đổi tổng mà trả khối liền: vùng dành riêng không nhập vào khối lớn.

`front` lấy 77,8 KB: `hot` của `dsp_afe` một micro 75 072 B, trong đó `ns_omlsa` 33 312 B.

### 4.3 Ống kính

Giữ MCU lấy nét ở reset (`cam reg 3000 20`), ghi tay DAC ống kính (`3603`, `3602`), chụp mỗi vị trí. Phương sai Laplace của ba
vùng trên cùng một cảnh:

| DAC | Cỡ JPEG B | Giấy | Bàn | Giữa |
|---|---|---|---|---|
| 0 | 44 414 | 2,14 | 4,80 | 3,04 |
| 200 | 44 500 | 2,12 | 4,76 | 3,08 |
| 400 | 44 553 | 2,08 | 4,77 | 2,99 |
| 600 | 44 374 | 2,20 | 4,88 | 3,08 |
| 800 | 44 531 | 2,16 | 4,91 | 2,96 |
| 1 000 | 44 847 | 2,09 | 4,62 | 2,98 |

Cả dải DAC không đổi ảnh, chênh trung bình giữa các ảnh dưới 1,2 mức xám. Bật phần sụn lấy nét thì sáu lần trên cùng cảnh dừng
ở 0x147, 0x2AC, 0x18F, 0x1D6, 0x02A, 0x055, ảnh vẫn y hệt: nó dò trên nhiễu. Chủ repo xác nhận ống kính là ống ren tròn. Mỗi lượt
dò tốn ~900 ms, nạp phần sụn ~4 s lúc boot, nên bỏ.

### 4.4 Chất lượng JPEG

`jpeg_quality` của OV5640 là hệ số của bảng lượng tử, không phải thang 0–100 quen thuộc:

| `jpeg_quality` | DC độ sáng | Trung bình bảng độ sáng / màu | Cỡ ảnh, cùng cảnh |
|---|---|---|---|
| 12 | 12 | 43,3 / 64,4, cỡ JPEG Q60 | 33 KB |
| 6 | 6 | 21,7 / 32,2 | 44 KB |

Khung 4:2:2. Mặc định `esp32-camera` cấp `width × height / 5` = 184 320 B, chật cho cảnh nhiều chi tiết ở mức 6, nên board C cấp
320 KB (`CAMERA_JPEG_MODE_FRAME_SIZE`).

### 4.5 Thời gian từng chặng

| Chặng | Bản đầu, 33 KB | Bản cuối, 51 KB |
|---|---|---|
| Từ lệnh tới khung | 1 539–1 581 ms, gồm lấy nét ~900 ms | 673–751 ms, gồm chờ phơi sáng 7–8 lần lấy khung, 493–653 ms |
| Gửi ảnh | 353–1 410 ms, chờ từng khúc | 4–39 ms, xếp vào outbox |
| Giải JPEG 1:2 | 1 153–1 199 ms, `esp_jpeg` | 270–389 ms, `esp_new_jpeg` |
| `detect` | 4 329 ms, vùng mượn 0 / 32 768 B | **1 930–1 936 ms** (4 lần), vùng mượn đủ |
| Ghi thẻ | 131–208 ms, trước khi gửi | 176–308 ms, sau cùng |
| Boot tới `vision` | 10,5 s, nạp phần sụn lấy nét ~4 s | 6,5 s |

Từ lệnh tới lúc khung lên MQTT cộng các chặng ~3,0 s; chưa đo trọn một lượt từ host 🔬. Khung đến mỗi ~70 ms một lần lấy, tức một khung
của sensor bỏ một, vì chỉ có một đệm.

Lượt `detect` tách theo ưu tiên, `nhin_task` so với `nhan_task` (ưu tiên 10):

| `nhin_task` | Tiền xử lý | Mạng | Giải khung | Cả lượt |
|---|---|---|---|---|
| ưu tiên 1, dưới chuỗi nghe | 41 ms | 3 266–3 303 ms | 34–38 ms | 3 345–3 385 ms |
| ưu tiên 12, trên chuỗi nghe | 30 ms | 1 863–1 865 ms | 26 ms | ~1 920 ms |

Trong một lượt 3 385 ms ở ưu tiên 1, theo bộ đếm thời gian chạy của FreeRTOS: `nhin_task` 1 971 ms, `nhan_task` 1 316 ms (39%), `mqtt_task`
47 ms, `wifi` 37 ms, `tcpip` 18 ms; nhân 1: `sach_task` 702 ms (21%), `IDLE1` 2 679 ms. Chuỗi nghe là thứ duy nhất đáng kể. Khi ở trên nó,
`nhan_task` đuổi kịp phần khung tồn trong ~1,1 s (1 090 ms đo). Trong lúc ấy `nhin_task` đã về ưu tiên 1, nên ghi thẻ và sự kiện `CAPTURE`
đứng sau; khung đã gửi thẳng từ trước. `clean_dropped` 0 qua mọi lượt đo.

Đo ở ưu tiên 12, 19, 24 như nhau: không task nào trên `nhan_task` chiếm thời gian đáng kể. Con số 3,03 s đọc được lúc đầu ở các ưu tiên ấy
là bấm giờ sau khi hạ ưu tiên, gộp cả ~1,1 s chuỗi nghe đuổi kịp.

Vùng mượn: `s_loan` từng là một biến chung, `WeightLoan` của `wake` xin 0 B vẫn xoá nó khi hết lượt. `detect` chạy ở ưu tiên 1 nên
mất vùng ngay sau bước `wake` đầu tiên, và một mạng không xin được RAM sẽ thấy vùng của `detect`. Vùng giờ theo từng task.
`AI_ENGINE_LOAN_RESERVE_BYTES` 16 KB làm vùng hụt 64 B (`32704 of 32768`), xuống 8 KB thì đủ.

Lúc nghe, sau `esp_new_jpeg` (+6,5 KB DIRAM): RAM nội trống 65,0 KiB, thấp nhất 35,5 KiB.

### 4.6 `detect` trên ảnh thật

Ảnh đầu (`jpeg_quality` 12, chưa có đèn) board ra 0 hộp. Cùng ảnh, giảm 1:2 trên PC, qua mô phỏng int8 của ESP-PPQ: điểm cao nhất
0,19 `microwave`, dưới ngưỡng 0,25, khớp board. Xoay ảnh đúng chiều thì `bottle` 0,31: khung xoay 90° làm `detect` kém đi.

Ảnh sau (`jpeg_quality` 6, đèn sáng) board ra `suitcase` 0,57–0,86 trên cái cốc, có lượt ra 0 hộp. Khung ở đúng chỗ, tên sai: cốc nằm
ngang và nhòe. Chưa có ảnh đúng chiều, nét, nên chưa chấm được độ đúng của `detect` trên board C 🔬.

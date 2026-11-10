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
  cách xoay ren ống kính, `make vision-live` hiện ảnh và độ nét. Cỡ JPEG không đo được độ nét: ảnh nhòe hẳn mà nhiễu ở độ lợi cao vẫn 69 KB, ảnh nét hơn 49 KB.
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

Ngăn xếp `main` (E15-T27, 05/11): `app_boot` dựng mọi mạng trên `main`, dùng 3 340 B, ở mặc định 3 584 B chỉ dư 244 B. Heap báo khối lớn nhất theo bậc 2 KB
(`tlsf_fit_size`), và lỗ `main` để lại sau `app_main` không liền với khối ấy, nên ngăn xếp `main` lớn hơn là khối ấy nhỏ đi:

| `main` / `thu_task` | `main` dư | Vùng mượn | `detect` |
|---|---|---|---|
| 3 584 / 3 072 B | 244 B | 26 560 B | 1 950–1 954 ms |
| 5 120 / 3 072 B | 1 780 B | 24 512 B | 2 020–2 026 ms |
| 4 608 / 2 048 B, bản chốt | 1 268 B | 26 560 B | 1 951–1 959 ms (3 lần, boot 60) |

`thu_task` dùng 848 B, nên trả 1 KB `.bss` cho heap để bù phần `main` nới thêm.

Gửi lại tin QoS 1 (E13-T18, 09/11): esp-mqtt gửi lại tin chưa được xác nhận sau 1 000 ms, mà chuỗi nghe đuổi kịp giành CPU của `mqtt_task` ~1,1 s sau mỗi lượt `detect`,
nên `up/detections` đến hai lần (cách nhau 1,3 s, cùng `captureSeq`). Đếm số bản `Receiver` bỏ, `make detect` nối tiếp, `make vision-live` của chủ repo chạy cả hai lần:

| `NET_MQTT_RETRANSMIT_MS` | Lượt `detect` | `detections` nhận | Bản trùng |
|---|---|---|---|
| 1 000 (thư viện) | 5 | 5 | 3 |
| 5 000 | 6 | 6 | **0** |

Mẫu nhỏ, nhưng 3 trong 5 mà thành 0 trong 6 không phải ngẫu nhiên. Một lượt `make detect` 3,7–3,8 s, có lần 5,0 s.

Outbox của esp-mqtt sang PSRAM (E15-T25): vùng mượn vẫn 26 560 B, `detect` 1 950–1 954 ms, RAM nội thấp nhất 23 955 B, nên outbox không phải lý do vùng hụt. `qCleanPeak` 122–127 / 192 sau mỗi lượt
`detect`, `clean_dropped` 0.

**Vùng mượn của `detect` theo từng boot (10/11).** Cùng app, cùng model COCO, khối RAM nội lớn nhất lúc `link up` chỉ có vài mức, mỗi mức chốt cho cả
boot: 29 696, 34 816, 36 864 hay 38 912 B. Khối lớn nhất lúc chạy nhỏ hơn mức ấy 0–2 KB (MQTT khởi động sau `link up`). Heap
trước khi bật Wi-Fi giống từng byte (86 016 B liền ở bước `vision`), nên khối nhỏ nằm giữa xuất hiện trong lúc Wi-Fi nối; hai boot liền nhau
khác `aid` (3 so với 5) và RSSI (−46 so với −32 dBm), chưa chứng minh là nguyên nhân 🔬.

| `AI_ENGINE_LOAN_RESERVE_BYTES` | Boot | Khối lớn nhất lúc `link up` | Vùng mượn | `detect` |
|---|---|---|---|---|
| 8 192 | 94, 96, 98, 100, 102 | 29 696 B | 21 440 B | 2 016–2 031 ms |
| 8 192 | 95 | 34 816 B | 26 560 B | 1 950–1 974 ms |
| 8 192 | 97, 99, 101, 103–105 | 36 864 B | 28 608 B | 1 951–1 962 ms |
| 2 048 | 107–110, 113 | 29 696 B | 27 584 B | 1 951–1 970 ms |
| 2 048 | 111, 112 | 36 864 B | 32 704 B | 1 941–1 956 ms |
| 2 048 | 114 | 38 912 B | 32 768 B, đủ | 1 931–1 936 ms |
| 2 048, giữ 40 960 B | 116–123 | 40 960 B sau `net settled` | đủ 32 768 B; một lượt 25 536 B | 1 930–1 948 ms; một lượt 1 992 |
| 2 048, giữ 36 864 B | 125–132 | 36 864 B sau `net settled` | đủ 32 768 B | 1 930–1 951 ms |

Vùng mượn là khối lớn nhất lúc chạy trừ dự trữ và 64 B đệm, nên dự trữ 8 KB biến bố cục 29 696 B thành vùng 21 440 B, dưới ngưỡng ~25 KB
mà tốc độ nhảy thêm ~65 ms. Các task khác chỉ cấp thêm khoảng 130 B RAM nội trong lúc `detect` chạy (RAM nội trống thấp nhất 23 955 B
trong khi vùng mượn 28 608 B đang giữ), nên dự trữ 8 KB không dùng tới. Hạ xuống 2 KB: RAM nội thấp nhất ở boot 114, vùng mượn đủ 32 768 B,
sau ba lượt `detect` và hai nhịp tim, 23,6 KiB (nhịp tim `heapInternalMin`), không thấp hơn trước; `dropped` dma, frames, clean, events, stream đều 0.
Chưa chạy dài để thấy đáy RAM nội 🔬.

**Giữ một khối RAM nội lúc Wi-Fi khởi động.** `net_task` giữ `APP_NET_HEAP_HOLD_BYTES` ngay trước `net_wifi_init` và trả nguyên khối khi MQTT lên (tối đa
10 s), nên Wi-Fi chia heap quanh khối ấy và khối liền lớn nhất sau `net settled` đúng bằng khối giữ, ở cả 16 boot đo, không còn mức 29 696 B.
- Giữ 40 960 B: đáy RAM nội 6,7 KiB; 23 trên 24 lượt `detect` 1 930–1 948 ms. Lượt còn lại chạy 1 992 ms vì `pitch` đang giữ vùng mượn
  14 272 B của nó lúc `detect` xin, nên `detect` chỉ được 25 536 B: hai vùng mượn chồng nhau, không do Wi-Fi.
- Giữ 36 864 B, đủ cho 32 768 B vùng mượn, 2 048 B dự trữ và 64 B đệm: đáy RAM nội **10,1 KiB** (nhịp tim `heapInternalMin`), 24 lượt `detect` 1 930–1 951 ms,
  `dropped` dma, frames, clean, events, stream đều 0; Wi-Fi và MQTT lên ở cả 8 boot.
- Lúc giữ khối, khối lớn nhất chỉ 7 680–8 704 B, nên `pitch` (chạy suốt lúc nghe) mượn 5 568 hay 6 592 B trên 14 272 B xin trong vài giây ấy. Chưa đo
  `pitch` chậm bao nhiêu 🔬.
- Đáy RAM nội trước khi giữ là 23,6 KiB; giữ 36 864 B lấy đi ~13,5 KiB lề lúc Wi-Fi khởi động. Mới thử trên mạng `esp` (hotspot điện thoại); nơi nhiều
  AP hơn thì lượt quét Wi-Fi cần nhiều RAM nội hơn 🔬.
- Không đưa vào sản phẩm: lợi thêm ~20 ms chênh lệch giữa các boot, đổi lấy ~13,5 KiB lề RAM nội lúc khởi động. Bản chốt chỉ hạ dự trữ xuống 2 KB; boot 133
  chạy 1 944, 2 029, 1 944 ms, lượt 2 029 ms nhiều khả năng là vùng mượn của `pitch` chồng lên `detect` như ở boot 123 🔬.

### 4.6 `detect` trên ảnh thật

Ảnh đầu (`jpeg_quality` 12, chưa có đèn) board ra 0 hộp. Cùng ảnh, giảm 1:2 trên PC, qua mô phỏng int8 của ESP-PPQ: điểm cao nhất
0,19 `microwave`, dưới ngưỡng 0,25, khớp board. Xoay ảnh đúng chiều thì `bottle` 0,31: khung xoay 90° làm `detect` kém đi.

Ảnh sau (`jpeg_quality` 6, đèn sáng) board ra `suitcase` 0,57–0,86 trên cái cốc, có lượt ra 0 hộp. Khung ở đúng chỗ, tên sai: cốc nằm
ngang và nhòe.

Chỉnh ống kính tay, lật sensor (`vflip`, `4514` = `BB`), xoay 90° lúc giải JPEG, cùng cái cốc ~30 cm:

| Ảnh qua `detect` | Kết quả |
|---|---|
| Nằm ngang, float trên PC | `book` 0,16, dưới ngưỡng |
| Đúng chiều, float trên PC | `cup` 0,72 |
| Đúng chiều, trên board, 3 lần liên tiếp | **`cup` 0,88–0,91**, khung (0, 122)–(359, 570) trên 360 × 640 bao đúng cốc, 1 921–1 947 ms |

Mới một vật, một cảnh: chưa phải phép chấm độ đúng của `detect` trên board C 🔬. Chữ trên cốc đọc rõ ở ~30 cm. Khung gửi đi 64 KB.

### 4.7 Xem trực tiếp

`make vision-live`, 1280 × 720, `jpeg_quality` 6, 49–64 KB một khung: 3,4–5,4 khung/s, tuổi khung mới nhất ở host dưới 0,2 s, QoS 0, không
ghi thẻ. Windows hay tự chuyển PC về mạng nhà, broker mất đường tới board, nên trang báo `NO SIGNAL` sau 2 s không có khung.

Ống kính rất nhạy: ren đi ~0,4 mm một vòng 🔬, còn từ vô cực tới 30 cm thấu kính 3,6 mm chỉ dịch ~0,04 mm, nên 1/8 vòng đã qua hẳn vùng
nét. Độ nét trên trang: bậc thang lớn nhất ở bản thu nhỏ 160 × 90 chia cho độ tương phản. Ảnh nhòe ~7, ảnh đầu ~33, sau khi chỉnh 43.

### 4.8 Khung 5 MP, bước 0 (10/11)

Bản thử, không commit: `contracts/vision.yaml` 2560 × 1920 (`FRAMESIZE_QSXGA`, 4:3), `CAMERA_JPEG_MODE_FRAME_SIZE` 716 800 B, trần hàng đợi gửi ảnh 64 KB,
chất lượng 6 và 4407 chỉnh qua `cam reg 4407 <giá trị>`. Cảnh phòng tĩnh, cùng chỗ đặt board.
- Vòng 1, giải mã 1:5 ra 512 × 384 (RGB 590 KB): PSRAM trống sau boot 125 004 B (412 748 B ở 720p), gửi ảnh báo `outbox_enqueue: Memory exhausted`, bộ giải JPEG
  báo `allocate memory failed`; ảnh vẫn lên thẻ.
- Vòng 2, giải mã 1:8 ra 320 × 240 (chỉ để nhường PSRAM, `detect` không dùng được): PSRAM trống 484 428 B, gửi và giải mã chạy.

| Chất lượng | JPEG | Từ lệnh tới khung | Gửi | Giải JPEG 1:8 (hiệu `detect` log và `detectMs`) | Thẻ | Cả lệnh từ host |
|---|---|---|---|---|---|---|
| 4 | 521 389 B | 3 292 ms | 2 981 ms | ~2 290 ms | 1 376 ms | 9,8 s |
| 6 | 363 892 B | 3 287 ms | 2 828 ms | ~2 079 ms | 1 203 ms | 9,1 s |
| 10 | 228 731 B | 3 250 ms | 895 ms | ~2 027 ms | 696 ms | 7,2 s |
| 16 | 163 787 B | 5 492 ms | 381 ms | ~2 003 ms | 496 ms | 8,8 s |
| 30 | 125 666 B | 5 272 ms | 2 445 ms | ~1 985 ms | 417 ms | 10,6 s |

720p cùng chất lượng 6 (§4.5): 51–57 KB, từ lệnh tới khung 673–751 ms, gửi 4–39 ms, giải JPEG 1:2 270–389 ms, thẻ 176–308 ms, cả lệnh ~3,7 s.
- Chờ phơi sáng 5–7 khung mất 2,8 s ở ba lượt đầu và 4,8–5,1 s ở hai lượt sau (cùng phơi sáng 8400, gain 23); chưa rõ vì sao.
- Gửi 126–521 KB mất 0,4–3,0 s với hàng đợi 64 KB, không đều giữa các lượt.
- Giải JPEG 5 MP về 320 × 240 mất khoảng 2,0–2,3 s, gấp 5–8 lần 720p.
- Góc nhìn: ảnh 5 MP là cả mảng 4:3, ảnh 720p là dải 16:9 ở giữa, tức 75% bề ngang của ảnh dựng đứng, cả chiều cao. Quy đổi khung từ ảnh `detect`
  (360 × 640) sang ảnh 5 MP dựng đứng (1920 × 2560): x' = 240 + x / 360 × 1440, y' = y / 640 × 2560. Kiểm bằng một vật (đầu ghế) lệch ~2% 🔬.
- Độ nét: trích 720 × 720 điểm ở giữa ảnh 5 MP vẫn mềm (lưới ghế không tách ra, nhiễu): cảnh ở xa ngoài tầm nét cố định 30 cm, nên điểm ảnh nhiều chưa
  thêm chi tiết. Chưa thử vật ở đúng 30 cm 🔬.

## 5. Loa và tiếng nói (E2-T14, 04/11)

`drv_audio` chạy TX cùng BCLK/WS với RX. `thu_task` đẩy một khối TX sau mỗi khối RX, nên trễ từ đó tới DMA là hằng số; bao nhiêu bước
chưa đo 🔬 (§6).
`speaker on`, 1 micro, 2 kênh; RAM nội lúc nghe 54,5 KiB, thấp nhất 23,9 KiB; vùng mượn của `detect` 26 560 / 32 768 B, `detect` vẫn
1 938–1 947 ms. Chủ repo nghe được bíp.

Micro nghe tiếng bíp sau từ đánh thức thành một câu. Log trước khi sửa, 4/4 lần:

```
23:37:16 WAKE 0.82
23:37:18 REJECT LOW_SCORE
23:37:20 COMMAND nhan_dien 0.97
```

`svc_front` tắt `vad` từ lúc tham chiếu có tiếng tới 420 ms sau hết tiếng. Sau khi sửa, 2/2 lần `WAKE` đi thẳng tới `COMMAND`. Mới hai lần 🔬.

Bằng giọng: `chup_anh` 0,88–0,90, `nhan_dien` 0,92–0,97, `COMMAND` tới `CAPTURE` ~2 s.

## 6. Khử vang (E10-T3, T4, 05/11)

`aec` MDF, 8 phân đoạn, 128 ms, một micro, toàn bộ ở PSRAM: RAM nội không đổi, lúc nghe 54,2 KiB, thấp nhất 31,9 KiB sau 14 phút, không rớt khung nào. Phòng
mô phỏng, bản Python:

| Phép thử | Kết quả |
|---|---|
| Chỉ tiếng loa, sau khi hội tụ | 40,5 dB; 14,8 dB trong 2 s đầu |
| Tắt học, đối chứng âm | 0,0 dB |
| Loa méo `tanh` | 12,3 dB, phần còn lại là việc của khe `ns` |
| Hai bên cùng nói | tiếng người mất 0,01 dB, không phân kỳ, 36,7 dB sau đó |

Bản C so với Python trên máy tính khớp từng bit, cả 5 ca của `aec` lẫn chuỗi `"MR"`, `"MMR"` qua `chain_aec`: phép nhân phức của numpy gộp nhân và cộng trên một số
CPU, nên bản Python nhân từng phần như `aec.c`, và bộ vàng sinh ra giống nhau dù tắt AVX2, FMA, SSE4. Hai đối chứng âm đều đỏ: chuỗi bỏ qua tham chiếu, và chuỗi giữ
tham chiếu qua chỗ hở ngắn. Chưa chạy trên chip 🔬.

So với SpeexDSP 1.2.1 (`mdf.c` dựng float, cùng khối 256, đuôi 2 048 mẫu, cùng đầu vào int16, 30 s, phòng 1 200 mẫu), ngưỡng khớp là ERLE sau hội tụ lệch
không quá 3 dB:

| Phép thử | `aec` | SpeexDSP |
|---|---|---|
| Chỉ tiếng loa, 2 s đầu / 2 s kế / cuối | 13,0 / 31,1 / 73,9 dB | 15,3 / 37,1 / 73,3 dB |
| Có nền phòng −60 dB, cuối | 41,0 dB | 41,1 dB |
| Hai bên cùng nói, ERLE lúc có người nói | 10,4 dB | 10,8 dB |
| Hai bên cùng nói, tiếng người giữ được | 0,00 dB | −0,15 dB |

Hội tụ chậm hơn chút ở 4 s đầu: SpeexDSP có thêm bộ lọc nhấn tần cao trước khi học. Ở đây lệch đó không đổi điểm dừng nên không thêm.

Trên board, loa phát nhiễu 10 s ở −12 dBFS, số do `aec` tự tính từ công suất micro và sai số:

| Lần | ERLE | Reset |
|---|---|---|
| Kênh tham chiếu trễ 7 bước | −0,1 dB, 0,0 dB | 0 |
| Kênh tham chiếu đi ngay lúc ghi | **26,1 / 26,3 / 25,9 dB** | 0 |
| Bản cuối, lượt nhiễu đầu sau khởi động | 25,1 dB | 0 |

Bản đầu giả định một khối ghi vào sẽ ra loa sau `dma_desc_num − 1` bước, nên làm trễ tham chiếu 7 bước. Tiếng ra sớm hơn thế, tham chiếu tới sau tiếng
vọng, và bộ lọc chỉ nhìn được về quá khứ.

Bản thu thật, `stream 11`, hai lượt nhiễu ở phòng yên, không hở khung (`interim/aec/echo1`). Trễ từ kênh tham chiếu tới micro **542 mẫu, 33,9 ms, 2,12 bước**,
đỉnh tương quan gấp 904 lần trung vị; mới một lần đo, chưa qua các lần khởi động 🔬.

Bản Python chạy trên bản thu ra 25,8 dB, khớp số board tự báo. Theo dải, giây 14–22, công suất dB tương đối:

| Dải | Micro | Sau `aec` | Nền phòng | Giảm |
|---|---|---|---|---|
| 60–250 Hz | −12,0 | −11,9 | −11,1 | loa không phát ở dải này |
| 250–500 Hz | −17,8 | −22,2 | −19,7 | chạm nền |
| 500 Hz–1 kHz | −5,6 | −27,9 | −29,4 | 22,3 dB, chạm nền |
| 1–2 kHz | −2,6 | −27,0 | −29,1 | 24,4 dB, chạm nền |
| 2–4 kHz | 8,5 | −18,7 | −38,4 | 27,3 dB, trên nền 20 dB |
| 4–8 kHz | 3,4 | −24,1 | −44,3 | 27,5 dB, trên nền 20 dB |

Nền phòng sau `hpf` −51,4 dBFS, micro lúc loa phát −21,1 dBFS, nên phòng này cho tối đa ~30 dB ở mức loa đã thử. SpeexDSP trên cùng bản thu với cùng đuôi
ra 26,2 dB, từng dải lệch không quá 0,1 dB. Phần còn lại trên 2 kHz là đuôi phòng dài hơn tầm với của bộ lọc, không phải méo loa, vì đuôi dài hơn thì nó giảm:

| Phân đoạn | `aec_delay` mẫu | Tầm với | ERLE | Lúc 4 s |
|---|---|---|---|---|
| 8 | 0 | 128 ms | 25,8 dB | 25,2 dB |
| 8 | 448 | 156 ms | 27,6 dB | 27,2 dB |
| 12 | 0 | 192 ms | 28,5 dB | 25,2 dB |
| 16 | 0 | 256 ms | 29,0 dB | 22,1 dB |
| SpeexDSP 4 096 mẫu | 0 | 256 ms | 29,7 dB | |

Đã qua cửa 20 dB nên giữ 8 phân đoạn và `aec_delay` 0: `aec_delay` lớn hơn trễ thật là về 0 dB như bản đầu, nên chỉ đặt sau khi đo lại qua nhiều lần khởi động.

Bộ lọc cần vài giây để học nên không giúp tiếng bíp 0,1 s; phần đó là `vad` tắt trong lúc loa phát (§5).

Chi phí, `bench_afe` chạy trên board C vì board B không cắm, trạng thái ở PSRAM như sản phẩm (`budget.md`):

| Dòng | Trung bình | Đỉnh | % khung 16 ms |
|---|---|---|---|
| `aec` một micro | 2 126 µs | 2 131 µs | 13,3 |
| `aec` hai micro | 4 368 µs | 4 378 µs | 27,3 |
| Chuỗi một micro, loa đang phát | 4 371 µs | 4 554 µs | 27,3 |
| Chuỗi một micro, loa im | 2 354 µs | 2 463 µs | 14,7 |

Các dòng còn lại trùng số của board B tới 0,1 µs (chuỗi hai micro 2 621,3 so với 2 621,2 µs), nên hai board đổi chỗ được trong `budget.md`. Trên sản phẩm, sau
hai lượt nhiễu 10 s, `dropped` của `dma`, khung, `clean` và sự kiện đều 0.

Hai bên cùng nói (E10-T6): chủ repo đọc 45 giây ở chỗ hay ra lệnh, loa im (`interim/aec/talker2`), rồi cộng từ giây 6 vào bản thu chỉ có tiếng loa. Phòng và `hpf`
tuyến tính nên phép cộng cho đúng tín hiệu hai bên cùng nói, và cho biết phần nào của lối ra là tiếng loa còn sót: lối ra trừ `hpf` của tiếng người.

| Phép thử | Tiếng loa / người lúc vào | Lúc ra | ERLE trước khi nói | ERLE lúc đang nói | Reset |
|---|---|---|---|---|---|
| Như đã thu: người −39,0, loa −21,6 dBFS | −17,4 dB | +8,1 dB | 24,1 dB | 25,5 dB | 0 |
| Người to hơn 15 dB | −2,4 dB | +22,0 dB | 24,1 dB | 24,4 dB | 0 |
| Đối chứng âm, tắt học | −17,4 dB | −17,4 dB | 0,0 dB | 0,0 dB | 0 |

Tiếng người giữ được, chiếu lối ra lên lối ra của cùng tiếng người không có loa, lúc loa phát: **+0,07 dB qua `aec`, −0,07 dB qua `aec` và `ns`**, cửa là mất không quá
3 dB. Không khung nào mang `DSP_AFE_FLAG_AEC_DIVERGED`.

Trễ tham chiếu → micro không còn hằng số sau một lần `thu_task` đứng (bản rà 05/11, E10-T7). Hàng TX của driver sâu `dma_desc_num − 1` ô và thường đầy, nên khối vừa ghi
phát ngay sau đó. `thu_task` đứng qua một EOF, như lúc xoá sector khi ghi `set.json` (27–168 ms, `latency.md` §20), rồi đọc dồn m + 1 khối RX và ghi m + 1 khối TX một mạch:
hàng tụt m ô và không đầy lại, trễ tăng 16·m ms tới lần đứng sau, tối đa +6 khối. Cơ chế đọc từ `i2s_common.c` của IDF 6.0.2; độ dài thật của mỗi lần đứng chưa đo trên board 🔬.
Bản Python trên `interim/aec/echo1`, cho tiếng vọng trễ thêm m khối (mô phỏng trên bản thu thật, chạy lại 05/11):

| m khối | Tham chiếu → micro | ERLE sau hội tụ | Lệch giữa hai lượt phát, giây đầu / giây thứ ba / 2 s cuối |
|---|---|---|---|
| 0 | 34 ms | 25,9 dB | 26,0 / 25,9 / 25,9 dB |
| 1 | 50 ms | 25,0 dB | −0,6 / 1,0 / 24,9 dB |
| 2 | 66 ms | 23,4 dB | −0,8 / 0,9 / 23,4 dB |
| 3 | 82 ms | 21,3 dB | −0,8 / 0,9 / 21,2 dB |
| 4 | 98 ms | 18,1 dB | −0,7 / 0,8 / 18,0 dB |
| 5 | 114 ms | 16,3 dB | −0,7 / 0,8 / 15,8 dB |
| 6 | 130 ms | −0,1 dB | −1,1 / −0,2 / −0,1 dB |

m ≥ 4 dưới cửa 20 dB, m = 6 quá tầm 128 ms. Lệch giữa hai lượt phát thì bộ lọc học lại gần hết lượt, nên tiếng nói ngắn sau một lần ghi flash gần như không được khử.

Sau khi sửa `drv_audio`, boot 56: `stream 11` trước và sau một lần ghi `set.json` 27 ms cùng ra **546 mẫu** (`interim/aec/txdelay_a`, `txdelay_b`). Chỗ đo mới 34,1 ms thay 33,9 ms, có tiếng cưa nhà bên: nền
p10 −50 dBFS thay −54, micro lúc loa phát −23,9 thay −21,1 dBFS. ERLE board 19,2 và 19,5 dB, bản Python trên cùng bản thu 19–20 dB, tức chuỗi vẫn khớp, phòng là thứ thấp đi.

Chưa đo: tiếng nói thật qua loa thay nhiễu, chạy dài 30 phút (E10-T1), âm lượng tối đa (E10-T2), parity trên chip 🔬.

## 7. GPS, hồng ngoại và hai thiết bị I2C (E2-T15, T16, T17, 05/11)

Đã chạy đầu cuối lên `up/sensors` và `make host-live` ở boot 50, rồi **tắt khỏi bản build của board C theo chủ repo** (`APP_SENSORS_ENABLE`, `sdkconfig.board_c`).
Code và test giữ nguyên.

| Khối | Đo được |
|---|---|
| 0x68 | `WHO_AM_I` 0x70: MPU-6500. Nằm yên −0,13 −0,99 +0,05 g, 32 °C; con quay lệch 2,9 3,1 −0,9 °/s chưa hiệu chỉnh |
| 0x48 | Cấu hình 0x8583, ngưỡng 0x8000, 0x7FFF lúc reset: họ ADS1x15. Kết quả có 0x0001, 0x000A, 4 bit thấp khác 0, nên là ADS1115 16 bit, không phải ADS1015 |
| Bốn đầu vào ADC | 0 tới 1 mV cả bốn: chưa nối gì, hoặc nối đất 🔬 |
| GPS | `no antenna`, như §1 |
| Hồng ngoại | Đổi `ON`, `off` theo tay, 2 lên 15 lần chạm trong ~70 s khi chủ repo được nhờ đưa tay 3 lần. Gia tốc đổi cùng lúc, tới −0,49 −0,62 +0,68 g, nên board bị chạm hoặc nhấc; chưa tách được là tay hay rung 🔬 |
| `do_task` | ngăn xếp dùng 1 568 / 4 096 B; không rớt khung |

Giá của chúng là `detect`. UART, I2C và ngắt GPIO ăn ~1 KB RAM nội nhưng khối liền lớn nhất mất 2 KB, vùng mượn của `detect` từ 26 560 xuống 24 512 / 32 768 B, và
một lượt từ 1 921–1 947 ms lên **2 007–2 016 ms**, 5 lần. Tắt đi thì vùng mượn về 26 560 B và một lượt về 1 944 ms. Bật lại thì phải lấy lại chừng ấy RAM nội trước.

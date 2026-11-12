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

**Bản đầu của hai cỡ khung (E15-T29, 10/11), bộ đệm khung 512 KB, ảnh đầy đủ chất lượng 10, trần outbox ảnh 128 KB:** 10 lượt
`make detect` và 5 lượt `PHOTO`, boot 140.
- Khung 720p chụp và lên host ở cả 10 lượt (53,8–54,7 KB, chụp 636–1 228 ms). Ảnh đầy đủ chụp và lên thẻ ở cả 15 lượt: 220,9–226,1 KB
  ở cảnh phòng, 266,9–309,5 KB khi cảnh đổi; chụp 2,9–3,4 s, có lượt 6,3 s và 11,2 s khi phơi sáng chờ 13–14 khung.
- PSRAM trống: 216 140 B ở bước `vision` lúc boot, nhưng **122 364 B lúc đứng yên** sau khi Wi-Fi và MQTT lên, thấp nhất 9 968 B.
- Hỏng: giải JPEG 720p báo `JPEG allocate memory failed` ở 9 trên 10 lượt (`DETECT_FAIL`), khúc đầu của ảnh đầy đủ báo
  `outbox_enqueue: Memory exhausted` ở 14 trên 15 lượt (lượt còn lại MQTT đã rớt); MQTT rớt ba lần `No PING_RESP`. Một lượt `detect` chạy được
  thì giải JPEG mất ~1,7 s thay vì 270–389 ms.
- Một lượt "nhận diện" cần ~130 KB PSRAM tạm (ảnh 720p trong outbox, bộ giải JPEG, `detect`), một ảnh đầy đủ tới 144 KB (trần outbox
  cộng một khúc): cả hai quá 122 KB.

**Bản chốt (E15-T29, 10/11): bộ đệm khung 384 KB, ảnh đầy đủ chất lượng 12, trần outbox ảnh 128 KB.** Boot 142: 10 lượt `make detect`,
5 lượt `PHOTO`, rồi 3 lượt `--capture DETECT --once` bấm giờ ở host. Cảnh lúc này có người ngồi làm việc.
- Đủ cả 15 lượt. Ảnh 720p 60,6–70,5 KB; khung ra ở cả 10 lượt (`person`, `tv`, `chair`), `detectMs` 1 935–2 029. Ảnh đầy đủ 226–258 KB;
  khung trên `_full_boxes.jpg` nằm đúng người, màn hình và ghế.
- PSRAM trống 347 212 B ở bước `vision`, **253 648 B lúc đứng yên, thấp nhất 84 260 B**; RAM nội thấp nhất 21 767 B. Không còn
  `Memory exhausted` hay `JPEG allocate memory failed`, MQTT không rớt.
- Ảnh 720p: chụp 756–888 ms, xếp outbox 4–59 ms, thẻ 250–461 ms. Từ sau lúc gửi tới trước lúc ghi thẻ 3 585–3 707 ms: giải JPEG ~0,4 s,
  `detect` ~1,94 s, rồi ~1,3 s tới lúc ghi thẻ, nhiều khả năng là `up/detections` chờ `mqtt_task` trong lúc chuỗi nghe đuổi kịp (§4.5) 🔬.
- Ảnh đầy đủ: chụp 2,9–3,4 s, gửi 0,3–2,0 s (hai lượt 7,3 và 10,1 s), thẻ 0,67–0,83 s.
- Ở host: khung về 3,4–3,6 s sau lệnh, như trước; ảnh đầy đủ về sau đó 5,2–5,7 s, cả lệnh 8,6–9,3 s. `PHOTO` 3,8–4,3 s, lượt đầu 10,9 s
  vì gửi mất 7,3 s.

**Khung `detect` 640 × 480 (E15-T30, 10/11), giải thẳng về 512 × 384, ảnh nhỏ không ghi thẻ, live ở khung đầy đủ.** Boot 145: 10 lượt
`make detect`, 5 lượt `PHOTO`, rồi 4 lượt bấm giờ ở host; `vision-live` tắt.
- Đủ cả 15 lượt. Ảnh `detect` 28,9–31,7 KB (60,6–70,5 KB ở 1280 × 720); `detectMs` 1 935–1 939; khung 4:3 thấy rộng hơn, có lượt ra 4 `person`.
- Bước 50 Hz theo hệ số đọc lại: 640 × 480 `line total 2060, PLL x180, 10 ms is 218 lines`, khung đầy đủ `2844, PLL x200, 175 lines`.
- PSRAM trống 448 588 B ở bước `vision`, **356 672 B lúc đứng yên, thấp nhất 191 740 B** (253 648 và 84 260 B với RGB 640 × 360).
- Chụp ảnh `detect` 681–1 266 ms, phần lớn 771 ms: không nhanh hơn 1 280 × 720 (756–888 ms), vì thời gian nằm ở các khung chờ phơi sáng.
  Từ sau lúc gửi tới hết `detect` 3 427–3 490 ms, nhanh hơn chừng 150 ms nhờ giải JPEG nhỏ; một lượt 5 819 ms.
- Ở host khung về 3,2–4,4 s sau lệnh, không khác 3,4–3,6 s của 1 280 × 720 trong độ dao động của lượt chụp.
- Ảnh đầy đủ sau khung 640 × 480: chụp 2,9–8,3 s (sau 1 280 × 720 là 2,9–3,4 s), về host 8,6–11,4 s sau lệnh. `PHOTO` không qua khung nhỏ:
  3,7–4,2 s. Nguyên nhân: thanh ghi phơi sáng đếm 1/16 dòng, và ba cỡ cùng ổn định ở 30 ms trong phòng này (8 400, 10 464 và 9 072, tức 525,
  654 và 567 dòng, đúng ba bước 10 ms của mỗi cỡ). Một dòng dài 56,9 µs ở khung đầy đủ (2 844 điểm, 50 MHz), 45,8 µs ở 640 × 480 (2 060,
  45 MHz), 52,9 µs ở 1 280 × 720 (2 644, 50 MHz). Giữ số dòng khi đổi cỡ thì khung đầy đủ phơi 37 ms sau 640 × 480 (quá 25%), 32 ms sau
  1 280 × 720 (quá 8%), và AEC dò lại lâu hơn.
- Live 5 MP: 0,83–1,02 khung/s, khung 2560 × 1920 ~274 KB.

**Quy đổi phơi sáng khi đổi cỡ (E15-T30, 10/11).** `drv_camera_use` quy phơi sáng theo tỉ lệ thời gian dòng, giữ độ lợi, ghi lúc AEC và AGC ở
chế độ tay rồi trả tự động. Boot 147, cùng bài thử 10 lượt `make detect` và 5 lượt `PHOTO`.
- Phép quy đổi đúng: `exposure 10464 -> 8421` khi sang khung đầy đủ (AEC ổn định ở 8 400), `8400 -> 10437` khi về 640 × 480 (AEC ở 10 464).
- Ảnh đầy đủ sau khung `detect`: chờ phơi sáng 6–7 khung, 2,30–2,78 s ở 7 trên 10 lượt, 10–11 khung, 4,1–4,6 s ở 2 lượt, 5 khung 4,5 s ở một lượt
  độ lợi nhảy 33 → 60; chụp 2,9–5,3 s. Hết các lượt 6–8 s của bản không quy đổi, nhưng chưa xuống dưới 6 khung.
- `PHOTO` từ trạng thái nghỉ: chờ 4–5 khung, 1,45–1,93 s, chụp 2,1–2,6 s (2,9–3,2 s ở bản không quy đổi).
- Khung `detect` sau ảnh đầy đủ: 4–7 khung, 0,54–1,07 s ở 8 trên 10 lượt, nhưng **18 khung, ~3 s ở 2 lượt** (chụp 3,2 s): chưa gặp ở bản không quy
  đổi (4–7 khung). Nghi giá trị quy đổi lệch lưới bước 10 ms của cỡ mới (10 437 so với 3 × 3 488 = 10 464; 8 421 so với 3 × 2 800 = 8 400), làm AEC
  dò quanh lưới; chưa thử làm tròn 🔬.
- `detectMs` 1 939–1 947; ảnh `detect` 24,7–30,3 KB; ảnh đầy đủ 195,8–360,6 KB (lượt 360,6 KB độ lợi 60). PSRAM trống lúc đứng yên
  354 336–358 232 B, thấp nhất 194 680 B; RAM nội thấp nhất 22 119 B.
- Hai lượt `PHOTO` hỏng vì MQTT rớt (`No PING_RESP`, `session down` hai lần): một ảnh không gửi được (`ESP_ERR_INVALID_STATE`, vẫn lên thẻ),
  một lệnh không tới board. Một lần ghi thẻ 5 361 ms.

**Làm tròn phơi sáng quy đổi về bước 10 ms của cỡ mới (E15-T31, 11/11).** Boot 156, cùng bài thử 10 lượt `make detect` và 5 lượt `PHOTO`.
- Quy đổi nằm đúng lưới: `exposure 10464 -> 8400` sang khung đầy đủ, `8400 -> 10464` về 640 × 480; AEC dừng ở đúng giá trị ấy cả 15 lượt.
- Khung `detect`: 6 khung (một lượt 7), 0,84–1,03 s, chụp 0,99–1,22 s; không còn lượt 18 khung.
- Ảnh đầy đủ sau khung `detect` và `PHOTO`: 6 khung, 2,31–2,37 s ở cả 15 lượt, chụp 2,94–3,01 s (2,9–5,3 s trước khi làm tròn). `PHOTO` về host
  3,8–4,6 s.
- `detectMs` 1 936–1 946; đủ ảnh cả 15 lượt; PSRAM trống lúc đứng yên 358 232 B, thấp nhất 193 768 B; RAM nội thấp nhất 22 071 B.
- Khung `detect` chờ 6 khung thay vì 4–5 của bản không quy đổi: độ lợi mang từ ảnh đầy đủ (30) về 28 ở 640 × 480 trong hai khung đầu, nên
  phép đếm khung đứng yên bắt đầu lại; chụp lâu hơn bản ấy ~0,3 s nhưng không còn lượt dài.
- Không lượt nào rớt MQTT. Dòng `session down` ở đầu log là lúc bài thử khởi động lại board (`net_wifi: down, reason 8`, tức
  `ASSOC_LEAVE`), không phải rớt mạng.
- Hai lần `No PING_RESP` của boot 147 là rớt thật: board rảnh giữa hai lượt, Wi-Fi vẫn nối (không có `net_wifi: down`), mà ping của esp-mqtt
  (gửi sau 7,5 s im lặng, chờ tới 15 s với keepalive 15 s) không có trả lời; log EMQX ở mức warning không ghi gì lúc ấy. Ba lần của boot 140
  trùng lúc PSRAM còn ~10 KB, chưa gặp lại từ khi bộ đệm còn 384 KB. Log serial của boot 147 không còn, nên chưa khoanh được chặng nào tắc 🔬.

**Chạy dài 30 phút có log mọi chặng (11/11, boot 156).** `make detect` mỗi 30 s; cùng lúc log serial gắn giờ PC, `ping.exe` tới board mỗi giây,
log EMQX mức info, `emqx ctl clients show` mỗi 5 s, và `srhost.live --jsonl`. Log ở `interim/net_soak/20261111/` của data root.
- MQTT không rớt: một phiên suốt 30 phút (`created_at` không đổi qua 328 lần đọc), 64 lần chụp.
- Ping 1 775 gói: trung vị 140 ms, 90% dưới 241 ms, 99% dưới 537 ms, 6 gói quá 1 s, lâu nhất 3,1 s. Mất 2 gói, mỗi lần một quãng ~4 s không có
  trả lời, lúc board rảnh, không có `net_wifi: down`. Độ trễ ấy là của modem sleep (`WIFI_PS_MIN_MODEM`): board thức theo nhịp DTIM của hotspot.
- esp-mqtt cắt phiên khi ping của nó không có trả lời trong 15 s (keepalive 15 s), nên quãng 4 s không đủ. Hai lần rớt của boot 147 cần quãng câm
  từ 15 s trở lên; chưa gặp lại 🔬.
- 3 trên 18 lượt đầu báo `no detections in 30 s` dù tin đã tới: `srhost.live` và `make detect` cùng lưu ảnh và vẽ khung vào một thư mục, một bên
  mở ảnh lúc bên kia đang ghi đè, lỗi của PIL làm `OneShot` bỏ qua tin `detections`. Ghi file nguyên tử và luôn ghi nhận tin đã tới: 46/46 lượt
  sau đó đủ.

**Ảnh đầy đủ hỏng khi phòng tối (11/11, 18:45, boot 173).** `PHOTO` và mọi lượt chụp khung đầy đủ ra `CAMERA_FAIL`: AEC lên độ lợi 234
(ban ngày 27–32), ảnh 2560 × 1920 ở chất lượng 12 nhiễu nên JPEG vượt bộ đệm khung 384 KB, `cam_hal` báo `FB-OVF` liên tục rồi
`NO-EOI`, `Failed to get frame: timeout`. Ban ngày cùng chất lượng 220–310 KB. Khung 640 × 480 vẫn chụp được.
- Chất lượng theo cảnh (E15-T35, boot 175, 176, độ lợi 248): khung chờ ở 50 nặng 229–258 KB; dự đoán ở 40 là 280–316 KB, thật
  274–299 KB (số mũ đo được ~0,76 giữa 50 và 40, contract dùng 0,9). 8 trên 8 lượt `PHOTO`, `DETECT` ra ảnh đủ, 7 lượt ở 40, một ở 50
  vì dự đoán ở 40 vượt ngưỡng 80% đúng 1 KB. Chất lượng 12 đặt lúc đổi cỡ từng để vài khung đầu ở 5 MP tràn (`FB-OVF`), đợt chờ
  1,83–1,93 s; đặt mức thô ngay lúc đổi cỡ: không còn tràn, đợt chờ 4 khung 1,37–1,46 s, chụp 2,51–2,57 s (bản cũ lúc sáng
  2,94–3,01 s). `PHOTO` về host 3,7–4,1 s. Ở độ lợi ấy mức 40 vỡ hạt: nhiễu chiếm dung lượng; khử nhiễu của OV5640 chưa thử 🔬.

### 4.9 Ghi nhật ký (E15-T32, 11/11)

Boot 158: bản đầu, mọi việc nằm trong `svc_vision`, hop ghi chú vào `svc_listen` như hop điếc. Boot 160: bản tách `svc_note` và `svc_journal`
(KẾ HOẠCH §3.19), hop ghi chú không vào `svc_listen`. Cả hai bật cảm biến. `make journal` 1 + 5 lượt ở boot 158, 5 lượt ở boot 160, không ai
nói với máy nhưng phòng có người nói chuyện (khung `detect` thấy 1–4 người). Log ở `interim/journal/20261111/` của data root, bản thu ở
`raw/images/sr-30eda01e4630/`, không vào git.
- Đủ cả 11 lượt: ảnh `detect`, khung, ảnh đầy đủ, `.wav`, `_journal.json`; `noteDropped` 0, `noteSaved` và `saved` đúng ở mọi lượt. WAV 16 kHz,
  một kênh, 16 bit; lượt đầu có tiếng nói −21 đến −25 dBFS từ 0,5 s đến 3,5 s, đuôi im −48 dBFS.
- Lời ghi chú dài 4,8–16,0 s: tiếng người trong phòng giữ đoạn ghi mở, nên chưa lượt nào dừng ở mốc 5 s "chưa ai nói". `qCleanPeak` 123 hop,
  `cleanDropped` 0.
- Hop điếc không giữ được bộ nghe: lượt 4 của boot 158 có `WAKE` 826 giữa lời ghi chú (09:33:05, ghi chú 09:33:01,9–09:33:15,5), rồi
  `REJECT LOW_MARGIN`, vì `wake` chạy trên mọi hop, kể cả hop điếc. Boot 160 không đưa hop ghi chú vào `svc_listen`: 5 lượt, 46 s ghi chú, không có
  `WAKE` hay `REJECT` nào suốt bài thử. Điều này đúng do cấu trúc; 5 lượt chỉ xác nhận, không đo được tỉ lệ.
- `detectMs` 1 946–1 969 khi cảm biến bật (1 936–1 947 khi tắt, §4.8); vùng mượn 30 656 của 32 768 B. Boot 160: RAM nội ở bước `sensors`
  101 771 B, khối lớn nhất 86 016 B; lúc nối mạng 51 375 B, khối lớn nhất 34 816 B; PSRAM ở bước `vision` 337 460 B, bằng boot 158, vì đệm khúc
  16 KB chuyển từ `svc_vision` sang `net_mqtt` mà không đổi cỡ.
- Heartbeat boot 160: RAM nội thấp nhất 22 699 B, PSRAM thấp nhất 78 672 B, PSRAM trống 208 364–246 508 B. Lúc đứng yên thấp hơn §4.8 ~112 KB:
  `sb_note` 97 KB, khối thẻ 8 KB, cảm biến ~4 KB.
- Lời ghi chú ghi xong thì khung `detect` chụp 1,09–1,34 s (6–8 khung đứng yên), ảnh đầy đủ chụp 2,94–2,96 s, gửi 0,3–0,9 s (một lượt 6,2 s),
  ghi thẻ 0,70–0,89 s. Mục nhật ký về host 16,0–29,3 s sau lệnh, phần lớn là lời ghi chú. Khung `detect` của boot 160 chờ 7–8 khung thay vì 6
  (độ lợi 27 so với 30–32), chưa rõ vì cảnh hay vì gì khác; code camera không đổi giữa hai bản 🔬.
- Lượt đầu tiên của boot 158 chậm hẳn: ảnh đầy đủ chụp 7,5 s, ghi thẻ 4,6 s. Chưa gặp lại ở 10 lượt sau 🔬.
- `_journal.json` mang bản `up/sensors` mới nhất: GPS chưa có fix trong nhà (thấy 12 vệ tinh, ăng-ten báo hở), hồng ngoại, gia tốc, con quay,
  nhiệt độ của MPU-6500, ADC 0 mV ở cả bốn kênh vì chưa nối gì (§7). Board C không có cảm biến quang phổ.
- `.JSN` trên thẻ (boot 162, 164, 3 + 3 lượt): `card kept` cả 6 lượt. Bản đầu in vào đệm 8 KB cố định: PSRAM ở bước `vision` 329 160 B,
  thấp nhất 68 784 B. Bản in vào vùng JSON của `nhin_task`: 337 356 B ở bước `vision` (kém bản không có `.JSN` 104 B, chỗ cho một lần đọc
  cảm biến), thấp nhất 79 876 B, `jsonArenaPeak` 7 896 B. Mục thật ~0,8 KB; tệ nhất, 32 khung, ~4,1 KB (tính). Nội dung trên thẻ chưa
  đọc lại: console chưa có lệnh đọc thẻ 🔬.
- Tiếng bíp nhận lệnh lọt vào đầu lời ghi chú (chủ repo nghe thấy, 11/11). Lời ghi chú bắt đầu lúc lệnh được nhận, khi hai tiếng bíp
  1500 Hz còn đang phát; khử vang bớt chúng còn −38 đến −49 dBFS. 5 trên 13 lượt nói bằng giọng ở boot 164 (`000008`, `000009`,
  `000017`–`000019`) có 1500 Hz chiếm 85–97% năng lượng ở 40–140 ms đầu, nền sau đó −29 đến −31 dBFS; các lượt `make journal` không có
  tiếng bíp nên sạch. Tiếng bíp ra loa ở −12 dBFS: khử vang bớt 26–37 dB, nhưng một tiếng thuần trên nền im vẫn nghe rõ.
- Lời ghi chú và ảnh cùng lúc (boot 166, 5 lượt `make journal`, `ghi_task` ghi thẻ trong lúc `nhin_task` chụp): đủ cả 5 lượt, `noteDropped`
  0, `cleanDropped` 0. Khung `detect` chụp ngay sau lệnh, 1,27–1,36 s; ảnh đầy đủ chụp 3,02–3,53 s, ghi thẻ 0,41–0,87 s trong lúc
  `ghi_task` cùng ghi. Mục về host 9,3–15,1 s sau lệnh với lời ghi chú 5,2–12,2 s; bản nối tiếp cần lời ghi chú cộng ~10 s.
- `sb_note` đầy nhất 122–125 hop (~2 s, 65% của 3 s): lượt `detect` chạy trên `nhan_task`, `nhan_task` chép bù một lượt khi xong.
- Chặng `detect` của `svc_vision` (giải JPEG, `detect`, gửi khung) 2,12–2,15 s, so với 3,37–3,46 s khi chụp sau lời ghi chú;
  `detectMs` 1 945–1 949 không đổi. Trong lúc ghi chú `svc_listen` không chạy, nên nhân 0 rảnh hơn cho phần giải JPEG ở ưu tiên 1.
- RAM: `ghi_task` dùng 928 của 4 096 B ngăn xếp ở PSRAM; PSRAM ở bước `vision` 333 340 B (kém 4 KB), thấp nhất 76 748 B; RAM nội ở bước
  `sensors` 101 227 B, khối lớn nhất 81 920 B (kém 4 KB), lúc nối mạng vẫn 34 816 B, vùng mượn vẫn 30 656 B; RAM nội thấp nhất 22 131 B.

### 4.10 Bản ghi, thẻ và gửi bù (E15-T36, 11/11)

Boot 178 và 180, cảm biến bật, chụp bằng `cam shot` trên console, `srhost.live` nhận và gửi ack, broker tắt bằng `docker stop sr-emqx`.
Log và script ở `interim/outbox/20261111/` của data root, ảnh và lời ghi chú ở `raw/images/sr-30eda01e4630/`, không vào git.
- Có broker (boot 178, `PHOTO`, `DETECT`; boot 180, 3 × `DETECT`): mỗi lần chụp `record sent, kept`, ack về 0,0–1,0 s sau bản ghi, dấu
  `IMG/PND/` xoá ngay; host ghi `<stem>_record.json` đủ ba khoá.
- Tắt broker giữa một lần ghi nhật ký (`000003`) rồi chụp 5 lần (`000004`–`000008`, `PHOTO`, `DETECT`, `JOURNAL`, `PHOTO`, `DETECT`):
  ảnh, `.JSN`, `.WAV` ghi thẻ như thường, `sd ls IMG/PND` thấy đúng 6 dấu. Lời ghi chú của `000003` không gửi được, mục vẫn đủ trên thẻ.
- Bật broker: board nối lại sau 13 s, quét thấy 6 mục và gửi bù: `000003` (ảnh 258 KB, lời ghi chú 960 KB) xong trong 5,4 s, cả 6 mục
  trong 21 s, 2,76 MB, kể cả ~8 s chụp `000009` chen giữa. Lệnh `DETECT` ra giữa lúc gửi bù (`000009`) được chụp ngay, khung về host như lúc
  không có việc gửi bù.
- `srhost.live` chỉ đăng ký lại 13 s sau board (paho chờ lùi dần), nên bản ghi `000003`–`000006` và các khúc đầu của ảnh `000007`
  không tới ai; `000008` và `000009` có ack. Lượt gửi lại sau `outbox.retry_s` (120 s) bù `000003`–`000005` có ack trong 0,2–0,4 s; `000006`
  có ack ở lượt ấy, `000007` gửi lại ngay khi phiên mới nối sau khởi động lại (boot 180) và có ack. Hết bài thử không còn dấu nào.
- Giờ chụp host suy ra khớp lúc gõ lệnh trong khoảng 0,4 s ở mọi mục, kể cả mục gửi bù 2–3 phút sau (`000008`: lệnh 20:01:12,2,
  suy ra 20:01:11,8). Mục gửi bù có khung vẽ trên ảnh đầy đủ (`_full_boxes.jpg`) từ `detections` của bản ghi.
- `detectMs` 1 994–2 002 ở boot 178, khi trạng thái mới nằm ở RAM nội (~350 B): vùng mượn 25 536 B, dưới ngưỡng ~25 KB của §4.5. Trạng
  thái của `svc_outbox` và khối điều khiển của `q_ack` sang PSRAM, RAM nội chỉ hơn 32 B: boot 180 vùng mượn 26 560 B, `detectMs`
  1 961–1 968, trong dải theo boot ở §4.5. Chặng `detect` của `svc_vision` 3,48–3,52 s như ở §4.9; con số ấy gồm giải JPEG và gửi khung.
- PSRAM lúc đứng yên 224,4–224,8 KB, thấp nhất 61,9 KB, so với 239,6 KB và 75,4 KB ở §4.9: hai bản ghi 1 592 B và chữ `.JSN` 6 KB
  (đã hạ về 5 KB, bằng phòng in của `net_mqtt`, sau bài thử). `jsonArenaPeak` 9 256 B của 40 960 B. RAM nội thấp nhất 21 327 B.
- Ghi thẻ ảnh đầy đủ 0,50–0,97 s; lần đầu của boot 178 3,7 s, lúc thư mục `B178` còn chưa có, như lượt đầu của boot 158 ở §4.9 🔬.

### 4.11 Một khung cho cả ảnh và `detect` (E15-T37, 12/11)

Boot 183, phòng sáng ban ngày (độ lợi 21–22, phơi sáng 8 400), 5 lượt `make detect`, 2 lượt `PHOTO` và 1 lượt `JOURNAL` qua `cam shot`,
rồi 1 lượt `make detect` kiểm ack. Log ở `interim/camera/20261112/single/` của data root, ảnh ở `raw/images/sr-30eda01e4630/`, không vào git.
- Đủ cả 9 lượt, không `allocate memory failed`, không mất khung. Ảnh 5 MP ở mức 20, 154–158 KB. `detectMs` 1 963–1 964, vùng mượn
  26 560 B. Khung nằm đúng người và ghế trên `_full_boxes.jpg`: cùng một khung nên không còn lệch giữa hai lần chụp.
- Chụp 3,26–3,76 s, trong đó chờ phơi sáng 6–7 khung, 2,33–2,78 s (mỗi khung 5 MP ~0,4 s). Chặng giải JPEG + `detect` + gửi khung
  3,95–4,05 s, tức giải JPEG 5 MP về 512 × 384 ~2,0 s. Gửi ảnh 0,14–0,77 s, ghi thẻ 0,48–0,66 s.
- Khung xong trên board ~7,7 s sau lúc nhận lệnh; `make detect` đủ khung và ảnh 8,3–9,2 s sau lệnh. Khung nhỏ của §4.8 về host
  3,2–4,4 s và ảnh đầy đủ sau đó, nên khung muộn hơn ~4 s, cả lệnh như cũ.
- PSRAM trống lúc đứng yên 225 464 B, **thấp nhất 38 412 B** (61,9 KB ở §4.10): bộ giải JPEG cho ảnh rộng 2 560 điểm lấy ~187 KB tạm
  lúc chạy, tính từ hai số này 🔬. RAM nội thấp nhất 22 007 B. `dropped` frames, clean, events đều 0.
- `make detect` thoát ngay khi có khung và ảnh, trước bản ghi, nên không ack: board gửi bù cả lần chụp sau `outbox.retry_s`
  (`000004`, `000005`). Nó giờ chờ bản ghi tới 5 s sau khi in thời gian; lượt kiểm `000009` có ack 1,0 s sau bản ghi.

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

**Tiếng bíp của chính máy trong âm đã lọc (E10-T9, 11/11).**

Bản thu `stream 11` (micro và tham chiếu) ở boot 166 vừa khởi động, phòng yên: `tone command` × 6, `wake` × 2, `done`, `fail`, cách nhau
~3 s (`interim/aec/beeps1`, script và phép phân tích ở `interim/aec/beeps1_run/`). Chạy lại offline bằng bản soi gương Python của chuỗi
board C (`hpf`, `aec`, `ns_omlsa`, `vad`, `agc`). Mức là dBFS của dải ±80 Hz quanh tần số tiếng bíp trong 0,4 s từ lúc bíp; nền là toàn
dải lúc không bíp. `tone erle` ngay sau loạt bíp: **−0,5 dB**.

| Tầng | Tiếng bíp 1500 Hz | Nền |
|---|---|---|
| Micro | −44 đến −46 | −48,9 |
| `aec`, bộ lọc từ 0 như sau boot | −43 ở tiếng đầu, −55 đến −57 từ tiếng thứ tư | −51,4 |
| `aec` + `ns` | −44 ở tiếng đầu, −59 đến −60 về sau | −55,0 |
| Cả chuỗi, sau `agc` | −21 đến −35 | −29,6 |

- `aec` chỉ bớt 0–13 dB trên tiếng bíp 70–300 ms: bộ lọc bắt đầu từ 0 sau mỗi boot và cần vài giây tiếng loa để học (KẾ HOẠCH §3.5).
- Khử vọng dư thêm 1–4 dB: vọng dư ước bằng `leak × vọng đã ước`, và `ns` dìm mỗi vạch tối đa `ns_floor_db`, −12 dB.
- `agc` nâng nền và phần vọng dư cùng ~25 dB, nên tiếng bíp nổi trên nền như ở micro. So với thước §3.5, ERLE sau khử vọng dư trên tiếng
  bíp chỉ ~12 dB, dưới 20 dB.
- Thử trên bản soi gương: sàn −40 dB và vọng dư × 10 đưa tiếng bíp từ tiếng thứ ba về −68 đến −79; hai tiếng đầu vẫn −47 đến −52 vì
  bộ lọc chưa ước được vọng. Riêng sàn −40 dB hay riêng × 10 chỉ thêm 0–7 dB.
- Bộ lọc học trước bằng bản `echo1` của 04/11 (giả lập lưu trọng số rồi nạp lúc boot) làm tiếng bíp đầu to hơn micro (−39,9 so với −44,4):
  board đã đổi chỗ, đường vọng khác. Lưu trọng số qua boot không dùng được.

**Sau khử vọng dư theo độ ghép (E10-T9, 11/11, boot 167).** Độ ghép loa → micro học lúc chạy, vọng dư giữ thêm 0,4 s sau tham chiếu,
sàn vọng −40 dB trong `ns` (KẾ HOẠCH §3.5, §3.9). Bản soi gương khớp bản C ở mọi ca golden.
- Bản soi gương trên `beeps1` (sau `aec` + `ns`): tiếng bíp nhận lệnh, kể cả tiếng đầu sau boot, −82 đến −94 dBFS trong dải, nền dải
  1500 Hz −80,6; `wake` −88, −89; `done` −72,5, nền 1000 Hz −74,0. Lúc `fail` có người nói trong phòng (toàn dải −28 đến −41 dBFS ở micro),
  nên số ấy không phải vọng. Người gần cộng từ `talker2` giữ −0,07 dB.
- Board, `stream 10` (micro và lối ra của chính board, `interim/aec/beeps2_clean`): ba tiếng bíp nhận lệnh −60,1, −58,6, −56,8 dBFS trong
  dải sau `agc`, `wake` −61,2, nền dải 1500 Hz của lối ra −52,1: dưới nền 5–9 dB. `tone erle` sau 17 s tiếng nói: 15,7 dB.
- Tiếng nói qua loa: `play IMG/B164/000009.WAV` (lời ghi chú 17,7 s của chủ repo), `stream 11` (`interim/aec/beeps2_ref`), đo phần lối ra
  một FIR 10 hop của tham chiếu giải thích được ở từng vạch, nên tiếng phòng không tính vào:

| Tầng | Bản cũ: vọng so với cả lối ra | Bản mới |
|---|---|---|
| `aec` | −10,7 dB, ERLE 27,7 dB | như cũ |
| `aec` + `ns` | −11,0 dB | −27,5 dB |
| Cả chuỗi, như lối ra ghi thẻ | −12,0 dB | −29,4 dB |

  Gain của `ns` đổi theo thời gian làm độ kết hợp giảm, nên phép đo này có thể nhẹ tay với lối ra của `ns`; hai bản đo cùng cách.
- Người gần nói đè lên tiếng nói của loa (`talker2` cộng vào đoạn phát): người −38,7 dBFS dưới vọng −34,5 thì giữ −2,06 dB (bản cũ
  −0,12); người −23,7 dBFS thì −0,20 dB (bản cũ 0,00). Trong cửa 3 dB, nhưng khi bộ lọc đã học, độ ghép × tham chiếu ước dư vọng.
- `make journal` 2 lượt sau bản này: đủ ảnh, lời ghi chú, `.JSN`; `detectMs` 1 963–1 971, vùng mượn 30 656 B như trước: trạng thái thêm
  của `aec` nằm ở PSRAM. `sd cat` của `IMG/B164/000001.JSN` (649 B) và `000009.JSN` (729 B): đủ ba khoá; `journal` và `detections` trùng từng trường với
  `_journal.json` và `<stem>.json` của host; `sensors` là lần đọc mới nhất (`uptimeS` 51 so với bản `up/sensors` 47 host giữ).
- Lệnh console `sd` và `play` với argtable (boot 168, 169): `detectMs` 2 226–2 235 thay 1 958–1 972. Mỗi `arg_str` và `arg_end` xin
  RAM nội lúc đăng ký và giữ mãi: RAM nội lúc nối mạng 50 363 B thay 50 803, khối lớn nhất 32 768 B thay 34 816, nên vùng mượn của
  `detect` hụt một bậc 2 KB. Hai lệnh tự đọc `argv` (boot 171): 50 699 B, khối 34 816 B, vùng mượn 30 656 B, `detectMs` 1 958–1 972.
  Chỉ profile dev và bench có console; `sdkconfig.prod` tắt `APP_CONSOLE`.

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

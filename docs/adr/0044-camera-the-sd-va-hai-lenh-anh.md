# ADR-0044 — Camera, thẻ SD, ngoại vi của board C và hai lệnh "chụp ảnh", "nhận diện"

- **Trạng thái**: Chấp nhận (chủ repo 04/11: "Ok", cả năm điểm theo đề xuất)
- **Ngày**: 2026-11-04
- **Liên quan**: KẾ HOẠCH §2.1, §2.2, §3.18, §4.5, §5.2, §5.3, §6.5, §6.6, §7.3, §7.5; ADR-0030, ADR-0042; TASKS E2-T13,
  E15-T6, E15-T7, E15-T15; `docs/measurements/board_c.md`, `docs/measurements/ram.md` §5

---

## Bối cảnh

Board C có OV5640, thẻ SD, loa, GPS, cảm biến hồng ngoại và hai thiết bị I2C, cả phần cứng đã chạy (`board_c.md`). Firmware
mới chạy chuỗi nghe. Chủ repo muốn hai lệnh nói:

- **"chụp ảnh"**: chụp, lưu thẻ, đẩy ảnh về host qua MQTT.
- **"nhận diện"**: như trên, rồi chạy YOLO26n có sẵn và gửi khung về host.

Năm ràng buộc quyết định thiết kế:

1. **RAM nội là chỗ chật.** Board C còn 48,6 KiB, thấp nhất 20,2 KiB. `detect` lấy 6,4 KB. Đệm DMA của camera ở RAM nội là
   30 KB ngay ở QVGA, cộng lại không vừa.
2. **PSRAM còn 1,39 MB sau `detect`** (`ram.md` §5, số của board B 🔬). RGB565 VGA 614 KB cộng RGB888 VGA 921 KB đã vượt.
3. **Camera chạy liên tục tốn băng thông PSRAM.** Repo face attendance đo suy luận chậm 16,6% khi nhân kia đẩy ~8,7 MB/s qua
   PSRAM (KẾ HOẠCH §6.6), mà `wake` và lệnh chạy suốt.
4. **Nghe không được chậm đi.** `nhin_task` ở nhân 0, ưu tiên thấp nhất, không chạy `detect` ở nhân 1 (ADR-0027).
5. **Board chưa biết giờ:** chưa có SNTP, nên tên file không dựa vào đồng hồ.

## Các phương án

| Câu hỏi | Phương án | Được | Mất |
|---|---|---|---|
| Khuôn khung | RGB565, CPU nén JPEG, đổi RGB888 | không phụ thuộc bộ nén của sensor | hơn 1,5 MB PSRAM, CPU nén ở nhân 0 |
| | **JPEG từ OV5640, giải 1:2 khi cần `detect`** | một khung dùng cho thẻ, MQTT, `detect`; ~0,9 MB PSRAM | thêm một bước giải JPEG 🔬 |
| Sensor giữa hai lần chụp | chạy liên tục | chụp ngay | chiếm PSRAM suốt, nghe chậm đi |
| | **nghỉ, đánh thức khi có lệnh** | không tốn gì lúc nghe | mỗi lần chụp thêm thời gian ổn định sáng và lấy nét 🔬 |
| Đệm DMA camera | **đệm 16 KB ở RAM nội, khung ở PSRAM** | dò dấu kết thúc JPEG trên cả khung, mọi khung đều dùng được | 21 764 B RAM nội, lấy lại từ Wi-Fi và lwIP |
| | PSRAM (`CONFIG_CAMERA_PSRAM_DMA`) | gần như không tốn RAM nội | thử trên board: `esp32-camera` chỉ dò 1 KB cuối, bỏ gần hết khung (`board_c.md` §4.1) |
| Ảnh lên MQTT | base64 trong JSON | dùng lại `gen_payload` | to thêm 33%, tốn vùng JSON |
| | **nhị phân theo khúc, topic riêng** | không qua cJSON, bộ nhớ gửi nhỏ | thêm một khuôn ở `contracts/` |

## Quyết định

### 1. Component mới

| Component | Tầng | Việc | Xuất phát từ |
|---|---|---|---|
| `drv_camera` | L2 | bọc `esp32-camera` 2.1.7: dựng lúc boot, JPEG, khung ở PSRAM, nghỉ qua SCCB | `drv_camera` của repo face attendance, bỏ vòng phơi sáng tay chỉ dùng cho RGB565 |
| `drv_sd` | L2 | SDSPI, FAT ở `/sdcard`, báo thẻ vắng, đầy | viết mới; `check_sd` của app kiểm phần cứng |
| `drv_gps` | L2 | UART1 RX GPIO 20, 9600 baud, giữ vị trí mới nhất; phân tích NMEA ở phần thuần, có test máy tính | viết mới |
| `drv_ir` | L2 | GPIO 19, lọc rung, báo cạnh | viết mới |
| `drv_i2c` | L2 | bus I2C GPIO 1/2 và driver của hai thiết bị ở 0x48, 0x68, mỗi thiết bị một file | viết mới, sau khi nhận dạng bằng thanh ghi |
| `svc_vision` | L4 | `nhin_task`: chụp, lưu, đẩy, nhận diện; `REQUIRES common drv_camera drv_sd ai_engine net_mqtt` | viết mới |
| `svc_sensors` | L4 | `do_task`: đọc GPS, hồng ngoại, I2C, đăng `up/sensors`; `REQUIRES common drv_gps drv_ir drv_i2c net_mqtt` | viết mới |

Mọi ngoại vi của board đều có driver. Bản đầu của các khối này báo trạng thái lên host; dùng chúng vào việc gì thêm thì tính sau.

- **Loa** không cần component mới: `drv_audio` đã có chiều TX sau `APP_SPEAKER_ENABLE`. Bật cho board C, thêm tiếng bíp xác nhận
  theo `drv_audio` của repo face attendance. Khi TX chạy, `bsp_board` thôi giữ GPIO 14 ở mức thấp.
- **Đèn GPIO 42** ở `bsp_board`, đã có.
- Board B không có camera: Kconfig `APP_CAMERA_ENABLE` bỏ nguồn ra khỏi CMake, đúng luật §4.1 của CLAUDE.md.

### 2. Luồng ảnh

```
COMMAND chup_anh | nhan_dien  (sau kiểm người nói, nếu bật)
  dieu_task  -> q_vision
  nhin_task  -> đánh thức sensor, bật đèn, chờ phơi sáng đứng yên, chụp một khung JPEG 1280x720
             -> tắt đèn, sensor nghỉ
             -> xếp ảnh vào outbox của up/image theo từng khúc
             -> nhan_dien: giải JPEG 1:2 thành RGB888 640x360, lên trên chuỗi nghe, ai_engine_detect_run, gửi up/detections
             -> ghi /sdcard/IMG/B<boot>/<seq>.JPG, gửi CAPTURE
```

- Ảnh xếp vào outbox trước, đi trong lúc giải JPEG, nên host thường có ảnh trước khung.
- Không có thẻ thì vẫn đẩy ảnh, sự kiện ghi `saved: false`. Mất MQTT thì ảnh vẫn ở thẻ, chưa gửi lại ở bản đầu.
- Đang chạy mà có lệnh mới thì từ chối ngay với mã `VISION_BUSY`, không xếp hàng.

### 3. Hợp đồng ở `contracts/`

- `mqtt_topics.yaml`: `up/image` (QoS 1, nhị phân), `up/detections` (QoS 1, JSON), `up/sensors` (QoS 0, JSON: vị trí GPS, số vệ
  tinh, cạnh hồng ngoại, số đọc I2C; khi đổi và mỗi 10 s).
- `contracts/vision.yaml`: id lệnh ứng với việc (`chup_anh`, `nhan_dien`), cỡ ảnh, chất lượng JPEG, cỡ khúc, số khung ổn định sáng.
  Không hằng số nào gõ vào code (KẾ HOẠCH §4.9).
- Khúc ảnh: đầu cố định gồm mã lần chụp, số khúc, tổng khúc, tổng byte, CRC32, rồi tới dữ liệu; khúc 16 KB 🔬.
- `up/detections`: mã lần chụp, cỡ ảnh, ms, tới 32 × {tên, điểm, x1, y1, x2, y2}.
- `device_cmd` thêm `CAPTURE` với `mode`, để host chụp được mà không cần nói.
- `event` thêm `CAPTURE`, có `saved` báo ảnh đã vào thẻ, và mã lỗi `VISION_BUSY`, `CAMERA_FAIL`, `DETECT_FAIL`.
- Bộ `host/sets/demo_vi.json` đã có `chup_anh` và `nhan_dien`. `default_vi.json` giữ nguyên 10 lệnh, vì số ấy là số lệnh đăng ký giọng.

### 4. Task và hàng đợi

| Đối tượng | Nội dung |
|---|---|
| `nhin_task` | `svc_vision`, nhân 0, ưu tiên 1, lên 11 trong lượt `detect`, ngăn xếp ở PSRAM như `mqtt_task` vì task này không ghi flash |
| `do_task` | `svc_sensors`, nhân 0, ưu tiên 2, nhịp 1 s, ngăn xếp ở PSRAM; không chạm nhân 1 |
| `q_vision` | depth 1, `app_vision_req_t`; yêu cầu nằm trong hàng tới hết ảnh, đầy thì `dieu_task` ra `ERROR VISION_BUSY`, không chờ |
| `q_dialog` | bắt đầu có người gửi: nơi chốt `COMMAND` gửi với timeout 0, `dieu_task` tra `vision.yaml` rồi đẩy `q_vision` |
| `net_mqtt` | thêm `net_mqtt_publish_image`; chỉ `nhin_task` gọi |

Thêm mạng `detect` vào `app_boot`, trước mọi task, theo E15-T17. `docs/FREERTOS.md` soát cùng commit.

### 5. Ngân sách

| Khoản | RAM nội | PSRAM |
|---|---|---|
| `detect` | 332 B lúc boot | 2 837 040 B |
| Camera: đệm DMA 16 KB, `cam_task`; một khung JPEG 1280 × 720 | 21 764 B | ~184 KB |
| RGB888 640 × 360, đệm giải JPEG, khúc gửi | | 771 KB |
| SDSPI, FAT, `svc_vision` | 2 380 B | |
| `nhin_task`, `do_task` | hai TCB | 20 KB |
| UART1 của GPS, bus I2C | ~3 KB 🔬 | |
| **Cộng** | **~27 KB** | **~3,8 MB của 4,26 MB còn** |

RAM nội thiếu thật: camera ở RAM nội tốn gấp đôi ước tính. Đã gỡ bằng `sdkconfig.board_c`: mã Wi-Fi ra khỏi IRAM, `.bss` và đệm
của Wi-Fi, lwIP ở PSRAM, ngăn xếp các task không chạm flash ở PSRAM, bỏ bảng thanh ghi sensor không lắp, tổng 64 KB. Lúc nghe còn
75,6 KiB, thấp nhất 35,2 KiB (`board_c.md` §4.2), vùng mượn của `ConvLut` (§6.5) đủ 32 KB.

Thời gian đo: "chụp ảnh" 0,67 s chụp, đẩy ảnh không chờ, 0,15–0,4 s ghi thẻ; "nhận diện" thêm 0,33 s giải JPEG và 1,92 s `detect`
(`board_c.md` §4.5).

### 6. Riêng tư

KẾ HOẠCH §7.5 thêm dòng ảnh: ảnh chỉ tạo khi có lệnh; đèn sáng trong lúc chụp; host lưu ở data root, không vào git, như bản thu
tiếng người. Ảnh trên thẻ không mã hoá.

## Thứ tự làm

| Bước | Việc | Xong khi |
|---|---|---|
| 1 | Sửa KẾ HOẠCH theo ADR; `contracts/` và `make gen` (E15-T6) | schema có đối chứng âm, host và firmware dùng code sinh |
| 2 | `drv_camera`, app thử `test_apps/board_c` (E15-T15) | JPEG 1280 × 720, nghỉ và đánh thức; RAM nội đo được |
| 3 | `drv_sd` | ghi đọc lại khớp sha256; rút thẻ không treo |
| 4 | `make product BOARD=C` nạp cả `vision_0`; `detect` dựng lúc boot, `nhin_task` (E15-T7) | board nghe như cũ, RAM nội không dưới mức an toàn |
| 5 | "chụp ảnh" đầu cuối; `host/vision.py` ghi ảnh, `make host-live` | nói lệnh, host có ảnh, sha256 khớp file trên thẻ |
| 6 | "nhận diện" đầu cuối | khung đúng trên ảnh thật, host vẽ được |
| 7 | Đo: độ trễ, RAM, `COMMAND` khi ảnh đang chạy, chạy dài | `docs/measurements/`; ngoài lượt `detect` độ trễ lệnh không tăng; không rớt khung |
| 8 | Loa bíp khi nhận lệnh và khi chụp | nghe được; `wake` không tự thức vì tiếng bíp |
| 9 | `drv_gps`, `drv_ir` | NMEA có test máy tính; GPS có vị trí khi có anten; hồng ngoại không rung |
| 10 | Nhận dạng 0x48, 0x68 bằng thanh ghi, `drv_i2c` | đọc được số thật của cả hai |
| 11 | `svc_sensors`, `up/sensors`, host live hiện | cả ba khối lên host; heartbeat không rớt khung |

Bước 2 và 3 độc lập, bước 5 cần cả hai. Lệnh đầu tiên chạy được ở bước 5. Bước 8–10 độc lập với luồng ảnh.

## Chủ repo đã chốt

1. **Cỡ ảnh** 1280 × 720, cho cả ảnh lưu lẫn `detect` (giải 1:2).
2. **Đèn GPIO 42** bật khi chụp, vừa làm đèn flash vừa báo đang chụp.
3. **Bỏ `down/image`** của E15-T7: đã có camera, và `ai-unit-detect` kiểm khớp từng giá trị.
4. **Hồng ngoại** chưa tự kích chụp: bản đầu chỉ báo lên host.
5. **Vị trí GPS** ghi kèm ảnh khi có vị trí; cần anten trước.
6. **`detect` chạy trên chuỗi nghe** (04/11, sau khi đo; thay ràng buộc 4 trong lượt ấy): dưới chuỗi nghe một ảnh ~3,4 s, trên chuỗi
   nghe 1,92 s. Chuỗi nghe dừng ~1,9 s mỗi lần "nhận diện", khung nằm ở `q_clean`, lệnh nói đúng lúc ấy chậm tới ~3 s. Ngoài lượt ấy
   không đổi.

## Hệ quả

- KẾ HOẠCH đổi ở §2.1, §3.18, §4 (cây thư mục, bảng tầng), §5.2, §5.3, §6.5, §6.6, §7.3, §7.5; TASKS thêm các bước trên.
- Thêm dependency `espressif/esp32-camera` ghim 2.1.7 ở chính `drv_camera`, `espressif/esp_new_jpeg` ghim 1.0.2 ở `svc_vision`
  (KẾ HOẠCH §4.5.1).
- Ống kính hoá ra nét cố định: phần tự lấy nét không dùng trên board này (KẾ HOẠCH §3.19, `board_c.md` §4.3).
- Bước 4 đã xét lại đệm DMA (bảng phương án ở trên). Xét lại nữa khi độ trễ lệnh tăng ở bước 7.

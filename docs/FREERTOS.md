# Sổ kiểm lỗi đồng thời FreeRTOS

Danh mục những kiểu hỏng mà một hệ nhiều task hay dính, kèm **luật của repo này**, **cách kiểm bằng
máy** và **trạng thái hiện tại**. Soát lại mỗi khi thêm một task, một khoá, một hàng đợi, hoặc chuyển
một việc sang nhân khác — đó là lúc các luật dưới đây bị phá (KẾ HOẠCH §5.5).

Bảng task, hàng đợi và khoá là KẾ HOẠCH §5.2 và §5.3. File này không chép lại chúng; nó chỉ hỏi
"chúng có dính lỗi kinh điển nào không".

Ký hiệu: ✅ đã kiểm và sạch · ⚠ có phát hiện, xem §14 · ⏳ chưa kiểm được vì phần liên quan chưa tồn tại.

**Trạng thái ngày lập (26/09/2026): chưa có dòng firmware nào, nên mọi dòng là ⏳.** Cột "Luật" là thứ
được viết vào code ngay từ đầu; cột "Cách kiểm" là thứ chạy khi code có mặt.

---

## 0. Ba chỗ hệ này khác một hệ điều khiển thường

| Chỗ | Vì sao nguy hiểm | Luật đỡ |
|---|---|---|
| **Hạn cứng 16 ms, mất là mất vĩnh viễn** | một khung lỡ làm STFT đứt mạch, AEC và BSS mất trạng thái hội tụ | nhân 1 chỉ có `thu_task` và `sach_task`; không mutex, không mạng, không log trong vòng khung |
| **Ghi flash đóng băng cả hai nhân** | xoá một sector tắt cache hàng chục tới hàng trăm ms 🔬; `thu_task` nằm trong flash nên không chạy được | DMA RX 128 ms; không ghi flash theo nhịp; OTA dừng chuỗi nghe |
| **Wi-Fi chung nhân với `nhan_task`** | Wi-Fi ưu tiên 23 chen bất cứ lúc nào | `q_clean` sâu 1 s; điểm cao nhất xuất ra `heartbeat` |

---

## 1. Vòng đời task

| # | Kiểu hỏng | Luật | Cách kiểm | Trạng thái |
|---|---|---|---|---|
| 1.1 | `vTaskDelete` không trả bộ nhớ ngay — IDLE mới là kẻ dọn | chỉ `net_task` tự xoá, và nó ở nhân 0 nơi IDLE0 chạy được | đọc `app_tasks.c` | ⏳ |
| 1.2 | Task tự xoá mà chưa dọn tài nguyên của nó | `net_task` không xin heap, không cầm khoá | đọc thân task trước `vTaskDelete(NULL)` | ⏳ |
| 1.3 | `vTaskSuspend` xoá phần trễ còn lại | không dùng | `grep vTaskSuspend` ra rỗng | ⏳ |
| 1.4 | Tạo task động có thể thất bại lúc chạy | **mọi task tạo tĩnh** bằng `xTaskCreateStaticPinnedToCore` | thiếu RAM lộ lúc link | ⏳ |

## 2. Lập lịch và ưu tiên

| # | Kiểu hỏng | Luật | Cách kiểm | Trạng thái |
|---|---|---|---|---|
| 2.1 | Bỏ đói: task không bao giờ Blocked → IDLE đói → watchdog `IDLE1` | mỗi vòng lặp có một chỗ chặn; `sach_task` chặn trên `q_frame` | tìm `for(;;)` không có lời gọi chặn | ⏳ |
| 2.2 | `vTaskDelay(0)` không đưa vào Blocked | không dùng | `grep "vTaskDelay(0)"` | ⏳ |
| 2.3 | Xếp ưu tiên theo độ quan trọng thay vì độ gấp của hạn chót | `thu` 17 > `sach` 16; `nhan` 10 > `dieu` 8 > `mqtt` = `noi` 5 > `gui` 4 > `luong` = `net` 3 > việc cửa sổ của `nhan` 2, ngang console | đối chiếu KẾ HOẠCH §5.2 | ⏳ |
| 2.4 | Task ứng dụng chèn Wi-Fi | mọi task ứng dụng dưới 18 | đọc bảng `kTasks[]` | ⏳ |
| 2.5 | Task IDF không ghim chạy sang nhân 1 | `LWIP_TCPIP_TASK_AFFINITY_CPU0`, `ESP_TIMER_TASK_AFFINITY_CPU0`, `MQTT_USE_CORE_0` | `grep` ba khoá trong `sdkconfig.defaults.esp32s3`; ở `bench` in `vTaskGetInfo` từng task lúc boot | ⏳ |
| 2.6 | Khoá log của IDF lấy bằng `portMAX_DELAY` — một `ESP_LOGx` có thể chặn sau lưng | không log trong vòng khung của nhân 1, không log trong vùng khoá, không log trong ISR | `grep ESP_LOG` trong `svc_front/src` chỉ ra đường khởi tạo và lỗi hiếm | ⏳ |

## 3. Chặn đúng cách

**Luật**: mỗi vòng lặp có ít nhất một chỗ đưa task vào Blocked, và mọi chỗ chờ có hạn.

| Chờ gì | Dùng gì | Hạn |
|---|---|---|
| Một khung DMA | `drv_audio_read_frame` (bên trong `i2s_channel_read`) | một khung cộng biên; hết hạn là lỗi phần cứng, log và đếm |
| Khung thô | `xQueueReceive(q_frame)` | 100 ms; hết hạn thì nạp watchdog và đếm |
| Khung sạch | `xQueueReceive(q_clean)` | 100 ms; 1 tick khi `svc_listen` có bước của cửa sổ đang chạy theo luồng hay cửa sổ đã chốt chờ chấm. Hết tick mà không có khung, `nhan_task` chạy liền các bước của cửa sổ (cao độ, rồi một khối mạng, khoảng 84 ms với mạng bề rộng 160 theo `measurements/latency.md` §18) tới khi `q_clean` có khung chờ hay hết việc; các bước ấy chạy ở ưu tiên 2 nên `mqtt_task`, `gui_task`, `luong_task` chen vào ngay khi có việc, và tick chờ giữa hai đợt là lượt của IDLE0 (KẾ HOẠCH §5.2, §5.4) |
| Sự kiện, lệnh | `xQueueReceive(q_dialog / q_cmd / q_speak)` | 1 s, rồi nạp watchdog |
| Nhịp `gui_task` | `vTaskDelayUntil` 100 ms | — |

## 4. Thời gian và tick

| # | Kiểu hỏng | Luật | Trạng thái |
|---|---|---|---|
| 4.1 | `pdMS_TO_TICKS` về 0 tick | `FREERTOS_HZ = 1000` | ⏳ — ràng buộc phụ thuộc cấu hình, hạ tick là dính lại |
| 4.2 | Đo thời gian bằng đồng hồ tường mà SNTP chỉnh | đo khoảng bằng `esp_timer_get_time()`; `t_us` của khung luồng tiếng cũng lấy từ đó | ⏳ |
| 4.3 | Nhịp khung trôi theo tick | nhịp của chuỗi nghe là **nhịp DMA**, không phải timer | ⏳ |

## 5. Ngăn xếp

| # | Kiểu hỏng | Luật | Cách kiểm | Trạng thái |
|---|---|---|---|---|
| 5.1 | Tràn ngăn xếp im lặng, lần chạy xanh che lần panic | `dev` bật `COMPILER_STACK_CHECK_MODE_NORM` và canary | `bench_mem` in watermark từng task sau 10 phút tải thật | ⏳ |
| 5.2 | Mảng lớn trên ngăn xếp trong đường xử lý khung | vùng làm việc của `dsp_*` do người gọi cấp từ heap lúc boot, không đặt trên ngăn xếp | `-Wstack-usage=1024` cho `dsp_*`, `lang_*` | ⏳ |
| 5.3 | Nhầm đơn vị watermark | nhân `sizeof(StackType_t)` | đọc code | ⏳ |
| 5.4 | Ngăn xếp ở PSRAM bị chạm khi cache tắt | chỉ `mqtt_task` có ngăn xếp ở PSRAM; không task nào của nhân 1 | đọc `sdkconfig` và bảng task | ⏳ |

## 6. Heap

| # | Kiểu hỏng | Luật | Cách kiểm | Trạng thái |
|---|---|---|---|---|
| 6.1 | Rò heap | xin một lần lúc boot, không bao giờ trả | `soak` ghi heap nội và PSRAM mỗi phút, đường phẳng | ⏳ |
| 6.2 | Phân mảnh: còn trống nhiều mà mảnh liền không đủ | vùng lớn (`aec`, `bss`, pool khung, đệm DMA) xin **trước** Wi-Fi | in `heap_caps_get_largest_free_block` sau mỗi bước của `app_boot` | ⏳ |
| 6.3 | Cấp phát sau boot | cấm (CLAUDE.md §4.1); `dsp_*` không có `malloc` nào | `check_purity.py`; `grep malloc` trong `svc_*` chỉ ra `*_init` | ⏳ |
| 6.4 | `prod` tắt poisoning nên hỏng heap ngoài hiện trường vô hình | `soak` chạy với `ci` (poisoning đầy đủ) trước mỗi bản phát hành | — | ⏳ |

## 7. PSRAM, cache và ghi flash

| # | Kiểu hỏng | Luật | Cách kiểm | Trạng thái |
|---|---|---|---|---|
| 7.1 | ISR chạm PSRAM hoặc mã trong flash khi cache tắt | ISR của I2S ở IRAM (`I2S_ISR_IRAM_SAFE`), chỉ chạm bộ đếm trong RAM nội | đọc callback trong `drv_audio` | ⏳ |
| 7.2 | **Ghi flash đóng băng `thu_task` quá chiều sâu DMA** | DMA RX 8 × 256 mẫu = 128 ms; chỉ ghi flash khi đổi cấu hình, đổi bộ lệnh, hiệu chuẩn | E14-T7: ghi NVS liên tục 10 phút, đếm khung mất | ⏳ |
| 7.3 | Đệm DMA ở PSRAM | đệm DMA luôn `MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL` | đọc `drv_audio` | ⏳ |
| 7.4 | Tranh chấp băng thông MSPI giữa flash, PSRAM và Wi-Fi làm chậm `sach_task` | trọng số `command` đọc từ PSRAM chỉ trong `LENH` | E14-T6 đo µs đỉnh của `sach_task` trong `LENH` so với `NGHE` | ⏳ |
| 7.5 | `nhan_task` ghi `set.json` hay chạy `ai_engine_command_prepare` lâu quá 1 s đệm của `q_clean` | ghi một lần mỗi bộ lệnh mới, sau khi `lang_vi` đọc được mọi dòng và `prepare` nhận bộ ấy; bộ trùng `version` với bộ đang dùng (bản retained gửi lại mỗi lần nối) không ghi; `ctc` chỉ chép nhãn, còn `rnnt` chạy mạng dự đoán một lần cho mỗi ngữ cảnh chưa gặp, bộ lệnh lớn có thể chạm 1 s 🔬 | log in thời gian `lang_vi` và thời gian ghi; trên board B `clean_dropped` và `frames_dropped` của `heartbeat` không tăng qua một lần đổi bộ lệnh | ⏳ |

## 8. Hàng đợi và đệm

| # | Kiểu hỏng | Luật | Cách kiểm | Trạng thái |
|---|---|---|---|---|
| 8.1 | Gửi bỏ qua kết quả → mất khung im lặng | mọi `xQueueSend` kiểm giá trị trả về; thất bại thì tăng bộ đếm có tên | `grep` các lời gửi không nằm trong điều kiện | ⏳ |
| 8.2 | Nhận không kiểm kết quả | mọi `xQueueReceive` nằm trong điều kiện | `grep` | ⏳ |
| 8.3 | Người gửi nhân 1 chờ người nhận nhân 0 | `thu_task` và `sach_task` gửi với timeout 0 | `grep "xQueueSend.*q_clean"` phải thấy `0` | ⏳ |
| 8.4 | Pool nhỏ hơn nhịp tiêu thụ | pool `q_frame` 8 ô = 128 ms biên | `heartbeat` khai điểm cao nhất `q_frame` | ⏳ |
| 8.9 | Hai task cùng giữ một ô pool: người ghi ghi đè ô người đọc đang đọc | một ô chỉ đi qua đúng một vòng `q_free` → `thu_task` → `q_frame` → `sach_task` → `q_free`; `thu_task` chỉ ghi vào ô vừa lấy từ `q_free` | đọc `app_tasks.c`; test đẩy `sach_task` chậm quá 8 khung thì chỉ `frames_dropped` tăng, dữ liệu ô không bao giờ lẫn `seq` | ⏳ |
| 8.5 | Stream buffer bị ghi nửa khung | `sach_task` kiểm `xStreamBufferSpacesAvailable` trước, không đủ thì bỏ cả khung | đọc code; `host` không bao giờ thấy khung cụt | ✅ `svc_report_stream_push` @ `253ddf7`; máy nhận nghẽn 30 s trên board B: không khung cụt nào, mọi khung thiếu đều nằm ở `streamDropped` |
| 8.6 | Stream buffer có hai người ghi | chỉ `sach_task` ghi `sb_stream` | đọc code | ✅ `xStreamBufferSend` chỉ có ở `svc_report_stream_push`, gọi từ `sach_task` @ `253ddf7` |
| 8.7 | Hở `seq` không ai xử lý | `sach_task`: hở không quá `chain.gap_keep_s` thì `dsp_afe_resume`, dài hơn thì `dsp_afe_reset` (KẾ HOẠCH §4.5.5); `nhan_task` thấy hở thì đặt lại STFT và bộ dò cao độ, câu đang mở thành `REJECT FRAME_GAP` (KẾ HOẠCH §5.4); `host` ghi hở vào json | phép kiểm chặn mạng 5 s ở E5-T10 | ⏳ |
| 8.8 | Callback esp-mqtt làm việc dài | callback chỉ phân tích rồi bỏ vào `q_cmd` / `q_cmdset` | đọc `app_wiring.c` | ⏳ |
| 8.10 | Bản mới từ `down/commands` ghi đè bộ lệnh `nhan_task` đang đọc | hai ô bộ lệnh của `net_mqtt`, cấp lúc boot; task esp-mqtt chỉ ghi vào ô nó đang điền dở, ô nó rút lại được khỏi `q_cmdset` (bản chờ), hoặc, khi `q_cmdset` rỗng, ô không phải ô gửi sau cùng: `nhan_task` nhận theo thứ tự gửi và xong ô trước rồi mới nhận ô sau, nên ô ấy đã xong. Bản mới thay bản chờ bằng `xQueueOverwrite` | đọc `commands_slot` trong `net_mqtt.c`; trên board B gửi ba bộ lệnh liền nhau trong lúc `nhan_task` đang ghi `set.json`: bộ cuối được dùng, không bộ nào lẫn dòng của bộ khác | ⏳ |

## 9. Khoá

| # | Kiểu hỏng | Luật | Cách kiểm | Trạng thái |
|---|---|---|---|---|
| 9.1 | Mutex chờ vô hạn | cấm `portMAX_DELAY` cho mutex (CLAUDE.md §4.1) | `grep` ra rỗng | ⏳ |
| 9.2 | Deadlock | chỉ một khoá, `m_storage`, là khoá lá | thêm khoá thứ hai phải ghi thứ tự vào KẾ HOẠCH §5.3 trước | ⏳ |
| 9.3 | Đảo ngược ưu tiên giữa nhân 1 và nhân 0 | task nhân 1 không lấy mutex nào; dữ liệu chung chỉ qua spinlock vài chục byte | `grep xSemaphoreTake` trong `svc_front` ra rỗng | ⏳ |
| 9.4 | Giữ spinlock quá lâu | vùng `portENTER_CRITICAL` chỉ chép struct, không gọi hàm | đọc code | ⏳ |

## 10. ISR

| # | Kiểu hỏng | Luật | Trạng thái |
|---|---|---|---|
| 10.1 | Log, `malloc`, I2C trong ISR | cấm; chỉ `*FromISR` + trả cờ yield | ⏳ |
| 10.2 | Float trong ISR | cấm: ngữ cảnh FPU không được lưu cho ISR trên Xtensa | ⏳ |
| 10.3 | Mutex trong ISR | cấm; cần khoá thì `portENTER_CRITICAL_ISR` | ⏳ |

## 11. Watchdog

| # | Kiểu hỏng | Luật | Trạng thái |
|---|---|---|---|
| 11.1 | Task có vòng lặp mà không đăng ký watchdog | mọi task trong bảng §5.2 đăng ký và nạp mỗi vòng | ⏳ |
| 11.2 | Task có watchdog chặn vô hạn → watchdog kêu nhầm tên | mọi chỗ chờ có hạn (§3), hết hạn thì nạp rồi chờ tiếp | ⏳ |
| 11.3 | IDLE1 không bao giờ chạy | `sach_task` chặn trên `q_frame` giữa hai khung; ở tải tới ~51% (NSNet-16k L, KẾ HOẠCH §5.6) IDLE1 luôn có lượt | ⏳ |

## 12. FPU và nhân

| # | Kiểu hỏng | Luật | Trạng thái |
|---|---|---|---|
| 12.1 | Task không ghim dùng float bị IDF ghim ngầm vào nhân nó chạy lần đầu | mọi task ghim khi tạo | ⏳ |
| 12.2 | Việc có hạn cứng nằm chung nhân với Wi-Fi | chỉ `thu` + `sach` ở nhân 1 | ⏳ |

## 13. Đo tải và mất khung

| # | Cần biết | Đo bằng | Trạng thái |
|---|---|---|---|
| 13.1 | Tải từng nhân | `FREERTOS_GENERATE_RUN_TIME_STATS` ở `bench` | ⏳ |
| 13.2 | Khung mất ở DMA | callback tràn của I2S → `dma_overflows` | ⏳ |
| 13.3 | Khung mất giữa `thu` và `sach` | `frames_dropped` | ⏳ |
| 13.4 | Khung mất giữa `sach` và `nhan` | `clean_dropped` + điểm cao nhất `q_clean` | ⏳ |
| 13.5 | µs đỉnh của `sach_task` | `SR_PROFILING` ở `bench`, ghi histogram | ⏳ |

Cả năm số đi trong `heartbeat` (KẾ HOẠCH §7.3), nên chạy dài không cần cáp.

## 14. Phát hiện đang mở

| # | Bẫy | Chỗ | Hướng ra | Row |
|---|---|---|---|---|
| **P1** | Cấp phát sau boot (6.3) | `*_to_json` sinh từ `contracts/` cấp phát qua cJSON mỗi lần dựng payload; `gui_task` dựng một `telemetry` mỗi giây | `net_mqtt` gọi `cJSON_InitHooks` với bộ cấp phát trên một vùng nhớ xin lúc boot, đặt lại sau mỗi lần gửi; `heartbeat` khai điểm cao nhất của vùng ấy | E5-T9 |

## 15. Lệnh soát nhanh

```bash
# mutex chờ vô hạn — phải ra rỗng
grep -rn "portMAX_DELAY" firmware/components firmware/main | grep -i "semaphoretake"

# gửi hàng đợi bỏ qua kết quả — mỗi dòng phải nằm trong một điều kiện
grep -rn "xQueueSend\|xStreamBufferSend" firmware/components firmware/main

# nhân 1 không lấy khoá — phải ra rỗng
grep -rn "xSemaphoreTake" firmware/components/svc_front

# cấp phát sau boot — chỉ được thấy trong *_init
grep -rn "malloc\|heap_caps_" firmware/components/svc_* firmware/components/dsp_* firmware/components/lang_*

# task IDF ghim về nhân 0
grep -n "AFFINITY_CPU0\|MQTT_USE_CORE_0\|WIFI_TASK_PINNED_TO_CORE_0" firmware/sdkconfig.defaults.esp32s3

# tầng phụ thuộc và độ thuần
python3 tools/check_layers.py && python3 tools/check_purity.py
```

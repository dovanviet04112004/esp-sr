# ADR-0028 — Đường chip của `speaker` dùng ReDimNet2 b3; `models_0` 11 MB, `command_ctc` đọc thẳng từ flash

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-28)
- **Ngày**: 2026-10-28
- **Liên quan**: KẾ HOẠCH §3.17, §5.2, §6.1, §6.6; ADR-0018, ADR-0021, ADR-0025, ADR-0027; TASKS E11-T35;
  `docs/measurements/latency.md` §31, `docs/measurements/speaker.md` §7

---

## Bối cảnh

Đường chip của `speaker` chạy ReDimNet2 b0 (ADR-0021). E11-T35 đưa b3 nhiều ngôn ngữ (`b3-vb2+vox2+cnc2_v0-lm`,
4,51 M tham số) lên board B:

| | b0, hàng kl | b3, hàng kl |
|---|---|---|
| EER int8 trên vật liệu khảo sát | 4,00% | 2,99% |
| Giữ lệnh được nhận ở 1% / 5% người lạ lọt | 95,8% / 98,2% | 98,2% / 99,4% |
| Một cửa sổ 1,5 s, một nhân (đồ thị ở flash) | 342,7 ms | ~1,40 s 🔬 hàng kl, 1 401,9 ms hàng `timing` |
| `.espdl` | 1,48 MB | 5,40 MB |
| PSRAM cho tensor | 1,43 MB | 4,12 MB |

Hai chỗ chặn:
- **Flash.** Ảnh model nằm trong `models_0` 6 MB (ADR-0018). `command_ctc` 3,45 MB, b3 5,40 MB và cohort 0,29 MB
  là ~9,14 MB.
- **PSRAM.** Khi nghe chỉ còn trống 0,51 MB với b0 (`ram.md` §4), mà tensor b3 cần thêm ~2,7 MB.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| Giữ b0 | không đổi gì | EER cao hơn b3 một phần tư |
| **b3; bỏ `ota_1` và `voice`; `models_0` 11 MB** | ~1,9 MB dư cho `ns`, `wake`, `synth` | không còn khe OTA thứ hai; `synth` ghép mẩu (E12-T2) mất phân vùng riêng |
| b3; hai khe app 2,5 MB, bỏ `voice`, `storage` 1 MB | còn OTA | `models_0` ~9,75 MB, dư ~0,6 MB; app chỉ còn biên 0,38 MB |
| b3 hai nhân (ADR-0027) | 1,31 s thay 1,40 s | cần đo đỉnh µs của `sach` trước khi bật |

PSRAM: §6.6 đã xếp thứ tự gỡ, trong đó có để trọng số `command_ctc` ở flash như speaker. Việc ấy trả lại ~3,37 MB.

## Quyết định

Chủ repo chọn ngày 28/10:
- **Đường chip chạy b3**, hàng int8 `kl`, deploy bằng `make speaker-deploy` như b0, một nhân (`AI_ENGINE_CORES_ONE`).
- **Bảng phân vùng:**
  - bỏ `ota_1` và `voice`;
  - `models_0` thành 11 MB, bắt đầu ở 0x320000, ngay sau `ota_0`;
  - `nvs`, `storage` và `coredump` giữ nguyên địa chỉ, nên bộ lệnh, ngưỡng và danh sách người nói còn nguyên qua lần
    nạp lại.
- **`command_ctc` để ở flash:** mục `ESPDL` của nó mang cờ `IN_FLASH` như mục `speaker`, PSRAM chỉ giữ tensor của nó.
  Cờ nằm ở `deploy.weights_in_flash` của `configs/models/command_ctc.yaml`.

## Hệ quả

- **Không còn OTA.** Một khe app không cập nhật qua mạng được. E13 muốn OTA thì phải xếp lại bảng, ví dụ khi ảnh model
  nhỏ đi. Đổi bảng phân vùng là nạp lại qua cổng CH340 (§6.1).
- **`synth` ghép mẩu** (E12-T2) đặt mẩu tiếng trong phần dư của `models_0` hay trong `storage` khi tới lượt.
- **Mẫu giọng của b0 không dùng được với b3.** `model_id` đổi, nên board coi người đã đăng ký là chưa đăng ký; mỗi
  người phải đăng ký lại (`make speaker-enrol`).
- **Xác minh chậm hơn.** Mỗi lệnh thêm ~1,4–1,5 s 🔬, chưa đạt mức ~1 s của điều kiện cắm (ADR-0021); chủ repo nhận.
  `xac_task` giữ nhân 0 chừng ấy, dưới 2 s của `q_clean`; đo `clean_dropped` qua lệnh được xác minh.
- **Bước của `command` chậm đi** khi đọc trọng số từ flash: đo trên board B trước khi chốt, kể cả phần đuổi kịp đầu
  cửa sổ (§5.4).
- **Vùng mượn 79 680 B** (ADR-0025) phải còn trong RAM nội của firmware sản phẩm lúc speaker chạy. Không cấp được thì
  op đọc trọng số tại chỗ, chậm hơn mà cùng kết quả. Đo trên board B.
- **Xét lại** khi: OTA thành việc phải làm; ảnh model vượt 11 MB; hai nhân qua phép đo của `sach` (ADR-0027).

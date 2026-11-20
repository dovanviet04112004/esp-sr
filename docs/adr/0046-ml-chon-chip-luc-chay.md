# ADR-0046 — `ml/` chọn chip lúc chạy: đích ESP-PPQ, thư mục int8 và phép làm tròn của các bản vá mô phỏng theo chip

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-11-20
- **Liên quan**: KẾ HOẠCH §3.14, §4.4; ADR-0045; fork `esp-sr-s31`: ADR-1000, TASKS_S31 S31-T5

---

## Bối cảnh

Fork `esp-sr-s31` cần lượng tử lại mọi model cho ESP32-S31 (S31-T6). `ml/` ghim S3 ở mấy chỗ: `TARGET = "esp32s3"` ở
`ptq_espdl.py` và `tasks/detect/quant.py`; mọi nhánh ghi kết quả lượng tử vào `<run>/int8…`; `DATA_CACHE_BYTES` của bản
vá bố cục là dcache S3; bản vá mô phỏng `rmsnorm_as_espdl` và `layernorm_as_espdl` làm tròn đầu ra bằng `floor(v + 0,5)`
viết tay; `expf_s3`, `exp_table_s3` mang tên S3. Lượng tử S31 chạy ở repo này trên chính các run huấn luyện của S3 (fork
không có `ml/artifacts`), nên kết quả S31 không được đè kết quả S3.

Dữ kiện đọc từ mã nguồn, ngày 20/11:

- ESP-PPQ 1.3.11: `esp32s3` là nền `ESPDL_S3_INT8`/`INT16`, `esp32s31` là `ESPDL_INT8`/`INT16`, nhóm của `esp32p4`.
  `RoundingPolicy.ROUND_HALF_UP` của nó đúng là `floor(v + 0,5)`.
- esp-dl 3.3.14, `dl_define_private.hpp`: `CONFIG_ROUND_HALF_EVEN_ENABLED` bật cho ESP32-P4 và ESP32-S31, tắt cho S3;
  `tool::round` theo đó làm tròn nửa về chẵn (`fcvt.w.s … rne`) trên S31, nửa lên trên S3.
- `expf` của S31 là đúng `libm/math/sf_exp.c` của picolibc mà `expf_s3` mô phỏng; bản RISC-V chỉ có phép float32 rời,
  không gộp nhân-cộng, phép chia IEEE (board S3 cho thấy `__divsf3` trùng IEEE, `docs/measurements/parity.md`).

## Các phương án

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| **A. Chip chọn một lần cho tiến trình** (`configs/common/chip.yaml`, `SRPIPE_CHIP`) | không đổi chữ ký nào trên hàng chục lối gọi; S3 là mặc định, không phải đặt gì | trạng thái toàn tiến trình: hai chip trong một tiến trình không chạy được | — |
| B. Truyền `target` qua mọi hàm | rõ từng lối gọi | sửa `quantize`, `Simulator`, `Int8Net`, `applied` và mọi nhánh; xung đột với mọi việc đang làm ở `ml/` | — |

## Quyết định

Chọn **A** (chủ repo, 20/11), và làm luôn phần làm tròn của các bản vá mô phỏng cho S31.

1. `ml/configs/common/chip.yaml` khai chip mặc định `esp32s3` và với mỗi chip: tên đích ESP-PPQ, thư mục kết quả lượng tử
   (`int8` cho S3, giữ nguyên; `int8_esp32s31` cho S31), cỡ dcache. `SRPIPE_CHIP` chọn chip khác, đọc ở `core/config.py`
   như `SRPIPE_DATA_ROOT`; tên lạ thì dừng ngay.
2. `ptq_espdl.target()` thay hằng `TARGET`; `detect` dùng cùng hàm. Mọi nhánh lấy thư mục int8 của chip; config đã giải của
   run mới mang chip, nên `cfghash` tách run S31 khỏi run S3.
3. `rmsnorm_as_espdl` và `layernorm_as_espdl` làm tròn đầu ra bằng `ppq_tensor_round(v, out.rounding)`: nửa lên trên S3 —
   đúng `floor(v + 0,5)` như trước —, nửa về chẵn trên S31 như `tool::round` của esp-dl. `conv_s16_as_espdl` vốn đã theo
   `out.rounding`.
4. `expf_s3`, `exp_table_s3` đổi tên thành `expf_picolibc`, `exp_table`: cùng phép trên cả hai chip.
5. Phần còn lại của các bản vá mô phỏng (thứ tự cộng, phép gộp dưới `-ffast-math`, nghịch đảo thay phép chia) mô hình hoá
   kernel esp-dl của S3; với S31 chúng là 🔬 tới khi so mô phỏng `esp32s31` với bản C (S31-T6) và board S31 (S31-T7).

## Hệ quả

- KẾ HOẠCH §4.4 thêm `configs/common/chip.yaml`; §3.14 ghi phép làm tròn theo nền của bản vá mô phỏng.
- S3 không đổi: mọi test của `ml/` vẫn xanh, và mô phỏng một nhánh S3 trước và sau ra cùng số nguyên.
- S31: một nhánh lượng tử, mô phỏng và xuất được với `SRPIPE_CHIP=esp32s31`, kết quả ở `int8_esp32s31/`.
- Xét lại khi cần hai chip trong một tiến trình, hay khi board S31 cho thấy esp-dl S31 tính khác các bản vá.

# ADR-0045 — Kernel của `ai_engine` có bản C thuần, op gọi kernel bằng tên không gắn chip

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-11-20
- **Liên quan**: KẾ HOẠCH §3.14, §4.5.2, §4.5.3 luật 5, §4.5.7; ADR-0026; fork `esp-sr-s31`: ADR-1000, TASKS_S31 S31-T4

---

## Bối cảnh

Fork `esp-sr-s31` đưa sản phẩm sang ESP32-S31 (RISC-V, ADR-1000 của fork). `ai_engine` dịch không được cho chip đó:
76 hàm kernel nằm trong 12 file PIE của S3 (`kernels/*_esp32s3.S`), 20 file op gọi chúng bằng tên `*_esp32s3` ở khoảng
190 chỗ, mỗi op tự khai một khối `extern "C"`, và nhiều op lấy địa chỉ kernel vào bảng con trỏ. Ngoài kernel còn sáu chỗ
gắn với S3: `CONFIG_ESP32S3_DATA_CACHE_SIZE` ở `espdl_conv_lut.cpp` (hai dòng) và `espdl_pointwise_s8.cpp`; ROM cache và
`CONFIG_ESP32S3_DATA_CACHE_*` ở `espdl_preload.cpp`; `divide`, `round_half_up` bằng asm trong `espdl_float_s3.hpp`; một
`floor.s` riêng ở `espdl_global_stats.cpp`.

Chủ repo chốt 20/11: bản C, phép kiểm asm–C và việc gỡ sáu chỗ trên làm ở repo này trước; fork merge về và chỉ thêm phần
chọn của S31. Sản phẩm S3 không được đổi một bit nào.

## Các phương án

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| **A. Op gọi tên không hậu tố, một header nối tên sang bản đã dựng lúc tiền xử lý** | tên đúng nghĩa trên mọi chip; thêm kernel SIMD của chip khác không đụng op; mã máy của S3 giữ nguyên | đổi tên ~190 chỗ trong 20 op | — |
| B. Op giữ tên `*_esp32s3`, linker gán tên đó sang bản C trên chip khác (`--defsym`) | không sửa op | trên S31 hàm mang tên `_esp32s3` lại là C; tới kernel SIMD của S31 vẫn phải đổi tên; 76 dòng `--defsym` trong CMake, và linker không tự kéo đích của `--defsym` ra khỏi thư viện | — |
| C. Bảng con trỏ hàm chọn lúc chạy | không đụng tên | gọi gián tiếp đổi mã máy của S3, không còn chứng minh được "không đổi bit nào" bằng so mã máy | — |

Đây là quyết định cấu trúc; số đo là phép kiểm ở mục Quyết định, không phải đối chứng giữa các phương án.

## Quyết định

Chọn **A** (chủ repo, 20/11).

1. **Bản C**: `kernels/<họ>_s8.c` (bản int16 mang `_s16`), C thuần, mỗi hàm cùng chữ ký với bản asm và mang hậu tố `_c`
   (`ai_engine_pw_conv_rows_s8_c`); cùng phép làm tròn và bão hoà của PIE S3, từng bit. File `.S` không đổi một ký tự.
2. **Tên không hậu tố**: `kernels/espdl_kernels.hpp` khai 76 nguyên mẫu `extern "C"` bằng tên không hậu tố, sau khi
   include `espdl_kernels_impl.hpp` của bản đang dựng: `kernels/impl/pie_esp32s3/` (`#define <tên> <tên>_esp32s3`) hoặc
   `kernels/impl/c/` (`#define <tên> <tên>_c`). Đổi tên ở tiền xử lý nên địa chỉ op lấy vẫn là của chính kernel đã chọn,
   không có hàm bọc. Mỗi op bỏ khối `extern "C"` riêng và include header chung.
3. **Kconfig `AI_ENGINE_KERNELS`** (`pie` | `c`): `pie` chỉ có trên `esp32s3` và là mặc định ở đó; chip khác chỉ có `c`.
   CMake đưa `.S` hay `.c` vào danh sách nguồn và chọn thư mục `impl/` theo lựa chọn (KẾ HOẠCH §4.5.3 luật 5).
4. **Hằng và bước float theo chip**: `chip/<IDF_TARGET>/espdl_chip.hpp`. Bản `esp32s3` mang cỡ và dòng dcache từ
   `CONFIG_ESP32S3_DATA_CACHE_*`, cùng `divide` và `round_half_up` bằng asm (nội dung hiện nay của `espdl_float_s3.hpp`,
   `floor.s` của `espdl_global_stats.cpp` dùng lại `round_half_up`). `ops/espdl_float.hpp` không gắn chip giữ `bits_of`,
   `sqrt_newton` và include header chip. `ops/espdl_float_ieee.hpp` có `divide` (`a / b`) và `round_half_up` (`floorf`
   có kẹp) bằng C chuẩn, cho chip không có asm riêng.
5. **Nạp trước dcache**: `runtime/espdl_preload_<IDF_TARGET>.cpp`; bản S3 là `espdl_preload.cpp` đổi tên, không đổi nội
   dung. Chip khác thêm file của nó.
6. **Kiểm**:
   - trên board S3, `ai_engine/test_apps/unit/main/test_kernels.cpp`: mỗi hàm trong 76 nhận dữ liệu giả ngẫu nhiên từ
     một bộ sinh xorshift32 hạt cố định, gọi bản `_esp32s3` và `_c`, so từng byte; `divide`, `round_half_up` asm so với
     bản IEEE trên dải float, gồm các biên; in digest SHA-256 của từng hàm;
   - trên máy tính, `ai_engine/test_apps/host`: dịch `kernels/*.c` bằng gcc của máy, cùng bộ sinh và hạt, in digest;
     `make ai-kernels` so digest của máy tính với digest của board, khớp từng bit;
   - đối chứng âm: một biến thể C cố ý làm tròn nửa xuống phải làm phép so đỏ;
   - toàn mạng: `ai-unit` với `AI_ENGINE_KERNELS=c` trên S3, mọi probe vẫn khớp mô phỏng từng bit như bản `pie`;
   - S3 không đổi: mã máy (`objdump -d`) của mọi op giống hệt trước và sau khi đổi tên, và `ai-unit` mặc định vẫn xanh.

## Hệ quả

- KẾ HOẠCH §4.5.2 (cây của `ai_engine`), §4.5.7 (test và `make ai-kernels`), §3.14 (bước float32 theo chip) sửa trước
  code.
- Ảnh firmware của S3 không đổi mã máy; bản C chỉ vào ảnh khi chọn `AI_ENGINE_KERNELS=c`.
- Fork chỉ thêm file: `chip/esp32s31/espdl_chip.hpp`, `runtime/espdl_preload_esp32s31.cpp`; Kconfig tự cho S31 bản `c`.
- Xét lại khi có kernel SIMD của chip khác (S31-T8 của fork): thêm `kernels/impl/pie_<chip>/` và cho `pie` có trên chip đó.

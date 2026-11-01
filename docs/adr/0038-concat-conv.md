# ADR-0038 — Op `ConcatConv`: tích chập 1 × 1 đọc thẳng các phần của Concat, gộp các lượt đổi số mũ quanh nó

- **Trạng thái**: Không dùng (đo 01/11, `detect.md` §10): khớp từng giá trị nhưng mỗi ảnh chậm hơn 61 ms. Code ở nhánh
  `feat/firmware-ai-engine-concat-conv`. Trước đó: chấp nhận theo KẾ HOẠCH §3.18 bước 6 và 7 (chủ repo 31/10: "tiếp tục
  tối ưu mà không làm giảm chất lượng")
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §3.18, §6.5; ADR-0034, ADR-0035, ADR-0037; TASKS E15-T16; `docs/measurements/detect.md` §8

---

## Bối cảnh

Lượt đo của lock 31/10 (`detect.md` §8): Concat tốn 78 ms trên 20 op, RequantizeLinear 67 ms, trong đó 53 ms là đổi int8
↔ int16 quanh `model.16`, `19`, `22`. Mỗi Concat chép các phần của nó ra PSRAM cho đúng một tích chập 1 × 1 đọc lại.

Đuôi khối C3k2 `model.16`, ở 64 × 64:
- cv1 ra y, int8, số mũ −5; Slice cắt y0, y1;
- hai lượt đổi số mũ đưa y0, y1 về −4, số mũ của Concat;
- Concat [y0, y1, f] 96 kênh;
- đổi sang int16, số mũ −12;
- cv2 là ConvLut int16 1 × 1;
- đổi kết quả về int8 cho `model.17`.

Mỗi bước ghi một map ra PSRAM rồi bước sau đọc lại: Concat 13,8 ms, đổi sang int16 20,4 ms, đổi về int8 11,6 ms, Slice
6,6 ms. `model.19` cùng dạng ở 32 × 32. Các Concat còn lại trước một tích chập 1 × 1 nối các phần cùng số mũ:
- Concat trong C3k, `m.0/Concat`, trước `m.0/cv3`;
- `model.6/Concat`, `model.13/Concat`, `model.10/Concat`, `model.18/Concat`.

Phép đổi số mũ của esp-dl trên S3 là phép dịch:
- số mũ ra lớn hơn: dịch phải, làm tròn nửa lên, rồi bão hoà;
- số mũ ra nhỏ hơn: nhân với luỹ thừa của 2, bão hoà.

Phép ấy làm được trên từng giá trị khi dựng hàng, nên gộp vào op vẫn ra đúng các số nguyên ấy.

## Các phương án

| Phương án | Lợi ước | Mất |
|---|---|---|
| Giữ các op | — | — |
| Op chép Concat và đổi số mũ nhanh hơn | nhỏ: mỗi lượt đã gần trần băng thông PSRAM | — |
| **Một op: dựng mỗi hàng Concat trong cache từ các phần, đổi số mũ khi chép, chạy tích chập trên hàng ấy** | ~80–110 ms một ảnh 🔬 | một op mới |

## Quyết định

1. **Bản vá xuất `concat_conv`**, chạy sau `upsample_concat_conv`. Nó thay một ConvLut 1 × 1 bước 1 một nhóm, int8 hay
   int16, có bảng hay không, cùng Concat kênh mà nó đọc riêng, khi:
   - ConvLut int16 thì đọc Concat qua một RequantizeLinear sang int16 mà chỉ nó đọc;
   - mỗi đầu vào của Concat là một map int8, hoặc phần kênh liền của một map (Slice bước 1 hay một đầu ra của Split trên
     trục kênh), có thể qua một RequantizeLinear int8;
   - Slice, Split hay RequantizeLinear của một phần chỉ Concat đọc; map nguồn thì op khác vẫn đọc được.

   Nút đọc các map nguồn, filter, bias. Thuộc tính gồm, với mỗi phần, kênh đầu, số kênh và độ dịch sang số mũ của Concat;
   thêm độ dịch nới sang int16 và số mũ tích chập. Khi ra của ConvLut có một RequantizeLinear sang int8 đọc, op ghi luôn
   bản int8 ấy làm đầu ra thứ hai, và RequantizeLinear bỏ đi.
2. **Op `ConcatConv`** (`ops/espdl_concat_conv.cpp`): với mỗi hàng r, dựng hàng Concat ở vùng hàng chung như ADR-0037. Mỗi
   phần chép kênh của nó từ hàng r, qua phép dịch của esp-dl, nới sang int16 khi tích chập int16. Op chạy kernel 1 × 1 của
   esp-dl trên hàng ấy, tra bảng bằng kernel `lut` của repo, rồi ghi bản int8 của hàng ra bằng cùng phép dịch. Trọng số
   chép vào vùng mượn như ConvLut, tối đa 32 KB, lớn hơn thì theo lát kênh ra (ADR-0034).
3. `UpsampleConcatConv` giữ nguyên. Concat trước các tích chập mà `cache_sized_convolutions` đã cắt thành lát kênh, như
   `model.8/cv2`, `model.9/cv2` và `model.22/cv1`, `cv2`, giữ đường cũ.

## Hệ quả

- Số mỗi ảnh, khớp từng giá trị và PSRAM của đồ thị ghi ở `detect.md` §8 sau lượt đo 🔬.
- Vùng hàng chung lớn bằng hàng Concat rộng nhất, kể cả khi nới sang int16: 64 × 96 × 2 = 12 KB ở `model.16`.

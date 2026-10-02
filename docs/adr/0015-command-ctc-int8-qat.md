# ADR-0015 — `command` `ctc` lên chip bằng QAT trên hiệu chuẩn percentile, int8 thuần

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-10-02
- **Liên quan**: KẾ HOẠCH §3.12, §3.14; TASKS E11-T12, E11-T19; `docs/measurements/command.md` §2;
  `docs/measurements/latency.md` §14

---

## Bối cảnh

esp-dl trên S3 nhận một số mũ luỹ thừa 2 cho cả tensor, nên mỗi nhánh đi thang lượng tử của KẾ HOẠCH §3.14. Mạng float
của run `20261002_128545c-dirty_64e7a4` còn trượt Cửa 3 (lệnh điểm cao nhất đúng 85/112, "tắt" nghe thành "bật"), nên
không bậc nào "đạt cửa" để dừng: thang đo đủ bốn bậc và chọn bậc mất ít nhất so với float trong ngân sách µs của §3.3.
Phải chọn bây giờ vì mạng phải lên board để đo cả chuỗi `ai_engine_command_*` trên mạng thật (E11-T19).

## Các phương án

Cùng run, cùng 64 câu hiệu chuẩn, cùng 2 000 câu thử và cùng phiên board của Cửa 3 (`δ₁` 300‰, `δ₂` 50‰, luật phần
của KẾ HOẠCH §3.12); mô phỏng int8 dưới bốn bản sửa ESP-PPQ của nhánh, đúng số chip tính (`command.md` §2).

| Phương án | Được | Mất | Số đo: lỗi đơn vị · lệnh đúng · nhận đúng · nhận nhầm |
|---|---|---|---|
| float (đối chứng) | — | không chạy được trên chip | 34,45% · 85/112 · 74/112 · 2/86 |
| bậc 2: percentile | không học thêm; cách hiệu chuẩn tốt nhất trong bốn | 1,89 điểm lỗi đơn vị | 36,34% · 83/112 · 69/112 · 3/86 |
| bậc 3: int16 cho 1, 2, 4 tích chập đứng đầu bảng sai số từng lớp | tới bốn câu nhận đúng hơn bậc 2 | phép int16 trên chip; lỗi đơn vị ngang bậc 2, trong nhiễu | 36,13–36,36% · 83–84/112 · 70–73/112 · 2–3/86 |
| **bậc 4: QAT trên percentile** | lỗi đơn vị thấp nhất; lệnh đúng 86/112, hơn float một câu; int8 thuần nên cùng phép và cùng µs với bậc 2 | một lượt học trên GPU (2 000 bước) mỗi lần mạng đổi | **35,75% · 86/112 · 68/112 · 3/86** |

Mọi dòng int8 trừ minmax kém dòng nhận đúng cao nhất (73/112) không quá `quant.gate_tie` 5 câu, cỡ một sai số chuẩn của
phép đếm trên 112 câu, nên lỗi đơn vị trên 2 000 câu thử quyết như luật của §3.14.

## Quyết định

Mạng `command` `ctc` trên chip là đồ thị `qat` của run trên: hiệu chuẩn percentile, cân bằng lớp và sửa bias của bậc 1,
rồi QAT 2 000 bước trên đồ thị ấy; không lớp nào int16. `make ctc-deploy ROW=qat` xuất nó vào `firmware/models/command/`
và khoá nó trong `contracts/models.lock.json`; khối `command_ctc` của `quant.yaml` ghi hiệu chuẩn percentile.

## Hệ quả

- `quant.yaml`: `command_ctc` lấy `calibration: percentile` thay mặc định KL.
- Mỗi lần học lại mạng: `make ctc-ptq`, `make ctc-qat`, rồi `make ctc-deploy ROW=qat`; bậc 3 không vào sản phẩm.
- Xét lại khi: mạng float đạt Cửa 3 mà int8 làm nó trượt; mạng đổi cỡ hoặc đổi dữ liệu học; hay QAT lâu hơn, tốc độ học
  cao hơn gỡ thêm phần int8 làm mất. Lỗi đơn vị `val` của đồ thị học gần như không đổi suốt 2 000 bước (31,38% ở bước 0
  và ở bước 2 000), nên chưa tách được phần QAT hơn percentile đến từ việc học hay từ thang hiệu chuẩn trên lô 16.

# ADR-0021 — `speaker` ReDimNet2 b0 lên chip: int8 thuần, hiệu chuẩn kl

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-10-11
- **Liên quan**: KẾ HOẠCH §3.14, §3.17; TASKS E11-T25, E11-T26; `docs/measurements/speaker.md` §6;
  `docs/measurements/latency.md` §25

---

## Bối cảnh

Đường chip của `speaker` (KẾ HOẠCH §3.17) đưa ReDimNet2 b0 học sẵn lên ESP32-S3 qua esp-dl. Muốn cắm vào chuỗi, b0
phải đạt hai cửa:

- một lần chạy ≤ 1 s trên cửa sổ 1,5 s;
- sau int8 giữ ≥ 95% lệnh được nhận ở cả 1 m và 3 m, với ≤ 5% người lạ lọt.

Phải chọn một hàng của thang lượng tử (KẾ HOẠCH §3.14) để đo và chốt cả hai cửa. CLAUDE.md §4.3 không cho chốt cấu
hình lượng tử khi chưa có bảng đối chứng và ADR.

## Các phương án

Cùng run `20261010_5a45475-dirty_94e5a9`, cùng 64 cửa sổ hiệu chỉnh, cùng 209 cửa sổ của chủ repo và 777 cửa sổ người
lạ, k = 10, 20 lần rút (`speaker.md` §6). Mô phỏng ESP-PPQ dưới các bản vá của nhánh, đúng số chip tính. Cột 1 m và
3 m gộp các phiên của khoảng cách ấy, ở 5% người lạ lọt.

| Phương án | Được | Mất | Số đo: EER · 1 m · 3 m |
|---|---|---|---|
| float (đối chứng) | — | không chạy được trên chip | 4,00% · 97,8% · 99,4% |
| **bậc 2: kl** | EER bằng float; qua cửa chất lượng; khớp chip từng bit, 1128,5 ms | 3 m kém float 1,9 điểm (2 cửa sổ phiên 28/09) | **4,00% · 98,9% · 97,5%** |
| bậc 2: percentile, minmax, mse | không | EER gần gấp đôi | 7,47–8,55% |
| bậc 3: 2–16 op đầu bảng sai số từng lớp lên int16 | embedding gần float hơn (cosine 0,51 → 0,72) | EER 5,0–6,6%; 3 m trượt từ 4 op; op int16 chậm hơn | 5,02–6,55% · 96,1–98,9% · 93,7–96,2% |
| bậc 4: QAT trên kl | có thể lấy lại 3 m | một lượt học trên GPU mỗi lần mạng đổi; cần chủ repo cho | chưa chạy |

## Quyết định

b0 trên chip là hàng `kl` của thang: hiệu chuẩn kl, cân bằng lớp và sửa bias của bậc 1, không op nào int16, đồ thị ở
`<run>/int8/kl/graph.native`. Hàng ấy qua cửa chất lượng của §3.17.

Về thời gian, hàng ấy chạy 1128,5 ms một cửa sổ, nhỉnh hơn 1 s. Đã tối ưu hết cách không đổi số nguyên (`latency.md`
§25); phần còn lại là các nhân float của esp-dl và vòng lặp theo vị trí của conv depthwise. Chủ repo nhận mức ấy ngày
11/10, nên b0 cắm vào chuỗi (E11-T26).

## Hệ quả

- `quant.yaml` không đổi: nhánh `speaker` dùng hiệu chuẩn mặc định kl.
- Bậc 3 không vào sản phẩm. Bậc 4 chỉ chạy khi chủ repo cho, và chỉ khi cần lấy lại phần 3 m.
- E11-T26 cắm hàng kl: khâu đặc trưng của b0 và hậu xử lý theo bốn bước, mẫu giọng trên flash, lệnh `deploy`.
- Muốn nhanh hơn nữa thì phải đổi mạng (nhỏ hơn b0, học trên lưới của hợp đồng), cửa sổ (ngắn hơn 1,5 s) hay chip
  (P4); hướng nào cũng phải đi lại thang và bản dò.
- Xét lại khi: ngưỡng thời gian của §3.17 đổi; esp-dl có nhân int8 cho LayerNorm hay Softmax; hay b0 được học lại trên
  dữ liệu của board.

# ADR-0047 — GTCRN học sẵn vào khe `ns`: C float32 viết tay, khe nhận phổ phức, đổi lúc chạy với OM-LSA

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-11-22
- **Liên quan**: KẾ HOẠCH §3.2, §3.9, §3.16, §4.5.2, §4.5.5, §6.2; ADR-0014; `measurements/afe/compare.md` (mục GTCRN),
  `measurements/afe/ns.md` §10; TASKS E9-T15, E9-T16, E9-T17, E9-T18

---

## Bối cảnh

GTCRN (Rong và cộng sự, ICASSP 2024; mã MIT, commit ghim `502ebfab`), checkpoint DNS3 chính thức, hơn OM-LSA, NSNet2 và
RNNoise trên bàn so. Nhưng chấm bằng thước của bộ nhận dạng thì nó thua sàn: `wake` bắt 26/37 so với 33/35, lệnh kém nhất
được nhận 18% so với 60%. Lý do là checkpoint khử cả vang (`ns.md` §10.1). Sửa gain mà không học lại cũng không tới sàn
(§10.2). Chủ repo không cho học lại trên dữ liệu tự dựng. Ngày 22/11 chủ repo vẫn quyết đưa GTCRN lên chip, tối ưu như các
model khác, và muốn một cấu hình đổi được lúc chạy giữa GTCRN và OM-LSA.

Dữ kiện đo ngày 22/11:

- Khe `ns` của `contract-v1` chỉ nhận công suất 257 vạch. GTCRN cần phổ phức: nó nhận độ lớn, phần thực, phần ảo.
- Một bước GTCRN chạy dòng viết lại được bằng các op esp-dl có sẵn, khớp `StreamGTCRN` tới 7,4e-6 so với đỉnh, qua 400
  bước của một phiên board. Các trạng thái tách thành 14 tensor.
- ESP-PPQ lượng tử được đồ thị ấy (332 op ONNX, trong đó 58 Slice, 36 Transpose), nhưng gain lệch quá xa bản float:

  | Cách lượng tử | Gain lệch trung vị | Ở vạch có gain float > 0,1 |
  |---|---|---|
  | int8 `minmax` | 22 dB | 47 dB |
  | int8 thang `ns` (`kl`, cân bằng trọng số, sửa bias) | 23 dB | 9,5 dB |
  | lớp đầu và lớp ra mặt nạ int16 | 5,6 dB | 28 dB |
  | mọi op int16 trừ 14 GRU | 12 dB | 7,6 dB |
  | cả GRU int16 | không chạy: ESP-PPQ không có đường int16 cho GRU | — |

  Nhiễu lượng tử đã bằng 31% tín hiệu ngay ở conv đầu, rồi lên 1–24 lần qua bộ mã hoá. GTCRN nhận phổ tuyến tính, không
  có bước chuẩn hoá ở lối vào. BN có bias và khối TRA đi qua sigmoid của công suất, nên mạng không giữ được hình dạng khi
  mức tín hiệu đổi. Tiếng board nằm ở khoảng −47 dBFS.
- Mỗi bước có 507 413 phép nhân-cộng trong các lớp, cộng 50 688 phép trong ô GRU theo thời gian, tức 34,9 triệu mỗi giây.
  Bộ lọc ERB thưa: viết tay thì chỉ còn khoảng 2 000 phép thay cho 61 440. Có 50 054 tham số, 196 KB ở float32.

## Các phương án

| Phương án | Được | Mất | Số đo |
|---|---|---|---|
| A. esp-dl int8 hay int16 | dùng lại đường của mọi model; nhanh trên PIE | gain lệch 7,6–47 dB | bảng trên |
| B. Học lại có lượng tử (QAT) | int8 nhỏ, nhanh | là học lại; chủ repo không cho | — |
| **C. C float32 viết tay** | chất lượng đúng bản float; không còn 332 op rời; bản Python soi gương được từng bit | phải tự tối ưu vòng lặp; FPU của S3 vô hướng | bản thử có dáng GTCRN trên board C: 52 ms một bước viết thẳng, 28–29 ms khi đệm sẵn hàng vào (`ns.md` §10.3) |
| D. Không đưa lên chip | không tốn gì | trái quyết định của chủ repo | — |

## Quyết định

Chọn **C** (chủ repo, 22/11).

1. `ai_engine/src/ns_gtcrn/` là GTCRN chạy dòng viết tay bằng C float32. Trọng số là checkpoint DNS3 ghim, xuất ra một mục
   float32 của ảnh model. Ở `ml/` có bản soi gương float32 viết bằng vòng lặp numba theo đúng thứ tự cộng của C, nên C khớp
   nó từng bit. Golden có đối chứng âm, như mọi khối.
2. **Khe `ns` nhận phổ phức** 257 vạch, thực và ảo xen nhau, thay cho công suất. OM-LSA tự tính công suất từ phổ. Đây là
   đổi hợp đồng đã đóng băng, làm trong một commit có `!` theo CLAUDE.md §1.3.
3. **Đổi lúc chạy.** NVS `afe/ns_engine` (u8: 0 là OM-LSA, 1 là mạng đã dựng), gieo 0, đổi bằng `SET_CONFIG`.
   `svc_front` cấp vùng trạng thái của khe đủ cho bản lớn hơn, và đổi bản trên `sach_task` giữa hai khung bằng
   `dsp_afe_set_ns`. Hàm này dựng lại trạng thái của bản mới. Kconfig `AI_ENGINE_NS_BACKEND` thêm `gtcrn` và chỉ quyết
   mạng có được biên dịch vào hay không.
4. **Đo trên board trước khi cắm vào chuỗi**: µs trung bình và đỉnh mỗi bước, có Wi-Fi. Bản thử đầu trên S3 đã vượt
   nhịp, 28–29 ms cho bước 16 ms (`ns.md` §10.3). Nếu kernel tối ưu vẫn vượt nhịp cạnh `wake` và `command`, thì bản dựng S3
   để `AI_ENGINE_NS_BACKEND` khác `gtcrn`, còn fork S31 mang GTCRN.
5. Mặc định vẫn là OM-LSA. Chỉ đổi mặc định khi GTCRN hơn sàn trên phiên thu qua board có nhiễu nền, theo luật chọn của
   KẾ HOẠCH §3.9.

## Hệ quả

- KẾ HOẠCH §3.2, §3.9, §4.5.2, §4.5.5, §6.2 viết lại theo các mục trên. §4 thêm `ai_engine/src/ns_gtcrn/` và bản soi gương.
- TASKS: E9-T16 là C và bản soi gương của GTCRN; E9-T17 là đổi khe sang phổ phức; E9-T18 là công tắc lúc chạy.
- RNNoise-16k và NSNet-16k, nếu có ngày được dựng, cũng nhận phổ phức và tự tính công suất.
- Xét lại khi một bản lượng tử của GTCRN có gain lệch dưới 1 dB, hoặc khi board cho thấy C float32 không vừa nhịp ở cả S3
  lẫn S31.

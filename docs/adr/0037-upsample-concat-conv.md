# ADR-0037 — Op `UpsampleConcatConv`: tích chập 1 × 1 của cổ YOLO đọc thẳng map phóng đôi và map kia

- **Trạng thái**: Chấp nhận theo KẾ HOẠCH §3.18 bước 6 và 7 (chủ repo 31/10: "tối ưu xử nốt yolo cho done"); chủ repo
  chưa xem riêng ADR này
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §3.18; ADR-0031, ADR-0034, ADR-0036; TASKS E15-T13; `docs/measurements/detect.md` §8

---

## Bối cảnh

Ở cổ YOLO26n, `model.14` phóng đôi map 32 × 32 × 128 bằng Resize gần nhất, `model.15` nối nó với map 64 × 64 × 128 của
`model.4` thành Concat 64 × 64 × 256, và `model.16/cv1`, ConvLut 1 × 1, đọc lại Concat ấy. Resize ghi 512 KB, Concat đọc
1 MB rồi ghi 1 MB ra PSRAM: ~43 ms trong 1 891 ms một ảnh. `model.11`, `model.12`, `model.13/cv1` cùng dạng ở 32 × 32.
Resize, hai đầu vào của Concat và Concat cùng một số mũ, nên Resize và Concat chỉ chép.

## Các phương án

| Phương án | Lợi ước | Mất |
|---|---|---|
| Giữ ba op | — | — |
| **Một op: dựng mỗi hàng của Concat trong cache rồi chạy tích chập trên hàng ấy** | đo 1 891 → 1 833 ms một ảnh | một op mới |

## Quyết định

1. **Bản vá xuất `upsample_concat_conv`**, chạy sau `conv_lut`, thay ConvLut 1 × 1 bước 1 một nhóm có bảng, int8, cùng Concat
   kênh của hai map mà nó đọc riêng, khi:
   - một đầu vào là Resize gần nhất, `asymmetric`, `floor`, phóng đôi cả hàng lẫn cột, chỉ Concat đọc;
   - hai đầu vào và Concat cùng số mũ.

   Nút đọc map trước Resize, map kia, filter, bias; giữ các thuộc tính của ConvLut, thêm `up_first` cho thứ tự trong Concat.
2. **Op `UpsampleConcatConv`** (`ops/espdl_upsample_concat_conv.cpp`): với mỗi hàng ra r, chép hàng r/2 của map nhỏ, mỗi
   điểm ảnh hai lần, và hàng r của map kia vào một hàng Concat ở vùng PSRAM chung, rồi chạy kernel 1 × 1 của esp-dl trên hàng
   ấy và tra bảng bằng kernel `lut` của repo. Op xin vùng mượn như ConvLut, tối đa 32 KB: filter lớn hơn vùng thì chạy
   theo lát kênh ra, mỗi lát chép vào vùng một lần rồi qua mọi hàng, bảng tra đi đúng số kênh của lát.

## Hệ quả

- Board B, vùng 32 KB: một ảnh 1 833 ms, từ 1 891, khớp ESP-PPQ từng giá trị trên 8 ảnh. `model.16/cv1` 41,6 ms,
  `model.13/cv1` 28,9 ms gồm cả Resize và Concat (`detect.md` §8).
- Xin cả filter 48 KB của `model.13/cv1` thì vùng của cả mạng lên 48 KB và một ảnh 1 805 ms, vì mọi ConvLut có lát to
  hơn; giữ mức 32 KB của ADR-0034 vì RAM nội của sản phẩm.
- Vùng hàng chung của op thêm 16 KB PSRAM.

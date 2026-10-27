# ADR-0025 — `speaker` trên chip: `ConvBlock` mượn RAM nội cho trọng số của nó trong lúc mạng chạy

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-28)
- **Ngày**: 2026-10-28
- **Liên quan**: KẾ HOẠCH §3.14, §6.5; TASKS E11-T35; `docs/measurements/latency.md` §31

---

## Bối cảnh

Trên board B, b3 (E11-T35) chạy 3,64 s một cửa sổ, trong đó 21 `ConvBlock` chiếm 2,43 s. Khối của b3 rộng 96, 144 và
192 kênh, nên hai pointwise của một khối cần 18–74 KB trọng số. Đồ thị đọc trọng số thẳng từ flash (`IN_FLASH`).

App đo cho ba kết quả (`latency.md` §31):
- Pointwise với trọng số đã nóng trong cache chạy như nhau dù trọng số ở flash, PSRAM hay RAM nội.
- dcache của S3 không giữ dòng nóng: đọc 8 KB dữ liệu khác chen giữa hai lượt pointwise 144 kênh làm lượt sau chậm 34%.
- Mỗi hàng của khối chuyển x, y và các hàng vòng qua cache, nên trọng số liên tục bị đẩy ra và đọc lại từ flash.

KẾ HOẠCH §6.5 cấm model chiếm RAM nội. Giữ luật ấy thì phần chậm này không gỡ hết được.

## Các phương án

| Phương án | Được | Mất | Số đo, ms một khối stage 0 / 1 / 5 |
|---|---|---|---|
| Như cũ | — | — | 289 / 386 / 106; 21 khối 2,43 s |
| Chỉ PSRAM: lô R hàng, mỗi pha một pointwise; hàng theo dải P điểm ảnh | giữ luật §6.5 | vòng R + 2 hàng ở PSRAM, tới ~0,5 MB; stage nhỏ chậm đi | 215 / 222 / 35; 21 khối ~1,6 s 🔬 |
| Khoá dòng cache bằng hàm ROM `Cache_Lock_DCache_Items` | không tốn RAM nội | IDF dặn không gọi trong ứng dụng; cache dùng chung hai nhân, AFE và `wake` ở nhân kia mất phần cache bị khoá | chưa đo |
| **Mượn RAM nội trong lúc mạng chạy; hàng theo dải 4 điểm ảnh** | khối nhanh như khi trọng số ở RAM nội | trái luật §6.5; mỗi lượt chạy cần ≤ 77 KB RAM nội liền | 149 / 118 / 14, kể cả lần chép; 21 khối ~0,93 s 🔬 |

## Quyết định

Chủ repo chọn mượn RAM nội ngày 28/10. Chi tiết ở KẾ HOẠCH §3.14 và §6.5.

- **Ai mượn:** khối chạy nhân chung, tức C khác 24 và 48. Mỗi khối ghi lại số byte nó cần lúc dựng.
- **Cấp và nhả:** `EspdlNet::step` của đồ thị có khối như thế cấp một vùng RAM nội cỡ khối lớn nhất ngay trước lượt
  chạy, và nhả ngay sau lượt ấy. Các khối mượn vùng ấy lần lượt.
- **Mỗi khối chép vào đó lúc bắt đầu** mọi trọng số nó đọc ở từng điểm ảnh:
  - tap của hai depthwise;
  - hai pointwise;
  - các bias và BN;
  - conv trước khối, nếu có.

  b3 cần tới 77 KB (stage 5); lần chép mất 2,5 ms.
- **Cấp không được** thì khối đọc trọng số tại chỗ: chậm hơn, cùng kết quả.
- **Mỗi hàng chạy theo dải 4 điểm ảnh:** depthwise rồi pointwise của một dải xong mới sang dải sau.

## Hệ quả

- **§6.5 có ngoại lệ thứ hai** cho luật mọi model ở PSRAM. Ngoại lệ chỉ sống trong lúc `speaker` chạy.
- **Cần đo trên firmware sản phẩm** RAM nội liền còn lại lúc `xac_task` chạy speaker. Không đủ 77 KB thì speaker chậm
  lại chứ không sai.
- **b0 không đổi:** khối 24 và 48 kênh chạy nhân trải phẳng và không mượn.
- **Xét lại** khi:
  - RAM nội lúc chạy speaker không đủ chỗ;
  - hay một model khác cũng cần mượn.

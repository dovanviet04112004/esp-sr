# ADR-0025 — `speaker` trên chip: op của `ai_engine` mượn RAM nội cho trọng số và hàng vòng trong lúc mạng chạy

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-28)
- **Ngày**: 2026-10-28
- **Liên quan**: KẾ HOẠCH §3.14, §6.5; TASKS E11-T35; `docs/measurements/latency.md` §31

---

## Bối cảnh

Trên board B, b3 (E11-T35) chạy 3,64 s một cửa sổ, trong đó 21 `ConvBlock` chiếm 2,43 s và 33 `PointwiseS8` 0,53 s.
Khối của b3 rộng 96, 144 và 192 kênh, nên hai pointwise của một khối cần 18–74 KB trọng số. Vài `PointwiseS8` còn
lớn hơn: `red_dim` 1728 → 72 cần 138 KB, conv 2016 → 128 cần 258 KB. Đồ thị đọc trọng số thẳng từ flash
(`IN_FLASH`).

App đo cho ba kết quả (`latency.md` §31):
- Pointwise với trọng số đã nóng trong cache chạy như nhau dù trọng số ở flash, PSRAM hay RAM nội.
- dcache của S3 không giữ dòng nóng: đọc 8 KB dữ liệu khác chen giữa hai lượt pointwise 144 kênh làm lượt sau chậm 34%.
- Mỗi hàng chuyển đầu vào, đầu ra và các hàng giữa qua cache, nên trọng số liên tục bị đẩy ra và đọc lại từ flash.

KẾ HOẠCH §6.5 cấm model chiếm RAM nội. Giữ luật ấy thì phần chậm này không gỡ hết được.

## Các phương án

| Phương án | Được | Mất | Số đo, ms một khối stage 0 / 1 / 5 |
|---|---|---|---|
| Như cũ | — | — | 289 / 386 / 106; 21 khối 2,43 s |
| Chỉ PSRAM: lô R hàng, mỗi pha một pointwise; hàng theo dải P điểm ảnh | giữ luật §6.5 | vòng R + 2 hàng ở PSRAM, tới ~0,5 MB; stage nhỏ chậm đi | 215 / 222 / 35; 21 khối ~1,6 s 🔬 |
| Khoá dòng cache bằng hàm ROM `Cache_Lock_DCache_Items` | không tốn RAM nội | IDF dặn không gọi trong ứng dụng; cache dùng chung hai nhân, AFE và `wake` ở nhân kia mất phần cache bị khoá | chưa đo |
| **Mượn RAM nội trong lúc mạng chạy; hàng theo dải 4 điểm ảnh** | khối nhanh như khi trọng số ở RAM nội | trái luật §6.5; mỗi lượt chạy cần 79 680 B RAM nội liền | 149 / 118 / 14, kể cả lần chép; 21 khối 919 ms đo |

Cùng vùng mượn, `PointwiseS8` theo lát chép vào đó trên app đo: `red_dim` 42,4 → 17,1 ms, conv 2016 → 128 87,7 →
29,0 ms, `head` 44,8 → 23,0 ms. Khi hàng vòng của khối cũng nằm trong vùng ấy: một khối stage 0 149,7 → 109,5 ms,
stage 1 118,8 → 107,2 ms, stage 2 32,3 → 28,6 ms.

## Quyết định

Chủ repo chọn ngày 28/10, theo năm bước: mượn RAM nội cho trọng số của `ConvBlock`; cho `PointwiseS8` mượn cùng vùng
ấy; cho `ConvBlock` đặt cả hàng vòng vào vùng ấy khi còn vừa; cho mọi op riêng của `ai_engine` chép hằng số của nó
vào vùng ấy; rồi cho chúng dùng vùng ấy làm vùng nháp. Không bước nào xin thêm byte. Chi tiết ở KẾ HOẠCH §3.14 và §6.5; mã ở `espdl_loan`.

- **Ai xin:** khối `ConvBlock` chạy nhân chung, tức C khác 24 và 48. Mỗi khối xin lúc dựng số byte mọi trọng số nó
  đọc ở từng điểm ảnh:
  - tap của hai depthwise;
  - hai pointwise;
  - các bias và BN;
  - conv trước khối, nếu có.
- **Cấp và nhả:** `EspdlNet::step` của đồ thị có lần xin cấp một vùng ngay trước lượt chạy, và nhả ngay sau lượt
  ấy.
  - Vùng là khối RAM nội liền lớn nhất còn trống lúc ấy, tối đa bằng lần xin lớn nhất, chừa lại
    `AI_ENGINE_LOAN_RESERVE_BYTES` (Kconfig) cho các task khác.
  - b3 xin 79 680 B (stage 5). Trên firmware sản phẩm, lúc nghe khối lớn nhất chỉ còn 49 152 B (chủ repo duyệt cấp
    theo khối còn trống, 28/10).
- **Ai mượn:**
  - mỗi khối chép trọng số của nó vào vùng ấy lúc bắt đầu. Trọng số cộng hai vòng ba hàng và một đệm dải còn vừa vùng
    thì các hàng ấy cũng nằm ở đó, không thì ở PSRAM; khối không xin thêm byte nào cho chúng;
  - `PointwiseS8` nhân có vòng không xin thêm byte nào. Nó chạy theo lát vừa vùng ấy, chép trọng số và bias của mỗi
    lát vào đó trước khi chạy lát.
  - Mọi op riêng khác của `ai_engine` được chép hằng số nó đọc lại nhiều lần vào vùng ấy khi vừa, cũng không xin thêm
    byte nào; `StageSum` chép trọng số theo kênh của mọi số hạng (6 op 154,5 → 116,1 ms).
  - Op riêng cũng được dùng vùng ấy làm vùng nháp khi vừa: hàng đệm, hàng ra, tổng tạm. `TimeDepthwise` đặt hàng
    đệm và hàng ra ở đó; ở PSRAM, mỗi op của b3 nạp nguội chúng ~0,5 ms mỗi lần chạy.
- **Cấp ít hơn lần xin, hay không cấp được:** mỗi op chọn cách chạy theo cỡ được cấp thật. Thứ vừa thì chép vào,
  thứ không vừa thì đọc tại chỗ. Kết quả như nhau ở mọi cỡ, chỉ giờ chạy khác.
- **Dải:** khối nhân chung chạy mỗi hàng theo dải 4 điểm ảnh, depthwise rồi pointwise của một dải xong mới sang dải sau.

## Hệ quả

- **§6.5 có ngoại lệ thứ hai** cho luật mọi model ở PSRAM, cả trọng số lẫn hàng vòng của `ConvBlock`. Ngoại lệ chỉ
  sống trong lúc mạng chạy, và không lớn hơn lần xin lớn nhất.
- **Đo trên firmware sản phẩm** (28/10, `measurements/ram.md` §4): lúc nghe, sau `dsp_afe`, khối RAM nội liền lớn
  nhất là 49 152 B. Một vùng đúng 79 680 B không bao giờ cấp được, nên vùng lấy phần còn trống; giờ của b3 ở cỡ ấy đo
  ở `measurements/latency.md` §31.
- **b0 không đổi:** khối 24 và 48 kênh chạy nhân trải phẳng, không xin nên đồ thị b0 không có vùng mượn.
- **Xét lại** khi:
  - RAM nội lúc chạy speaker không đủ chỗ;
  - một model khác cũng cần mượn;
  - hay một op cần vùng lớn hơn 79 680 B.

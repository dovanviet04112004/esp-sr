# ADR-0026 — `ai_engine` nạp trước vào dcache bằng bộ preload của ROM trong lúc mạng chạy

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-28)
- **Ngày**: 2026-10-28
- **Liên quan**: KẾ HOẠCH §3.14; ADR-0025; TASKS E11-T35; `docs/measurements/latency.md` §31

---

## Bối cảnh

Trên board B, b3 (E11-T35) chạy 1 525 ms một cửa sổ trên một nhân, trong đó 21 `ConvBlock` chiếm ~795 ms. Sau
ADR-0025, trọng số và hàng vòng của khối nằm trong RAM nội, nên phần tính của mỗi hàng không chạm PSRAM. Còn lại hai
lần chạm mỗi hàng, cả hai làm CPU đứng chờ:
- chép hàng x kế tiếp từ PSRAM vào vòng;
- ghi hàng y ra PSRAM theo từng dải. Cache nạp mỗi dòng trước khi ghi đè rồi mới ghi ngược, nên ghi một dòng tốn gấp
  đôi đọc nó.

App đo (`latency.md` §31):
- Autoload của dcache không giúp: đọc tuần tự đã sát trần bus.
- Vòng hàng như `ConvBlock` stage 0 (hàng 6 912 B, tính ~0,5 ms trong RAM nội) chạy 718,7 µs một hàng. Ghi hàng y tốn
  ~140 µs, đọc hàng x ~79 µs.

S3 có bộ preload thủ công cho dcache. Nó nạp một vùng vào cache trong nền, CPU vẫn tính tiếp.

## Các phương án

| Phương án | Được | Mất | µs một hàng ở app đo |
|---|---|---|---|
| Như cũ | — | — | 718,7 |
| Ghi y bằng GDMA từ RAM nội, không qua cache | không nạp dòng trước khi ghi | chiếm một kênh GDMA; cần đệm RAM nội và giữ cache nhất quán | chưa đo |
| **Bộ preload của ROM: hàng y này rồi hàng x sau, nối nhau giữa các dải** | không tốn RAM, không chiếm ngoại vi | IDF ghi các hàm là nội bộ | **548,1**, không lần nào phải chờ |
| Chỉ preload hàng x sau | như trên | lợi ít hơn | 664,0 |
| Chỉ preload hàng y này | như trên | lợi ít hơn | 608,4 |

## Quyết định

Chủ repo chọn ngày 28/10: op riêng của `ai_engine` được dùng bộ preload của dcache trong lúc mạng chạy. Chi tiết ở
KẾ HOẠCH §3.14, mã ở `espdl_preload`.

- **Hàm:** `Cache_Start_DCache_Preload`, `Cache_DCache_Preload_Done`, `Cache_End_DCache_Preload` của ROM S3. Driver
  LCD RGB của IDF cũng gọi chúng.
- **Chuỗi vùng:** bộ preload chạy một vùng mỗi lần. Op xếp các vùng sắp ghi rồi sắp đọc thành chuỗi, và khởi vùng sau
  giữa các bước tính.
- **Kết thúc:** op chờ vùng đang chạy xong và kết thúc mọi preload trước khi trả về, nên không preload nào sống qua
  ranh giới op.
- **Ai dùng:** `ConvBlock` trước, mỗi hàng nạp hàng nó sẽ ghi rồi hàng vào của hàng sau. `PointwiseS8` và op khác dùng
  sau, khi app đo cho thấy chúng tính đủ lâu giữa các lần chạm PSRAM.

## Hệ quả

- **Không tốn RAM:** vùng nạp trước nằm trong chính dcache 64 KB.
- **Cache dùng chung hai nhân:** preload nạp đúng các dòng op sẽ chạm, và tổng lượng đọc từ bus không tăng. Op chỉ dời
  lúc đọc lên sớm hơn.
- **Ghi flash rơi giữa preload:** `Cache_Suspend_DCache` bản IDF vá chờ cache rảnh, nên lần ghi chờ preload xong. Cần
  một lượt thử ghi NVS song song lúc speaker chạy.
- **Xét lại** khi:
  - IDF đổi bản, vì các hàm này là nội bộ;
  - một thành phần khác cũng cần bộ preload;
  - bản dò không còn khớp từng bit, hay giờ một cửa sổ không giảm.

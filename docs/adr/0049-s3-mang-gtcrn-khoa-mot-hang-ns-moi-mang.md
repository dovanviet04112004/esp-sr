# ADR-0049 — Bản dựng S3 mang GTCRN, khoá model một hàng `ns` mỗi mạng, `wake` học lại trên chuỗi có GTCRN

- **Trạng thái**: Chấp nhận (chủ repo, 2026-11-26)
- **Ngày**: 2026-11-26
- **Liên quan**: KẾ HOẠCH §3.9, §3.11, §4, §4.5.8, §6.3; ADR-0047, ADR-0048; `measurements/afe/ns.md` §10.8–§10.10;
  TASKS E9-T16, E9-T18; fork `esp-sr-s31` S31-T9

---

## Bối cảnh

ADR-0048 chọn UL-UNAS làm mạng chính của khe `ns` và giữ GTCRN, mạng chọn lúc dựng. Khi ấy cả hai đều quá nhịp 16 ms của
nhân 1 trên S3, nên bản dựng S3 để `none`. Sau hai vòng tối ưu C, đều khớp golden từng bit (`ns.md` §10.8, §10.9):

| Mạng | Một bước, trạng thái ở PSRAM | Trong chuỗi thật của board B |
|---|---|---|
| GTCRN | 11,4 ms | 155 s không bỏ khung, không cảnh báo watchdog, đổi sàn ↔ GTCRN không mất khung (§10.10) |
| UL-UNAS | 23,2 ms | không thử: tự nó đã quá nhịp |

UL-UNAS đọc ~590 KB trọng số mỗi bước. Bus PSRAM của S3 đọc ~90 MB/s, cả khi trượt cache lẫn khi dùng ROM preload
(§10.9), nên riêng phần đọc đã ~6,6 ms. Trên S3 nó không thể vừa nhịp; fork S31 dựng nó (KẾ HOẠCH S31 §3.9).

Khoá model giữ một hàng `ns` duy nhất, của UL-UNAS, nên bản dựng S3 mang GTCRN không đóng gói được trọng số của nó.

`wake` và `command` học trên chuỗi có sàn OM-LSA. Chấm trên ba phiên có nhiễu, `wake` bắt 33/35 với sàn và 29/35 khi
khe chạy GTCRN cùng sàn (§10.5).

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| S3 để `none` như cũ | không đổi gì | mạng đã vừa nhịp mà sản phẩm không dùng được |
| S3 mang GTCRN, mặc định sàn, bộ nhận dạng giữ nguyên | an toàn với `wake` hiện tại | GTCRN chỉ bật bằng `SET_CONFIG`, và bật thì `wake` kém đi |
| **S3 mang GTCRN, học lại `wake` trên chuỗi có GTCRN, mặc định GTCRN khi bản học lại thắng** | khe chạy mạng mà bộ nhận dạng đã học cùng | một lượt dựng đặc trưng và một lượt học trên GPU |

Với khoá:

| Phương án | Được | Mất |
|---|---|---|
| **Một hàng mỗi mạng: `ns_gtcrn`, `ns_ulunas`** | S3 và S31 đóng gói từ cùng một khoá; mạng thứ ba thêm một hàng | đổi tên hàng `ns` và thư mục `firmware/models/ns/` |
| Khoá riêng từng chip | mỗi chip tự do | chạm mọi model và luật giữ file S3 y hệt ở fork |

## Quyết định

Chủ repo chọn ngày 26/11:

- **Bản dựng S3 mang GTCRN:** `sdkconfig.defaults.esp32s3` đặt `AI_ENGINE_NS_BACKEND` là `gtcrn`. `NS=` của `make` vẫn đổi
  được mạng cho một bản dựng (KẾ HOẠCH §4.5.8).
- **Khoá một hàng mỗi mạng:** `contracts/models.lock.json` có hàng `ns_gtcrn` và `ns_ulunas`; trọng số và `meta.json` ở
  `firmware/models/ns_gtcrn/`, `firmware/models/ns_ulunas/`. Bước đóng gói chỉ đưa vào ảnh hàng của mạng mà bản dựng
  mang. `make` đọc mạng ấy từ `sdkconfig.defaults.<chip>`, hoặc lấy từ `NS=` (KẾ HOẠCH §6.3).
- **`wake` học lại ngay trên chuỗi có GTCRN:**
  - đặc trưng dựng với `SRPIPE_NS=gtcrn` trên split `wake/vung1_gtcrn` và `wake/noise4_gtcrn`, chép nguyên byte từ
    `vung1` và `noise4` (KẾ HOẠCH §3.9);
  - chấm trên phiên board qua cùng chuỗi, so bản đang dùng chạy chuỗi có sàn.
- **Mặc định của khe:** `afe/ns_engine` gieo 0 (sàn) tới khi bản `wake` học lại thắng. Khi ấy `ns.engine` của
  `contracts/afe.yaml` đổi sang 1 cùng lúc với deploy bản ấy. `command` được chấm trên chuỗi có GTCRN trước khi đổi.

## Hệ quả

- Bản dựng S3 nặng thêm ~14 KB mã, và ảnh model thêm mục `ns_gtcrn` 99 520 B ở PSRAM.
- Fork S31 lấy hàng `ns_ulunas` theo `sdkconfig.defaults.esp32s31`, nên việc đóng gói UL-UNAS cho S31 (S31-T9) xong
  cùng lúc.
- Board C (có loa, có `aec`) chưa đo GTCRN trong chuỗi: phần còn lại của chuỗi ở đó ~3,7 ms (`ns.md` §10.4). Trước khi
  đổi mặc định sang GTCRN trên board C phải đo; quá nhịp thì board C giữ sàn.
- **Xét lại** khi:
  - bản `wake` học lại không thắng bản hiện tại;
  - board C quá nhịp;
  - một mạng nhỏ hơn GTCRN mà tốt hơn xuất hiện.

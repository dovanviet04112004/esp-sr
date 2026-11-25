# ADR-0048 — UL-UNAS học sẵn là mạng chính của khe `ns`, GTCRN giữ lại, chọn mạng lúc dựng, sàn gain dùng chung

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-11-25
- **Liên quan**: KẾ HOẠCH §1, §3.2, §3.3, §3.9, §4, §4.5.2, §4.5.5, §6.2, §6.3; ADR-0047; `measurements/afe/ns.md` §10.5;
  TASKS E9-T18, E9-T19, E9-T20

---

## Bối cảnh

GTCRN DNS3 đã có C float32 khớp bản soi gương từng bit (ADR-0047), nhưng trên thước của bộ nhận dạng nó thua sàn: `wake`
bắt 26/37 so với 33/35 của OM-LSA, vì checkpoint khử cả vang (`ns.md` §10.1). Ngày 25/11 chủ repo hỏi có mạng học sẵn nào
hơn không.

UL-UNAS (Rong và cộng sự, IEEE TASLP 2026; mã MIT, commit ghim `00f7c700`, checkpoint DNS3 công bố 02/2026) là mạng kế
tiếp của cùng tác giả:

- Cùng lưới STFT 512 điểm bước 256 ở 16 kHz, cùng bộ lọc ERB 65 + 64 dải, cùng hai khối DPGRNN 16 kênh × 33 dải như GTCRN.
- Khác ở phần còn lại: vào là log10 độ lớn một kênh; bộ mã hoá và giải mã 5 tầng với ba loại khối, AffinePReLU, và chú ý
  thời gian–tần số cTFA ở cả 10 khối (một GRU theo thời gian, một GRU hai chiều theo tần số); ra mặt nạ thực qua sigmoid,
  nên gain ≤ 1 sẵn.
- 171 328 tham số, trong đó 24 576 là ERB cố định; ~35 triệu phép nhân-cộng mỗi giây theo bài báo. Trạng thái chạy dòng
  6 816 số float32.
- Học với cửa sổ Hann thường, còn khe `ns` trao vạch của cửa sổ căn Hann (§3.1).
- Bài báo đo trên tập DNS3 tổng hợp: PESQ 2,30 so với 2,10 của GTCRN học lại cùng cấu hình. Đích học giữ 100 ms phản xạ
  đầu, tức vẫn khử vang muộn. Bài không đo bản thu thật hay bộ nhận dạng.

Chấm như `ns.md` §10.1 và §10.2, cùng ba phiên board và cùng hàng `kl` 800‰ của `wake` đang chạy (`ns.md` §10.5):

| Khe `ns` | "vừng ơi" |
|---|---|
| OM-LSA (sàn) | **33/35** |
| UL-UNAS, gain ≥ −12 dB | 32/35 |
| UL-UNAS | 30/34 |
| GTCRN, gain ≥ −12 dB | 29/35 |
| GTCRN | 26/37 |

Với sàn −12 dB, mức lời và quãng lặng của UL-UNAS gần như trùng OM-LSA. Sàn ấy đúng bằng `G_min` của OM-LSA.
Cùng sàn, `command` nhận lệnh kém nhất 45% so với 60% của OM-LSA và 18% của GTCRN, mọi lệnh 76% ngang OM-LSA, lời không
phải lệnh bị từ chối 93–97% so với 95–98%.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| A. Chỉ GTCRN, như ADR-0047 | C đã xong, nhỏ (99,5 KB) | thua sàn rõ trên mọi thước |
| B. UL-UNAS thay hẳn GTCRN | mạng tốt nhất trong hai | bỏ phí C đã khớp từng bit của GTCRN |
| **C. Cả hai, chọn mạng lúc dựng** | UL-UNAS khi cần chất lượng, GTCRN khi cần nhỏ; một ảnh chỉ mang một mạng | hai bản soi gương, hai bộ C |
| D. Cả hai trong một bản dựng, đổi lúc chạy | đổi bất kỳ lúc nào qua MQTT | ảnh mang cả hai bộ trọng số, ~0,7 MB PSRAM 🔬 |

## Quyết định

Chọn **C** (chủ repo, 25/11). Sàn gain dùng chung với OM-LSA (chủ repo, 25/11).

1. `ai_engine/src/ns_ulunas/` là UL-UNAS chạy dòng viết tay bằng C float32, theo đúng cách của GTCRN: trọng số của
   checkpoint ghim là mục `WEIGHTS` `ns_ulunas` của ảnh model, bản soi gương float32 `tasks/ns/ulunas_mirror.py` theo thứ
   tự cộng của C, golden có đối chứng âm. Không lượng tử: chỉ xét lại int8 khi một bản int8 cho cùng điểm trên thước của
   §3.15 và gain lệch bản float dưới 1 dB.
2. Chuỗi Python có `tasks/ns/ulunas.py` (`SRPIPE_NS=ulunas`), như `tasks/ns/gtcrn.py`.
3. Khe trao vạch căn Hann. Vạch được nhân với tỉ số tổng hai cửa sổ (Hann trên căn Hann, 0,785) trước khi vào mạng, để một
   tông giữ đúng mức như lúc mạng học; hệ số ấy nằm trong mục trọng số, bản soi gương tính từ định nghĩa hai cửa sổ.
4. Kconfig `AI_ENGINE_NS_BACKEND` thêm `ulunas`: `none` | `rnnoise` | `nsnet` | `gtcrn` | `ulunas`, tối đa một mạng mỗi bản
   dựng. NVS `afe/ns_engine` giữ nghĩa của ADR-0047: 0 là sàn, 1 là mạng đã dựng.
5. **Sàn gain dùng chung**: `dsp_afe` chặn dưới gain của mọi bản cắm từ ngoài ở `afe/ns_floor_db`, chính tham số OM-LSA
   dùng làm `G_min`, gieo −12 dB từ `contracts/afe.yaml`, đổi bằng `SET_CONFIG`. Chuỗi Python làm y như vậy.
6. Mặc định vẫn là sàn. Fork S31 dựng `ulunas`; bản dựng S3 để `none` tới khi một mạng vừa nhịp 16 ms cạnh `wake` và
   `command`.

## Hệ quả

- KẾ HOẠCH §1, §3.2, §3.3, §3.9, §4, §4.5.2, §4.5.5, §6.3 viết lại theo các mục trên; KẾ HOẠCH S31 §3.9 đổi sang `ulunas`.
- TASKS: E9-T19 là khe Python và phép chấm của UL-UNAS; E9-T20 là C và bản soi gương; E9-T18 thêm sàn dùng chung.
- Phép thử quyết định vẫn là học lại `wake` trên chuỗi có mạng, chấm trên phiên board có nhiễu nền; tới lúc ấy hai mạng
  chỉ được chấm bằng `wake` và `command` đã học trên chuỗi có sàn.
- Xét lại khi một mạng thắng sàn trên phiên có nhiễu, khi UL-UNAS không vừa nhịp trên S31, hay khi có checkpoint học với
  đích giữ vang.

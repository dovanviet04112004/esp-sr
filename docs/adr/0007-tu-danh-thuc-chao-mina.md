# ADR-0007 — Từ đánh thức là "Chào Mina", đọc "chào mi na"

- **Trạng thái**: Chấp nhận
- **Ngày**: 2026-09-28
- **Liên quan**: KẾ HOẠCH §1.2, §3.11; TASKS E11-T5, E11-T7, E11-T11; `ml/src/srpipe/tasks/wake/candidates.py`

---

## Bối cảnh

`wake` học đúng một cụm 3–4 âm tiết (§3.11), và dữ liệu dương đến từ tiếng tổng hợp (E11-T7), nên cụm phải chốt trước
khi dựng dữ liệu. Cụm tốt là cụm hiếm trong lời nói thường, ít cụm nghe gần nó, ba miền đọc như nhau, và người dùng nói
nó giống nhau mọi lần.

Thước: `python -m srpipe.tasks.wake.candidates` trên lời của Common Voice, VIVOS, FPT, VLSP và Bud500 qua `lang_vi`,
**7 970 463 âm tiết, 10 438 âm tiết khác nhau**. Mỗi cụm được: số thanh, số âm chính, số cách đọc qua ba miền, tần suất
cả cụm, và số cụm cùng độ dài lệch nó không quá 1, 2, 3 thành phần âm tiết (âm đầu, âm đệm, âm chính, âm cuối, thanh),
tính trên mỗi triệu cụm. Kho phần lớn là câu đọc và tin tức, nên số này chỉ để sàng lọc; báo nhầm thật đo ở Cửa 2.

## Các phương án

| Phương án | Được | Mất | Hàng xóm ≤ 3 thành phần, mỗi triệu cụm |
|---|---|---|---|
| chào bé bắp ơi | 4 âm tiết, 3 thanh, không hàng xóm | nghe trẻ con, dài | 0 |
| chào mí na | 3 thanh, ba miền một cách đọc | chữ "Mina" được đọc bằng, dấu sắc lệch cách người dùng nói | 1,6 |
| **chào mi na** | đọc tự nhiên theo chữ "Mina", ngắn, ba miền một cách đọc | 2 thanh | **10,4**; gần nhất "trào thi đua" (24 lần), "trà my và" (4) |
| trợ lý ơi, chào trợ lý | nghĩa rõ | tin tức nói "trợ lý …" dày đặc | 12,0 … 23,3 |
| bí đỏ ơi, bông gòn ơi, cá bống ơi | ngắn | gần "bị bỏ rơi" (48), "không còn ai" (63), "các bác ơi" (7) | 6,5 … 11,6 |
| tên một thanh: ki na ơi, lu na ơi | ngắn | cả cụm thanh ngang | 2,3 … 8,8 |

## Quyết định

**"Chào Mina"**, đọc **"chào mi na"**: `c a: w T2 . m i T1 . n a: T1`, một cách đọc cho cả Bắc, Trung, Nam. Chủ dự án
chọn theo độ dễ nói: người thấy chữ "Mina" đọc bằng, và một từ đánh thức mà người dùng nói khác với cách mô hình học
thì bắt trượt, tệ hơn việc có nhiều hàng xóm hơn. Bản "chào mí na" ít hàng xóm hơn 6,5 lần nhưng nghe gượng và lệch cách
nói thật.

## Hệ quả

- `configs/models/wake.yaml` ghi `word: chào mi na`; tiếng tổng hợp chỉ đọc bản bằng. Bản thu qua board (E11-T6) mà cho
  thấy nhiều người nhấn giọng thành "mí na" thì thêm bản ấy vào dương, không đổi từ.
- E11-T7: âm bản khó gồm các hàng xóm `candidates.py` tìm ra ("trào thi đua", "trà my và", "nào đi ra", "vào mỹ ra", …)
  cùng cụm gần âm tự chọn ("chào mi", "mi na ơi", "chào chị na", "chào mi nhé").
- E11-T11: Cửa 2 đo bắt ≥ 95% ở 1 m và báo nhầm ≤ 1 lần mỗi giờ trên ≥ 24 giờ âm bản. Trượt vì báo nhầm từ các hàng xóm
  trên thì xét lại quyết định này: thêm một âm tiết, như "chào Mina ơi".
- Xét lại khi sản phẩm có tên riêng.

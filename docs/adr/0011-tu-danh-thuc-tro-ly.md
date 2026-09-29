# ADR-0011 — Từ đánh thức là "trợ lý"; bản demo ưu tiên bắt được hơn chặn cụm na ná

- **Trạng thái**: Chấp nhận; thay ADR-0007
- **Ngày**: 2026-09-29
- **Liên quan**: KẾ HOẠCH §3.11; TASKS E11-T5, E11-T6, E11-T7, E11-T11; `docs/measurements/wake.md`

---

## Bối cảnh

"Chào Mina" (ADR-0007) qua ba lượt học không đạt trên bản thu thật (`docs/measurements/wake.md` §1–§5). Hai gốc:
mẫu dương chỉ có TTS vì không người thật nào trong kho nói cả cụm; và cụm có nhiều họ na ná ("chào" + một âm tiết,
"chào mi nhé", "…mươi lăm") nên muốn chặn chúng thì mạng phải khắt khe, và càng khắt khe giọng thật càng bị loại.
Việc trước mắt là một bản demo chạy được: nói từ đánh thức thì máy thức.

## Các phương án

Số của `python -m srpipe.tasks.wake.candidates` trên 7 970 463 âm tiết của năm kho, và số câu có cả cụm trong kho học:

| Phương án | Được | Mất | Số |
|---|---|---|---|
| giữ "chào mi na" | dữ liệu và kết quả đã có | không người thật nào nói cả cụm; họ na ná dày | 10,4 hàng xóm mỗi triệu cụm; 0 câu người thật |
| "kẹo lạc ơi", "chú Cuội ơi", "kẹo mè xửng" | gần như không hàng xóm | chủ dự án thấy nghe gượng; 0 câu người thật | 0 … 0,5 hàng xóm mỗi triệu cụm |
| **"trợ lý"** | tự nhiên, ai cũng nói được ngay; **223 lần người thật nói nó** trong kho, 222 câu trong kho học | 2 âm tiết, dài chừng nửa giây, ngược luật 3–4 âm tiết của §3.11; từ thật dùng hằng ngày | 28 lần mỗi triệu âm tiết, cỡ một lần mỗi hai giờ lời nói liên tục |

## Quyết định

**"trợ lý"**, do chủ dự án chọn vì tự nhiên. Bản demo ưu tiên **bắt được**: cụm na ná như "trợ giúp", "trợ cấp" bị bắt
nhầm cũng chấp nhận, không có âm bản khó, và mục tiêu báo nhầm để chọn ngưỡng nới ra ở cấu hình; ngưỡng trên máy chỉnh
được qua NVS `kws/wake_th` mà không học lại. Mẫu dương gồm TTS và 222 lần người thật nói "trợ lý" trong kho học, cắt
đúng chỗ bằng mốc thời gian từng từ của PhoWhisper. Cách học giữ những gì `wake.md` §5 đã đo là tốt nhất: 64 kênh,
việc phụ CTC, trọng số cuối, `val` 10,9 giờ.

## Hệ quả

- `configs/models/wake.yaml`: từ mới, split `wake/v4`, không âm bản khó, không họ cụm gần âm; cơ chế `train_hard`
  vẫn ở code để bật lại khi siết.
- Mọi dữ liệu sinh cho "chào mi na" bỏ đi: bộ TTS, bản mô phỏng. Giọng mẫu TTS (`synth_refs`) và bản thu board giữ:
  phiên "chào mi na" là âm bản cho từ mới.
- Chủ dự án thu lại các phiên chấm qua board với "trợ lý" (E11-T6), chỉ để chấm.
- Cửa 2 (bắt ≥ 95% ở 1 m, báo nhầm ≤ 1 lần mỗi giờ) không đổi. Xét lại khi bản demo chạy và có dữ liệu thu thật: siết
  báo nhầm bằng âm bản khó, hoặc đổi sang cụm 3–4 âm tiết nếu "trợ lý" báo nhầm quá nhiều trong phòng.

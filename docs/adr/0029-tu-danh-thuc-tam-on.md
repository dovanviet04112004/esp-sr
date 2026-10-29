# ADR-0029 — Từ đánh thức tạm là "on" của Speech Commands, tới khi có giọng thật nói "trợ lý"

- **Trạng thái**: Chấp nhận (chủ repo, 2026-10-29)
- **Ngày**: 2026-10-29
- **Liên quan**: KẾ HOẠCH §1.1, §3.11, §5.4; ADR-0011, ADR-0017; TASKS E11-T11;
  `docs/measurements/wake.md`, `docs/measurements/speech_commands.md` §4

---

## Bối cảnh

Từ đánh thức của sản phẩm là "trợ lý" (ADR-0011), nhưng chưa có model nào dùng được: `wake/v4` bắt 1/104 câu trên
board. Mẫu dương là gốc của lỗi. Chủ repo không cho tiếng TTS làm dữ liệu học, và việc đào "trợ lý" từ các kho trên
Hugging Face chỉ ra vài chục mẩu sạch, chủ repo đã cho dừng ngày 13/10 (`wake.md` §7). Cùng TCN, cùng đường board,
pilot "yes" của Speech Commands v0.02, dữ liệu thật của nhiều nghìn người nói, bắt 4/5 câu trên board với 2–4 lần báo
nhầm mỗi giờ (`speech_commands.md` §4). Sản phẩm vẫn chấm mọi câu `vad` bắt được như một lệnh, vì `svc_listen` chưa
có chế độ `NGHE`.

Mọi run `wake` đã có học trên log-mel 40 dải và FFT trước ADR-0020, nên dùng từ nào cũng phải học lại trên front
hiện tại. Âm bản học của `wake/v4` có 107 169 trên 122 229 mẩu của Bud500, kho đã xoá ngày 06/10.

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| Chờ thu "trợ lý" qua board (E11-T6) | đúng từ của sản phẩm | cần người nói và phiếu đồng ý; `NGHE` chưa thử được tới lúc ấy |
| **"on"** | ~3 800 mẩu của hàng nghìn người nói; dễ đọc | một âm tiết, gần "ông", "không", "con" của tiếng Việt: báo nhầm có thể cao hơn "yes" |
| "marvin", "sheila" | hai âm tiết, tên riêng, ít báo nhầm | ~2 000 mẩu; người Việt phải tập đọc |

## Quyết định

Chủ repo chọn **"on"** làm từ đánh thức tạm. "trợ lý" vẫn là đích của ADR-0011 khi có giọng thật.

- Mẫu dương là mọi mẩu "on" của Speech Commands v0.02, cắt ở cuối tiếng nói như mọi mẩu dương. `val` lấy từ
  `testing_list.txt`, `train` từ mẩu không nằm ở danh sách nào, nên người nói tách như bộ tách.
- Âm bản khó là các từ còn lại của bộ, rút theo seed như pilot "yes".
- Âm bản học rút mới 100 giờ lời nói tiếng Việt từ các kho còn trên đĩa, không Bud500. `val_neg` và `test_neg` giữ
  nguyên của `wake/v4`, vốn không có Bud500.
- Học lại trên log-mel 80 dải của `contracts/listen.yaml`, cùng công thức đã sửa lối tắt đầu câu của pilot "yes".

## Hệ quả

- Speech Commands v0.02 (CC BY 4.0) vào sản phẩm, ghi nguồn ở `DU_LIEU.md`; kho tải lại vào `raw/speech/speech_commands`.
- `configs/models/wake.yaml` ghi từ `"on"` có ngoặc kép, vì YAML đọc `on` trơn thành giá trị đúng/sai; split `wake/on1`.
- Pilot của `srpipe.tasks.wake.data` rút âm bản mới khi không có split cũ để chép.
- Khi đủ bản thu "trợ lý" qua board, học lại trên đúng từ ấy và bỏ "on".

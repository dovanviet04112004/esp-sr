# ADR-0033 — Từ đánh thức tạm là "happy" của Speech Commands, thay "on"

- **Trạng thái**: Thay bởi ADR-0043 (chủ repo, 2026-11-03)
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §1.1, §3.11; ADR-0011, ADR-0029; TASKS E11-T39; `docs/measurements/wake.md` §8, §9

---

## Bối cảnh

Từ đánh thức tạm "on" (ADR-0029) chạy trên board B từ 29/10. `on3` ở 850‰ chỉ qua 28/41 câu "on" chủ repo thu qua
board, với 7,4 lần báo nhầm mỗi giờ trên `test` (`wake.md` §8); chủ repo thấy nhiều lúc nhận tệ. "on" là một âm tiết
chừng 0,3 s, gần "ông", "không", "con" của tiếng Việt, nên vừa ít đặc trưng để bắt vừa dễ trùng lời thường.

"on" lại là từ nói riêng nhiều dữ liệu nhất của Speech Commands ngoài chữ số (3 845 mẩu). Không có bộ từ đánh thức tiếng
Việt công khai; MSWC tiếng Việt có 47 từ, 350 mẩu cả bộ (`wake.md` §9).

## Các phương án

| Phương án | Dữ liệu | Mất |
|---|---|---|
| Giữ "on" | 3 845 mẩu nói riêng | một âm tiết ngắn: bắt 28/41 câu của chủ repo |
| **"happy"** | 2 054 mẩu nói riêng của Speech Commands, thêm 1 911 mẩu cắt khỏi câu của MSWC | hát "Happy birthday" có thể đánh thức |
| "marvin", "sheila" | ~2 100 và ~2 000 mẩu nói riêng, MSWC gần như không có | tên riêng, người Việt phải tập đọc |
| "Firefox", "Hey Firefox" | 931 mẩu MSWC; Common Voice thu "hey" và "Firefox" thành hai mẩu riêng | nhãn hiệu |
| Hey Snips | 1 774 người nói, làm riêng cho từ đánh thức | giấy phép chỉ cho nghiên cứu |
| Thu "trợ lý" qua board (ADR-0011) | chưa có | cần nhiều người nói và phiếu đồng ý |

## Quyết định

Chủ repo chọn **"happy"**, đọc gần "hép-pi", làm từ đánh thức tạm. "trợ lý" vẫn là đích của ADR-0011.

- Split `wake/happy1` theo đúng công thức của `on3`: dương là mọi mẩu "happy" của Speech Commands, cắt ở cuối tiếng nói;
  âm bản khó là các từ còn lại, kể cả "on"; 100 giờ âm bản học rút mới, bỏ câu có nói từ đánh thức; `val_neg`, `test_neg`
  của `wake/v4`.
- Lúc học giữ mọi thứ của `on3`: trọng số cao độ, độ lợi của từ đứng riêng, việc phụ CTC, 20 000 bước.
- Mẩu MSWC chỉ thêm vào khi bản thử của Speech Commands không đủ, và sau khi chủ repo nghe thử: chúng cắt khỏi câu đọc liền
  như mẩu `hf_extract` đã gặp (`wake.md` §7).

## Hệ quả

- Ngưỡng chọn lại trên `val` của `happy1`; ảnh model mới gieo lại `kws/wake_th` như mọi `wake` mới (KẾ HOẠCH §6.2).
- Bắt được đo trên một phiên chủ repo nói "happy" qua board; báo nhầm đo trên phiên lời nói tiếng Việt, gồm cả hát
  "Happy birthday" và âm gần như "hấp", "hẹp".
- `on3` giữ trong các run cũ; quay lại "on" chỉ là deploy lại run ấy.

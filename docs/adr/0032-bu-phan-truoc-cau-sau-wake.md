# ADR-0032 — Bù phần trước câu của cửa sổ lệnh sau `wake` bằng tiếng nền trước từ đánh thức

- **Trạng thái**: Chấp nhận (chủ repo 31/10: sửa lỗi `PART` sau `wake` bằng đoạn bù tiếng nền)
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §3.12, §5.4; ADR-0019, ADR-0029; TASKS E11-T38; `docs/measurements/command.md` §12.32, §12.37,
  §12.38

---

## Bối cảnh

Ngày 31/10 chủ repo nói trực tiếp với board B (`command.md` §12.37): 72 `WAKE`, 51 `COMMAND`, lệnh nào nhận cũng đúng
lệnh đã nói, và `PART` là lý do từ chối hay gặp nhất (8 lần). Cùng một lệnh lúc được lúc không.

Trong `LENH`, mốc chặn của cửa sổ lệnh là bước sau quyết định của `wake` (KẾ HOẠCH §5.4), để từ đánh thức không bị chấm
thành lệnh. Mạng `command` bắt đầu cửa sổ từ bộ đệm rỗng như lúc học, và cần chừng 2 s trước câu để nghe tốt âm tiết đầu
(ADR-0019), ít nhất 0,5 s (§12.32). Lệnh nói liền sau "on" chỉ còn vài bước trước câu: mạng nghe yếu âm tiết đầu, nên một
phần của lệnh khớp ngang cả lệnh và cửa sổ bị từ chối `PART`.

Cửa 3 đo lại với model đang deploy và ngưỡng của board (δ₁ 300 ‰, δ₂ 10 ‰, không trần âm tiết), mốc chặn giả đặt trước
bước `vad` đầu của mỗi câu như một quyết định `wake` (§12.38): không có mốc nhận đúng 181/209 câu lệnh; mốc 0, 96, 256 và
496 ms trước câu nhận 6, 45, 160 và 174, gần hết số còn lại là `PART`.

## Các phương án

Ô là số câu lệnh Cửa 3 nhận đúng trên 209, mốc chặn 0 / 96 / 256 / 496 ms trước câu (§12.38).

| Phương án | Nhận đúng | Mất |
|---|---|---|
| Giữ nguyên; người dùng ngừng sau "on" | 6 / 45 / 160 / 174 | sản phẩm phụ thuộc cách nói; board tắt loa nên không có tiếng báo lúc nên nói |
| Mạng chạy qua 2 s tiếng thật trước mốc mà không chấm | 163 / 163 / 163 / 163; không mốc 164 thay 181 | mạng nghe tiếng nói (câu trước, từ đánh thức) trong phần trước câu, điều mẫu học không bao giờ có: `PART` thành 30 |
| Như trên, chỉ 0,5 s và không lùi qua cửa sổ trước | 174 / 169 / 173 / 168 | trên board 0,5 s ấy chính là từ đánh thức: chưa đo được; kém mạng nguội ở 496 ms |
| **Bù phần trước câu bằng tiếng nền ngay trước câu chứa từ đánh thức** | 170 / 169 / 172 / 173, cận dưới | cửa sổ gồm hai đoạn không liền nhau; `LENH` chưa có bản soi gương Python |
| Học lại với từ đánh thức đứng trước lệnh | không đo | dựng lại dữ liệu và học lại `command` |
| Mạng chạy liên tục cả phiên | không đo | chủ repo không duyệt (ADR-0019) |

Trong Cửa 3 không có từ đánh thức thật: 25 bước ngay trước mốc đóng vai "on" và bị bỏ, tiếng nền trước đó hay bị cửa sổ
trước chặn, nên số của phương án chọn là cận dưới. Trên board, người nói thường im đủ lâu trước "on".

## Quyết định

- Trong `LENH`, khi mốc của `wake` giữ bước đầu cửa sổ muộn hơn `utterance.lead_s` trước câu, `svc_listen` bù phần còn
  thiếu bằng các bước ngay trước bước `vad` đầu của câu chứa từ đánh thức: không lùi qua mốc chặn trước `wake` (cửa sổ
  trước, chỗ luồng đứt), không quá vòng đệm, cộng với phần trước câu sẵn có không quá `utterance.lead_s`.
- Mạng bắt đầu từ bộ đệm rỗng ở đầu đoạn bù, chạy đoạn bù rồi cửa sổ, và chấm cả hai như phần trước câu lúc học. Từ
  đánh thức không bao giờ vào mạng `command`.
- Cửa sổ cộng đoạn bù dài quá `window_s` thì lùi từ cuối như mọi cửa sổ, bỏ đoạn bù.
- Dòng log của mỗi quyết định in thêm bước đầu và số bước của đoạn bù, để máy tính dựng lại đúng cửa sổ từ luồng tiếng.
- `ai_engine` không đổi: đoạn bù đi qua `ai_engine_command_step` như mọi bước.

## Hệ quả

- Cửa 3, `listen-unit` và mẫu học không đổi: chúng không có `wake`.
- `LENH` chưa có bản soi gương Python và golden. Board B kiểm bằng một phiên chủ repo nói trực tiếp, so `PART` với
  §12.37; phép so từng quyết định với Python dựng lại từ luồng tiếng `mode` 5 làm sau.
- Dải độ ồn của kiểm người nói vẫn đo trên phần trước câu của chính cửa sổ (ADR-0024), không trên đoạn bù.

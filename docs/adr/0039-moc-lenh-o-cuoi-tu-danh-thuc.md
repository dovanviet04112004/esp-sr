# ADR-0039 — Mốc chặn của cửa sổ lệnh ở cuối từ đánh thức; từ chối không kết thúc `LENH`

- **Trạng thái**: Chấp nhận (chủ repo 31/10: "Sửa", sau bản rà `command.md` §12.44)
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §5.4; ADR-0032, ADR-0033; TASKS E11-T40; `docs/measurements/command.md` §12.44,
  `docs/measurements/wake.md` §10

---

## Bối cảnh

Với "happy" ở 880 ‰ trên board B, chủ repo thấy hai điều (`command.md` §12.44):
- nói "happy" xong board ra ngay `REJECT LOW_SCORE`;
- lệnh nói liền sau từ kém hơn lệnh nói sau 1–2 s.

Mã đặt mốc chặn của cửa sổ lệnh ở k + 1, k là khung đầu có điểm làm trơn vượt ngưỡng. Trên `val_pos` của `happy1`, k đến
trước cuối từ trung vị 6 bước, có lần 14 bước. Vì vậy mốc k + 1 để đuôi "-py" lọt vào cửa sổ ở 68% số lần (`wake.md` §10):
- câu chạy qua mốc có cửa sổ `wake_tail` từ k + 1; đuôi từ cộng một hơi thở trong 0,64 s đủ 31 bước để cửa sổ được giữ,
  rồi bị chấm ra `REJECT`;
- lệnh nói liền thì mạng nghe đuôi "-py" ngay trước lệnh, điều lúc học không có;
- quyết định nào cũng đưa máy về `NGHE`, nên `REJECT` của cái đuôi rơi vào lúc người nói đang ngừng và nuốt lệnh đến sau.

Ngoài ra luật giữ cửa sổ so `run_last − run_first` bằng `uint32_t`. Khi k rơi sau bước `vad` cuối của câu mà câu chưa
chốt, hiệu tràn và một cửa sổ rỗng được giữ.

Điểm học là 1 tới 0,3 s sau cuối từ, nên bước j điểm rơi lại dưới ngưỡng bám cuối từ chặt hơn k: trung vị +18 bước,
p25–p90 +12 tới +21.

## Các phương án

| Phương án | Đuôi lọt vào cửa sổ | Mất |
|---|---|---|
| Giữ k + 1 | 68% | hai triệu chứng |
| k + 1 cộng một số bước cố định | tuỳ số | mốc muộn cắt đầu lệnh khi k đã muộn (p90 +6) |
| Chỗ lặng nhất sau k | — | khoảng lặng của âm tắc /p/ giữa "hap-py" bị nhận nhầm là cuối từ |
| **max(k + 1, j − `SVC_LISTEN_WAKE_END_MS`)** | **21%** | mốc chốt ~0,4 s sau cuối từ; mốc trung vị muộn hơn cuối từ 64 ms |
| Mạng `command` chấp nhận đoạn lời tự do trước lệnh | — | đổi phép chấm, đo lại Cửa 3 và mọi ngưỡng |

## Quyết định

1. **Mốc chặn ở cuối từ đánh thức.** Vượt ngưỡng ở k vẫn ra `WAKE` và vào `LENH` ngay, mốc tạm là k + 1. Khi điểm rơi lại
   dưới ngưỡng ở khung j, mốc chốt ở max(k + 1, j − `SVC_LISTEN_WAKE_END_MS`). Cửa sổ `wake_tail` của câu đang chạy dời
   bước đầu về mốc. Mạng giữ phần đoạn bù đã chạy khi nó chưa nghe bước nào sau mốc tạm, không thì chạy lại từ đầu.
   - Điểm chưa rơi sau `SVC_LISTEN_WAKE_FALL_MS` kể từ lúc vượt, hay câu chốt trước khi điểm rơi, thì mốc giữ k + 1.
   - Hai hằng ở `Kconfig` của `svc_listen` như `SVC_LISTEN_WAKE_LEAD_MS`: 224 ms (14 bước, `wake.md` §10) và 768 ms.
     Đổi từ đánh thức hay model `wake` thì đo lại.
2. **Cửa sổ của câu chứa từ đánh thức chỉ giữ khi có lời sau cuối từ.** Luật giữ của KẾ HOẠCH §5.4 (`min_s` cộng hangover
   của `vad` sau mốc) tính từ mốc mới. Phép so dùng hiệu có dấu.
3. **Chỉ `COMMAND` đưa máy về `NGHE`.** `REJECT`, kể cả `FRAME_GAP` và cửa sổ bị bỏ vì lỗi, để máy chờ tiếp, nên người
   nói nói lại được ngay. Thời gian chờ `SVC_LISTEN_COMMAND_WAIT_MS` tính từ lúc không còn cửa sổ nào, không từ lúc `vad`
   tắt: tiếng nền giữ `vad` bật thì câu không chốt và không mở cửa sổ mới, nên chờ theo `vad` sẽ giữ `LENH` mãi. Từ đánh
   thức nói lại trong lúc chờ vẫn là một câu của `LENH` như trước.

4. **Kiểm người nói của lệnh nói liền đọc 1,5 s liền từ vòng đệm**, kết ở bước cuối của cửa sổ, nên có cả từ đánh thức
   do chính người nói ấy nói. Cửa sổ có đoạn bù chỉ dài 79–87 bước; ghép đoạn bù rồi cửa sổ, đệm số 0 phía trước, thì lời
   chỉ chiếm nửa cửa sổ. Trên board, 3/10 lệnh nói liền sau "happy" bị `SPEAKER` dù mạng lệnh chấm 908–992 ‰
   (`command.md` §12.45). Cửa sổ không có đoạn bù giữ cách cũ.

5. **Thức lại trong `LENH`.** Lần vượt ngưỡng trong cùng câu với lần thức trước vẫn không tính, vì điểm dao động quanh
   ngưỡng ngay trong một từ. Lần vượt ngưỡng ở một câu mới ra `WAKE` mới, dời cửa sổ của câu ấy về mốc của nó và tính lại
   thời gian chờ, thay cho việc chấm lần nói lại ấy thành một lệnh rồi từ chối. Chủ repo hỏi 31/10 khi nói "happy" nhiều
   lần liền.

## Hệ quả

- "happy" nói riêng không còn ra quyết định khi phần sau mốc ngắn hơn 31 bước; lệnh nói liền có cửa sổ bắt đầu ở cuối từ.
  Số trên phiên chủ repo đo ở E11-T40 🔬.
- Một tiếng động hay một lệnh không rõ trong `LENH` ra `REJECT` mà máy vẫn chờ, nên trong 3 s đó một câu nữa có thể thành lệnh.
- Cửa sổ của câu chứa từ đánh thức có thể bắt đầu muộn hơn ~0,4 s so với trước khi mạng được chạy tiếp; quyết định của lệnh
  nói liền chậm theo khi mạng phải chạy lại đoạn bù.
- Lời của từ đánh thức vẫn có thể lọt vào ở 21% số lần, ít hơn 68%; phép chấm của mạng `command` không đổi.

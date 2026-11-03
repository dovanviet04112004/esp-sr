# ADR-0041 — Gọi lại dưới ngưỡng, giới hạn từ chối và từ chối vì người nói trong `LENH`

- **Trạng thái**: Chấp nhận (chủ repo 01/11: "chọn cái tốt nhất đúng nhất", sau bản rà chuỗi `wake` → `LENH` của agent)
- **Ngày**: 2026-11-01
- **Liên quan**: KẾ HOẠCH §5.3, §5.4; ADR-0039; `docs/measurements/wake.md` §12; `docs/measurements/command.md` §12.45

---

## Bối cảnh

Trên board B với "vừng ơi" ở 850‰, chủ repo thức máy rồi nói lại "vừng ơi" giọng cao, to mỗi 2 s. Các câu ấy chỉ được
0,5–0,7 điểm `wake`, dưới ngưỡng, nên không thành thức lại (ADR-0039 điểm 5). Mỗi câu bị chấm như một lệnh, ra `REJECT
LOW_SCORE`, `PART`, `LOW_MARGIN`, và `LENH` không dứt chừng nào người ấy còn nói. Bản rà chuỗi bằng agent cùng ngày tìm
thêm:

- `LENH` không có giới hạn trên. Một câu không phải lệnh, hay tiếng động cách nhau dưới 3 s, giữ máy chờ mãi.
- Một tiếng click mở cửa sổ ngay bước `vad` đầu, nên đồng hồ chờ tính lại dù cửa sổ ấy sau đó bị bỏ vì ngắn.
- Lệnh bị `xac_task` chặn (`SPEAKER`, `SPEAKER_BUSY`, `SPEAKER_UNENROLLED`) đã đưa máy về `NGHE` từ lúc mạng `command`
  nhận nó. Người nói phải gọi lại từ đánh thức, trái với KẾ HOẠCH §5.4 "chỉ `COMMAND` đưa máy về `NGHE`".
- Lệnh của một cửa sổ mở trước lần thức lại kết thúc `LENH` của lần thức sau, nên lệnh kế tiếp bị nuốt.
- Vài lỗi đếm bước và cờ: phép so độ dài cửa sổ không dấu ở `extend_open`, cờ `cut_back` còn lại khi cửa sổ thành cửa
  sổ của `LENH`, lỗi của mạng `wake` bỏ qua phần sổ sách của bước, phần còn lại của câu bị luồng đứt mở cửa sổ mới.

Điểm `wake` (bản int8 `percentile` 910‰ của `vung1`, chuỗi ấm) trên các phiên của chủ repo: 617 câu lệnh có p99 60‰,
cao nhất 293‰; câu gần âm cao nhất 177‰; câu "vừng ơi" giọng cao, to bị hụt ở 507–688‰.

## Các phương án

| Vấn đề | Phương án | Chọn |
|---|---|---|
| Gọi lại dưới ngưỡng | giữ ADR-0039: câu ấy chấm như lệnh | |
| | ngưỡng thức thấp hơn cho câu mới trong `LENH` | ✓ |
| `LENH` không dứt | chỉ đồng hồ chờ | |
| | tối đa N cửa sổ bị từ chối mỗi lần thức | ✓ |
| Từ chối vì người nói | về `NGHE` như hiện tại | |
| | máy chờ tiếp, tính là một lần từ chối | ✓ |
| Click giữ `LENH` | đồng hồ chờ tính lại với mọi cửa sổ | |
| | chỉ với câu đã dài tới `utterance.min_s` hay cửa sổ đã chốt | ✓ |

## Quyết định

1. **Gọi lại.** Trong `LENH`, một câu mới (không phải câu vừa thức máy) vượt `SVC_LISTEN_WAKE_REPEAT_PERMILLE` (450‰,
   `Kconfig` của `svc_listen`) là thức lại như ADR-0039 điểm 5. 450‰ cao hơn mọi câu lệnh của chủ repo 157‰ và thấp hơn
   mọi câu "vừng ơi" hụt; đổi từ đánh thức hay model thì đo lại.
2. **Giới hạn từ chối.** Mỗi lần thức cho tối đa `SVC_LISTEN_COMMAND_TRIES` (2) cửa sổ bị từ chối, rồi về `NGHE`. Chỉ
   quyết định của mạng `command` mới tính, `FRAME_GAP` và cửa sổ bỏ vì lỗi thì không. Thức lại đặt lại số đếm.
3. **Từ chối vì người nói** để máy chờ tiếp và tính là một lần từ chối. `xac_task` ghi thế hệ `LENH` của cửa sổ vào
   một biến atomic, và `nhan_task` mở lại `LENH` ở bước kế khi thế hệ ấy là lần thức hiện tại và số lần từ chối còn
   cho phép. Cửa sổ mở ở `NGHE`, như lúc đăng ký giọng, không mở `LENH`.
4. **Đồng hồ chờ** chỉ tính lại khi có cửa sổ đã chốt chờ chấm, hay câu đang mở đã dài tới `utterance.min_s`.
5. **Sửa cho đúng KẾ HOẠCH:**
   - `COMMAND` chỉ kết thúc `LENH` khi cửa sổ của nó mở sau lần thức gần nhất;
   - mọi phép so bước và độ dài cửa sổ dùng hiệu có dấu, và cửa sổ của `LENH` không bao giờ chốt quá bước hiện tại;
   - cửa sổ thành cửa sổ của `LENH` thì bỏ `cut_back`;
   - lỗi của mạng `wake` vẫn để bước chạy hết phần sổ sách rồi mới trả lỗi;
   - phần còn lại của câu bị luồng đứt không mở cửa sổ, tới khi câu ấy chốt.

6. **Thức lại không ra `WAKE`.** Máy đã ở `LENH` nên không có trạng thái mới để báo; chỉ lần chuyển `NGHE` → `LENH`
   ra sự kiện `WAKE`. Thức lại vẫn dời cửa sổ về mốc mới và tính lại thời gian chờ, kèm một dòng log có bước và điểm. Điều
   này thay phần "một `WAKE` mới" của ADR-0039 điểm 5. Chủ repo 01/11, thấy một chuỗi `WAKE` 0,48–0,58 khi gọi lại: "ẩn nó
   đi".

Không ghi lệnh gần nhất vào `REJECT`: `ai_engine_command_result_t` không mang nó, và đó là hợp đồng đã đóng băng ở
`contract-v1` (KẾ HOẠCH §4.5.5).

## Hệ quả

- "Vừng ơi" nói lại trong `LENH` với điểm từ 450‰ là thức lại lặng lẽ, thay vì một chuỗi `REJECT`. Một câu lệnh có điểm `wake` từ
  450‰ sẽ thành thức lại và mất. Trên các phiên của chủ repo chưa câu nào như thế, nhưng giọng người khác chưa đo 🔬.
- Hai lần từ chối liền là hết `LENH`: người nói phải gọi lại từ đánh thức.
- Lệnh bị chặn vì người nói cho nói lại ngay, không phải gọi lại.
- Đo lại trên board bằng `make listen-unit` và phiên chủ repo sau khi nạp 🔬.

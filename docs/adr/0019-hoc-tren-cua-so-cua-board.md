# ADR-0019 — Học trên đúng cửa sổ board cắt, cửa sổ mở 2,0 s trước câu

- **Trạng thái**: Chấp nhận (chủ repo 05/10: sửa toàn diện mọi chỗ lúc học khác lúc chạy, rồi một lượt dựng dữ liệu, một
  lượt học; mạng không chạy liên tục; cao độ chạy liền mỗi bước)
- **Ngày**: 2026-10-05
- **Liên quan**: KẾ HOẠCH §1.2, §3.11, §3.12, §5.4, §5.6, §6.3; `measurements/command.md` §12; TASKS E11-T21

---

## Bối cảnh

B1 đúng 89/112 câu Cửa 3. Gần hết câu sai là âm tiết đầu của cặp lệnh chung âm tiết sau: "tắt" nghe thành "bật", "đóng
cửa" thành "mở cửa" (`measurements/command.md` §12.1). Đo ở §12.2–12.4 cho thấy năm chỗ lúc học khác lúc chạy:

- (a) Mạng bắt đầu mỗi cửa sổ từ bộ đệm rỗng và cần khoảng 2 s mới ổn định. Cửa sổ mở 2,0 s trước câu thay 1,25 s, B1
  lên 98/112 mà không học lại; phần trước câu trên `val` còn 0,3 s thì thanh đúng ở âm tiết đầu từ 69% xuống 39%.
- (b) Mẫu học bắt đầu 0–1,5 s trước tiếng và kết thúc sau tiếng 0,2–1,6 s, theo mẩu; board mở cửa sổ 1,25 s trước bước
  `vad` đầu và đóng ở bước `vad` cuối. Bộ dò cao độ đặt lại ở đầu mẩu lúc học, ở đầu cửa sổ trên board.
- (c) `agc` của phiên mô phỏng leo gần đích (+21 dB trung vị); phiên lệnh thật chạy chuỗi mới nên còn +5…+7 dB.
- (d) Đáp tuyến micro, cổng âm và vỏ coi là phẳng.
- (e) Cao độ không nhìn sau làm mức log F0 ở âm tiết đầu dịch theo phiên tới 0,6–0,8.

## Các phương án

Mạng thử học 8 000 bước cùng dữ liệu, cùng seed (§12.5); bản thường A được 87/112, "tắt" 4/22.

| Phương án | Đo được | Mất |
|---|---|---|
| S. Mạng trí nhớ ngắn: nhân 5, 3, 3, 3, bộ trộn 2 khung | 81/112; phần trước câu ngắn vẫn kém như A | không ấm nhanh hơn |
| K. Màu ngẫu nhiên ±4 dB mỗi câu | 83/112, "tắt" 0/22 | |
| G. Mức ngẫu nhiên ±6 dB mỗi câu | 82/112, UER 0,445 | |
| N. Log-mel trừ độ lợi `agc` | 80/112, "tắt" 0/22 | |
| D. Thêm log F0 thô, 84 chiều | 78/112 | |
| C. Cao độ chuẩn hoá nhìn sau 0,75 s | 88/112, "tắt" 7/22 | chậm thêm 0,75 s mỗi lệnh |
| Mạng chạy liên tục cả phiên, không đặt lại | B1 92/112 | mạng chạy cả lúc im; chủ repo không duyệt |
| Mạng chạy qua 0,75–2,75 s trước cửa sổ mà không chấm | B1 96–97/112 | cùng chi phí, kém phương án dưới |
| Bộ đệm khởi tạo bằng khung đầu | không đo | esp-dl `StreamingCache` chỉ đặt lại về 0 |
| **Cửa sổ mở 2,0 s, cao độ liền mỗi bước, mẫu học cắt bằng luật board, `agc` đầu phiên ngẫu nhiên** | B1 98/112 ở 2,0 s khi chưa học lại; chậm sau bước chốt ước 61 ms trung vị, 96 ms p90 🔬 (§12.7) | cao độ tốn 1,98 ms mỗi bước ở nhân 0 cả lúc không có lệnh; dựng lại dữ liệu, học lại |

## Quyết định

- `contracts/listen.yaml` version 4: `utterance.lead_s` 2,0 s (125 bước), `window_s` 3,75 s (234 bước).
- `svc_listen` tính cao độ mỗi bước trong `feed`, vào vòng đệm cùng log-mel, và chỉ đặt lại bộ dò khi mất khung; `work`
  chỉ chạy mạng trên hai vòng đệm (KẾ HOẠCH §5.4).
- Mô phỏng cắt mẫu học bằng `utterances` và `command_cut`, như board, trên `vad` của riêng tiếng người nói ở mức đích
  của `agc`, và tính cao độ liền cả phiên (KẾ HOẠCH §1.2). Các mẩu chung một câu gộp thành một mẫu, nhãn nối theo thứ
  tự. `vad` của tín hiệu có nhiễu thì cắt mất lời mà nhãn đòi ở 14% mẫu `val` (`measurements/command.md` §12.6).
- Mỗi phiên mô phỏng bắt đầu `agc` ở độ lợi rút đều trong [0, +30] dB, trên một luồng ngẫu nhiên riêng.
- Run ghi băm `listen.yaml`; ảnh model mang băm ấy, và `ai_engine` để `command` tắt khi băm khác bản dựng (KẾ HOẠCH §6.3).
- Mạng, tăng cường và bộ dò cao độ (không nhìn sau) giữ nguyên.

## Hệ quả

- `command/v5` dựng lại một lượt, danh sách mẩu như `command/v4`, rồi học một lượt; B1 không học tiếp.
- Firmware mới không nạp ảnh `format_ver` 1 và không chạy `command` của model không ghi băm: board nhận lệnh lại khi
  model v5 được deploy và nạp.
- Cao độ chuyển từ worker sang `nhan_task` mỗi bước (KẾ HOẠCH §5.6). Độ trễ quyết định và CPU nhân 0 đo lại trên board B
  với model v5.
- (d) và (e) để sau. Đo đáp tuyến board bằng sweep chỉ khi v5 vẫn hụt "tắt" hay "đóng cửa" và chủ repo đồng ý; không thu
  nền, không cộng nhiễu thu thật vào mô phỏng.
- `wake` có thể đọc cao độ từ cùng vòng đệm về sau.

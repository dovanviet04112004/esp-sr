# ADR-0024 — `speaker` trên chip: ngưỡng theo độ ồn board ước trên cửa sổ

- **Trạng thái**: Chấp nhận, kèm cổng chất lượng (chủ repo, 2026-10-12)
- **Ngày**: 2026-10-12
- **Liên quan**: KẾ HOẠCH §3.17, §4.5.5, §6.3; TASKS E11-T33; ADR-0023; `docs/measurements/speaker.md` §6.9

---

## Bối cảnh

Chủ repo thấy lệnh của mình qua kiểm người nói khi phòng yên, nhưng có chút nhạc nền thì tụt dưới ngưỡng. Ngưỡng của
ADR-0023 đặt trên vật liệu không có nguồn ồn nào. `speaker.md` §6.9 đo lại trên đường chip, thêm nhạc MUSAN vào cùng
vật liệu. Với ngưỡng 1 người 2964, chủ repo giữ:
- 98,0 / 97,4% khi yên;
- ~92% ở 20 dB nhạc;
- 67,6 / 71,9% ở 10 dB;
- 57,1 / 68,8% ở 5 dB.

Người lạ lại lọt *ít* hơn khi có nhạc, 0,08–0,37% so với 0,92% khi yên: nhạc kéo điểm của mọi người xuống. Ngưỡng của
phòng yên vì thế chặt quá mức cần khi có ồn.

## Các phương án

Cùng vật liệu và cùng 20 lần rút của `speaker-deploy`, một người có mẫu giọng, nhạc 10 dB:

| Phương án | Chủ repo giữ 1 m / 3 m | Người lạ lọt | Được | Mất |
|---|---|---|---|---|
| Ngưỡng cố định, ADR-0023 | 67,6 / 71,9% | 0,37% | không đổi gì | chặn ~30% lệnh được nhận khi có nhạc vừa |
| Đăng ký 5/10 lệnh lúc có nhạc | 73,8 / 81,7% | 0,49% | không đổi firmware | lúc yên người lạ lọt 1,12%, quá 1%; tuỳ người dùng làm đúng |
| Cửa sổ chỉ lời, cắt theo VAD | 37,8 / 43,0% | 0,53% | — | tệ hơn ở mọi mức, kể cả lúc yên (87,7 / 82,5%) |
| **Ngưỡng theo dải SNR board ước trên cửa sổ** | 79,9 / 81,9% | 0,72% | gần cận trên của ngưỡng đặt riêng cho từng mức nhạc (79,0 / 83,2%); mạng và mẫu giọng không đổi | khuôn ảnh mới, hợp đồng gọi đổi, `speaker-deploy` chạy thêm vật liệu có ồn |
| Học lại b0 có trộn nhạc và ồn | chưa đo | | sửa gốc | GPU; chủ repo chưa cho (11/10) |

Ngưỡng theo dải SNR lấy lại ~12 điểm ở 10 dB, ~2–3 điểm ở 20 dB, và người lạ lọt ≤ 1% ở mọi mức có nhạc. Một điều kiện
đi kèm: dải yên nhất phải giữ ngưỡng của phòng yên. Ngưỡng tính riêng cho dải ấy gộp cả người lạ của 20 dB, nên lúc
yên để lọt 1,48%.

## Quyết định

Chi tiết ở KẾ HOẠCH §3.17.

- **Độ ồn của một lệnh** là tỉ số công suất lời trên công suất đoạn trước lời, cùng trong cửa sổ lệnh, trên mẫu `clean`
  int16:
  - đoạn trước lời chạy từ bước đầu cửa sổ tới trước bước VAD đầu của câu; đoạn lời từ bước ấy tới bước cuối cửa sổ;
  - tổng bình phương là số nguyên, so với biên bằng double theo một thứ tự cố định, nên bản soi gương Python và chip
    cho cùng một dải;
  - không có đoạn trước lời, đoạn ấy câm, hay đoạn ấy đã trôi khỏi vòng PCM, thì lệnh vào dải yên nhất.
- **Bốn dải**, phân bởi ba biên tăng dần, là tỉ số công suất float32 trong header ảnh.
- **Ngưỡng:**
  - dải yên nhất dùng bảng 8 ngưỡng của ADR-0023, đặt trên vật liệu yên, và không thấp hơn mức để 1% người lạ của dải ấy
    lọt;
  - mỗi dải ồn có một bảng 8 ngưỡng riêng, đặt ở 1% người lạ của dải.
- **`speaker-deploy`** chạy vật liệu yên như cũ, cộng ba mức ồn 20, 10, 5 dB rút từ các kho ồn của `scenes/device.yaml`
  theo trọng số của nó: ồn, nhạc, tiếng người, DEMAND.
  - Phiên của chủ repo trộn ồn phát từ một phòng của kho mô phỏng. Người lạ nghe ồn qua nguồn ồn của mô phỏng.
  - Biên là ba điểm tứ phân vị của độ ồn trên cửa sổ người lạ ở cả bốn mức.
- **Cổng:** cùng các cửa sổ ấy được chấm thêm dưới một ngưỡng như ADR-0023. Bảng dải chỉ vào ảnh khi, ở mọi số
  người:
  - lúc yên nó không để lọt nhiều người lạ hơn, và không giữ ít lệnh của chủ repo hơn ở khoảng cách nào;
  - ở mỗi mức ồn nó để lọt không quá 1% người lạ, hay không quá một ngưỡng nếu một ngưỡng đã quá 1%.

  Không qua thì ba biên là 0: mọi cửa sổ vào dải yên nhất, và board chấm như ADR-0023.

## Hệ quả

- **Ảnh model đổi sang khuôn 9:** header thêm ba biên và bảng 8 × 3 ngưỡng của các dải ồn (KẾ HOẠCH §6.3).
- **Hợp đồng gọi của `ai_engine` đổi** (§4.5.5):
  - thêm `ai_engine_speaker_noise_bin`;
  - `ai_engine_speaker_passes` nhận thêm dải.
- **`svc_listen` cộng hai tổng bình phương** lúc chép tiếng sang ô xác minh, trên cửa sổ lệnh dài tới 234 bước, tức
  60 000 mẫu. Đo trên board, cả bước giữ tiếng (chép đuôi và hai tổng) mất 3,66 ms trung bình, 4,47 ms đỉnh, ở
  `nhan_task` trên nhân 0 (`latency.md` §29).
- **`speaker-deploy` chạy lâu gấp ~4 lần,** cỡ một giờ, nên chạy trong tmux.
- **Không ồn thì như ADR-0023:** cửa sổ yên rơi vào dải yên nhất, dùng đúng bảng cũ.
- **Với các kho ồn của board, bảng dải không qua cổng** (`speaker.md` §6.9, đợt 3), nên ảnh mang biên 0:
  - lúc yên nó mất 0,3 điểm, và với 3–8 người để lọt 1,16–1,36% người lạ;
  - cận trên, biết đúng mức ồn, chỉ được 1–3 điểm: hỗn hợp ấy gần như không kéo điểm người lạ xuống, khác với nhạc;
  - người lạ mô phỏng đọc câu dài nối sát nhau, nên độ ồn đo trên đoạn trước lời của họ gần như không theo mức ồn.

  Bảng dải chỉ có thể có lợi khi có vật liệu hiệu chỉnh tốt hơn, là người lạ thật nói lệnh qua board lúc có ồn
  (E11-T27).
- **Giới hạn:**
  - ồn và nhạc là mô phỏng trộn vào bản thu thật, người lạ là người mô phỏng; số thật cần thu qua board (E11-T27);
  - ngưỡng theo độ ồn chỉ giúp bước kiểm người nói. Ở 10 dB nhạc, bộ nhận lệnh chỉ nhận 61% cửa sổ, so với 85% lúc yên;
    đó là việc riêng.

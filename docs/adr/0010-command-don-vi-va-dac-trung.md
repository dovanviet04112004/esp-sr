# ADR-0010 — `command` là CRNN + CTC trên 44 đơn vị `lang_vi`, thanh chen trong chuỗi, từ log-mel 40 cộng cao độ

- **Trạng thái**: Chấp nhận; phần kiến trúc thay bởi ADR-0013
- **Ngày**: 2026-09-29
- **Liên quan**: KẾ HOẠCH §3.3, §3.11, §3.12; TASKS E11-T3, E11-T8, E11-T12

---

## Bối cảnh

KẾ HOẠCH để hai câu hỏi của `command` cho phép đo: bốn đường đơn vị nhận dạng (E11-T3) và ba bộ đặc trưng (E11-T8),
mỗi phương án một lượt mô phỏng và học, chấm trên các cặp lệnh chỉ khác thanh; cộng lại tám, chín lượt trước khi học
mạng thật. Chủ dự án chọn theo số đã công bố và chọn đường chính xác nhất thay vì chạy lại các phép so ấy. Bảng dưới là
số của các bài ấy, không phải số đo của repo: dữ liệu, cỡ mạng và điều kiện của họ khác ta (GPU, không int8, không qua
mic board).

## Các phương án

**Đơn vị** (tiếng Việt có 6 thanh là âm vị):

| Phương án | Được | Mất | Số đã công bố |
|---|---|---|---|
| Âm đoạn + đầu ra thanh riêng | bộ ký hiệu nhỏ | hai đầu ra phải khớp theo thời gian | CTC, 3 ngôn ngữ có thanh (tiếng Việt 18 giờ): tầng thanh đứng riêng có lỗi thanh tiếng Việt cao nhất, 48,1% [1] |
| Âm vị mang thanh | một đầu ra | vần × 6 thanh, hàng trăm ký hiệu; đổi hợp đồng `lang_vi` đã đóng băng (KẾ HOẠCH §4.5.5) | cùng bài: lỗi âm và thanh gộp 53,45%, ngang các mô hình nhiều tầng (53,37–53,49%) [1] |
| Âm tiết | kho âm tiết đóng | vài nghìn ký hiệu cho mạng ≤ 1,8 MB | tiếng Trung, CTC: âm vị, âm tiết mang thanh và chữ Hán cho lỗi xấp xỉ nhau [2] |
| **Thanh chen trong chuỗi CTC** | một đầu ra, 44 ký hiệu, CTC tự căn; `lang_vi` đã sinh đúng bộ này, C và Python khớp từng bit | chưa có bài nào so trực tiếp cách này | đơn vị có thanh giảm lỗi từ tiếng Việt khoảng 19% tương đối so với đơn vị không thanh [3]; đầu, vần cộng ký hiệu thanh riêng thắng chữ cái trên VIVOS, CER 11,96% so với 18,54% [4] |

**Đặc trưng:**

| Phương án | Được | Mất | Số đã công bố |
|---|---|---|---|
| log-mel 40 | đã có trên board | 40 dải mel thô ở 100–300 Hz nơi F0 nằm | mốc so |
| log-mel 80 | dải thấp mịn hơn | tính mel và đầu vào gấp đôi | không tìm thấy số cho mạng nhỏ |
| **log-mel 40 + log F0, delta, độ hữu thanh** | đường thanh vào thẳng mạng | module mới `dsp_spec/pitch` đủ bốn bước, ~300 µs mỗi khung 🔬 | tiếng Việt: cao độ giảm lỗi từ khoảng 18% tương đối, 13,55% → 11,0% [3]; bộ dò cao độ của Kaldi cải thiện lớn trên các ngôn ngữ có thanh của Babel [5]; thêm F0 cải thiện mọi mô hình CTC của [1] |

## Quyết định

- **Đơn vị**: 44 đơn vị của `lang_vi`, mỗi âm tiết theo thứ tự đầu, đệm, chính, cuối, thanh (`má` là `m a: T5`), một đầu
  ra CTC. Các bài không cho thấy cách đặt thanh nào hạ lỗi gộp rõ rệt [1, 2], nên chọn theo giá: bộ ký hiệu nhỏ nhất,
  một đầu ra, không đổi hợp đồng đã đóng băng. Điều chắc từ các bài là thanh phải nằm trong đơn vị [3, 4], và cách này
  giữ nó.
- **Đặc trưng**: log-mel 40 cộng ba chiều cao độ; dựng `dsp_spec/pitch`. Mọi bài đo được đều thấy cao độ có lợi cho
  ngôn ngữ có thanh [1, 3, 5]; không có số nào cho 80 dải ở mạng nhỏ.
- **Kiến trúc**: CRNN nhỏ (tích chập rồi GRU một chiều) + CTC, như MultiNet của Espressif chạy nhận lệnh trên chính
  ESP32-S3, đầu ra âm vị, lệnh thêm bằng chữ [6]. Ta chạy bằng esp-dl chứ không bằng bộ chạy đóng của Espressif, và GRU
  int8 chạy dòng qua esp-dl chưa được thử trong repo, nên một phép thử trên board đi trước: xuất qua ESP-PPQ, đẩy từng
  bước, khớp mô phỏng, đo µs, như E11-T10 đã làm cho TCN. Không đạt thì lùi về TCN nhân quả tách chiều sâu, đường đã
  chạy khớp từng bit trên board.

## Hệ quả

- KẾ HOẠCH §3.3, §3.11, §3.12 ghi ba lựa chọn; E11-T3 đóng bằng ADR này; E11-T8 thành việc dựng `dsp_spec/pitch`
  (Python, C, golden có đối chứng âm, đo trên board) và chọn `log_floor`; E11-T12 bắt đầu bằng phép thử GRU trên board.
- `wake` giữ log-mel 40: lỗi của nó là dữ liệu dương toàn TTS (`docs/measurements/wake.md` §4), không phải thanh.
- Mạng `command` học xong thì đo tỉ lệ đúng trên các cặp lệnh chỉ khác thanh và ghi vào `docs/measurements/`. Xét lại
  quyết định khi các cặp ấy trượt Cửa 3 trong khi các lệnh khác đạt: thử đường âm vị mang thanh, rồi 80 dải.

## Nguồn

1. J. Li, M. Hasegawa-Johnson, *Autosegmental Neural Nets: Should Phones and Tones be Synchronous or Asynchronous?*,
   Interspeech 2020, arXiv:2007.14351, bảng 3.
2. W. Zou và cộng sự, *A comparable study of modeling units for end-to-end Mandarin speech recognition*, 2018,
   arXiv:1805.03832.
3. Q. B. Nguyen và cộng sự, *The Effect of Tone Modeling in Vietnamese LVCSR System*, Procedia Computer Science 81, 2016.
4. *ViSpeechFormer: A Phonemic Approach for Vietnamese Automatic Speech Recognition*, 2026, arXiv:2602.10003, bảng 3.
5. P. Ghahremani và cộng sự, *A pitch extraction algorithm tuned for automatic speech recognition*, ICASSP 2014.
6. Espressif, ESP-SR, *Speech Command Recognition* (MultiNet): mô hình nhẹ dựa trên CRNN và CTC, đầu ra âm vị.

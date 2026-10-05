# `vad` (E7-T3)

## 1. So với ngưỡng năng lượng trần trên tiếng Việt có nhãn

`make eval-vad` (`srpipe.scenes.vad`, `ml/configs/afe/vad.yaml`) tại `7edbdbb`, bản Python soi gương; hai lần chạy cho số trùng từng chữ.

| Mục | Giá trị |
|---|---|
| Tiếng | VIVOS `test`: 760 câu đọc, 19 người nói, 16 kHz (`manifests/speech/vivos.yaml`) |
| Cảnh | 48 = 6 nhiễu × 4 SNR × 2 mức; mỗi cảnh 120 s tiếng đọc chọn ngẫu nhiên, trước mỗi câu một khoảng im 0,5–2 s |
| Nhiễu | `_background_noise_` của Speech Commands: rửa bát, xe đạp tập, mèo kêu, ồn hồng, vòi nước, ồn trắng; lặp từ điểm ngẫu nhiên |
| SNR | 20, 10, 5, 0 dB: mức tiếng trên các bước nói so với mức nhiễu cả cảnh |
| Mức | −26 và −40 dBFS trên các bước nói, bình phương toàn thang, trước `agc` |
| Nhãn | mỗi bước 16 ms từ câu sạch: trong 40 dB dưới bước to nhất của câu **và** trên 10 dB so với nền của chính câu (phân vị 10) |
| Tổng | 227 430 bước nói, 267 847 bước im, ≈ 2,2 giờ |
| Máy dò trần | năng lượng bước so một ngưỡng cố định; ngưỡng được quét −80 … −10 dBFS và lấy giá trị cho F1 tốt nhất trên **toàn bộ** cảnh — đối thủ mạnh nhất nó có thể có |

Ô: F1 (% bỏ sót / % báo nhầm), gộp mọi cảnh của cột.

**Kéo dài 240 ms, như `dsp_afe` ra** (cả hai máy dò cùng kéo dài):

| Máy dò | tất cả | SNR 20 dB | SNR 10 dB | SNR 5 dB | SNR 0 dB | mức −26 dBFS | mức −40 dBFS |
|---|---|---|---|---|---|---|---|
| vad GMM, aggressiveness 0 | 0,775 (3,3 / 44,7) | 0,817 (0,9 / 36,4) | 0,819 (1,6 / 36,1) | 0,783 (2,7 / 43,7) | 0,689 (8,2 / 62,9) | 0,748 (0,9 / 56,2) | 0,807 (5,8 / 33,3) |
| vad GMM, aggressiveness 1 | 0,780 (6,9 / 38,8) | 0,828 (1,0 / 33,7) | 0,833 (2,5 / 31,6) | 0,806 (6,5 / 32,9) | 0,658 (17,7 / 57,3) | 0,766 (1,4 / 49,9) | 0,795 (12,3 / 27,8) |
| vad GMM, aggressiveness 2 | 0,791 (13,8 / 26,8) | 0,837 (1,2 / 31,3) | 0,841 (5,2 / 26,6) | 0,785 (18,7 / 22,0) | 0,688 (30,5 / 27,4) | 0,809 (7,1 / 31,3) | 0,772 (20,6 / 22,4) |
| vad GMM, aggressiveness 3 | 0,765 (24,1 / 19,2) | 0,860 (3,4 / 23,5) | 0,818 (14,4 / 20,5) | 0,734 (30,8 / 16,4) | 0,607 (48,1 / 16,1) | 0,796 (15,0 / 24,3) | 0,728 (33,2 / 14,0) |
| năng lượng trần, ngưỡng tốt nhất −42 dBFS | 0,717 (4,6 / 60,1) | 0,831 (8,1 / 24,5) | 0,720 (6,8 / 56,8) | 0,706 (2,9 / 66,4) | 0,641 (0,6 / 93,4) | 0,673 (0,2 / 82,6) | 0,772 (9,1 / 37,8) |

**Quyết định thô**, không kéo dài:

| Máy dò | tất cả | SNR 20 dB | SNR 10 dB | SNR 5 dB | SNR 0 dB | mức −26 dBFS | mức −40 dBFS |
|---|---|---|---|---|---|---|---|
| vad GMM, aggressiveness 0 | 0,798 (18,7 / 19,0) | 0,911 (6,1 / 10,2) | 0,868 (16,2 / 7,9) | 0,800 (24,6 / 11,1) | 0,632 (28,1 / 46,9) | 0,798 (9,8 / 30,6) | 0,798 (27,7 / 7,5) |
| vad GMM, aggressiveness 1 | 0,765 (27,5 / 14,4) | 0,913 (8,1 / 8,0) | 0,828 (24,9 / 5,4) | 0,725 (38,7 / 6,6) | 0,595 (38,6 / 38,0) | 0,790 (16,7 / 23,5) | 0,733 (38,4 / 5,4) |
| vad GMM, aggressiveness 2 | 0,711 (41,6 / 5,1) | 0,912 (9,8 / 6,4) | 0,768 (35,2 / 3,4) | 0,583 (57,3 / 3,3) | 0,495 (64,3 / 7,3) | 0,750 (35,4 / 6,5) | 0,668 (47,7 / 3,7) |
| vad GMM, aggressiveness 3 | 0,606 (55,3 / 2,5) | 0,849 (23,9 / 2,7) | 0,648 (51,0 / 2,0) | 0,476 (68,1 / 1,8) | 0,342 (78,5 / 3,7) | 0,685 (45,6 / 3,8) | 0,513 (65,0 / 1,2) |
| năng lượng trần, ngưỡng tốt nhất −45 dBFS | 0,697 (12,4 / 54,2) | 0,812 (21,7 / 12,2) | 0,691 (18,0 / 47,8) | 0,685 (7,4 / 66,0) | 0,639 (2,1 / 91,4) | 0,681 (0,8 / 78,7) | 0,720 (23,9 / 29,8) |

## 2. Đọc bảng

- GMM hơn máy dò trần ở **mọi** `aggressiveness` khi gộp mọi cảnh: F1 0,765–0,791 so với 0,717 có kéo dài; 0,798 so với 0,697 ở
  mức 0 thô. Đó là điều kiện xong của E7-T3.
- Chỗ máy dò trần thua là mức tiếng đổi: một ngưỡng phải phục vụ cả −26 và −40 dBFS, nên ở −26 dBFS nó báo nhầm 82,6%. GMM
  học mức nhiễu nền theo thời gian nên không cần biết trước mức.
- Ở SNR 20 dB máy dò trần có ngưỡng tốt nhất ngang mức 0 và 1 (0,831 so với 0,817 và 0,828), thua mức 2 và 3.
- `aggressiveness` đổi bỏ sót lấy báo nhầm: có kéo dài, mức 0 bỏ sót 3,3% và báo nhầm 44,7%, mức 3 bỏ sót 24,1% và báo
  nhầm 19,2%. Báo nhầm cao phần lớn do kéo dài phủ khoảng nghỉ: bỏ kéo dài, mức 0 còn 19,0%.
- Mức mặc định của chuỗi (`afe/vad_mode`) chưa chốt ở đây: `agc` chỉ thích nghi khi `vad = 1` (E7-T4) và thước cuối là của bộ
  nhận dạng (KẾ HOẠCH §3.15), nên chọn mức khi có số của hai thứ ấy.

## 3. Độ trung thành với WebRTC

Bản soi gương chạy khung 20 ms kèm bộ đếm kéo dài của WebRTC, so từng khung với gói `webrtcvad` (bản dựng sẵn của
`py-webrtcvad`) trên tiếng tổng hợp giống lời nói, ba mức, ồn trắng, hồng, nâu, SNR 20 và 5 dB, 18 cảnh × 12 s mỗi mức:
khớp **99,6%, 99,5%, 99,4%, 99,3%** số khung ở mức 0–3. Phần lệch nằm ở tín hiệu rất nhỏ và lúc mô hình nhiễu đang hội tụ,
nơi số học nguyên của WebRTC làm tròn bước cập nhật nhỏ về 0. `ml/tests/test_vad.py` giữ phép so này với ngưỡng 97%.

## 4. Giới hạn: nền ồn to

WebRTC chặn trung bình mô hình nhiễu ở 67–72 dB thang `int16` mỗi dải (`noise_mean_max_db`), nên nền ồn to hơn cỡ
−40 dBFS không được học thành nhiễu và bị gọi là lời nói. Trong phép đo `agc` (`agc.md`), lời nói −10 dBFS trên ồn trắng
SNR 20 dB (nền −30 dBFS) bị `vad` mức 0 gắn cờ nói gần như suốt cảnh. Mức 2 chịu được mọi cảnh ở đó; đó là lý do mức
gieo cho `afe/vad_mode` là 2.

## 5. Mức thấp của board B (E7-T6)

Lời nói trên board B ở −57 … −52 dBFS trước `agc` (`measurements/command.md` §12.4), dưới cả hai mức của §1. Cùng cảnh
của §1 (VIVOS test, nền `speech_commands`, nghỉ 0,5–2 s), thêm mức −50, −55, −60, −65 dBFS; `vad` mức 2 với 240 ms kéo
dài, đầu vào như chuỗi đưa hay nhân một độ lợi cố định chỉ cho `vad`. Đo 05/10 bằng script chẩn đoán chạy một lần; mỗi ô
gộp mọi nền của một mức và một SNR. Câu không chạm: câu của nhãn không bước `vad` nào chạm.

| Mức, SNR | F1 ở 0 / +6 / +12 / +18 / +24 dB | Bước có tiếng bị sót ở 0 / +12 / +24 dB | Báo nhầm ở 0 / +12 / +24 dB | Câu không chạm ở 0 / +12 / +24 dB |
|---|---|---|---|---|
| −40, 20 dB | 0,846 / 0,845 / 0,844 / 0,841 / 0,840 | 1,3 / 1,3 / 1,1% | 29,5 / 30,0 / 31,2% | 2 / 2 / 2 trên 232 |
| −40, 5 dB | 0,763 / 0,768 / 0,813 / 0,810 / 0,643 | 25,1 / 14,2 / 0,2% | 18,5 / 21,7 / 95,0% | 6 / 1 / 0 trên 221 |
| −40, 0 dB | 0,592 / 0,630 / 0,727 / 0,652 / 0,640 | 51,1 / 27,1 / 0,0% | 14,6 / 24,4 / 99,7% | 50 / 16 / 0 trên 227 |
| −50, 10 dB | 0,835 / 0,848 / 0,848 / 0,847 / 0,850 | 9,6 / 5,1 / 3,6% | 22,8 / 25,2 / 26,5% | 3 / 1 / 1 trên 224 |
| −55, 10 dB | 0,761 / 0,840 / 0,842 / 0,842 / 0,842 | 26,3 / 7,1 / 6,6% | 17,0 / 23,6 / 24,2% | 12 / 0 / 0 trên 231 |
| −60, 10 dB | 0,647 / 0,782 / 0,839 / 0,842 / 0,841 | 45,1 / 6,1 / 4,5% | 12,9 / 26,1 / 27,5% | 47 / 1 / 1 trên 230 |
| −60, 5 dB | 0,442 / 0,624 / 0,745 / 0,757 / 0,758 | 68,6 / 26,8 / 23,6% | 8,9 / 19,3 / 20,9% | 112 / 21 / 15 trên 231 |
| −65, 10 dB | 0,454 / 0,654 / 0,815 / 0,842 / 0,847 | 68,5 / 14,8 / 5,0% | 6,3 / 21,2 / 26,0% | 82 / 6 / 0 trên 217 |
| −65, 20 dB | 0,802 / 0,864 / 0,850 / 0,849 / 0,846 | 22,5 / 1,6 / 1,4% | 14,0 / 29,1 / 30,2% | 4 / 0 / 0 trên 223 |

Ở mức của board, đầu vào như chuỗi đưa sót phần lớn tiếng khi SNR từ 10 dB trở xuống, và nâng 12–24 dB đưa F1 về mức của
−40 dBFS. Ở −40 dBFS, nâng từ 18 dB làm nền ồn của SNR 5 và 0 dB thành tiếng nói (báo nhầm 94–100%); ở −26 dBFS của §1
còn tệ hơn. Không có một độ lợi cố định cho mọi mức: độ lợi cho `vad` phải theo mức của chính tín hiệu, ví dụ đưa nền
ồn về quanh −58 … −70 dBFS rồi chặn trần, mà mọi ô trên đều hợp: nền −45 dBFS của −40 dBFS SNR 5 không được nâng, nền
−70 dBFS của −60 dBFS SNR 10 được nâng 12 dB.

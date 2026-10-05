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

Độ lợi theo mức của chính tín hiệu: nền ồn là cực tiểu trượt nhân quả 1,5 s của năng lượng bước đã làm mượt (hệ số 0,25),
độ lợi cho `vad` bước sau = mức tham chiếu trừ nền, kẹp 0 … 24 dB (tham chiếu −58 … −70) hay 0 … 30 dB (−42 … −54);
cùng cảnh, thêm mức −26 dBFS của §1. Trung bình F1 của 4 SNR (20, 10, 5, 0 dB) mỗi mức, rồi của các mức trong cột:

| Tham chiếu nền | F1 ở −26 và −40 dBFS | F1 ở −50 dBFS | F1 ở −55 … −65 dBFS | Câu không chạm, −55 … −65 dBFS | Ô −26/−40 tụt nhất |
|---|---|---|---|---|---|
| như chuỗi đưa | 0,785 | 0,733 | 0,536 | 872 / 2 705 | — |
| −70 | 0,784 | 0,731 | 0,586 | 744 | 0,004 |
| −66 | 0,784 | 0,727 | 0,620 | 643 | 0,002 |
| −62 | 0,784 | 0,724 | 0,648 | 542 | 0,003 |
| −58 | 0,783 | 0,730 | 0,681 | 424 | 0,004 |
| −54 | 0,782 | 0,725 | 0,708 | 310 | 0,007 |
| −50 | 0,779 | 0,732 | 0,729 | 245 | 0,010 |
| −46 | 0,776 | 0,737 | 0,736 | 208 | 0,015 |
| −42 | 0,773 | 0,744 | 0,744 | 184 | 0,026 |

Tham chiếu càng cao càng đưa `vad` tới lời nhỏ, nhưng ở −26 và −40 dBFS cực tiểu trượt có lúc thấp hơn nền thật, nên
nâng một chút và báo nhầm thêm: F1 tụt tới 0,007 ở −54. Điều kiện xong của E7-T6 đòi F1 trên cảnh của §1 không kém, nên
bộ ước nền phải không bao giờ nâng cảnh to.

Độ lợi bám đỉnh: đỉnh là năng lượng bước lớn nhất, giữ rồi nhả 2 dB/s; độ lợi bước sau = đích trừ đỉnh, kẹp 0 … 30 dB, nên
không bao giờ nâng cảnh có đỉnh trên đích. Cùng cảnh và cùng cột với bảng trên:

| Đích đỉnh | F1 ở −26 và −40 dBFS | F1 ở −50 dBFS | F1 ở −55 … −65 dBFS | Câu không chạm, −55 … −65 dBFS | Ô −26/−40 tụt nhất |
|---|---|---|---|---|---|
| như chuỗi đưa | 0,785 | 0,733 | 0,536 | 872 / 2 705 | — |
| −40 | 0,782 | 0,702 | 0,703 | 358 | 0,016 |
| −36 | 0,771 | 0,698 | 0,707 | 353 | 0,050 |
| −32 | 0,765 | 0,708 | 0,719 | 290 | 0,083 |

Thua tham chiếu nền −54 ở mọi cột, và kéo −50 dBFS xuống 0,03 (−50 dBFS SNR 5 dB: 0,735 → 0,680, bỏ sót 30,0 → 37,8%).

Độ lợi theo phân vị: phân vị 10 (nền) và 90 (lời) của năng lượng các bước trong 6 s trước bước, nhân quả; độ lợi = tham
chiếu nền trừ nền, hay nhỏ hơn nữa nếu tham chiếu lời trừ lời nhỏ hơn, kẹp 0 … 30 dB; một biến thể đổi độ lợi không quá
6 dB/s. Cùng cảnh, cùng cột, chạy lại cả hai bộ ước trên (số trùng); thêm độ lợi trung bình trên bước có tiếng / bước im
của nhãn ở −40 dBFS SNR 20 dB:

| Bộ ước | F1 ở −26 và −40 dBFS | F1 ở −50 dBFS | F1 ở −55 … −65 dBFS | Câu không chạm, −55 … −65 dBFS | Ô −26/−40 tụt nhất | Độ lợi tiếng / im, dB |
|---|---|---|---|---|---|---|
| như chuỗi đưa | 0,785 | 0,733 | 0,536 | 872 / 2 705 | — | 0 / 0 |
| cực tiểu trượt, nền −54 | 0,782 | 0,725 | 0,708 | 310 | 0,007 | 6,6 / 8,1 |
| bám đỉnh, đích −40 | 0,782 | 0,702 | 0,703 | 358 | 0,016 | 0,1 / 1,1 |
| phân vị, nền −54 | 0,785 | 0,738 | 0,718 | 315 | 0,001 | 9,0 / 9,0 |
| phân vị, nền −54, lời −36 | 0,785 | 0,738 | 0,718 | 315 | 0,001 | 2,9 / 3,7 |
| phân vị, nền −54, lời −32 | 0,785 | 0,738 | 0,718 | 315 | 0,002 | 6,2 / 6,6 |
| phân vị, nền −54, lời −36, 6 dB/s | 0,785 | 0,741 | 0,721 | 313 | 0,001 | 3,0 / 3,6 |
| phân vị, nền −50, lời −36 | 0,784 | 0,742 | 0,738 | 245 | 0,003 | 3,0 / 3,9 |
| phân vị, nền −50, lời −32 | 0,783 | 0,743 | 0,739 | 244 | 0,005 | 6,7 / 7,6 |

Cái hại không nằm ở mức nâng mà ở độ lợi lệch giữa lúc nói và lúc im. Ở −40 dBFS SNR 20 dB, nền phân vị nâng 9,0 dB đều
cả hai loại bước và F1 giữ 0,833 như chuỗi đưa; cực tiểu trượt nâng ít hơn nhưng lúc im cao hơn lúc nói 1,5 dB, F1 tụt
0,827. Bám đỉnh lên trong khoảng nghỉ rồi sập ở bước to đầu câu: lúc im cao hơn lúc nói 1,0 dB ở đây, 2,0 dB ở −50 dBFS
SNR 5 dB (4,8 / 6,8), 2,5 dB ở −60 dBFS SNR 10 dB (15,4 / 17,9). Mô hình nhiễu của GMM học nền đã nâng, lời nói tới với
độ lợi thấp hơn, nên tiếng bị sót thêm đúng ở các ô SNR thấp.

Nền phân vị −54 giữ F1 trên cảnh của §1 ở 0,785 (lệch dưới 0,0005), ô tụt nhất 0,001, và hơn cực tiểu trượt ở mọi cột;
thêm tham chiếu lời −36 không đổi số mà bớt nâng cảnh vốn đủ to (9,0 → 2,9 dB ở −40 dBFS SNR 20 dB); giới hạn 6 dB/s nhích
thêm ở mức thấp. Nền −50 tới được lời nhỏ hơn (câu không chạm 245 so với 315) nhưng F1 trên §1 tụt 0,001–0,002. Ở
−55 … −65 dBFS câu không chạm còn 11,6%, gần hết ở SNR 5 và 0 dB. Thước quyết định là của E7-T6: các bộ ước này chạy trong
chuỗi trên 198 phiên `val` của `command/v5`, cắt như board (`measurements/command.md` §12.6).

## 6. Trong chuỗi, cắt như board trên `val` (E7-T6)

198 phiên `val` của `command/v5` (1 583 mẩu) dựng lại từng phiên qua đường mô phỏng board, chuỗi sản phẩm và luật cắt
cửa sổ của `contracts/listen.yaml` trên `vad` của chuỗi, như `measurements/command.md` §12.6; script chẩn đoán chạy một lần
05/10 trên code hiện tại. Bộ ước của §5 đưa đầu vào đã nâng cho `vad` của chuỗi (phân vị trên tần suất 1 dB của năng lượng
bước trong 6 s); `agc` vẫn theo `vad` ấy. Phiên lấy nhiễu với xác suất 0,85, SNR 0 … 30 dB, từ bốn kho: ồn `musan` (trọng
số 3), DEMAND (2), nhạc `musan` (1), người nói `musan` (1) (`configs/scenes/device.yaml`).

Mẩu mất vì gộp là mẩu thứ hai trở đi của một cửa sổ nhiều mẩu: board chỉ quyết định 3,75 s cuối của câu. Mẩu một mình, đủ
tiếng là mẩu đứng một mình trong cửa sổ, tiếng sau điểm kết không quá 15 bước và không có tiếng trước điểm mở.

| Đầu vào `vad` | Mẩu không chạm | Cửa sổ mất > 15 bước tiếng cuối | Mẩu mất vì gộp | Cửa sổ dài hơn 12 s | Đủ 2,0 s trước câu | Mẩu một mình, đủ tiếng |
|---|---|---|---|---|---|---|
| như chuỗi đưa | 50 (3,2%) | 12,9% | 6,9% | 40 | 70,9% | 74,7% |
| nền −50 | 0 | 2,8% | 21,4% | 118 | 39,5% | 67,0% |
| nền −54 | 0 | 2,9% | 21,7% | 119 | 39,2% | 66,3% |
| nền −58 | 0 | 3,0% | 21,9% | 116 | 38,8% | 66,1% |
| nền −62 | 0 | 3,1% | 21,4% | 116 | 40,6% | 66,8% |
| nền −54, lời −36, 6 dB/s | 0 | 3,5% | 14,2% | 71 | 52,7% | 76,2% |
| nền −58, lời −36, 6 dB/s | 0 | 3,5% | 14,1% | 70 | 52,6% | 76,2% |
| nền −62, lời −36, 6 dB/s | 0 | 3,5% | 14,0% | — | — | 76,4% |
| nền −58, lời −40, 6 dB/s | 0 | 3,5% | 13,5% | — | — | 77,2% |
| nền −58, lời −44, 6 dB/s | 0 | 3,7% | 12,8% | — | — | 77,8% |

Tách theo kho nhiễu, mẩu mất vì gộp / mẩu một mình, đủ tiếng:

| Kho (phiên, mẩu) | như chuỗi đưa | nền −54 | nền −58, lời −44, 6 dB/s |
|---|---|---|---|
| không nhiễu (34, 271) | 0,0% / 84,1% (không chạm 5,2%) | 2,2% / 92,6% | 0,0% / 94,8% |
| DEMAND (49, 392) | 1,5% / 83,2% | 15,3% / 72,4% | 5,6% / 86,2% |
| ồn `musan` (72, 576) | 10,1% / 71,0% | 21,0% / 68,1% | 15,5% / 75,3% |
| nhạc `musan` (19, 152) | 5,9% / 67,8% | 46,1% / 37,5% | 21,7% / 65,8% |
| người nói `musan` (24, 192) | 18,8% / 60,9% | 45,3% / 34,4% | 30,2% / 53,6% |

Nâng chữa đúng hai lỗi E7-T6 nhắm tới: không mẩu nào bị sót, mất tiếng cuối từ 12,9% còn 3%. Ở phòng im nó là lợi thật,
84,1 → 92,6%. Nhưng nó đưa cả nền vào vùng GMM nghe được, và GMM không tách được người nói với nhạc hay tiếng người nền:
ở mọi kho có nhiễu `vad` bật trong khoảng nghỉ 1,2–3,0 s giữa hai mẩu, nối chúng thành một cửa sổ. Ở SNR 20–30 dB vẫn mất
18,2% vì gộp, so với 3,0% của chuỗi đưa: chuỗi hiện tại không nghe nền nhỏ, và chính sự điếc ấy giữ cửa sổ của nó gọn.
Mức tham chiếu nền −50 … −62 không đổi gì; giới hạn theo lời bớt nâng khi phân vị 90 đã cao, tức đúng ở phiên có nhạc hay
người nói nền. Tham chiếu lời càng thấp càng gọn: −36 cho 76,2%, −44 cho 77,8%, hơn chuỗi đưa 3,1 điểm, gần hết nhờ phòng
im và ồn không phải tiếng người; với nhạc và người nói nền vẫn thua chuỗi đưa 2 và 7 điểm.

Kết luận 05/10: nâng đầu vào chỉ đổi sự điếc với lời nhỏ lấy sự điếc với nền nhỏ, vì GMM chỉ phân biệt theo mức và phổ
thô, không tách được người dùng với tiếng người hay nhạc ở nền. Mức lợi trên thước gián tiếp (3 điểm) không đáng một lượt
dựng lại mọi đặc trưng; chủ repo chốt giữ `vad` như hiện tại và chuyển câu hỏi sang `vad` học, đầu VAD của RNNoise-16k ở
E9, đo bằng chính bảng trên. Cửa 3 với chuỗi nâng không chạy.

RNNoise gốc của xiph (`pyrnnoise` 0.4.5, `ml/afe_ref/rnnoise`) cũng phụ thuộc mức. Trên phiên `val` đầu tiên (người nói
nền `musan` ở SNR 6,9 dB; người dùng −75 dBFS trước `agc`, nền −86 dBFS), xác suất tiếng nói của nó lớn nhất 0,49 với
đầu vào như chuỗi đưa. Nâng đầu vào cố định, bước có tiếng người dùng / bước khác vượt 0,5: +10 dB 1% / 0%, +20 dB 18% /
3%, +30 dB 38% / 9%, +40 dB 47% / 13%. Nên `vad` học cũng phải được đưa về mức nó học, hoặc học ở mọi mức. Bảng trên cho
RNNoise gốc, như chuỗi đưa và nâng như dòng nền −58, lời −44, chạy sau khi xong `command/v5` vì tốn CPU.

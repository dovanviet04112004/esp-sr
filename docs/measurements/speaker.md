# Khảo sát nhận diện người nói (E11-T24, KẾ HOẠCH §3.17)

Đo ngày 10/10 bằng `make eval-speaker` (`srpipe.scenes.speaker`, `ml/configs/scenes/speaker.yaml`).
- **Bộ trích:** 16 bộ học sẵn, chạy qua `ml/spk_ref/`. ECAPA và 3D-Speaker chạy trên CPU, ReDimNet trên GPU (RTX 3050 Laptop).
- **Thời gian:** lượt cuối 13 phút 41 giây; embedding của các bộ đã chạy được lấy lại từ cache theo khoá sha256 của cửa sổ và bản ghim.
- **Dữ liệu cá nhân:** embedding chỉ nằm ở `cache/spk_ref/` (KẾ HOẠCH §1.4).

## 1. Vật liệu

| Vai | Nguồn | Cửa sổ |
|---|---|---|
| Chủ repo (`spk_001`) | cửa sổ lệnh mà Cửa 3 chấm: phiên board B qua chuỗi Python, cắt như `svc_listen` trên lối ra `clean` | 28/09 1 m: 60; 28/09 3 m: 52; 07/10 1 m: 51; 07/10 3 m: 46. Tổng 209, đúng số câu lệnh của Cửa 3 |
| Chủ repo, lệnh được nhận | trong 209 cửa sổ trên, các cửa sổ mà `command/v8` (lượt `20261007_439d763-dirty_f438bd`) nhận đúng lệnh như board B quyết: mọi chiều cao độ giữ ở trung bình, `δ₁` 200, `δ₂` 25 | 52; 33; 47; 46. Tổng 178 |
| Người lạ | 55 người nói `test` của `command/v1` (VIVOS, Common Voice) có từ 4 câu trở lên. Mỗi người nói 8 câu ở một phòng gần (0,7–1,3 m) và 8 câu ở một phòng xa (2,5–3,5 m) của bank mô phỏng; không nguồn nhiễu; cửa sổ cắt cùng luật | gần: 419; xa: 422 |
| Mốc ngôn ngữ | 760 câu VIVOS `test` của 19 người, nguyên câu, không qua board | — |

Độ dài cửa sổ:
- **Chủ repo:** trung vị 2,83 s (10–90%: 2,32–3,06 s). Trong đó ~2 s là đoạn trước lời mà cửa sổ lệnh luôn kèm (`UTTERANCE_LEAD_HOPS` 125 bước), nên lời lệnh chỉ cỡ 1 s.
- **Người lạ:** trung vị 3,74 s, tức trần 234 bước của cửa sổ, vì câu VIVOS và Common Voice dài hơn lệnh. Gần như cả cửa sổ là lời.

## 2. Các bộ trích

Ba nhóm theo dữ liệu học. Các cột EER đo trên mọi cặp, cùng người so với khác người.

| Bộ trích | Học trên | Tham số | VIVOS `test`, không board, nguyên câu | Người nói `test` qua mô phỏng board, cửa sổ lệnh |
|---|---|---|---|---|
| ECAPA-TDNN (SpeechBrain) | VoxCeleb 1 và 2 | ~20 M | 4,30% | 20,47% |
| ReDimNet b6 (IDRnD) | VoxCeleb2 | ~15 M | 0,88% | 21,68% |
| ReDimNet2 b0 (PalabraAI) | VoxCeleb2 | 1,12 M | 1,96% | 20,25% |
| ReDimNet2 b1 | VoxCeleb2 | 2,16 M | 1,41% | 19,51% |
| ReDimNet2 b2 | VoxCeleb2 | 3,68 M | 1,56% | 19,94% |
| ReDimNet2 b3 | VoxCeleb2 | ~4,1 M | 0,94% | 19,05% |
| ReDimNet2 b4 | VoxCeleb2 | ~6,6 M | 1,28% | 21,11% |
| ReDimNet2 b5 | VoxCeleb2 | ~8,9 M | 1,03% | 20,95% |
| ReDimNet2 b6 | VoxCeleb2 | ~12,3 M | 1,03% | 20,95% |
| CAM++ (3D-Speaker) | 200 000 người nói tiếng Trung | 6,85 M | 1,88% | 15,48% |
| ERes2NetV2 (3D-Speaker) | 200 000 người nói tiếng Trung | 17,8 M | 0,62% | 14,51% |
| CAM++ zh-en (3D-Speaker) | kho lớn tiếng Trung và tiếng Anh | 6,85 M | 1,09% | 14,55% |
| ReDimNet S (IDRnD) | VoxBlink2, VoxCeleb2, CN-Celeb | 3,15 M | 0,25% | 14,07% |
| ReDimNet M (IDRnD) | VoxBlink2, VoxCeleb2, CN-Celeb | 4,81 M | **0,14%** | 12,59% |
| ReDimNet2 b3 nhiều ngôn ngữ | VoxBlink2, VoxCeleb2, CN-Celeb2 | 4,51 M | 0,34% | 14,86% |
| ReDimNet2 b6 nhiều ngôn ngữ | VoxBlink2, VoxCeleb2, CN-Celeb2 | 12,46 M | 0,34% | **11,60%** |

- **Tham số:** đếm từ trọng số tải về; số có "~" lấy theo tác giả.
- **b5 và b6 VoxCeleb2:** hai mạng cho embedding khác nhau (cosine trung vị giữa chúng 0,035 trên cùng cửa sổ). EER trên VIVOS chỉ khác ở chữ số thứ tư (1,033% và 1,026%). EER giữa người lạ trùng tuyệt đối, là trùng ngẫu nhiên của hai tỉ lệ rời rạc.

## 3. Chủ repo với người lạ

- **Mẫu giọng:** trung bình k cửa sổ rút từ các phiên 28/09 1 m. Mỗi k rút 20 lần; số trong bảng là trung vị, trong ngoặc là lần rút tệ nhất.
- **Ngưỡng:** đặt để tối đa 1% cửa sổ người lạ lọt qua. Người lạ ở phòng xa lọt 0,9–1,7%, ở phòng gần 0,2–1,0%.
- **Cột "giữ":** phần cửa sổ của chủ repo còn lại sau ngưỡng, không tính các cửa sổ đã dùng để đăng ký.

### 3.1 k = 10, mọi bộ trích

| Bộ trích | EER | Giữ, mọi cửa sổ (tệ nhất) | 28/09 1 m | 28/09 3 m | 07/10 1 m | 07/10 3 m | Giữ, lệnh được nhận | 28/09 1 m | 28/09 3 m | 07/10 1 m | 07/10 3 m |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ECAPA-TDNN | 5,50% | 85,2% (64,3%) | 85,0% | 91,3% | 87,3% | 77,2% | 86,4% | 90,6% | 90,9% | 88,3% | 77,2% |
| ReDimNet b6 | 9,07% | 67,8% (52,3%) | 82,0% | 84,6% | 58,8% | 40,2% | 68,0% | 86,8% | 81,8% | 62,8% | 40,2% |
| ReDimNet2 b0 | 4,46% | 90,5% (80,9%) | 85,0% | 94,2% | 91,2% | 89,1% | 92,0% | 90,8% | 93,9% | 92,6% | 89,1% |
| ReDimNet2 b1 | 5,01% | 80,4% (63,3%) | 84,0% | 88,5% | 80,4% | 65,2% | 81,1% | 90,7% | 87,9% | 80,9% | 65,2% |
| ReDimNet2 b2 | 6,32% | 81,2% (64,3%) | 84,0% | 88,5% | 80,4% | 64,1% | 82,8% | 90,7% | 89,4% | 81,9% | 64,1% |
| ReDimNet2 b3 | 8,00% | 74,4% (56,8%) | 88,0% | 80,8% | 74,5% | 53,3% | 74,8% | 95,3% | 78,8% | 74,5% | 53,3% |
| ReDimNet2 b4 | 5,01% | 73,4% (53,8%) | 76,0% | 80,8% | 77,5% | 60,9% | 74,2% | 81,6% | 78,8% | 78,7% | 60,9% |
| ReDimNet2 b5 | 7,51% | 68,6% (47,2%) | 83,0% | 85,6% | 60,8% | 44,6% | 67,2% | 89,7% | 86,4% | 59,6% | 44,6% |
| ReDimNet2 b6 | 9,53% | 73,9% (50,8%) | 84,0% | 89,4% | 70,6% | 45,7% | 73,9% | 90,8% | 90,9% | 70,2% | 45,7% |
| CAM++ | 5,50% | 86,4% (69,8%) | 92,0% | 89,4% | 96,1% | 63,0% | 87,6% | 100,0% | 87,9% | 98,9% | 63,0% |
| ERes2NetV2 | 8,52% | 78,9% (68,3%) | 92,0% | 92,3% | 96,1% | 34,8% | 79,0% | 100,0% | 90,9% | 100,0% | 34,8% |
| CAM++ zh-en | 3,05% | 95,0% (89,9%) | 92,0% | 98,1% | 98,0% | 91,3% | 96,7% | 98,8% | 97,0% | 100,0% | 91,3% |
| ReDimNet S | 2,99% | 94,0% (83,9%) | 92,0% | 95,2% | 96,1% | 91,3% | 95,9% | 98,9% | 95,5% | 97,9% | 91,3% |
| **ReDimNet M** | **2,50%** | **97,0% (80,4%)** | 92,0% | 98,1% | 98,0% | 97,8% | **99,4%** | 100,0% | 100,0% | 100,0% | 97,8% |
| ReDimNet2 b3 nhiều ngôn ngữ | 2,99% | 94,0% (88,9%) | 92,0% | 98,1% | 90,2% | 95,7% | 95,6% | 100,0% | 100,0% | 91,5% | 95,7% |
| ReDimNet2 b6 nhiều ngôn ngữ | 2,50% | 95,0% (93,0%) | 92,0% | 100,0% | 94,1% | 93,5% | 96,5% | 100,0% | 100,0% | 95,7% | 93,5% |

### 3.2 Theo số câu đăng ký, các bộ dẫn đầu

| Bộ trích | k | EER | Giữ, mọi cửa sổ (tệ nhất) | Giữ, lệnh được nhận | 28/09 1 m | 28/09 3 m | 07/10 1 m | 07/10 3 m |
|---|---|---|---|---|---|---|---|---|
| ReDimNet M | 3 | 2,88% | 95,1% (62,1%) | 97,4% | 100,0% | 97,0% | 100,0% | 96,7% |
| ReDimNet M | 5 | 2,96% | 95,6% (77,5%) | 98,3% | 100,0% | 95,5% | 100,0% | 97,8% |
| ReDimNet2 b6 nhiều ngôn ngữ | 3 | 2,88% | 94,7% (75,7%) | 96,6% | 100,0% | 100,0% | 95,7% | 91,3% |
| ReDimNet2 b6 nhiều ngôn ngữ | 5 | 2,90% | 95,1% (89,7%) | 97,1% | 100,0% | 100,0% | 96,8% | 93,5% |
| CAM++ zh-en | 3 | 4,30% | 89,1% (69,4%) | 90,9% | 98,0% | 89,4% | 95,7% | 84,8% |
| CAM++ zh-en | 5 | 3,47% | 92,2% (73,0%) | 93,6% | 98,9% | 89,4% | 98,9% | 89,1% |

### 3.3 Nới ngưỡng: lệnh được nhận giữ bao nhiêu khi người lạ lọt nhiều hơn

Trên 178 cửa sổ mà `command/v8` nhận; ngưỡng đặt để người lạ lọt đúng tỉ lệ ở đầu cột, trung vị của 20 lần rút.

| Bộ trích | k | 0,5% | 1% | 2% | 5% | 10% |
|---|---|---|---|---|---|---|
| ReDimNet2 b0 (VoxCeleb2) | 3 | 70,1% | 80,3% | 87,7% | 95,4% | 98,3% |
| ReDimNet2 b0 (VoxCeleb2) | 10 | 85,0% | 92,0% | 94,4% | **98,2%** | 100% |
| ReDimNet2 b1 (VoxCeleb2) | 10 | 71,8% | 81,1% | 89,6% | 97,0% | 98,8% |
| CAM++ zh-en | 10 | 93,0% | 96,7% | 98,8% | 99,1% | 100% |
| ReDimNet S | 10 | 93,2% | 95,9% | 98,8% | 100% | 100% |
| ReDimNet2 b3 nhiều ngôn ngữ | 10 | 93,8% | 95,6% | 97,9% | 99,4% | 99,4% |
| ReDimNet2 b6 nhiều ngôn ngữ | 10 | 95,0% | 96,5% | 98,8% | 100% | 100% |
| ReDimNet M | 3 | 95,2% | 97,4% | 99,7% | 100% | 100% |
| ReDimNet M | 10 | 98,5% | 99,4% | 100% | 100% | 100% |

ReDimNet2 b0 với k = 10 theo phiên, 28/09 1 m / 28/09 3 m / 07/10 1 m / 07/10 3 m:
- ở 1% người lạ lọt: 91 / 94 / 93 / 89%;
- ở 5%: 98 / 100 / 98 / 98%.

"Người lạ lọt" là phần cửa sổ của người lạ vượt ngưỡng giọng. Trong sản phẩm, người lạ còn phải nói một lệnh mà `command`
nhận trước khi tới bước này.

### 3.4 Cửa sổ bộ nào cũng chặn

Trên mọi cửa sổ, ô 28/09 1 m dừng ở 92,0% với hầu hết các bộ dẫn đầu. Đo trên CAM++ zh-en và ReDimNet M với k = 10, các cửa sổ bị chặn ở quá nửa số lần rút:
- **#41, #42 (28/09 1 m) và #160 (07/10 1 m):** mức to nhất (p95) chỉ −48 đến −55 dBFS, không bước nào cao hơn trung vị 15 dB. Cửa sổ thường đạt −22 dBFS, nên ba cửa sổ này gần như không có lời.
- **#5 (28/09 1 m):** p95 −40,6 dBFS, lời rất nhỏ.
- **Số còn lại** (#6, #28, #91, #178, #179, #204, #208) có lời ở mức thường (p95 −20 đến −24 dBFS).

Trên các cửa sổ mà model `command` nhận đúng lệnh, ô 28/09 1 m lên 98,8–100% ở các bộ dẫn đầu.

Theo độ dài: 206 trong 209 cửa sổ dài từ 1,5 s trở lên. Ba cửa sổ 1,2–1,5 s, với k = 10:
- ReDimNet M giữ 1/3;
- CAM++ zh-en, ReDimNet2 b3 và b6 nhiều ngôn ngữ giữ 2/3;
- các bộ còn lại giữ 0–1/3.

## 4. Chi phí

Đếm bằng `torch.utils.flop_counter` trên tín hiệu 16 kHz, một MAC là hai FLOP. Cửa sổ 1,5 s là phần lời cộng một ít đầu, 2,83 s là cả cửa sổ lệnh như đang cắt.

| Bộ trích | MAC, 1,5 s | MAC, 2,83 s |
|---|---|---|
| ReDimNet2 b0 | 0,27 tỉ | 0,52 tỉ |
| ReDimNet2 b1 | 0,44 tỉ | 0,85 tỉ |
| ReDimNet2 b2 | 0,73 tỉ | 1,39 tỉ |
| CAM++, CAM++ zh-en | 0,84 tỉ | 1,59 tỉ |
| ReDimNet2 b3 nhiều ngôn ngữ | 2,11 tỉ | 4,00 tỉ |
| ReDimNet S | 2,78 tỉ | 5,25 tỉ |
| ReDimNet M | 4,99 tỉ | 9,42 tỉ |
| ReDimNet2 b6 nhiều ngôn ngữ | 9,74 tỉ | 18,45 tỉ |

**Ước trên chip** 🔬, chưa chạy thử trên chip nào:
- **ESP32-S3:** esp-dl trên board B đạt 0,11–0,53 tỉ MAC mỗi giây (`latency.md` §10). Cửa sổ 1,5 s vì thế tốn:
  - ReDimNet2 b0: 0,5–2,5 s;
  - ReDimNet2 b1: 0,8–4 s;
  - ReDimNet M: 9–45 s.
- **ESP32-P4:** chưa đo; nếu nhanh hơn S3 2–4 lần thì ReDimNet M vẫn mất 2,5–25 s.
- **Phép toán:** ReDimNet2 b0 đã qua ESP-PPQ và esp-dl sau ba chỗ viết lại và hai bản vá ESP-PPQ (§6).

**Trên máy tính, một cửa sổ:**

| Bộ trích | CPU, 2 luồng, cửa sổ 3 s | CPU, 2 luồng, cửa sổ 7 s | GPU, cửa sổ 3 s | GPU, cửa sổ 7 s |
|---|---|---|---|---|
| ReDimNet b6 | 0,67 s | 1,40 s | 65 ms | 102 ms |
| ReDimNet2 b6 nhiều ngôn ngữ | 9,38 s | 21,8 s | 42 ms | 101 ms |

Trên CPU, ReDimNet2 b6 chậm hơn ReDimNet b6 ~14 lần dù ít MAC hơn. Vì thế `ml/spk_ref/redimnet` chạy trên GPU khi có.

## 5. Đọc

- **Dữ liệu học quyết định, không phải cỡ mạng.**
  - Cùng kiến trúc ReDimNet2, cùng k = 10, chỉ thêm VoxBlink2 và CN-Celeb2 vào dữ liệu học: b3 từ 74,4% lên 94,0%, b6 từ 73,9% lên 95,0%.
  - Trong thang chỉ học VoxCeleb2, mạng càng to càng kém: b0 giữ 90,5%, b5 còn 68,6%.
  - EER trên VIVOS sạch hầu như không đoán được thứ hạng này. ERes2NetV2 đạt 0,62% trên VIVOS sạch nhưng chỉ giữ 78,9% qua board.
- **ReDimNet M đạt luật đi tiếp của §3.17 trên các lệnh được nhận.** Luật: giữ ≥ 95% ở cả 1 m và 3 m, với ≤ 1% người lạ lọt.
  - Kết quả: với k = 10 giữ 99,4% trong 178 cửa sổ được nhận (100%, 100%, 100%, 97,8%); với k = 3 giữ 97,4%, với k = 5 giữ 98,3%, cũng đạt.
  - Trên cả 209 cửa sổ thì không đạt (28/09 1 m 92,0%). Phần trượt nằm ở các cửa sổ gần như không có lời, mà model `command` cũng không nhận.
  - Các bộ khác đều hụt ở một phiên 3 m hay 1 m: ReDimNet2 b6 nhiều ngôn ngữ 93,5%, CAM++ zh-en 91,3%, ReDimNet S 91,3%, ReDimNet2 b3 nhiều ngôn ngữ 91,5%.
- **ReDimNet M chưa thể vào sản phẩm.** Hai chặn:
  - **Giấy phép:** trọng số không ghi giấy phép, dữ liệu học (VoxBlink2, CN-Celeb) chỉ cho nghiên cứu.
  - **Chi phí:** 5 tỉ MAC cho cửa sổ 1,5 s, quá sức cả ESP32-S3 lẫn P4 theo ước trên.

  Bản nhẹ nhất học trên VoxCeleb2, ReDimNet2 b0 (0,27 tỉ MAC), giữ 92,0% lệnh được nhận. Cùng kiến trúc mà thêm dữ liệu nhiều ngôn ngữ thì được ~20 điểm ở b3 và b6. Vì thế học lại một cỡ nhỏ trên dữ liệu nhiều ngôn ngữ cộng tiếng Việt là hướng có căn cứ; dữ liệu tiếng Việt có giấy phép CC BY-NC 4.0 là VieSpeaker, 4 715 người nói 🔬.
- **ReDimNet2 b0, bản nhẹ nhất, chỉ cách luật 3 điểm trên giọng chủ repo.** Học trên VoxCeleb2, k = 10, nó giữ 92,0%
  lệnh được nhận ở 1% người lạ lọt, và 98,2% (từ 98% trở lên ở cả bốn phiên) nếu nới tới 5%. Với k = 3 thì ở 1% chỉ giữ 80,3%.
- **Câu đăng ký vẫn quyết định nhiều.** Lần rút tệ nhất với k = 10 giữ 80,4% (ReDimNet M) đến 93,0% (ReDimNet2 b6 nhiều ngôn ngữ).
- **Ba lệch của vật liệu đều nghiêng về phía dễ tách** 🔬. Đây là suy luận, chưa đo:
  - người lạ nói ~3,7 s lời, chủ repo nói lệnh ~1 s;
  - người lạ đọc câu khác lời với lệnh;
  - người lạ đi qua mô phỏng, chủ repo qua board thật.

  Trước khi làm thật cần người lạ thu qua board, nói chính các lệnh (KẾ HOẠCH §3.17, cần phiếu đồng ý).

## 6. Đường chip: ReDimNet2 b0 int8 (E11-T25)

Đo ngày 10/10. b0 nhập thành run `ml/artifacts/speaker/runs/20261010_5a45475-dirty_94e5a9` (`make speaker-import`);
thang int8 bằng `make speaker-ptq` theo `ml/configs/models/speaker.yaml`.
- **Đồ thị:** b0 từ log-mel của chính nó tới embedding 192 chiều. Đầu vào là 72 băng × 149 khung của 1,5 s cuối cửa
  sổ lệnh, cửa sổ ngắn hơn thì đệm 0 phía trước. Khâu đặc trưng nằm ngoài đồ thị.
- **Viết lại cho esp-dl**, không đổi phép tính:
  - pad 'same' thành pad tường minh;
  - BatchNorm sau lớp gộp gộp vào lớp cuối;
  - ngữ cảnh toàn cửa sổ của ASTP cộng theo kênh, thay cho Expand mà esp-dl không có.

  Đồ thị viết lại lệch bộ trích tối đa 1,3·10⁻⁶ giá trị lớn nhất, trên 64 cửa sổ hiệu chỉnh.
- **Vật liệu:** 209 cửa sổ của chủ repo và 777 cửa sổ người lạ, tức 841 cửa sổ trừ 64 cửa sổ dùng để hiệu chỉnh int8;
  k = 10, 20 lần rút, như §3.

### 6.1 Float và int8

Lệnh được nhận giữ bao nhiêu, ở hai mức người lạ lọt:

| Đồ thị | Người lạ lọt | EER | Giữ, cả bốn phiên | 28/09 1 m | 28/09 3 m | 07/10 1 m | 07/10 3 m |
|---|---|---|---|---|---|---|---|
| float | 1% | 4,00% | 92,0% | 94,3% | 93,9% | 89,4% | 89,1% |
| float | 5% | 4,00% | 98,5% | 100% | 100% | 96,8% | 98,9% |
| int8 kl, GELU qua Erf | 1% | 4,99% | 86,8% | 97,7% | 90,9% | 86,2% | 75,0% |
| int8 kl, GELU qua Erf | 5% | 4,99% | 97,6% | 100% | 93,9% | 97,9% | 96,7% |
| int8 kl, đồ thị sửa đủ ba chỗ (§6.2) | 1% | 4,58% | 90,9% | 100% | 93,9% | 93,6% | 80,4% |
| int8 kl, đồ thị sửa đủ ba chỗ (§6.2) | 5% | 4,58% | 97,3% | 100% | 93,9% | 97,9% | 96,7% |

- **Float ở cửa sổ 1,5 s đạt điều kiện cắm của KẾ HOẠCH §3.17:** ≥ 95% ở mọi phiên khi 5% người lạ lọt.
- **Hàng "GELU qua Erf" không nạp được lên chip** (§6.2), nhưng cho thấy mức nhạy với int8: cosine giữa embedding
  int8 và float trên từng cửa sổ có trung vị 0,52, phân vị 10 là 0,38.
- **Hàng kl của đồ thị chạy được trên chip** chưa đạt điều kiện cắm: phiên 28/09 3 m còn 93,9% khi 5% người lạ lọt,
  dưới 95%. Cosine int8 với float có trung vị 0,48, phân vị 10 là 0,33. Ba cách hiệu chỉnh còn lại của bậc 2 chưa
  chạy trên đồ thị này.

### 6.2 Trên board B

- **Lần đầu, GELU còn là chuỗi Div, Erf, Add, Mul, Mul:** esp-dl 3.3.11 không nạp model, báo "Do not support Erf,
  please implement and register it first". Bản vá `fuse_erf_gelu` gộp chuỗi ấy thành một `Gelu` bằng hàm của chính
  ESP-PPQ; esp-dl chạy `Gelu` bằng bảng tra int8 của hàm đúng, nên chỉ làm tròn một lần thay vì năm lần.
- **Lần hai, GELU đã gộp:** board khởi động lại liên tục. Một `assert` trong `Reshape::get_output_shape` của esp-dl
  hỏng lúc dựng model, vì có tensor mang chiều không dương.
  - Gốc lỗi: esp-dl chỉ chạy conv nhóm theo kiểu depthwise, lấy số kênh ra bằng số kênh vào. b0 có 4 conv nhóm nhân
    đôi kênh (12→24 ở 1×1, và ba phép hạ mẫu 12→24, 24→48, 48→96), nên mọi hình dạng phía sau lệch.
  - Cách tìm: phát lại `get_output_shape` của esp-dl trên `.info` xuất ra rồi so với hình dạng ESP-PPQ ghi; chỗ lệch
    đầu tiên là `stage0.2`, conv 1×1 với groups = 12.
  - Cách sửa: mỗi conv như vậy thành một conv thường có trọng số khối chéo, phép tính không đổi; thêm 23,5 triệu MAC
    vào 0,27 tỉ.
- **Lỗi thứ ba, tìm ra trên PC trước khi lên board:** phát lại cách esp-dl suy hình dạng, kèm kiểm luật phát sóng của
  ONNX, cho thấy 7 LayerNorm kiểu channels-first của ReDimNet2 (ở stem và các `red_dim_conv`) nhân weight sai trục.
  - Gốc lỗi: ESP-PPQ chuyển dữ liệu sang kênh cuối nhưng để nguyên weight hình (C, 1) hay (C, 1, 1); esp-dl phát sóng
    bằng max từng chiều, không kiểm tương thích. Ở stage 4, 48 kênh nhiều hơn 37 khung nên hình dạng cũng sai.
  - Cách sửa: mỗi LayerNorm như vậy thành `layer_norm` trên trục kênh đã dời ra cuối. Phép tính y hệt, xuất ra một
    `LayerNormalization` int8; Pow và Sqrt F32 của chúng mất theo. Còn 10 op F32 (Pow, Sqrt trong GELU bản tanh và
    trong lớp gộp), esp-dl chạy được ở float.
  - Sau sửa, phát lại qua hết đồ thị: mọi chiều dương, mọi phép phát sóng hợp lệ, đầu ra [1, 192].
- **Lần ba, đồ thị sửa đủ ba chỗ:** model nạp, dựng và chạy hết trên chip; ảnh model 1722 KB trong PSRAM, `.espdl`
  1,76 MB. `model->test()` báo đầu ra lệch mô phỏng ESP-PPQ: phần tử đầu là 20, mô phỏng là 10 (int8). esp-dl chỉ
  báo chỗ lệch đầu tiên, nên chưa biết lệch từ op nào.
- **Lần bốn, đo giờ trước khi so:** một lần chạy mất **3,04 s**, gấp 3 lần ngưỡng cắm ≤ 1 s; tensor chiếm 3,28 MB
  PSRAM (`latency.md` §25). 158/192 giá trị lệch mô phỏng quá một bước, lệch lớn nhất 18, cùng dấu và cùng cỡ.
- **Đọc:** b0 int8 trên ESP32-S3 không đạt điều kiện cắm về thời gian, kể cả khi sửa xong chỗ lệch.
  - Đo từng lớp (`latency.md` §25): Conv 1,09 s, ReduceSum của phép gộp đầu ra các stage 0,60 s, Transpose 0,53 s.
  - Riêng Conv đã vượt 1 s, nên viết lại phép gộp và bớt Transpose chỉ đưa b0 về cỡ 2 s 🔬.
  - Đường chip cần mạng nhỏ hơn b0 (học trên lưới của hợp đồng), cửa sổ ngắn hơn, hay chip nhanh hơn (P4). Đây là
    quyết định của KẾ HOẠCH §3.17, chưa chốt.
- **Lần năm, sửa xong chỗ lệch:** chip và mô phỏng ESP-PPQ khớp từng bit. Mọi bản cắt và cả đồ thị qua
  `model->test()`; 0/192 giá trị embedding lệch, lệch lớn nhất 0. `make ai-unit-speaker` báo "2 Tests 0 Failures".
  - Cách tìm: bản dò cắt đồ thị tại các tensor rải đều (`probe.cuts`, `CUT_RANGE`), mỗi bản mang giá trị mô phỏng tại
    đó; năm lượt board chia đôi tới đúng op.
  - Op lệch đầu tiên là LayerNormalization của `stage0.7/red_dim_conv.1`: kết quả ra đúng 2,5 ở (t=62, kênh 15); chip
    làm tròn thành 3, mô phỏng thành 2. Kernel int8 của esp-dl cộng phương sai float32 theo từng kênh và lấy
    `1/sqrt_newton`, khác thứ tự phép tính của torch. Bản vá `layernorm_as_espdl` (`3a164af`).
  - Op lệch kế tiếp là Softmax trong attention của `stage1.7`: esp-dl tra `exp` trong bảng 256 giá trị float32 và
    cộng hàng theo thứ tự, không trừ giá trị lớn nhất như torch; 6/87 616 xác suất lệch một bước sau lượng tử. Bản vá
    `softmax_as_espdl` (`be128b3`).
  - Mỗi lệch chỉ một bước ở một chỗ, nhưng b0 khuếch đại thành ~160/192 giá trị lệch ở đầu ra.

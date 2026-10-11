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
| int8 kl, đồ thị 1,14 s trên chip (`latency.md` §25) | 1% | 4,00% | 95,3% | 100% | 90,9% | 97,9% | 91,3% |
| int8 kl, đồ thị 1,14 s trên chip (`latency.md` §25) | 5% | 4,00% | 98,2% | 100% | 93,9% | 97,9% | 100% |
| int8 kl, mô phỏng LayerNorm, Softmax theo mã máy (11/10) | 1% | 4,00% | 95,8% | 97,7% | 93,9% | 97,9% | 95,7% |
| int8 kl, mô phỏng LayerNorm, Softmax theo mã máy (11/10) | 5% | 4,00% | 98,2% | 100% | 93,9% | 97,9% | 100% |
| int8 percentile, như trên | 5% | 7,47% | 92,6% | 92,2% | 86,4% | 93,6% | 96,7% |
| int8 minmax, như trên | 5% | 8,01% | 89,3% | 86,2% | 81,8% | 93,6% | 91,3% |
| int8 mse, như trên | 5% | 8,55% | 87,2% | 86,5% | 78,8% | 93,6% | 87,0% |

- **Float ở cửa sổ 1,5 s đạt điều kiện cắm của KẾ HOẠCH §3.17:** ≥ 95% ở mọi phiên khi 5% người lạ lọt.
- **Hàng "GELU qua Erf" không nạp được lên chip** (§6.2), nhưng cho thấy mức nhạy với int8: cosine giữa embedding
  int8 và float trên từng cửa sổ có trung vị 0,52, phân vị 10 là 0,38.
- **Hàng kl của đồ thị chạy được trên chip** chưa đạt điều kiện cắm: phiên 28/09 3 m còn 93,9% khi 5% người lạ lọt,
  dưới 95%. Cosine int8 với float có trung vị 0,48, phân vị 10 là 0,33.
- **Hàng kl của đồ thị 1,14 s** (mọi bước tối ưu, `latency.md` §25): EER 4,00% như float, giữ 95,3% ở 1% người lạ
  lọt. Phiên 28/09 3 m vẫn 93,9% ở 5%, dưới 95%. Cosine int8 với float có trung vị 0,51, phân vị 10 là 0,36. Các viết
  lại ở mức mạng cho int8 giống hệt bản không viết lại dưới chính thang này (KẾ HOẠCH §3.14), nên không bước tối ưu
  nào đổi chất lượng. Khi ấy chip mới được thấy khớp mô phỏng trên một bản lượng tử hiệu chuẩn bằng nhiễu.
- **Thang chạy lại sau khi mô phỏng LayerNorm và Softmax theo mã máy của esp-dl** (11/10, `latency.md` §25): bias
  correction đổi theo mô phỏng, nên số nguyên đổi chút ít.
  - kl vẫn EER 4,00% như float. Ở 1% người lạ lọt giữ 95,8%, nhích từ 95,3%. Phiên 28/09 3 m vẫn 93,9% ở 5%: 31 trên
    33 cửa sổ, float giữ đủ 33.
  - Hàng kl này chạy trên board B khớp mô phỏng từng bit ở mọi bản cắt và cả đồ thị, 1128,5 ms.
  - percentile, minmax, mse kém hẳn: EER 7,47%, 8,01%, 8,55%. Thứ tự giữ nguyên như lượt cũ.
  - Cosine với float không báo trước chất lượng: kl thấp nhất (trung vị 0,51) mà EER tốt nhất; percentile 0,84 mà EER
    7,47%.
- **Cửa của KẾ HOẠCH §3.17** đòi giữ ≥ 95% ở cả 1 m và 3 m, gộp các phiên của một khoảng cách. Bảng trên tách từng
  phiên, chặt hơn câu chữ ấy.

**Bậc 3** (`make speaker-int16`, 11/10): trên kl, ESP-PPQ xếp hạng 254 op theo nhiễu trên tín hiệu khi lượng tử riêng
từng op, trên 8 cửa sổ hiệu chuẩn. Đứng đầu là năm `bn1`: stage1.4 0,26, stage2.3 0,20, stage3.7 0,18, stage3.6 0,14,
stage0.3 0,10. Mỗi hàng đưa N op đầu bảng lên int16. Số giữ được gộp theo khoảng cách:

| Hàng | Op int16 | Cosine với float | EER | Giữ ở 1% | 1 m ở 5% | 3 m ở 5% |
|---|---|---|---|---|---|---|
| float | — | 1 | 4,00% | 92,0% | 97,8% | 99,4% |
| **kl** (`int16_top0`) | 0 | 0,51 | **4,00%** | **95,8%** | **98,9%** | **97,5%** |
| `int16_top2` | 2 | 0,60 | 5,02% | 90,3% | 97,8% | 96,2% |
| `int16_top4` | 4 | 0,63 | 5,53% | 85,5% | 98,9% | 93,7% |
| `int16_top8` | 8 | 0,63 | 6,55% | 84,3% | 96,1% | 94,9% |
| `int16_top16` | 16 | 0,72 | 5,47% | 85,2% | 97,8% | 94,9% |

- **kl qua cửa chất lượng của §3.17:** gộp theo khoảng cách, ở 5% người lạ lọt, giữ 98,9% ở 1 m và 97,5% ở 3 m.
  So với float, nó hơn 1,1 điểm ở 1 m, kém 1,9 điểm ở 3 m (hai cửa sổ của phiên 28/09), EER bằng nhau.
- **Bậc 3 làm kém đi:** embedding gần float hơn (cosine tới 0,72) nhưng EER lên 5,0–6,6%, và giữ ở 1% xuống
  84–90%. Đưa vài op lên int16 đổi lưới và bias correction của các op int8 quanh chúng, mà phép đo cuối mới là thước.
  Hàng `int16_top0` cho lại đúng từng số của hàng kl, nên lượng tử lặp lại y hệt.
- **Chọn kl, int8 thuần** (ADR-0021). Bậc 4 (QAT) không cần cho cửa, và cần chủ repo cho học.
- **Sai số int8 đến từ đâu**, phân tích của ESP-PPQ trên 6 cửa sổ của chủ repo:
  - Từng op lượng tử riêng (`layerwise_error_analyse`, nhiễu trên tín hiệu ở đầu ra): lớn nhất là các BatchNorm sau
    ReLU trong khối 2-D (`bn1`): stage2.3 0,22, stage1.4 0,18, stage3.7 0,17, stage3.6 0,12; kế đến conv depthwise
    đầu khối 1-D của stage 0 (0,09). Phép nhân trọng số attention với giá trị riêng nó chỉ 0,005.
  - Tích luỹ qua đồ thị (`graphwise_error_analyse`): tới stage 1 nhiễu đã 0,2–0,6 công suất tín hiệu, lên quá 1 sau
    attention của stage 1 và 2. Hệ số BN của `bn1` chỉ chênh 5–56 lần giữa các kênh, nên lỗi không nằm ở trọng số
    int8 một thang mà ở lưới int8 của hoạt hoá quanh chúng 🔬.

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

### 6.3 Ngưỡng trên đường chip (E11-T26, 11/10)

`make speaker-deploy RUN=artifacts/speaker/runs/20261010_5a45475-dirty_94e5a9 ROW=kl`: cùng vật liệu của §6.1 (209 cửa
sổ chủ repo, 777 cửa sổ người lạ ngoài 64 cửa sổ hiệu chỉnh), mỗi cửa sổ chạy đúng như chip tính. Đặc trưng là của
`srpipe.dsp.spec.spk_fbank` (bản soi gương của `dsp_spec/spk_fbank`, khớp C từng bit), đặt lên lưới int8 đầu vào của
đồ thị, mô phỏng ESP-PPQ của hàng kl, rồi hậu xử lý bản soi gương. Chạy hai lần, ra y hệt.

| Đo | Kết quả |
|---|---|
| Đặc trưng bản soi gương so với `TFMelBanks` bằng torch | lệch tới 4,5e-3, trung bình 7,3e-7 |
| Giá trị đầu vào int8 bị đổi ô vì độ lệch ấy | 43 trên 10 577 808 |
| Bộ số kiểu khảo sát trên đường chip, 1% người lạ lọt | EER 4,00%; giữ 95,9% lệnh được nhận; 1 m 97,8%, 3 m 94,3% |
| Như trên, 5% người lạ lọt | giữ 98,2%; 1 m 98,9%, 3 m 97,5% |

Bộ số trùng hàng kl của §6.1, nên khâu đặc trưng viết lại không đổi chất lượng.

**Ngưỡng một số cho mọi mẫu giọng.** Lấy 20 lần rút 10 cửa sổ đăng ký như khảo sát, gộp điểm của mọi người lạ qua mọi
lần rút, rồi lấy phần nghìn thấp nhất để chỉ một tỉ lệ người lạ lọt qua. Phần lệnh được nhận giữ lại tính gộp theo
khoảng cách.

| Người lạ lọt | Ngưỡng | Người lạ lọt thật | Giữ ở 1 m | Giữ ở 3 m |
|---|---|---|---|---|
| 1% | 781‰ | 0,98% | 97,2% | **92,2%** |
| 5% | **744‰** | 4,86% | 98,7% | 97,5% |

- Ở 1%, 3 m bị chặn 7,8% lệnh được nhận, quá 5%. Luật của KẾ HOẠCH §3.17 vì thế nới tới 5%: ngưỡng **744‰**, ghi
  vào `firmware/models/speaker/meta.json` và `models.lock.json`, bước đóng gói đưa vào header ảnh.
- Người lạ ở đây là người nói `test` của `command/v1` qua đường mô phỏng board. Trước khi bật mặc định, ngưỡng đặt lại
  trên người lạ thu thật qua board (E11-T27).

### 6.4 Ngưỡng theo số người có mẫu giọng (E11-T31, 11/10)

`make speaker-deploy RUN=artifacts/speaker/runs/20261010_5a45475-dirty_94e5a9 ROW=kl`, cùng vật liệu và đường chip của
§6.3, đặt một ngưỡng cho mỗi số người có mẫu giọng, 1 tới 8 (KẾ HOẠCH §3.17).
- Mỗi lần rút 10 cửa sổ đăng ký của chủ repo, như §6.3, dùng cho mọi số người. Với n người, rút thêm n − 1 người lạ có
  ít nhất 10 cửa sổ (46 người đủ) làm người đã đăng ký; mẫu giọng mỗi người từ 10 cửa sổ của chính họ.
- Điểm của mọi cửa sổ là điểm lớn nhất qua n mẫu. Người lạ là mọi người lạ không được rút.
- Luật 1%, nới tới 5% xét riêng từng số người.

| Người có mẫu | Ngưỡng 1% | Giữ 1 m | Giữ 3 m | Ngưỡng chọn (5%) | Người lạ lọt | Giữ 1 m | Giữ 3 m |
|---|---|---|---|---|---|---|---|
| 1 | 781‰ | 97,2% | 92,2% | **744‰** | 4,9% | 98,7% | 97,5% |
| 2 | 856‰ | 41,6% | 20,4% | **823‰** | 4,9% | 75,7% | 54,8% |
| 3 | 856‰ | 41,6% | 20,4% | **828‰** | 4,9% | 70,4% | 49,4% |
| 4 | 862‰ | 35,3% | 16,2% | **835‰** | 4,9% | 63,1% | 41,6% |
| 5 | 866‰ | 31,2% | 13,4% | **841‰** | 4,9% | 57,2% | 35,5% |
| 6 | 873‰ | 25,4% | 9,4% | **849‰** | 4,9% | 49,0% | 27,3% |
| 7 | 873‰ | 25,4% | 9,4% | **852‰** | 4,8% | 46,0% | 24,4% |
| 8 | 878‰ | 21,7% | 7,3% | **853‰** | 4,9% | 45,1% | 23,4% |

- Từ 2 người, không mức nào giữ được 95% lệnh của chủ repo, nên luật lùi về 5% cho mọi số người. Bảng ấy vào
  `meta.json`, `models.lock.json` và header ảnh (khuôn 6). `speaker.espdl` giữ nguyên từng byte.
- Ngưỡng 1 người vẫn 744‰. Bảng đúng luật của KẾ HOẠCH §3.17, nhưng nhà có từ 2 người thì lệnh của chủ repo bị chặn
  từ một phần tư tới ba phần tư.

**Vì sao ngưỡng leo khi thêm người.** Đo lại trên chính các embedding đường chip ấy, bằng một script tạm, chưa vào
repo:

| Cặp so | EER | Ghi chú |
|---|---|---|
| Chủ repo (thu thật qua board B) với người lạ (qua đường mô phỏng) | 2,4% | điểm cùng người: trung vị 840‰. Người lạ: trung vị 631‰, p95 739‰ |
| Người lạ với người lạ: mỗi người đăng ký trên 10 cửa sổ của mình, thử trên cửa sổ còn lại | 19,3% | điểm cùng người: trung vị 819‰, p5 679‰. Khác người: trung vị 699‰, p95 817‰ |
| Như trên, trong cùng nhóm khoảng cách | 25,0% xa, 33,3% gần | |
| Cửa sổ của chủ repo với mẫu giọng của người lạ | — | trung vị 630‰, p95 748‰ |

- b0 tách chủ repo khỏi người lạ chủ yếu nhờ khác kênh thu (board thật với mô phỏng) và nhờ câu lệnh lặp lại. Giữa
  những người cùng kênh, nói câu khác nhau trong 1,5 s, nó gần như không tách được.
- Người đã đăng ký mà thủ tục rút thêm đều cùng kênh với người lạ, nên người lạ giống họ, và ngưỡng phải leo.
- Ở nhà thật, mọi người nói qua cùng một board. Vậy chỗ đứng gần thực tế hơn là hàng người lạ với người lạ, kể cả với
  1 người: ngưỡng 744‰ có thể quá lỏng với người lạ thu thật. Chỉ bản thu người lạ thật qua board B (E11-T27) trả lời
  được.

**Chuẩn hoá điểm theo từng mẫu giọng, thử trên PC.**
- Cách làm:
  - một nửa số người lạ làm nhóm chuẩn;
  - mỗi mẫu giọng lấy trung bình μ và độ lệch σ của điểm nó với nhóm chuẩn;
  - điểm thành (cos − μ)/σ, lấy lớn nhất qua các mẫu;
  - nửa người lạ còn lại làm người đăng ký thêm và người lạ thử.
- Giữ lệnh của chủ repo ở 5% người lạ lọt, 1 m / 3 m:

| Người có mẫu | Điểm thô | Z-norm | AS-norm (top 50) |
|---|---|---|---|
| 1 | 98,8% / 97,5% | 98,8% / 97,2% | 98,8% / 97,2% |
| 2 | 80,3% / 60,4% | 98,6% / 96,3% | 98,5% / 96,1% |
| 4 | 66,8% / 44,1% | 98,2% / 94,7% | 98,0% / 94,1% |
| 8 | 48,9% / 27,3% | 97,6% / 93,0% | 95,8% / 90,0% |

- Trên vật liệu này, Z-norm gần như xoá sụt giảm theo số người, và không thêm lần chạy b0 nào: μ, σ tính một lần lúc
  đăng ký.
- Nhóm chuẩn ở đây cùng kênh mô phỏng với người lạ thử. Ở nhà thật, nhóm chuẩn phải thu qua board như người dùng, không
  thì điểm của người lạ thật lệch như hàng chủ repo ở trên. Đổi cách chấm là đổi KẾ HOẠCH §3.17, cần chủ repo duyệt.

### 6.5 Z-norm theo từng mẫu giọng trên đường chip (ADR-0022, 11/10)

`make speaker-deploy RUN=artifacts/speaker/runs/20261010_5a45475-dirty_94e5a9 ROW=kl` ở `ca8e476`, khoá ở `be39219`.
- **Nhóm chuẩn:** 27 trên 55 người lạ, rút bằng seed của khảo sát ở luồng 3. 380 cửa sổ của họ, tính trên đường chip,
  thành `cohort.bin`, mục `speaker_cohort` của ảnh.
- **Đặt bảng:** 397 cửa sổ người lạ còn lại (23 người đủ 10 cửa sổ để làm người đăng ký thêm). Điểm của mỗi mẫu giọng
  là (cos − μ)/σ theo μ, σ của mẫu ấy trên nhóm chuẩn, phần nghìn.

| Người có mẫu | Ngưỡng 1% (‰ z) | Giữ 1 m | Giữ 3 m | Ngưỡng chọn, 5% | Người lạ lọt | Giữ 1 m | Giữ 3 m |
|---|---|---|---|---|---|---|---|
| 1 | 2165 | 95,0% | 87,3% | **1656** | 5,0% | 98,6% | 96,8% |
| 2 | 2170 | 94,8% | 87,3% | **1722** | 5,0% | 98,6% | 96,5% |
| 3 | 2289 | 91,0% | 80,3% | **1815** | 5,0% | 98,2% | 95,3% |
| 4 | 2456 | 84,9% | 70,1% | **1911** | 5,0% | 97,8% | 94,0% |
| 5 | 2415 | 86,6% | 72,7% | **1906** | 5,0% | 97,8% | 94,0% |
| 6 | 2427 | 86,0% | 72,0% | **1961** | 5,0% | 97,6% | 93,2% |
| 7 | 2487 | 83,0% | 67,5% | **2019** | 4,9% | 97,0% | 92,0% |
| 8 | 2506 | 82,0% | 66,5% | **2034** | 5,0% | 96,8% | 91,6% |

- So với điểm thô của §6.4 ở 5%:
  - 2 người: 75,7% / 54,8% → **98,6% / 96,5%**;
  - 4 người: 63,1% / 41,6% → **97,8% / 94,0%**;
  - 8 người: 45,1% / 23,4% → **96,8% / 91,6%**.

  Phần sụt theo số người đã hết.
- Luật "giữ ≥ 95% ở cả hai khoảng cách" đạt tới 3 người. Từ 4 người, 3 m hụt: 94,0% xuống 91,6%.
- Người lạ ở đây không trùng hẳn với §6.4, vì một nửa nay là nhóm chuẩn. Vậy so hai bảng là so gần, không phải so
  từng điểm.
- Mỗi số người rút riêng, nên bảng không tăng đều: 5 người thấp hơn 4 người 5 ‰ z.
- Z-norm không làm b0 tách người tốt hơn (EER 19,3% giữa người lạ cùng kênh, §6.4 vẫn đúng). Nhóm chuẩn và người lạ thử
  đều là mô phỏng; ở nhà thật, nhóm chuẩn phải thu qua board B (E11-T27, chủ repo chưa cho thu 11/10).

### 6.6 Người đăng ký thêm và việc gắn đúng tên: cosine, Z-norm, S-norm, AS-norm (E11-T31, 11/10)

Bảng của §6.5 chỉ đo lệnh của chủ repo. Ở đây đo thêm lệnh của những người đăng ký thêm, và mẫu cho điểm cao nhất có
đúng là mẫu của người nói không, tức `speakerId` của sự kiện.
- **Vật liệu:** embedding đường chip của `speaker-deploy` (khoá `be39219`), cùng nhóm chuẩn, 20 lần rút đăng ký và
  người đăng ký thêm như §6.5.
- **Ngưỡng:** mỗi cách đặt ngưỡng riêng, ở 5% người lạ lọt, trên nửa người lạ không thuộc nhóm chuẩn. Cột Z-norm trùng
  §6.5, nên các cột so được với nhau.
- **Người đăng ký thêm:** người lạ qua đường mô phỏng, cùng kênh với người lạ thử. Như người trong nhà nói qua cùng một
  board.
- Đo bằng script tạm, chưa có trong `speaker-deploy`.
- **Các cách chấm:**
  - S-norm: trung bình của hai z, z của mẫu giọng và z của chính cửa sổ lệnh, đều trên nhóm chuẩn.
  - AS-norm K: như S-norm, nhưng μ và σ mỗi phía chỉ lấy K điểm cao nhất trên nhóm chuẩn.

| Người có mẫu | Cách chấm | Chủ repo giữ 1 m / 3 m | Người đăng ký thêm giữ | Mẫu cao nhất đúng người, người đăng ký thêm |
|---|---|---|---|---|
| 2 | cosine | 81,9 / 63,1% | 40,3% | 99,3% |
| 2 | Z-norm | 98,6 / 96,5% | 21,6% | 88,1% |
| 2 | S-norm | 98,8 / 97,6% | 45,5% | 93,3% |
| 2 | AS-norm 100 | 98,7 / 97,5% | 44,8% | 92,5% |
| 4 | cosine | 62,5 / 41,1% | 41,1% | 88,5% |
| 4 | Z-norm | 97,8 / 94,0% | 27,7% | 76,2% |
| 4 | S-norm | 98,2 / 97,4% | 38,9% | 84,4% |
| 4 | AS-norm 100 | 98,3 / 97,4% | 43,0% | 83,3% |
| 8 | cosine | 52,7 / 30,6% | 32,2% | 78,7% |
| 8 | Z-norm | 96,8 / 91,6% | 23,0% | 70,4% |
| 8 | S-norm | 97,7 / 97,4% | 32,2% | 77,1% |
| 8 | AS-norm 100 | 98,0 / 97,3% | 36,3% | 77,7% |

- **Z-norm:**
  - Giữ lệnh của chủ repo gần bằng S-norm, trừ ở 3 m từ 4 người.
  - Lệnh của người đăng ký thêm được giữ ít hơn cả cosine: 23,0% so với 32,2% ở 8 người.
  - Gắn tên kém nhất: 70,4% ở 8 người.
- **S-norm và AS-norm:**
  - Hơn Z-norm ở mọi dòng.
  - Ở 3 m, chủ repo được giữ ≥ 97,3% tới 8 người, nên luật "giữ ≥ 95% ở cả hai khoảng cách" đạt đủ 8.
  - AS-norm giữ người đăng ký thêm nhiều nhất từ 3 người trở lên.
  - K từ 50 tới 200 cho số gần nhau. Ở 8 người: 37,3%, 36,3% và 36,2% người đăng ký thêm được giữ.
- **Gắn đúng tên:** cosine vẫn nhỉnh nhất, 78,7% so với 77,7% của AS-norm ở 8 người.
- Lệnh của chủ repo rơi đúng mẫu của mình 97,2–98,5% ở 8 người với cả bốn cách.
- **Giới hạn không đổi:** với cách tốt nhất, AS-norm 100, người đăng ký thêm cũng chỉ được giữ 36–59% từ 2 tới 8
  người. b0 tách kém những người cùng kênh (EER 19,3%, §6.4). Chủ repo được giữ cao vì khác kênh với người lạ mô phỏng.
- **Dòng AS-norm của ADR-0022 không tái tạo được:** script cho ra dòng ấy không còn. Bản một phía, 50 điểm cao nhất ở
  phía mẫu giọng, cho 98,2 / 95,3% ở 4 người và 97,4 / 93,4% ở 8 người, khác 98,0 / 94,1% và 95,8 / 90,0% mà ADR ghi.

### 6.7 AS-norm trên đường chip (ADR-0023, 11/10)

`make speaker-deploy RUN=artifacts/speaker/runs/20261010_5a45475-dirty_94e5a9 ROW=kl` ở `bddf1dd`, khoá ở `990fe70`.
Nhóm chuẩn như §6.5, K = 100. Mỗi số người có 20 lần rút; người đăng ký thêm lấy từ nửa người lạ ngoài nhóm chuẩn.
Cột "đúng người" là phần lệnh mà mẫu cho điểm cao nhất là mẫu của chính người nói.

| Người có mẫu | Ngưỡng 1% (‰ z) | Chủ repo giữ 1 m / 3 m | Người đăng ký thêm giữ | Ngưỡng 5% | Chủ repo giữ 1 m / 3 m | Người đăng ký thêm giữ | Đúng người: chủ repo / người đăng ký thêm |
|---|---|---|---|---|---|---|---|
| 1 | **2964** | 98,0 / 97,4% | — | 1486 | 98,9 / 97,5% | — | 100% / — |
| 2 | **3119** | 97,8 / 97,3% | 20,1% | 2100 | 98,7 / 97,5% | 44,8% | 99,7 / 92,5% |
| 3 | **3417** | 97,3 / 96,9% | 31,7% | 2430 | 98,5 / 97,5% | 58,8% | 99,3 / 87,8% |
| 4 | **3521** | 96,9 / 96,8% | 21,1% | 2640 | 98,3 / 97,4% | 43,0% | 99,0 / 83,0% |
| 5 | **3560** | 96,8 / 96,5% | 20,5% | 2691 | 98,3 / 97,4% | 43,8% | 98,8 / 83,1% |
| 6 | **3736** | 96,5 / 95,3% | 20,6% | 2846 | 98,2 / 97,4% | 39,3% | 98,6 / 80,5% |
| 7 | **3795** | 96,3 / 95,1% | 18,1% | 2882 | 98,2 / 97,4% | 37,9% | 98,5 / 73,4% |
| 8 | **3795** | 96,3 / 95,2% | 19,1% | 3020 | 98,0 / 97,3% | 36,3% | 98,2 / 77,7% |

- **Bảng chọn 1% cho mọi số người.** Luật của KẾ HOẠCH §3.17 chỉ nới tới 5% khi lệnh của chủ repo bị chặn quá 5% ở
  1%. Với AS-norm, chủ repo vẫn giữ ≥ 95,1% ở cả hai khoảng cách tới 8 người.
- **So với Z-norm ở 8 người** (§6.5, 5% người lạ lọt):
  - người lạ lọt 1% thay vì 5%;
  - chủ repo ở 3 m giữ 95,2% thay vì 91,6%.
- **Người đăng ký thêm** giữ 18–32% ở 1%, 36–59% ở 5%: b0 tách kém những người cùng kênh (§6.4, §6.6). Con số thật
  cần bản thu qua board (E11-T27).
- Cột 5% trùng §6.6. Vậy script tạm của §6.6 và `speaker-deploy` cho cùng số.

### 6.8 Đăng ký bằng 10 lệnh liền nhau (E11-T31, 11/10)

`make listen-unit SPEAKER=1` đăng ký trên 10 lệnh đầu của phiên 28/09 1 m (`latency.md` §27). Kịch bản phiên ấy lặp mỗi
câu liền nhau, nên 10 lệnh chỉ có 3 câu: "bật đèn" 5 lần, "tắt đèn" 4 lần, "bật quạt" 1 lần.

| Mẫu giọng của chủ repo, AS-norm, 1 người, ngưỡng 2964 | Giữ, mọi khoảng cách | 1 m | 3 m |
|---|---|---|---|
| 10 cửa sổ đầu của phiên 28/09 1 m | 94,6% | 95,5% | 93,7% |
| 20 lần rút ngẫu nhiên trong phiên ấy | 97,0–98,2%, trung vị 97,6% | | |
| Trên board, lượt phát lại | 93,8% (150/160) | | |

- Mẫu lệch về ba câu nên khớp kém với các lệnh khác, và kém mọi lần rút ngẫu nhiên.
- Khi đăng ký thật nên nói 10 lệnh khác nhau. Đăng ký hiện lấy 10 lệnh kế tiếp bất kỳ (KẾ HOẠCH §3.17), nên điều ấy
  chỉ nằm ở lời dặn người dùng.

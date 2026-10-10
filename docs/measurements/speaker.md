# Khảo sát nhận diện người nói (E11-T24, KẾ HOẠCH §3.17)

Đo ngày 10/10 bằng `make eval-speaker` (`srpipe.scenes.speaker`, `ml/configs/scenes/speaker.yaml`), trên CPU, 11 phút 46 giây.
Hai bộ trích học sẵn chạy qua `ml/spk_ref/`; embedding chỉ nằm ở `cache/spk_ref/` (KẾ HOẠCH §1.4).

## 1. Vật liệu

| Vai | Nguồn | Cửa sổ |
|---|---|---|
| Chủ repo (`spk_001`) | cửa sổ lệnh mà Cửa 3 chấm: phiên board B qua chuỗi Python, cắt như `svc_listen` trên lối ra `clean` | 28/09 1 m: 60; 28/09 3 m: 52; 07/10 1 m: 51; 07/10 3 m: 46. Tổng 209, đúng số câu lệnh của Cửa 3 |
| Người lạ | 55 người nói `test` của `command/v1` (VIVOS, Common Voice) có từ 4 câu trở lên. Mỗi người nói 8 câu ở một phòng gần (0,7–1,3 m) và 8 câu ở một phòng xa (2,5–3,5 m) của bank mô phỏng; không nguồn nhiễu; cửa sổ cắt cùng luật | gần: 419; xa: 422 |
| Mốc ngôn ngữ | 760 câu VIVOS `test` của 19 người, nguyên câu, không qua board | — |

Độ dài cửa sổ:
- **Chủ repo:** trung vị 2,83 s (10–90%: 2,32–3,06 s). Trong đó ~2 s là đoạn trước lời mà cửa sổ lệnh luôn kèm (`UTTERANCE_LEAD_HOPS` 125 bước), nên lời lệnh chỉ cỡ 1 s.
- **Người lạ:** trung vị 3,74 s, tức trần 234 bước của cửa sổ, vì câu VIVOS và Common Voice dài hơn lệnh. Gần như cả cửa sổ là lời.

## 2. Hai bộ trích trên tiếng Việt

EER trên mọi cặp: cùng người so với khác người.

| Bộ trích | VIVOS `test` không qua board, nguyên câu | Người nói `test` qua mô phỏng board, cửa sổ lệnh |
|---|---|---|
| ECAPA-TDNN của SpeechBrain (VoxCeleb) | 4,30% | 20,47% |
| CAM++ của 3D-Speaker (200 000 người nói tiếng Trung) | 1,88% | 15,48% |

## 3. Chủ repo với người lạ

- **Mẫu giọng:** trung bình k cửa sổ rút từ các phiên 28/09 1 m. Mỗi k rút 20 lần; số trong bảng là trung vị, trong ngoặc là lần rút tệ nhất.
- **Ngưỡng:** đặt để tối đa 1% cửa sổ người lạ lọt qua.
- **Cột "giữ":** phần cửa sổ của chủ repo còn lại sau ngưỡng, không tính các cửa sổ đã dùng để đăng ký.

| Bộ trích | k | EER | giữ, mọi phiên | 28/09 1 m | 28/09 3 m | 07/10 1 m | 07/10 3 m | lọt, người lạ xa | lọt, người lạ gần |
|---|---|---|---|---|---|---|---|---|---|
| ECAPA-TDNN | 3 | 9,25% | 60,9% (51,0%) | 71,9% | 83,7% | 58,8% | 34,8% | 0,9% | 1,0% |
| ECAPA-TDNN | 5 | 6,88% | 76,5% (53,4%) | 74,5% | 85,6% | 81,4% | 68,5% | 0,9% | 1,0% |
| ECAPA-TDNN | 10 | 5,50% | 85,2% (64,3%) | 85,0% | 91,3% | 87,3% | 77,2% | 1,1% | 0,8% |
| CAM++ | 3 | 7,09% | 80,8% (40,8%) | 93,0% | 81,7% | 93,1% | 47,8% | 0,7% | 1,2% |
| CAM++ | 5 | 6,07% | 84,1% (70,1%) | 92,7% | 90,4% | 94,1% | 59,8% | 0,8% | 1,1% |
| CAM++ | 10 | 5,50% | 86,4% (69,8%) | 92,0% | 89,4% | 96,1% | 63,0% | 0,9% | 1,0% |

Theo độ dài: 206 trong 209 cửa sổ dài từ 1,5 s trở lên. Ba cửa sổ dài 1,2–1,5 s bị chặn hết ở cả hai bộ trích (k = 10).

## 4. Đọc

- **CAM++ hợp giọng Việt hơn ECAPA.** Trên giọng Việt sạch, EER của CAM++ là 1,88%, của ECAPA là 4,30%. Mô hình học trên tiếng có thanh, với 200 000 người nói, chuyển sang tiếng Việt tốt hơn mô hình học trên VoxCeleb.
- **Đường qua board làm bài khó lên nhiều.** Giữa các người nói `test`, EER lên 15–20% khi đổi từ nguyên câu sạch sang cửa sổ lệnh qua mô phỏng board.
- **Luật đi tiếp của §3.17 không đạt.** Luật đòi giữ ≥ 95% ở cả 1 m và 3 m với ≤ 1% người lạ lọt. Tốt nhất là CAM++ với k = 10:
  - giữ 86,4% trên mọi phiên;
  - 1 m: 92,0% và 96,1%;
  - 3 m: 89,4% và 63,0%.
- **Câu đăng ký quyết định nhiều.** Lần rút tệ nhất với k = 10 chỉ giữ 64–70%.
- **Ba lệch của vật liệu đều nghiêng về phía dễ tách** 🔬. Đây là suy luận, chưa đo:
  - người lạ nói ~3,7 s lời, chủ repo nói lệnh ~1 s;
  - người lạ đọc câu khác lời với lệnh;
  - người lạ đi qua mô phỏng, chủ repo qua board thật.

  Gỡ các lệch ấy, nếu có tác dụng, chỉ hạ phần giữ xuống, nên kết luận không đổi.
- **Theo §3.17: ghi số và dừng,** không đề xuất làm thật. Học tiếp trên tiếng Việt (Vietnam-Celeb, dữ liệu VLSP) chỉ xét khi giấy phép của chúng cho phép.

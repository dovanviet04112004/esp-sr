# Dàn micro

Board B, 2 × INMP441 (KẾ HOẠCH §2.1, §2.3). Mốc chấm là của TỔNG QUAN V5.0.

## 1. Bốn chỉ tiêu (E2-T3, E2-T4)

| Chỉ tiêu | Mốc | Đo được | Cách đo | Ngày |
|---|---|---|---|---|
| Khoảng cách hai micro | 4–6,5 cm | | thước kẹp, tâm hai lỗ âm | |
| Chênh độ nhạy | ≤ 3 dB | | ồn trắng chính diện 1 m | |
| Chênh pha | ≤ 10° | | ồn trắng chính diện 1 m | |
| SNR `ch0` / `ch1` | ≥ 62 dB (INMP441 ghi 61 dBA) | | | |
| Nền ồn khi Wi-Fi phát / tắt | — | | | |

**Lần đo sơ bộ 26/09 — phòng không kiểm soát, không nguồn chuẩn**, từ `drv_audio/test_apps/unit`, `pcm_shift` 16, 10 s: `ch0` −39,7 dBFS, `ch1` −44,0 dBFS (RMS phần xoay chiều), một chiều −28,2 và −29,6 LSB. Chênh 4,3 dB giữa hai kênh là **dấu hiệu cần đo lại**, chưa phải số của chỉ tiêu "chênh độ nhạy": nguồn âm không ở chính diện và không biết phổ.

## 2. Dịch 24 → 16 bit (E2-T5)

| `pcm_shift` | Mức tiếng nói to ở 10 cm dBFS | Nền ồn phòng yên dBFS | Cắt đỉnh | Chọn |
|---|---|---|---|---|

## 3. Hiệu chuẩn `balance` (E2-T6)

| Lần | Chênh biên độ sau bù lớn nhất dB | Chênh pha sau bù lớn nhất ° | Nhiệt độ phòng | Ngày |
|---|---|---|---|---|

# Dàn micro

Board B, 2 × INMP441 (KẾ HOẠCH §2.1, §2.3). Mốc chấm là của TỔNG QUAN V5.0.

## 1. Bốn chỉ tiêu (E2-T3, E2-T4)

| Chỉ tiêu | Mốc | Đo được | Cách đo | Ngày |
|---|---|---|---|---|
| Khoảng cách hai micro | 4–6,5 cm | ~45 mm (ước, chủ dự án đo) | thước, tâm hai lỗ âm; E2-T4 kiểm bằng trễ vỗ tay ở đầu dàn | 26/09 |
| Chênh độ nhạy | ≤ 3 dB | | ồn trắng chính diện 1 m | |
| Chênh pha | ≤ 10° | | ồn trắng chính diện 1 m | |
| SNR `ch0` / `ch1` | ≥ 62 dB (INMP441 ghi 61 dBA) | | | |
| Nền ồn khi Wi-Fi phát / tắt | — | | | |

**Lần đo sơ bộ 26/09 — phòng không kiểm soát, không nguồn chuẩn**, từ `drv_audio/test_apps/unit`, `pcm_shift` 16, 10 s: `ch0` −39,7 dBFS, `ch1` −44,0 dBFS (RMS phần xoay chiều), một chiều −28,2 và −29,6 LSB. Chênh 4,3 dB giữa hai kênh là **dấu hiệu cần đo lại**, chưa phải số của chỉ tiêu "chênh độ nhạy": nguồn âm không ở chính diện và không biết phổ.

**Phiên 30 phút `20260926_home_001` (26/09) — phòng yên, không nguồn âm, Wi-Fi đang phát luồng `mode 2`**, app `capture`, `pcm_shift` 16, `srhost.score`: `ch0` −74,7 dBFS, `ch1` −64,0 dBFS (RMS phần xoay chiều), đỉnh 232 và 965 LSB, một chiều −0,5 LSB cả hai, không mẫu nào cắt. `ch1` ồn hơn `ch0` **10,7 dB** khi không có tiếng, lớn hơn nhiều mức chênh 4,3 dB có tiếng ở trên: dấu hiệu `ch1` có nền ồn riêng (điện, Wi-Fi hay chính con micro). E2-T4 tách bằng hai dòng nền ồn khi Wi-Fi phát và khi tắt.

## 2. Dịch 24 → 16 bit (E2-T5)

| `pcm_shift` | Mức tiếng nói to ở 10 cm dBFS | Nền ồn phòng yên dBFS | Cắt đỉnh | Chọn |
|---|---|---|---|---|

## 3. Hiệu chuẩn `balance` (E2-T6)

| Lần | Chênh biên độ sau bù lớn nhất dB | Chênh pha sau bù lớn nhất ° | Nhiệt độ phòng | Ngày |
|---|---|---|---|---|

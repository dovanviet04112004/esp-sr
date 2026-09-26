# Dàn micro

Board B, 2 × INMP441 (KẾ HOẠCH §2.1, §2.3). Mốc chấm là của TỔNG QUAN V5.0. Mức ghi theo dBFS của firmware
(`level_dbfs`: công suất so với sóng vuông toàn thang); datasheet INMP441 lấy sóng sin toàn thang làm 0 dBFS, nên số
datasheet cộng 3,01 dB là số của bảng này.

## 0. Quy trình đo (E2-T4, E2-T6, E2-T7)

Mỗi bước là một phiên `make session` rồi một lần `srhost.score`; cách tính ở KẾ HOẠCH §4.6. Board phải được thả khỏi
bootloader bởi chính `make session`; **không mở monitor serial trong lúc thu**, vì mở cổng CH340 làm board khởi động lại.

**Chuẩn bị.** Board nằm phẳng trên bàn, cách tường ít nhất 1 m, không gì che hai lỗ micro; tắt quạt, điều hoà nếu
được. Máy tính: Docker Desktop chạy, `host/.env` đủ biến, `. ~/esp/esp-idf/export.sh`, rồi `D=$(git describe --always
--tags)`. `--pcm-shift 16` tới khi E2-T5 ghi `calib/pcm_shift`. Ở phiên thường, bản thu bắt đầu khi `make session` in
`board connected`: giữ yên 3–5 s đầu để có khung nền, rồi mới tạo tiếng.

| Bước | Nạp | `make session ARGS="--kind probe --room <phòng> --pcm-shift 16 …"` | Người đo làm | Cho ra |
|---|---|---|---|---|
| 1. Nhận micro | `make capture-flash` | `--fw capture@$D --prompt 'cào micro A' --duration-s 15` | cào nhẹ cạnh lỗ micro A 5 lần | kênh có `Peak` lớn hơn hẳn là micro A (E2-T7) |
| 2. Nền, Wi-Fi phát | `make capture-flash` | `--fw capture@$D --prompt 'nền ồn, Wi-Fi phát' --duration-s 60` | im lặng | `Floor dBFS(A)` từng kênh |
| 3. Nền, Wi-Fi tắt | `make capture-radio-off-flash` | `--fw capture-radio-off@$D --prompt 'nền ồn, Wi-Fi tắt' --duration-s 60` | im lặng 62 s kể từ `board released` (board bỏ 1 s đầu, thu 60 s rồi mới gửi) | như trên, radio tắt |
| 4. Xoay board 180° | `make capture-flash` | như bước 2, `--prompt 'nền ồn, board xoay 180'` | xoay board tại chỗ, im lặng | phần ồn dư đi theo con micro hay theo chỗ đặt |
| 5. Vỗ tay đầu `ch1` | `make capture-flash` | `--fw capture@$D --doa-deg 0 --distance-cm 50 --prompt 'vỗ tay đầu ch1' --duration-s 30` | đứng trên đường thẳng nối hai micro, phía `ch1`, tay cách tâm dàn 50 cm, vỗ 10 lần cách nhau 2 s | dấu của `τ` (E2-T7), khoảng cách (E2-T4) |
| 6. Vỗ tay đầu `ch0` | `make capture-flash` | như bước 5, `--doa-deg 180 --prompt 'vỗ tay đầu ch0'` | như bước 5, phía `ch0` | như trên, kiểm chéo |
| 7. Ồn trắng chính diện | `make capture-flash` | `--fw capture@$D --doa-deg 90 --distance-cm 100 --prompt 'ồn trắng chính diện 1 m' --duration-s 45` | điện thoại phát ồn trắng trên đường trung trực, cách 1 m, cao bằng board; bật sau 5 s, tắt sau 30 s; chỉnh âm lượng để `Peak` < 16 000 LSB | chênh độ nhạy, chênh pha (E2-T4); bản thu cho `balance` (E2-T6) |

Chấm: `cd host && uv run --extra score python -m srhost.score $SRPIPE_DATA_ROOT/raw/device/board_b/<phiên>`. Chênh độ
nhạy là `ch1 - ch0 dB` lớn nhất về trị tuyệt đối trên các dải có độ kết hợp ≥ 0,9; chênh pha là cột `Phase after tau`
tương ứng. SNR là độ nhạy datasheet (−26 dBFS sin = −29,0 dBFS của bảng này ở 94 dB SPL) trừ `Floor dBFS(A)`; nền đo
trong phòng gồm cả tiếng phòng nên số ấy là **chặn dưới**.

## 1. Bốn chỉ tiêu (E2-T3, E2-T4)

| Chỉ tiêu | Mốc | Đo được | Cách đo | Ngày |
|---|---|---|---|---|
| Khoảng cách hai micro | 4–6,5 cm | ~45 mm (ước, chủ dự án đo) | thước, tâm hai lỗ âm; E2-T4 kiểm bằng trễ vỗ tay ở đầu dàn | 26/09 |
| Chênh độ nhạy | ≤ 3 dB | | ồn trắng chính diện 1 m | |
| Chênh pha | ≤ 10° | | ồn trắng chính diện 1 m | |
| SNR `ch0` / `ch1` | ≥ 62 dB (INMP441 ghi 61 dBA) | **≥ 54,4 / ≥ 49,3 dB** (chặn dưới) | −29,0 dBFS (độ nhạy datasheet ở 94 dB SPL) trừ nền A khi Wi-Fi tắt | 26/09 |
| Nền ồn khi Wi-Fi phát / tắt | — | `ch0` −82,9 / −83,4 dBFS(A); `ch1` −78,1 / −78,3 dBFS(A) | bước 2 và 3 của §0, 60 s mỗi phiên, liền nhau: `20260926_home_004`, `…_005` | 26/09 |

**Lần đo sơ bộ 26/09 — phòng không kiểm soát, không nguồn chuẩn**, từ `drv_audio/test_apps/unit`, `pcm_shift` 16, 10 s: `ch0` −39,7 dBFS, `ch1` −44,0 dBFS (RMS phần xoay chiều), một chiều −28,2 và −29,6 LSB. Chênh 4,3 dB giữa hai kênh là **dấu hiệu cần đo lại**, chưa phải số của chỉ tiêu "chênh độ nhạy": nguồn âm không ở chính diện và không biết phổ.

**Phiên 30 phút `20260926_home_001` (26/09) — phòng yên, không nguồn âm, Wi-Fi đang phát luồng `mode 2`**, app `capture`, `pcm_shift` 16, `srhost.score`: `ch0` −74,7 dBFS, `ch1` −64,0 dBFS (RMS phần xoay chiều), đỉnh 232 và 965 LSB, một chiều −0,5 LSB cả hai, không mẫu nào cắt. `ch1` ồn hơn `ch0` **10,7 dB** khi không có tiếng, lớn hơn nhiều mức chênh 4,3 dB có tiếng ở trên: dấu hiệu `ch1` có nền ồn riêng (điện, Wi-Fi hay chính con micro). E2-T4 tách bằng hai dòng nền ồn khi Wi-Fi phát và khi tắt.

**Wi-Fi không phải nguồn ồn dư của `ch1`** (26/09, `20260926_home_004` phát, `…_005` tắt, liền nhau, cùng chỗ): nền A
đổi không quá 0,5 dB khi tắt radio, ở cả hai kênh. `ch1` ồn hơn `ch0` **~10 dB dưới 1 kHz** và 2–3 dB trên 1 kHz, như
nhau khi bật hay tắt radio; theo trọng số A là **~5 dB**. Còn lại hai khả năng: chính con micro `ch1` (hay nguồn cấp
của nó), hoặc chỗ đặt board. Bước 4 của §0 (xoay board 180°) tách hai khả năng này. Phiên `20260926_home_003` là lần
thử đầu của chế độ tắt radio, còn dính 0,5 s micro vừa cấp điện (một chiều trôi từ −1400 LSB); đừng dùng mức RMS
của nó.

## 2. Dịch 24 → 16 bit (E2-T5)

| `pcm_shift` | Mức tiếng nói to ở 10 cm dBFS | Nền ồn phòng yên dBFS | Cắt đỉnh | Chọn |
|---|---|---|---|---|

## 3. Hiệu chuẩn `balance` (E2-T6)

| Lần | Chênh biên độ sau bù lớn nhất dB | Chênh pha sau bù lớn nhất ° | Nhiệt độ phòng | Ngày |
|---|---|---|---|---|

# Dàn micro

Board B, 2 × INMP441 (KẾ HOẠCH §2.1, §2.3). Mốc chấm là của TỔNG QUAN V5.0. Mức ghi theo dBFS của firmware
(`level_dbfs`: công suất so với sóng vuông toàn thang); datasheet INMP441 lấy sóng sin toàn thang làm 0 dBFS, nên số
datasheet cộng 3,01 dB là số của bảng này.

## 0. Quy trình đo (E2-T4, E2-T6, E2-T7)

Mỗi bước là một phiên `make session` rồi một lần `srhost.score`; cách tính ở KẾ HOẠCH §4.6. Board phải được thả khỏi
bootloader bởi chính `make session`; **không mở monitor serial trong lúc thu**, vì mở cổng CH340 làm board khởi động lại.

**Chuẩn bị.** Board nằm phẳng trên bàn, cách tường ít nhất 1 m, không gì che hai lỗ micro; tắt quạt, điều hoà nếu
được. Máy tính: Docker Desktop chạy, `host/.env` đủ biến, `. ~/esp/esp-idf/export.sh`, rồi `D=$(git describe --always
--tags)`. `--pcm-shift` là giá trị NVS `calib/pcm_shift` của board (`make calib-shift SHIFT=<n>` với `test_apps/calib` nạp sẵn), 16 khi chưa ghi. Ở phiên thường, bản thu bắt đầu khi `make session` in
`board connected`: giữ yên 3–5 s đầu để có khung nền, rồi mới tạo tiếng.

| Bước | Nạp | `make session ARGS="--kind probe --room <phòng> --pcm-shift 16 …"` | Người đo làm | Cho ra |
|---|---|---|---|---|
| 1. Nền, Wi-Fi phát | `make capture-flash` | `--fw capture@$D --prompt 'nền ồn, Wi-Fi phát' --duration-s 60` | im lặng | `Floor dBFS(A)` từng kênh |
| 2. Nền, Wi-Fi tắt | `make capture-radio-off-flash` | `--fw capture-radio-off@$D --prompt 'nền ồn, Wi-Fi tắt' --duration-s 60` | im lặng 62 s kể từ `board released` (board bỏ 1 s đầu, thu 60 s rồi mới gửi) | như trên, radio tắt |
| 3. Vỗ tay đầu `ch1` | `make capture-flash` | `--fw capture@$D --doa-deg 0 --distance-cm 50 --prompt 'vỗ tay đầu ch1' --duration-s 180` | đứng trên đường thẳng qua hai lỗ micro, phía `ch1`, tay cách tâm dàn 50 cm, cao ngang dàn, vỗ 10 lần cách nhau 2 s | dấu của `τ` (E2-T7), nửa khoảng cách (E2-T4) |
| 4. Vỗ tay đầu `ch0` | `make capture-flash` | như bước 3, `--doa-deg 180 --prompt 'vỗ tay đầu ch0'` | như bước 3, phía `ch0` | nửa còn lại: khoảng cách là nửa hiệu hai `τ` trung vị, lệch giờ giữa hai kênh là nửa tổng |
| 5. Ồn trắng chính diện | `make capture-flash` | `--fw capture@$D --doa-deg 90 --distance-cm 20 --prompt 'ồn trắng chính diện 20 cm' --duration-s 180` | tắt tiếng, im 10 s, rồi phát ồn trắng 30 s từ loa đặt trên đường trung trực, cách 20 cm, cao bằng board, âm lượng tối đa mà `Peak` < 16 000 LSB; tắt | chênh độ nhạy, chênh pha (E2-T4); bản thu cho `balance` (E2-T6) |

**Nhận micro bằng dấu của `τ`, không bằng mức.** Micro nào nghe to hơn khi cào cạnh lỗ còn tuỳ độ nhạy hai kênh: trên
board B cào lỗ `ch0` vẫn ra `ch1` to hơn 7–8 dB, vì `ch0` nghe nhỏ hơn ~10 dB ở mọi hướng. Đầu nào vỗ tay cho `τ` dương là
đầu `ch1`; nhãn `--doa-deg` ghi sai phía thì `score` báo `OPPOSITE`, sửa nhãn trong `session.json` và dòng manifest. Board B:
**lỗ A là `ch0`, lỗ B là `ch1`** (E2-T7). Ngồi đối diện hai micro thì lỗ A ở **bên trái**, lỗ B bên phải: 0° (phía `ch1`) là tay phải người ngồi đối diện, 180° là tay trái. Dừng phiên sớm bằng Ctrl-C (hay `docker kill --signal INT sr-session`) là an toàn:
máy nhận dừng ở ranh giới khung.

Chấm: `cd host && uv run --extra score python -m srhost.score $SRPIPE_DATA_ROOT/raw/device/board_b/<phiên>`. Chênh độ
nhạy là `ch1 - ch0 dB` lớn nhất về trị tuyệt đối trên các dải có độ kết hợp ≥ 0,9; chênh pha là cột `Phase after tau`
tương ứng. SNR là độ nhạy datasheet (−26 dBFS sin = −29,0 dBFS của bảng này ở 94 dB SPL) trừ `Floor dBFS(A)`; nền đo
trong phòng gồm cả tiếng phòng nên số ấy là **chặn dưới**.

## 1. Bốn chỉ tiêu (E2-T3, E2-T4)

| Chỉ tiêu | Mốc | Đo được | Cách đo | Ngày |
|---|---|---|---|---|
| Khoảng cách hai micro | 4–6,5 cm | **44,4 mm** bằng tiếng; ~45 mm bằng thước | bước 3 và 4 của §0: `τ` trung vị +1,89 và −2,25 mẫu, khoảng cách = nửa hiệu = 2,07 mẫu (`20260926_home_008`, `…_007`) | 26/09 |
| Chênh độ nhạy | ≤ 3 dB | **+10,8 … +11,6 dB** ở 200–1600 Hz, **+13,7 dB** ở 1,6–6,4 kHz (`ch1` to hơn) — **trượt** | ồn trắng chính diện 20 cm, hai lần (`20260926_home_010`, `…_011`); vỗ tay hai đầu cho cùng mức, kể cả phần nhô +13,6 … +15,7 dB ở 1,6–3,2 kHz: mức là của thiết bị | 26/09 |
| Chênh pha | ≤ 10° | **0 … −4,7°** ở 200–1600 Hz — **đạt**; dải 1,6–6,4 kHz đổi theo hướng đặt loa (+34 / +24° loa trước hộp, +12 / −8° loa sau hộp), nên là của phòng, không của linh kiện; lệch giờ tĩnh −0,14 … −0,21 mẫu | ồn trắng 20 cm trước hộp (`20260926_home_011`) và sau hộp (`…_012`); lệch giờ theo vỗ tay (−0,18) và đường pha của hai phiên | 26/09 |
| SNR `ch0` / `ch1` | ≥ 62 dB (INMP441 ghi 61 dBA) | **≥ 54,4 / ≥ 49,3 dB** (chặn dưới) | −29,0 dBFS (độ nhạy datasheet ở 94 dB SPL) trừ nền A khi Wi-Fi tắt | 26/09 |
| Nền ồn khi Wi-Fi phát / tắt | — | `ch0` −82,9 / −83,4 dBFS(A); `ch1` −78,1 / −78,3 dBFS(A) | bước 2 và 3 của §0, 60 s mỗi phiên, liền nhau: `20260926_home_004`, `…_005` | 26/09 |

**Lần đo sơ bộ 26/09 — phòng không kiểm soát, không nguồn chuẩn**, từ `drv_audio/test_apps/unit`, `pcm_shift` 16, 10 s: `ch0` −39,7 dBFS, `ch1` −44,0 dBFS (RMS phần xoay chiều), một chiều −28,2 và −29,6 LSB. Chênh 4,3 dB giữa hai kênh là **dấu hiệu cần đo lại**, chưa phải số của chỉ tiêu "chênh độ nhạy": nguồn âm không ở chính diện và không biết phổ.

**Phiên 30 phút `20260926_home_001` (26/09) — phòng yên, không nguồn âm, Wi-Fi đang phát luồng `mode 2`**, app `capture`, `pcm_shift` 16, `srhost.score`: `ch0` −74,7 dBFS, `ch1` −64,0 dBFS (RMS phần xoay chiều), đỉnh 232 và 965 LSB, một chiều −0,5 LSB cả hai, không mẫu nào cắt. `ch1` ồn hơn `ch0` **10,7 dB** khi không có tiếng, lớn hơn nhiều mức chênh 4,3 dB có tiếng ở trên: dấu hiệu `ch1` có nền ồn riêng (điện, Wi-Fi hay chính con micro). E2-T4 tách bằng hai dòng nền ồn khi Wi-Fi phát và khi tắt.

**Wi-Fi không làm micro ồn thêm** (26/09, `20260926_home_004` phát, `…_005` tắt, liền nhau, cùng chỗ): nền A đổi không
quá 0,5 dB khi tắt radio, ở cả hai kênh. Phiên `20260926_home_003` là lần thử đầu của chế độ tắt radio, còn dính 0,5 s
micro vừa cấp điện (một chiều trôi từ −1400 LSB); đừng dùng mức RMS của nó.

**Ồn trắng chính diện: gần mới đo được.** Loa điện thoại ở mức tối đa chỉ trên nền 20–30 dB và gần như không phát dưới
200 Hz, nên dải 50–200 Hz không đo được bằng nó. Ở 1 m (`20260926_home_009`) độ kết hợp trên 800 Hz chỉ 0,5–0,7: tiếng dội
của phòng lấn tiếng thẳng. Ở 20 cm, hai lần cùng chỗ (`…_010`, `…_011`), độ kết hợp 200–1600 Hz là 0,97–0,998 và dải
1,6–6,4 kHz ổn định ở ~0,8 với cùng chênh mức và chênh pha. Hai lần cùng chỗ chỉ chứng tỏ số lặp lại được, không tách được
thiết bị khỏi cách đặt loa; lần thứ ba đặt loa phía sau hộp (`…_012`) tách được: mức giữ nguyên (+10,9 … +12,0 dB, +14,2 dB
ở 1,6–3,2 kHz), pha dải cao đổi hẳn. Chênh mức là của thiết bị; pha dải cao là của phòng và chỗ đặt loa. Hệ số `balance` ước
từ một chỗ đặt loa vì thế chỉ tin được phần mức theo vạch và phần pha dạng trễ nhỏ cộng pha hằng mà mọi phép đo cùng đồng ý;
pha theo từng vạch ở dải cao sẽ học luôn tiếng dội của phòng lúc hiệu chuẩn. Phiên phải
bắt đầu bằng một đoạn im (`…_010` phát liền từ đầu nên `score` không tách được khung nguồn khỏi khung nền).

**`ch0` nghe nhỏ hơn `ch1` ~10,5 dB ở mọi hướng** (vỗ tay hai đầu, 26/09, board trong hộp). Điều này giải thích phần
"`ch1` ồn hơn" của các phiên im lặng: dưới 1 kHz tiếng phòng lấn nền điện tử của micro, và `ch0` nhận ít tiếng phòng hơn
~10 dB; trên 1 kHz nền điện tử lấn tiếng phòng nên hai kênh chỉ chênh 2–3 dB. Lần đo sơ bộ ở trên, trước khi đóng hộp,
`ch0` còn to hơn `ch1` 4,3 dB, nên nghi trước tiên **lỗ A của hộp (lỗ trên micro `ch0`) lệch hay bị che**, sau đó mới
tới chính con micro. `balance` bù được mức, nhưng micro nghe nhỏ đi 10 dB thì SNR của kênh ấy cũng mất 10 dB, bù số không
lấy lại được. Sửa lỗ rồi đo lại bước 3–5 của §0 trước khi hiệu chuẩn `balance`.

**Chênh độ nhạy lớn dần theo ngày.** Cùng board B, `ch1 − ch0` trên các khung có nguồn, dải 200–1600 Hz, đo lại
28/09 bằng `srpipe.metrics.mic_pair` trên bản thu của repo tinyai-signal và của esp-sr:

| Ngày | Bản thu | Nguồn | `ch1 − ch0` |
|---|---|---|---|
| 08/08 | tinyai `phien1.wav`, `phien2_nhac.wav` | người nói chính diện, loa nhạc | +4,1 / +4,4 dB (tinyai T04.2 ghi +4,70 dB) |
| 02/09 | tinyai `tach_2nguoi/*` (board trong vỏ) | hai người ở 1 m, nguồn quét 0,3 m | +8,8 … +9,1 dB |
| 17/09 | tinyai `loa_0917_cao24/loa40_{e,h}_*` | loa và người ở 40 cm | +10,6 / +10,8 dB |
| 26/09 | esp-sr `20260926_home_010`, `…_011` | ồn trắng chính diện 20 cm | +10,8 … +11,6 dB |

Linh kiện không trôi 7 dB trong bảy tuần; đường tiếng tới `ch0` bị che dần (lỗ A của vỏ lệch, bụi hay keo trong lỗ),
hoặc chính micro `ch0` đang hỏng. Soi lỗ A trước buổi thu kế tiếp. Sửa được thì `calib/bal` cũ hết đúng: đo lại bước
3–5 của §0 rồi `make calib-estimate`.

## 2. Dịch 24 → 16 bit (E2-T5)

**Chọn 13**, ghi NVS `calib/pcm_shift` của board B ngày 28/09 (`make calib-shift SHIFT=13`, đọc lại khớp). Hai phép đo trên
board B trong vỏ, phòng ở nhà: tiếng to đọc cách 10 cm (`20260928_home_045`, thu ở dịch 16 để không đỉnh nào cắt) và nền
phòng yên 60 s (`20260928_home_004`, thu ở dịch 13). Các dịch khác dựng lại đúng từng mẫu bằng `floor(x / 2^k)` như
`drv_audio` dịch (`srhost.score --shift`), riêng dịch 12 quy từ 13 bằng cộng 6,02 dB. Mức là RMS phần xoay chiều.

| `pcm_shift` | Tiếng to 10 cm `ch0` / `ch1` dBFS | Đỉnh `ch1` LSB | Nền phòng yên `ch0` / `ch1` dBFS | Cắt đỉnh | Chọn |
|---|---|---|---|---|---|
| 12 | −29,1 / −18,7 | 51 328 | −57,1 / −53,1 | **có** | |
| 13 | −35,1 / −24,7 | 25 664 | −63,1 / −59,1 | không, dư 2,1 dB | ✅ |
| 14 | −41,2 / −30,8 | 12 832 | −69,1 / −65,1 | không | |
| 16 | −53,2 / −42,8 | 3 208 | −81,1 / −77,1 | không | |

Ở dịch 13 nền phòng yên còn cao hơn một bước lượng tử `int16` (−90,3 dBFS) 27–31 dB. Tiếng to 10 cm không cắt, nhưng chỉ
dư 2,1 dB: hét sát micro thì cắt. Dịch 13 thắng vì chuỗi sản phẩm nghe được tiếng nhỏ và tiếng xa, cũng trên bản thu
hôm ấy (`srhost.score --shift`, `balance` của board, `ns` sàn, `vad` mức 2, tỉ lệ bước `vad = 1`):

| Phiên | Nội dung | Dịch 13 | Dịch 14 | Dịch 16 |
|---|---|---|---|---|
| `20260928_home_005` | đọc giọng thường 1 m | 53 % | 46 % | 6 % |
| `20260928_home_006` | đọc giọng nhỏ 1 m | 29 % | 3,8 % | 0 % |
| `20260928_home_007` | đọc giọng thường 3 m | 47 % | 11 % | 0 % |
| `20260928_home_037` | "Chào Mina" 10 lần, 3 m | 33 % | 21 % | 5,7 % |
| `20260928_home_004` | phòng yên, không ai nói | 0,4 % | 0 % | 0 % |

Ở dịch 14 chuỗi đã mất tiếng nhỏ và tiếng 3 m. Đó là lý do đổi lấy 6 dB dư trần.

### Hướng từ bản phát loa (28/09, E8-T1)

`host/plans/playback.tsv`: `interim/playback/vivos_test.wav` phát từ loa điện thoại cách tâm dàn 1 m, cao ngang board, ở
năm hướng; `srhost.score` đo trễ GCC-PHAT `tau = t0 − t1` theo mẫu 16 kHz. Hợp đồng `array.yaml` (44,4 mm) cho ±2,10 mẫu ở
hai đầu dàn. Góc suy ra bằng `arccos(tau / 2,10)`.

| Phiên | Loa ở | `tau` đo | `tau` hợp đồng | Góc suy ra | Dấu |
|---|---|---|---|---|---|
| `20260928_home_040` | 90° | −0,50 | 0,00 | ~104° | không xét gần chính diện |
| `20260928_home_041` | 0° (lỗ B, `ch1`) | +1,81 | +2,10 | ~31° | đúng |
| `20260928_home_042` | 45° | +0,97 | +1,48 | ~62° | đúng |
| `20260928_home_043` | 180° (lỗ A, `ch0`) | −2,62 | −2,10 | 180° | đúng |
| `20260928_home_044` | 135° | −1,13 | −1,48 | ~123° | đúng |

Dấu đúng ở cả bốn hướng xét được. Góc lệch 12–18° ở các hướng chéo: vang của phòng, loa đặt bằng tay, và trễ còn dò trên
số nguyên mẫu. Module `doa` chấm lại chính các phiên này ở `afe/doa.md` §2. Phiên 043 và 044 đặt loa ngược thứ tự
kịch bản; nhãn đã sửa theo chỗ loa thật, và `tau` xác nhận nhãn mới.

## 3. Hiệu chuẩn `balance` (E2-T6)

| Lần | Chênh biên độ sau bù lớn nhất dB | Chênh pha sau bù lớn nhất ° | Nhiệt độ phòng | Ngày |
|---|---|---|---|---|
| 1 | **0,47** (50 Hz – 1,6 kHz, bỏ ra từng chỗ đặt loa); 1,6–8 kHz không kiểm được, các chỗ đặt loa tự lệch ±1…3,6 dB | **3,4** (cùng dải) | không ghi | 26/09 |

Lần 1 ước từ ba chỗ đặt loa (`20260926_home_009` 1 m trước, `…_011` 20 cm trước, `…_012` 20 cm sau), 12 252 khung:
đường pha `τ` −0,10 mẫu, `φ₀` −2,2°; mức bù 1/3 octave, `ch1` giảm ~11 dB. Hệ số ở `calib/board_b_balance.csv`, ghi xuống
NVS `calib/bal` bằng `make calib-flash` rồi `make calib-write`, đọc lại CRC32 `0x20e480d4` khớp bản trên máy tính.

Dải 1,6–8 kHz chưa đạt được mốc "dưới 1 dB toàn băng" một cách kiểm chứng: ở đó độ kết hợp 0,3–0,8 và mức của từng chỗ đặt
loa tự khác nhau vài dB vì phòng, nên hệ số gộp là trung bình của ba chỗ, sai số cỡ ±2 dB. Hẹp sai số này cần thêm chỗ đặt
loa, hay một phòng ít vang. Lần đo ở nhiệt độ phòng thứ hai, để biết phần trôi theo nhiệt, **không làm**: chủ dự án chốt
đóng E2-T6 sau lần 1 (26/09). Khi làm: thu lại bước 5 của §0 ở ba chỗ, `make calib-estimate`, so pha sau bù bằng hệ số lần 1.

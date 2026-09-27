# Parity C ↔ Python

Kết quả `test_apps/parity` trên board, đọc `contracts/golden/`. Ngưỡng lấy từ `tolerance.yaml` của từng khối.
App dựng bằng cờ trình biên dịch của `bench`, cũng là của `prod` (`-O2`, KẾ HOẠCH §3.14), hai lần: mọi module tắt với bộ
vàng `chain`, và profile `modules` với bộ vàng của từng module thật. Chạy: `make parity-board`.

| Khối | Số ca | Sai số tuyệt đối lớn nhất | SNR nhỏ nhất dB | Ngưỡng | Đối chứng âm đỏ | Commit | Ngày |
|---|---|---|---|---|---|---|---|
| `stft` — phổ phân tích | 4 | 7,6e-6 | 137,2 | ≤ 1e-4, ≥ 115 dB | — | cab1eb3 | 27/09 |
| `stft` — tổng hợp từ phổ vàng | 4 | 3,0e-7 | 137,5 | ≤ 1e-5, ≥ 115 dB | lệch một mẫu: 0,96, −3,1 dB → đỏ | cab1eb3 | 27/09 |
| `mel` — log-mel, ba cấu hình (40, 80, 24 dải) | 3 | 1,9e-6 | 140,0 | ≤ 1e-4, ≥ 115 dB | lệch một dải: 2,24, 3,0 dB → đỏ | cab1eb3 | 27/09 |
| `mel` — MFCC (13, 20, 24 hệ số) | 3 | 1,5e-5 | 134,8 | ≤ 1e-3, ≥ 110 dB | — | cab1eb3 | 27/09 |
| `chain` — `pcm` ra của mặt tiền `dsp_afe`, mọi module tắt (int16) | 4 | 1 LSB | 77,9 | ≤ 1 LSB, ≥ 60 dB | lệch một mẫu: 15 090 LSB, −2,9 dB → đỏ | cab1eb3 | 27/09 |
| `chain` — `seq`, `doa_deg`, `doa_conf`, `vad`, `level_dbfs`, `gain_db`, `flags` | 4 | 0 | — | khớp tuyệt đối (`level_dbfs` ≤ 1) | — | cab1eb3 | 27/09 |
| `hpf` — biquad dạng II chuyển vị viết tay (ADR-0004), máy tính và board B | 4 | 0 | — | ≤ 1e-6, ≥ 120 dB | trễ một mẫu: 0,996, −3,1 dB → đỏ | cab1eb3 | 27/09 |
| `balance` — nhân phức viết tay (ADR-0005), máy tính và board B | 4 | 0 | — | ≤ 1e-6, ≥ 120 dB | hệ số liên hợp: 26,0, −3,5 dB → đỏ | cab1eb3 | 27/09 |
| `vad` — mức sáu dải, quyết định thô, `speech` sau kéo dài; máy tính và board B | 4 | 0 | — | mức ≤ 1e-6, ≥ 120 dB; quyết định khớp tuyệt đối | `speech` trễ một bước: 1 → đỏ | 17bdfac | 27/09 |
| `agc` — mọi mẫu ra, `gain_db` mỗi bước; máy tính và board B | 3 | 0 (ra), 4,8e-7 dB (`gain_db`, board) | 151,0 (`gain_db`) | ra ≤ 1e-6, ≥ 120 dB; `gain_db` ≤ 1e-5 | bỏ qua cờ nói: 1,5e-3, 19,5 dB → đỏ | 17bdfac | 27/09 |

## Bản tham chiếu Python: STFT phân tích rồi tổng hợp (E6-T1)

`srpipe.dsp.spec.stft`, float32, lưới §3.1 (512 / 256, căn Hann tuần hoàn), tín hiệu dài 64 bước. Ra trễ vào đúng **một bước (256 mẫu)**; trễ thuật toán từ lúc một mẫu tới cho tới lúc nó ra là tới một cửa sổ (32 ms).

| Tín hiệu | SNR dựng lại dB | Sai số tuyệt đối lớn nhất |
|---|---|---|
| ồn trắng ±0,5 | 138,8 | 1,79e-7 |
| sin 440 Hz biên độ 0,5 | 138,6 | 1,79e-7 |
| chirp 50 Hz → 7,9 kHz | 139,0 | 2,09e-7 |
| chuỗi xung 120 Hz biên độ 0,9 | 139,2 | 2,38e-7 |

Đây là trần float32 của chính bản tham chiếu; bộ vàng C ↔ Python (E6-T4) so với bảng trên.

## Chuỗi mặt tiền `dsp_afe` (E3-T4)

`pcm` lệch tới 1 LSB là bản chất của phép so, không phải lỗi: trung bình hai mẫu int16 có tổng lẻ rơi đúng vào nửa
LSB, và sai số float32 của hai thư viện FFT (`dl_fft` trên board, numpy ở Python) quyết định làm tròn lên hay xuống.
Ca có tổng hai kênh luôn chẵn (chirp giống nhau hai kênh, tiếng gần im) khớp tuyệt đối. Trên máy tính: 78,1 dB.

## Dựng lại trên phiên thu thật của board (E5-T11)

App khung rỗng (`main` bản `dev`, mọi module `dsp_afe` tắt), luồng `mode 5` (`ch0 ch1 clean`) 10 phút về
`srhost.stream_rx`; `srhost.score` chạy `srpipe.dsp.afe.chain` trên `ch0 ch1` rồi so với `clean` của board, bỏ hai
bước đầu, phán theo `contracts/golden/chain/tolerance.yaml`.

| Phiên | Bản dựng | Bước so | Sai số lớn nhất | Mẫu vượt ngưỡng | Mẫu lệch 1 LSB | SNR dB | Ngày |
|---|---|---|---|---|---|---|---|
| `20260926_home_002` | `dev` @ `3d3335d` | 37 498 | 1 LSB | 0 | 21,1 % | 25,1 | 26/09 |

Phòng yên, không nguồn âm: `clean` chỉ khoảng −72 dBFS (RMS ~8 LSB), nên sai số làm tròn 1 LSB của mục trên chiếm phần
lớn và kéo SNR xuống 25 dB. Ngưỡng SNR 60 dB của bộ vàng áp cho tín hiệu có biên độ của bộ vàng, không áp ở đây; phép
phán của phiên thật là sai số lớn nhất ≤ 1 LSB.

## `hpf` (E7-T1)

Bản C (dạng II chuyển vị viết tay, ADR-0004) và bản Python làm cùng các bước float32 theo cùng thứ tự, hệ số tính cos và
sin ở double rồi làm tròn một lần, và `dsp_afe` dựng không gộp nhân-cộng (ADR-0006): máy tính và board B ở `-O2` đều
khớp **từng bit** trên cả bốn ca. Bản gộp nhân-cộng lệch tới 2,0e-6 (`latency.md` §6), ngoài ngưỡng. Kernel `esp-dsp` dạng II mà
ADR-0003 chọn trước đó lệch tới 1,2e-4 (68,7 dB) trên board ở ca hum 50 Hz −10 dBFS trên một chiều, vì bản hợp ngữ làm
tròn khác và dạng II khuếch đại chênh lệch ở tần số thấp. Chạy lại bằng `make parity-board`.

## `balance` (E7-T2)

Bốn ca, mỗi ca 16 bước phổ `ch1` do bộ phân tích cho: hệ số dáng board B (−11 dB, đường pha −0,1 mẫu và −2,2°) trên ồn
đều, hệ số đơn vị trên chirp (ra phải bằng vào), hệ số ngẫu nhiên −40 … +20 dB với pha bất kỳ trên phổ gần tràn thang,
và hệ số dáng board trên phổ gần im lặng. Máy tính và board B ở `-O2` khớp **từng bit** cả bốn ca. Đối chứng âm nhân
với hệ số liên hợp, lỗi dấu dễ mắc nhất của phép nhân phức: lệch 26,0, −3,5 dB, đỏ. Chạy lại bằng `make parity-board`.

## `vad` (E7-T3)

Bốn ca đầu vào `int16` (chia 32 768 ở cả hai phía nên là cùng một số float), mỗi ca 160 bước, dài hơn cửa sổ 100 bước
của bộ dò tối thiểu: tiếng tổng hợp giống lời nói trên ồn trắng; tiếng nhỏ trên ồn đỏ ở `aggressiveness` 2; 40 bước im
tuyệt đối, không được mô hình hoá, rồi các đoạn to ở mức 3; nhiễu nhảy mức 28 dB ở mức 1. Máy tính và board B ở `-O2` khớp **từng
bit** cả ba tensor của cả bốn ca. Đối chứng âm cho `speech` trễ một bước: đỏ.

## `agc` (E7-T4)

Ba ca đầu vào `int16` kèm cờ nói mỗi bước: tiếng nhỏ để gain leo 64 bước, đóng băng 32 bước rồi leo tiếp (128 bước); tiếng
to có sáu cụm tràn thang để bộ chặn đỉnh giữ dưới −3 dBFS trong lúc gain hạ; nhiễu dưới đích −20 dBFS, cờ nói bốn trên
sáu bước. Máy tính và board B khớp **từng bit** mọi mẫu ra của
cả ba ca; `gain_db` là số báo qua `log10f`, khớp từng bit trên máy tính, lệch một bit cuối (4,8e-7 dB) ở một ca trên board
vì `log10f` của newlib làm tròn khác numpy. Đối chứng âm bỏ qua cờ nói nên gain không nhúc nhích: đỏ.

## Chỗ chứa bộ vàng

App parity có bảng phân vùng riêng (KẾ HOẠCH §4.3) với `storage` 8 MB. Bộ vàng hiện 1 982 265 B, 2 109 440 B tính theo
khối 4 KB của LittleFS, 25% phân vùng; mọi ca giữ đủ độ dài và đủ mẫu, ca lớn nhất 196 KB trong bộ đệm đọc 512 KB.

## Đường nối tiếp

Cầu CH340 sau usbipd có lúc rơi byte: có lượt mất cả dòng, có lượt một dòng cụt đuôi dính vào dòng sau; nhật ký kernel có
các URB bị huỷ của `vhci_hcd`. Kết quả vì thế đi qua `test_report` (KẾ HOẠCH §4.5.7): mỗi dòng có số thứ tự và CRC32,
dòng cuối là `end <n> lines`, máy tính bỏ dòng sai CRC và xin lại dòng thiếu. Lượt parity `modules` tại `9e56e13` gặp đúng
chuyện ấy: dòng 42 mất đuôi, dòng 43 mất hẳn, dòng 44 mất đầu; bộ gom xin lại `42 43 44`, board in lại cả ba với CRC đúng,
và lượt qua mà không phải chạy lại. Đổi một chữ số trong một dòng của log, hay xoá một dòng, bộ chấm đều từ chối.

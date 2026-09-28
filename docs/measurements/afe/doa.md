# `doa` (E8-T1)

## 1. Cảnh dựng có nhãn

`make eval-doa` (`srpipe.scenes.spatial doa`, `ml/configs/afe/doa.yaml`), bản Python soi gương, 28/09.

| Mục | Giá trị |
|---|---|
| Cảnh | 216 cảnh 8 s của `interim/scenes/standard` (E4-T4): phòng hộp 3–7 m, dàn hai micro 4,5 cm trên bàn, người nói VIVOS `test` cách 0,5–3 m |
| Điều kiện | mỗi RT60 0,2 / 0,4 / 0,6 s: người nói một mình, rồi thêm một nhiễu có hướng (DEMAND hoặc người nói thứ hai) lệch ≥ 60° ở SNR 20, 10, 5, 0 dB; 8 cảnh mỗi ô |
| Chuỗi | `hpf` → STFT → `doa`, dò lưới mỗi hai bước sau một bước người nói đang nói (nhãn lấy từ tiếng sạch) |
| Chấm | mọi bước người nói đang nói, so góc 3D tới trục `ch0`→`ch1` của người nói; ô: lỗi góc trung bình / % bước trong ±10°; bước chưa có góc tính là trượt |

**Người nói một mình**, theo vùng góc thật:

| Dải dùng | Vạch | 0–30° | 30–150° | 150–180° | RT60 0,2 s | RT60 0,4 s | RT60 0,6 s |
|---|---|---|---|---|---|---|---|
| 200 Hz – c/2d (3,81 kHz) | 115 | 15,6° / 4% | 7,9° / 73% | 22,2° / 12% | 7,8° / 70% | 12,1° / 43% | 14,7° / 50% |
| 0 – 8 kHz | 256 | 6,3° / 95% | 2,9° / 98% | 10,3° / 55% | 4,8° / 90% | 4,6° / 90% | 4,5° / 90% |
| 200 Hz – 8 kHz | 250 | 6,3° / 95% | 2,9° / 98% | 10,3° / 55% | 4,8° / 90% | 4,6° / 90% | 4,5° / 90% |
| 1 – 8 kHz | 225 | 6,2° / 97% | 2,9° / 99% | 10,1° / 56% | 4,7° / 90% | 4,5° / 90% | 4,4° / 91% |
| **2 – 8 kHz — đang dùng** | 193 | **5,6° / 99%** | **2,7° / 99%** | **9,5° / 61%** | **4,5° / 91%** | **4,2° / 90%** | **4,1° / 97%** |

**Có nhiễu có hướng**, dải 200 Hz – c/2d → 2 – 8 kHz:

| Điều kiện | RT60 0,2 s | RT60 0,4 s | RT60 0,6 s |
|---|---|---|---|
| nhiễu 20 dB | 10,0° / 70% → 9,8° / 89% | 8,3° / 64% → 7,0° / 85% | 24,0° / 17% → 10,1° / 72% |
| nhiễu 10 dB | 9,0° / 81% → 9,4° / 91% | 25,1° / 38% → 27,0° / 55% | 19,4° / 37% → 20,2° / 55% |
| nhiễu 5 dB | 14,0° / 52% → 14,0° / 66% | 26,9° / 52% → 31,1° / 55% | 20,7° / 43% → 18,2° / 75% |
| nhiễu 0 dB | 33,9° / 38% → 39,4° / 49% | 34,9° / 13% → 59,0° / 19% | 28,5° / 14% → 36,1° / 45% |
| người nói khác 20 dB | 11,1° / 67% → 7,1° / 90% | 12,4° / 52% → 8,5° / 84% | 15,6° / 38% → 7,9° / 84% |
| người nói khác 10 dB | 23,2° / 52% → 20,3° / 67% | 28,3° / 32% → 23,5° / 63% | 19,7° / 29% → 15,3° / 82% |
| người nói khác 5 dB | 30,0° / 42% → 31,4° / 69% | 39,7° / 12% → 30,2° / 55% | 34,0° / 15% → 29,3° / 50% |
| người nói khác 0 dB | 45,7° / 18% → 48,6° / 43% | 34,3° / 10% → 35,0° / 56% | 35,5° / 16% → 32,7° / 43% |

Ở SNR thấp một bước "người nói đang nói" vẫn có thể do nguồn kia trội, nên lỗi trung bình tăng ở cả hai dải; phần trăm
trong ±10° thì dải 2–8 kHz hơn ở mọi ô. Để chuỗi tự gác bằng `vad` của nó thay cho nhãn đổi rất ít: dải cũ 7,8° / 69%,
12,1° / 42%, 14,9° / 49% khi người nói một mình, lệch nhãn tối đa 1 điểm phần trăm ở mọi ô.

**Vì sao dải thấp kéo về chính diện.** Hai micro cách 4,5 cm: trong trường vang khuếch tán hai micro có hàm kết hợp thực
`sinc(kd)`, 0,89 ở 1 kHz, tức tiếng vang ở dải thấp giống nhau ở hai micro **với pha 0**. Phổ chéo đã làm trơn cộng phần
ấy vào pha của đường thẳng, và góc bị kéo về 90°: trên 24 cảnh một mình, góc thật 159° ra 118°, 150° ra 114°. Trên tần
số gập `sinc(kd)` về gần 0, tiếng vang mất kết hợp và chỉ còn đường thẳng mang pha. Đỉnh ma của từng vạch trên tần số gập
nằm mỗi vạch một chỗ, nên tổng trên cả dải vẫn chỉ có một đỉnh.

## 2. Bản thu qua board B

Phiên `20260928_home_040` … `044`: loa phát `interim/playback/vivos_test.wav` cách 1 m ở năm hướng (`mic_array.md`),
`pcm_shift` 13, `balance` của `docs/measurements/calib/board_b_balance.csv`, chuỗi sản phẩm với `vad` của nó gác.
`uv run python -m srhost.score <phiên>` in dòng `doa` từ 28/09. Ô: trung vị (khoảng tứ phân vị) trên các bước `vad = 1`.

| Phiên | Hướng loa | 200 Hz – c/2d | 200 Hz – 8 kHz | 1 – 8 kHz | **2 – 8 kHz** |
|---|---|---|---|---|---|
| 040 | 90° | 86° (84–86) | 100° (96–102) | 100° (96–102) | 102° (98–104) |
| 041 | 0° (`ch1`) | 52° (40–62) | 28° (24–32) | 28° (24–32) | 28° (24–30) |
| 042 | 45° | 74° (72–76) | 54° (48–58) | 52° (48–58) | 52° (48–58) |
| 043 | 180° (`ch0`) | 106° (102–110) | 180° (166–180) | 180° (172–180) | 180° (180–180) |
| 044 | 135° | 98° (98–100) | 122° (116–126) | 122° (118–126) | 124° (120–130) |

Dải cũ lệch 29–74° ở mọi hướng trừ chính diện; dải 2–8 kHz lệch 0–28°, đúng phía ở cả năm hướng; loa đặt tay theo
hướng nên phần lệch còn lại gồm cả sai số đặt loa. Phiên 041 lệch 28° cũng như phép GCC-PHAT độ trễ (`mic_array.md`: ~31°): loa đặt ở 0° theo mắt
thường, và gần đầu dàn một chút lệch vị trí thành nhiều độ.

## 3. Chi phí trên board B

`make bench-board`, profile `bench`, 28/09 (`budget.md`): gộp phổ chéo mỗi bước 22,1 µs; gộp và dò 91 góc × 193 vạch
640,9 µs, chỉ ở bước dò (mỗi hai bước khi `vad = 1`); vùng trạng thái 4 304 B. Cách tính dò lưới chọn ở ADR-0008
(`latency.md` §9).

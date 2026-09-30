# Ngân sách chạy theo module

Bảng dưới do `tools/budget.py` ghi từ CSV của `test_apps/bench_*` (TỔNG QUAN V5.0.10, TASKS E5-T13).
Chỉ số từ profile `bench`; số từ `dev` không vào bảng này.

| Module | Nhân | RAM tĩnh B | Vùng `hot` B | Vùng `cold` B | µs trung bình | µs đỉnh | % khung 16 ms | Commit | Ngày |
|---|---|---|---|---|---|---|---|---|---|
| dsp_afe agc | 1 | 0 | 1200 | 0 | 258,3 | 261,9 | 1,6 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe agc chặn đỉnh mọi mẫu | 1 | 0 | 1200 | 0 | 341,8 | 357,7 | 2,1 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe balance | 1 | 0 | 2056 | 0 | 18,4 | 22,7 | 0,1 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe chuỗi (hpf + stft x2 + balance + doa + trộn + ns + istft + vad + agc) | 1 | 6244 | 81824 | 0 | 2615,1 | 3297,9 | 16,3 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe doa gộp phổ chéo | 1 | 0 | 4304 | 0 | 22,1 | 26,4 | 0,1 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe doa gộp và dò lưới | 1 | 0 | 4304 | 0 | 640,9 | 642,6 | 4,0 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe gsc học | 1 | 0 | 4160 | 0 | 140,5 | 144,2 | 0,9 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe gsc học và đổi góc lái mỗi bước | 1 | 0 | 4160 | 0 | 485,4 | 492,3 | 3,0 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe hpf (2 kênh) | 1 | 0 | 64 | 0 | 41,1 | 45,3 | 0,3 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe ns_omlsa | 1 | 0 | 33280 | 0 | 1689,5 | 1876,3 | 10,6 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_afe vad | 1 | 0 | 2640 | 0 | 138,0 | 175,3 | 0,9 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_spec istft | 1 | 28 | 8256 | 0 | 164,5 | 168,5 | 1,0 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_spec stft (2 kênh) | 1 | 28 | 14432 | 0 | 286,2 | 289,7 | 1,8 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_spec log-mel (40 dải) | 0 | 0 | 3680 | 0 | 62,8 | 68,3 | 0,4 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| dsp_spec pitch Kaldi (vùng làm việc PSRAM) | 0 | 0 | 155920 | 0 | 1980,2 | 2080,7 | 12,4 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |
| lang_vi lexicon_entry một lệnh ba vùng lúc nạp bộ lệnh | 0 | 0 | 0 | 0 | 1183,0 | 1188,0 | 7,4 | `contract-v1-496-g614c607-dirty` | 2026-09-30 |

Tổng nhân 1: trung bình 16,3%, đỉnh cộng dồn 20,6% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 50%, đỉnh một khung ≤ 80%); gồm dsp_afe chuỗi (hpf + stft x2 + balance + doa + trộn + ns + istft + vad + agc).
Tổng nhân 0: trung bình 12,8%, đỉnh cộng dồn 13,4% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 70%); gồm dsp_spec log-mel (40 dải), dsp_spec pitch Kaldi (vùng làm việc PSRAM).

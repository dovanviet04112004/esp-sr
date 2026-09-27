# Ngân sách chạy theo module

Bảng dưới do `tools/budget.py` ghi từ CSV của `test_apps/bench_*` (TỔNG QUAN V5.0.10, TASKS E5-T13).
Chỉ số từ profile `bench`; số từ `dev` không vào bảng này.

| Module | Nhân | RAM tĩnh B | Vùng `hot` B | Vùng `cold` B | µs trung bình | µs đỉnh | % khung 16 ms | Commit | Ngày |
|---|---|---|---|---|---|---|---|---|---|
| dsp_afe agc | 1 | 0 | 1200 | 0 | 258,3 | 262,0 | 1,6 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_afe agc chặn đỉnh mọi mẫu | 1 | 0 | 1200 | 0 | 341,8 | 357,7 | 2,1 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_afe balance | 1 | 0 | 2056 | 0 | 18,4 | 22,7 | 0,1 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_afe chuỗi (hpf + stft x2 + balance + trộn + istft + vad + agc) | 1 | 6244 | 44224 | 0 | 1099,9 | 1132,4 | 6,9 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_afe hpf (2 kênh) | 1 | 0 | 64 | 0 | 41,1 | 45,3 | 0,3 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_afe vad | 1 | 0 | 2640 | 0 | 138,0 | 175,3 | 0,9 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_spec istft | 1 | 28 | 8256 | 0 | 164,5 | 168,5 | 1,0 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_spec stft (2 kênh) | 1 | 28 | 14432 | 0 | 286,3 | 289,9 | 1,8 | `contract-v1-199-g17bdfac` | 2026-09-27 |
| dsp_spec log-mel (40 dải) | 0 | 0 | 3680 | 0 | 62,8 | 68,4 | 0,4 | `contract-v1-199-g17bdfac` | 2026-09-27 |

Tổng nhân 1: trung bình 6,9%, đỉnh cộng dồn 7,1% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 50%, đỉnh một khung ≤ 80%); gồm dsp_afe chuỗi (hpf + stft x2 + balance + trộn + istft + vad + agc).
Tổng nhân 0: trung bình 0,4%, đỉnh cộng dồn 0,4% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 70%); gồm dsp_spec log-mel (40 dải).

# Ngân sách chạy theo module

Bảng dưới do `tools/budget.py` ghi từ CSV của `test_apps/bench_*` (TỔNG QUAN V5.0.10, TASKS E5-T13).
Chỉ số từ profile `bench`; số từ `dev` không vào bảng này.

| Module | Nhân | RAM tĩnh B | Vùng `hot` B | Vùng `cold` B | µs trung bình | µs đỉnh | % khung 16 ms | Commit | Ngày |
|---|---|---|---|---|---|---|---|---|---|
| dsp_afe balance | 1 | 0 | 2056 | 0 | 18,4 | 22,7 | 0,1 | `contract-v1-139-g4594e30` | 2026-09-27 |
| dsp_afe chuỗi (hpf + stft x2 + balance + trộn + istft) | 1 | 6244 | 40368 | 0 | 725,8 | 727,5 | 4,5 | `contract-v1-139-g4594e30` | 2026-09-27 |
| dsp_afe hpf (2 kênh) | 1 | 0 | 64 | 0 | 41,1 | 45,3 | 0,3 | `contract-v1-139-g4594e30` | 2026-09-27 |
| dsp_spec istft | 1 | 28 | 8256 | 0 | 164,5 | 168,5 | 1,0 | `contract-v1-139-g4594e30` | 2026-09-27 |
| dsp_spec stft (2 kênh) | 1 | 28 | 14432 | 0 | 286,3 | 289,7 | 1,8 | `contract-v1-139-g4594e30` | 2026-09-27 |
| dsp_spec log-mel (40 dải) | 0 | 0 | 3680 | 0 | 62,8 | 68,4 | 0,4 | `contract-v1-139-g4594e30` | 2026-09-27 |

Tổng nhân 1: trung bình 4,5%, đỉnh cộng dồn 4,5% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 50%, đỉnh một khung ≤ 80%); gồm dsp_afe chuỗi (hpf + stft x2 + balance + trộn + istft).
Tổng nhân 0: trung bình 0,4%, đỉnh cộng dồn 0,4% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 70%); gồm dsp_spec log-mel (40 dải).

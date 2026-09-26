# Ngân sách chạy theo module

Bảng dưới do `tools/budget.py` ghi từ CSV của `test_apps/bench_*` (TỔNG QUAN V5.0.10, TASKS E5-T13).
Chỉ số từ profile `bench`; số từ `dev` không vào bảng này.

| Module | Nhân | RAM tĩnh B | Vùng `hot` B | Vùng `cold` B | µs trung bình | µs đỉnh | % khung 16 ms | Commit | Ngày |
|---|---|---|---|---|---|---|---|---|---|
| dsp_afe chuỗi (hpf + stft x2 + trộn + istft) | 1 | 6244 | 40368 | 0 | 695,7 | 697,6 | 4,3 | `contract-v1-124-ge07ef05` | 2026-09-26 |
| dsp_afe hpf (2 kênh) | 1 | 0 | 64 | 0 | 36,8 | 41,0 | 0,2 | `contract-v1-124-ge07ef05` | 2026-09-26 |
| dsp_spec istft | 1 | 28 | 8256 | 0 | 164,5 | 168,5 | 1,0 | `contract-v1-124-ge07ef05` | 2026-09-26 |
| dsp_spec stft (2 kênh) | 1 | 28 | 14432 | 0 | 286,3 | 289,7 | 1,8 | `contract-v1-124-ge07ef05` | 2026-09-26 |
| dsp_spec log-mel (40 dải) | 0 | 0 | 3680 | 0 | 62,8 | 68,4 | 0,4 | `contract-v1-124-ge07ef05` | 2026-09-26 |

Tổng nhân 1: trung bình 4,3%, đỉnh cộng dồn 4,4% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 50%, đỉnh một khung ≤ 80%); gồm dsp_afe chuỗi (hpf + stft x2 + trộn + istft).
Tổng nhân 0: trung bình 0,4%, đỉnh cộng dồn 0,4% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 70%); gồm dsp_spec log-mel (40 dải).

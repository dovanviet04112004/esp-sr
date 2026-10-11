# Ngân sách chạy theo module

Bảng dưới do `tools/budget.py` ghi từ CSV của `test_apps/bench_*` (TỔNG QUAN V5.0.10, TASKS E5-T13).
Chỉ số từ profile `bench`; số từ `dev` không vào bảng này.

| Module | Nhân | RAM tĩnh B | Vùng `hot` B | Vùng `cold` B | µs trung bình | µs đỉnh | % khung 16 ms | Commit | Ngày |
|---|---|---|---|---|---|---|---|---|---|
| dsp_afe agc | 1 | 0 | 1216 | 0 | 257,2 | 261,1 | 1,6 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe agc chặn đỉnh mọi mẫu | 1 | 0 | 1216 | 0 | 340,8 | 356,6 | 2,1 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe balance | 1 | 0 | 2056 | 0 | 18,4 | 22,7 | 0,1 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe chuỗi (hpf + stft x2 + balance + doa + trộn + ns + istft + vad + agc) | 1 | 0 | 85424 | 0 | 2601,8 | 3284,6 | 16,3 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe doa gộp phổ chéo | 1 | 0 | 4304 | 0 | 22,1 | 26,4 | 0,1 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe doa gộp và dò lưới | 1 | 0 | 4304 | 0 | 640,9 | 642,6 | 4,0 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe gsc học | 1 | 0 | 4160 | 0 | 140,5 | 144,2 | 0,9 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe gsc học và đổi góc lái mỗi bước | 1 | 0 | 4160 | 0 | 485,4 | 492,3 | 3,0 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe hpf (2 kênh) | 1 | 0 | 64 | 0 | 41,1 | 45,3 | 0,3 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe ns_omlsa | 1 | 0 | 33280 | 0 | 1687,6 | 1875,4 | 10,5 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_afe vad | 1 | 0 | 2640 | 0 | 138,0 | 175,3 | 0,9 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_spec istft | 1 | 0 | 11856 | 0 | 158,0 | 162,0 | 1,0 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_spec stft (2 kênh) | 1 | 0 | 18032 | 0 | 283,7 | 287,0 | 1,8 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_spec log-mel (80 dải) | 0 | 0 | 3680 | 0 | 87,5 | 92,8 | 0,5 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| dsp_spec pitch Kaldi (vùng làm việc PSRAM) | 0 | 0 | 158496 | 0 | 2101,6 | 2203,3 | 13,1 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |
| lang_vi lexicon_entry một lệnh ba vùng lúc nạp bộ lệnh | 0 | 0 | 0 | 0 | 1183,0 | 1188,1 | 7,4 | `contract-v1-1100-gf277696-dirty` | 2026-10-08 |

Tổng nhân 1: trung bình 16,3%, đỉnh cộng dồn 20,5% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 50%, đỉnh một khung ≤ 80%); gồm dsp_afe chuỗi (hpf + stft x2 + balance + doa + trộn + ns + istft + vad + agc).
Tổng nhân 0: trung bình 13,7%, đỉnh cộng dồn 14,4% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 70%); gồm dsp_spec log-mel (80 dải), dsp_spec pitch Kaldi (vùng làm việc PSRAM).

## Việc theo lệnh, không theo khung

Việc chạy một lần mỗi lệnh hay lúc nạp, không nằm trong nhịp 16 ms nên không vào bảng trên. Số đo trên board B,
profile `bench`, ghi tay từ `measurements/latency.md` §27.

| Việc | Nhân | Khi nào | Thời gian | RAM nội | PSRAM | Commit | Ngày |
|---|---|---|---|---|---|---|---|
| `speaker`: một lần xác minh, `spk_fbank` + b0 + chấm với 8 mẫu giọng (`xac_task`) | 0 | mỗi lệnh được nhận, khi kiểm người nói bật | 1257,0 ms trung bình, 1257,5 ms đỉnh | 0 B thêm | 0 B thêm | `ec612f9` | 2026-10-11 |
| trong đó `spk_fbank` một cửa sổ 1,5 s | 0 | như trên | 79,3 ms | 0 B thêm | 0 B thêm | `ec612f9` | 2026-10-11 |
| trong đó mạng b0, trọng số đọc thẳng từ flash | 0 | như trên | 1174,6 ms | 0 B thêm | 0 B thêm | `1fb1725` | 2026-10-11 |
| `speaker` lúc nạp: dựng b0 cùng một lần chạy thử | 0 | lúc boot | 1784 ms | ~1,2 KB, bảng `exp` của Softmax | 2 134 924 B tensor | `46592aa` | 2026-10-11 |
| `speaker` lúc nạp: `spk_fbank_init` | 0 | lúc boot | ~141 ms | 0 | ~12 KB vùng làm việc 🔬 | `6e9b719` | 2026-10-11 |

- "0 B thêm" là heap còn trống trước và sau lần xác minh đầu tiên sau nạp; ca chuỗi đỏ nếu lệch một byte.
- Một lần chạy b0 giữ nhân 0 ~1,26 s, ở ưu tiên 3 dưới mọi task theo nhịp khung và theo mạng (KẾ HOẠCH §5.2).

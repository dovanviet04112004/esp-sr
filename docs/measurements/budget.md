# Ngân sách chạy theo module

Bảng dưới do `tools/budget.py` ghi từ CSV của `test_apps/bench_*` (TỔNG QUAN V5.0.10, TASKS E5-T13).
Chỉ số từ profile `bench`; số từ `dev` không vào bảng này.

| Module | Nhân | RAM tĩnh B | Vùng `hot` B | Vùng `cold` B | µs trung bình | µs đỉnh | % khung 16 ms | Commit | Ngày |
|---|---|---|---|---|---|---|---|---|---|
| dsp_afe agc | 1 | 0 | 1216 | 0 | 257,2 | 261,2 | 1,6 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe agc chặn đỉnh mọi mẫu | 1 | 0 | 1216 | 0 | 340,8 | 356,6 | 2,1 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe balance | 1 | 0 | 2056 | 0 | 18,4 | 22,7 | 0,1 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe bss LEARN và lối ra trễ 4 s của bss_live | 1 | 0 | 4768 | 770000 | 287,1 | 456,8 | 1,8 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe bss LEARN, hai người đổi lượt | 1 | 0 | 4288 | 0 | 86,1 | 254,8 | 0,5 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe bss chia ở chính diện | 1 | 0 | 4288 | 0 | 25,4 | 30,0 | 0,2 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe chuỗi (hpf + stft x2 + balance + doa + trộn + ns + istft + vad + agc) | 1 | 0 | 86464 | 0 | 2606,2 | 3291,1 | 16,3 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe doa gộp phổ chéo | 1 | 0 | 4304 | 0 | 22,1 | 26,4 | 0,1 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe doa gộp và dò lưới | 1 | 0 | 4304 | 0 | 640,9 | 642,6 | 4,0 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe gsc học | 1 | 0 | 4160 | 0 | 140,5 | 144,2 | 0,9 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe gsc học và đổi góc lái mỗi bước | 1 | 0 | 4160 | 0 | 485,4 | 492,3 | 3,0 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe hpf (2 kênh) | 1 | 0 | 64 | 0 | 41,1 | 45,3 | 0,3 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe ns_omlsa | 1 | 0 | 33280 | 0 | 1687,6 | 1875,4 | 10,5 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_afe vad | 1 | 0 | 2640 | 0 | 138,0 | 175,2 | 0,9 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_spec istft | 1 | 0 | 11856 | 0 | 158,0 | 162,0 | 1,0 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_spec stft (2 kênh) | 1 | 0 | 18032 | 0 | 283,7 | 287,0 | 1,8 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_spec log-mel (80 dải) | 0 | 0 | 3680 | 0 | 87,7 | 93,3 | 0,5 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_spec pitch Kaldi (vùng làm việc PSRAM) | 0 | 0 | 158496 | 0 | 2101,9 | 2204,1 | 13,1 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| dsp_spec spk_fbank (72 băng) một cửa sổ speaker | 0 | 0 | 0 | 59568 | 79147,2 | 79149,9 | 494,7 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |
| lang_vi lexicon_entry một lệnh ba vùng lúc nạp bộ lệnh | 0 | 0 | 0 | 0 | 1183,0 | 1188,0 | 7,4 | `contract-v1-1519-gd708e4e-dirty` | 2026-10-20 |

Tổng nhân 1: trung bình 16,3%, đỉnh cộng dồn 20,6% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 50%, đỉnh một khung ≤ 80%); gồm dsp_afe chuỗi (hpf + stft x2 + balance + doa + trộn + ns + istft + vad + agc).
Tổng nhân 0: trung bình 13,7%, đỉnh cộng dồn 14,4% một khung (mục tiêu KẾ HOẠCH §5.6: trung bình ≤ 70%); gồm dsp_spec log-mel (80 dải), dsp_spec pitch Kaldi (vùng làm việc PSRAM).

## Việc theo lệnh, không theo khung

Việc chạy một lần mỗi lệnh hay lúc nạp, không nằm trong nhịp 16 ms nên không vào bảng trên. Số đo trên board B,
profile `bench`, ghi tay từ `measurements/latency.md` §27.

| Việc | Nhân | Khi nào | Thời gian | RAM nội | PSRAM | Commit | Ngày |
|---|---|---|---|---|---|---|---|
| `speaker`: một lần xác minh, `spk_fbank` + b0 + chấm Z-norm với 8 mẫu giọng (`xac_task`) | 0 | mỗi lệnh được nhận, khi kiểm người nói bật | 1256,7 ms trung bình, 1257,9 ms đỉnh | 0 B thêm | 0 B thêm | `7c77692` | 2026-10-11 |
| trong đó `spk_fbank` một cửa sổ 1,5 s | 0 | như trên | 79,3 ms | 0 B thêm | 0 B thêm | `7c77692` | 2026-10-11 |
| trong đó mạng b0, trọng số đọc thẳng từ flash | 0 | như trên | 1173,7 ms | 0 B thêm | 0 B thêm | `7c77692` | 2026-10-11 |
| `speaker` lúc nạp: dựng b0 cùng một lần chạy thử | 0 | lúc boot | 1783 ms | 0 | 2 134 920 B: tensor, bảng `exp` của Softmax | `7c77692` | 2026-10-11 |
| `speaker` lúc nạp: `spk_fbank_init` | 0 | lúc boot | ~141 ms | 0 | 59 568 B vùng làm việc, đo bằng `bench_afe` | `6521d03` | 2026-10-11 |
| `svc_listen_hold`: chép 1,5 s cuối cửa sổ sang ô xác minh, cộng hai tổng bình phương của cửa sổ lệnh (`nhan_task`) | 0 | mỗi lệnh được nhận, khi kiểm người nói bật | 3,66 ms trung bình, 4,47 ms đỉnh, 170 lần | 0 B thêm | 0 B thêm | `898a1ba` | 2026-10-13 |

- "0 B thêm" là heap còn trống trước và sau lần xác minh đầu tiên sau nạp; ca chuỗi đỏ nếu lệch một byte.
- Một lần chạy b0 giữ nhân 0 ~1,26 s, ở ưu tiên 3 dưới mọi task theo nhịp khung và theo mạng (KẾ HOẠCH §5.2).

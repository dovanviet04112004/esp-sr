# Split `ns/v1`

Dựng bằng `python -m srpipe.tasks.ns.data split` từ `configs/models/ns.yaml` (KẾ HOẠCH §3.9, ADR-0014).

- Tiếng sạch `train`: mọi mẩu qua luật sạch của `split.train` và dải tần ≥ 7000 Hz đo ở bước `clean`, không trần giờ; người nói của `val` và `test` không vào; Common Voice không vào.
- `val`, `test`: các dòng của `command/v1` qua khoảng động ≥ 30 dB và cùng luật dải tần.
- Nhiễu và nền phòng: mỗi bể một luật nhóm; nhóm chia vai theo seed 20260930, DEMAND theo môi trường; không nhóm nào ở hai vai.

| File | Loại | Dòng | Giờ | Người nói |
|---|---|---|---|---|
| train.txt | speech | 150614 | 135.10 | 41 |
| train.txt | noise | 59845 | 201.44 | 0 |
| train.txt | rir | 52 | 0.42 | 0 |
| val.txt | speech | 1520 | 1.81 | 25 |
| val.txt | noise | 3446 | 14.56 | 0 |
| val.txt | rir | 20 | 0.17 | 0 |
| test.txt | speech | 1780 | 1.74 | 57 |
| test.txt | noise | 3470 | 13.45 | 0 |
| test.txt | rir | 20 | 0.17 | 0 |

- train.txt: be481e7723f0801819de7747ca8eed44bbb96ed865be2867248c19983fe5e6ab
- val.txt: 409599f416014ba448af1e77dbbb995f8af599fdd755742f2f331d6eaa7918fd
- test.txt: 0305590de0a84a57116052340b984eb935839f4d20df680a3db45960061ce2d5

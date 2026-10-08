# ADR-0006 — `dsp_afe` dựng với `-ffp-contract=off`; parity dựng như bản chạy thật

- **Trạng thái**: Chấp nhận; ADR-0020 đưa `dsp_spec` vào cùng luật
- **Ngày**: 2026-09-27
- **Liên quan**: KẾ HOẠCH §3.3, §3.14, §4.5.7, §4.5.8; TASKS E7-T1, E7-T2; ADR-0004, ADR-0005; `docs/measurements/latency.md` §6, `parity.md`

---

## Bối cảnh

Bản Python soi gương của `dsp_afe` làm từng phép float32 theo đúng thứ tự của bản C, mỗi phép làm tròn một lần như numpy.
Ở `-O2` (profile `bench` và `prod`), GCC cho Xtensa gộp `a * b + c` thành lệnh `madd.s` / `msub.s` của S3, và lệnh ấy chỉ
làm tròn một lần cho cả phép nhân lẫn phép cộng. Kết quả vì thế khác bản Python ở bit cuối. App parity lại dựng ở `-Og`,
nơi GCC không gộp, nên parity báo "khớp từng bit" cho một bản build không phải bản chạy thật.

Đo trên board B, profile `bench`: `hpf` gộp lệch khỏi bản không gộp ở 99,7% số mẫu, tối đa 2,0e-6, **vượt ngưỡng 1e-6
của chính nó**; `balance` gộp lệch 1,9e-6 ở 25% số vạch. Máy tính x86-64 không gộp vì không bật FMA; máy aarch64 thì có.

## Các phương án

| Phương án | Được | Mất | Số đo (hai kênh `hpf` + 257 vạch `balance`) |
|---|---|---|---|
| Giữ gộp nhân-cộng, nới ngưỡng | nhanh hơn ~11%; mỗi phép gộp làm tròn ít hơn một lần | C khác Python ở bit cuối, khác nhau giữa máy và mức tối ưu; ở `aec`, `bss`, `ns` (đệ quy, thích nghi) sai lệch tích lại theo thời gian, ngưỡng khó đặt và che lỗi thật | 36,8 + 16,2 = 53,0 µs |
| **Tắt gộp cho `dsp_afe`**, parity dựng bằng cờ của `bench` | C khớp Python từng bit ở mọi profile, trên mọi máy; parity kiểm đúng mã chạy thật | chậm hơn ~11% ở phần tính của module | 40,9 + 18,4 = 59,3 µs, tức thêm 6,3 µs = 0,04% một bước 16 ms |

## Quyết định

`dsp_afe` dựng với `-ffp-contract=off`, trên board và trong bản dựng máy tính. App parity lấy cờ trình biên dịch từ
`sdkconfig.bench`, giống `prod`. Nhân 1 đang dùng 4,3% một bước so với trần 50% (KẾ HOẠCH §5.6); 0,04% đổi lấy việc
bản C là đúng bản Python ở mọi nơi là rẻ.

## Hệ quả

- `hpf` từ ~37 µs lên ~41 µs, `balance` ~18 µs; `budget.md` đo lại.
- Ngưỡng của `hpf` và `balance` chặt hơn độ lệch của phép gộp (1e-6 so với 2,0e-6 và 1,5e-5 trên bộ vàng): bản build
  nào lỡ gộp thì parity đỏ.
- `dsp_spec` không đổi: `dl_fft` là mã hợp ngữ của thư viện, vốn không khớp numpy từng bit, và ngưỡng của nó đã tính phần ấy.
- Xét lại khi: nhân 1 chật, hoặc một module có phép tính mà gộp nhân-cộng cải thiện độ chính xác đo được bằng thước của
  bộ nhận dạng (KẾ HOẠCH §3.15).

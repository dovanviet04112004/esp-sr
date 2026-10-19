# `bss` (E8-T3)

`bss` là cổng hướng theo ghế của KẾ HOẠCH §3.8, dựng theo module `separation` của `micarray` trong `tinyai-signal`
(commit `642e6e4`). Mọi số dưới đây đo ngày 19/10 trên bản Python soi gương.

**Thước** của mục này:
- **mất**: phần năng lượng của người được giữ mà cổng làm mất;
- **rò**: phần năng lượng của người kia lọt vào luồng của người được giữ, tính cả bài, và tính lại từ 0,5 s sau mỗi lần
  đổi người nói.

Cả hai đều so với chính đầu vào của người ấy, theo dB. Cổng là một hệ số cho cả bước, nên hai số tính đúng, không phải
ước.

## 1. Vì sao bỏ AuxIVA

Vật liệu là **bản ghép A/B**:
- lấy hai bản thu một người của board B trong `raw/device/tinyai`: `A_trai_1` và `B_phai_1`, mỗi người 15 s, cách 1 m;
- cắt thành lượt 5 s rồi xếp xen kẽ, thành 30 s có nhãn từng bước.

AuxIVA online (bản dở dang ngày 28/09, không có chốt ổn định nào) chạy trên bản ghép ấy:

| Quên τ | Mất A / B | Rò sang A / B |
|---|---|---|
| 1 s | −0,9 / −1,4 dB | −1,4 / −0,9 dB |
| 3 s | −0,7 / −2,1 dB | −2,1 / −0,7 dB |

- Hai luồng ra gần như vẫn là trộn: AuxIVA không tách được.
- Mấy giây đầu số vỡ, năng lượng lối ra lên +87 dB so với đầu vào.
- Lý do: hai người nói lần lượt, mỗi lúc chỉ có một nguồn, nên ma trận hiệp phương sai suy biến.

## 2. Lấy hướng từ đâu

Độ trễ giữa hai micro (mẫu, τ_max = 2,10) khi mỗi người nói một mình trên bản ghép A/B:

| Nguồn hướng | A: trung vị (p10 / p90) | B: trung vị (p10 / p90) |
|---|---|---|
| Bộ ước trễ của `tinyai-signal`: 300–3 800 Hz, cửa sổ 0,4 s, chuyển sang bước 16 ms | −0,80 (−1,48 / −0,12) | +0,56 (−0,17 / +1,00) |
| `doa` của chuỗi: 2–8 kHz, dò lưới 2° | +1,40 (+1,17 / +1,59) | −1,35 (−1,46 / −1,23) |

Hai bộ ước đặt quy ước dấu ngược nhau. Bộ ước của `tinyai-signal` cho hai người chồng lên nhau quanh 0. `doa` cho hai
cụm tách hẳn, khớp với đo ở E8-T1 rằng dải thấp bị tiếng vang kéo về chính diện.

Cổng chia ở chính diện, bản thử đầu trên bản ghép A/B: rò sang A / B là −10,6 / −13,8 dB với bộ ước của `tinyai-signal`,
−11,9 / −18,1 dB với `doa`.

Độ nhanh của `doa`:

| `doa` làm trơn, dò mỗi | Rò cả bài A / B | Rò từ 0,5 s sau đổi lượt A / B |
|---|---|---|
| **0,2 s, 2 bước (đang dùng)** | −11,9 / −18,1 dB | **−300 / −231 dB** |
| 0,1 s, 1 bước | −12,6 / −19,7 dB | −19,5 / −29,0 dB |
| 0,05 s, 1 bước | −11,7 / −12,3 dB | −14,5 / −12,7 dB |

Gần như mọi phần rò nằm trong 0,5 s đầu mỗi lượt, lúc `doa` còn bám người trước. Bắt `doa` bám nhanh hơn không gỡ
được phần ấy, mà lại làm rò lúc ổn định tăng lên.

## 3. Hai chỗ hỏng tìm ra khi dựng

**Cổng tự khoá.** `doa` chỉ dò khi `vad` của bước trước báo có tiếng, mà `vad` nghe lối ra đã qua cổng. Cổng vừa dìm
ghế kia là `vad` tắt, `doa` thôi dò, cổng kẹt luôn. Trên bản ghép A/B, hai luồng không tách được gì. Trên bản thu luân
phiên thật, lối ra mất 13,4 dB.

Sửa: dưới `bss`, một `vad` thứ hai trên trung bình hai micro sau `hpf` báo cho `doa` lúc có tiếng (KẾ HOẠCH §3.8, §3.10).
Độ tin của `doa` không thay được `vad` này:

| Bản thu | Độ tin p10 / p50 / p90 khi có tiếng | Khi im |
|---|---|---|
| Bản ghép A/B | 35 / 47 / 58 | 31 / 44 / 57 |
| Luân phiên 30 s | 37 / 46 / 55 | 30 / 40 / 56 |

**Ghế ma ở chính diện.** Ngay lúc khởi động, phần kéo dài của `vad` báo có tiếng 0,26 s, trong khi quyết định thô của
nó là không và đầu vào chỉ có −53 đến −62 dBFS. Lúc đó `doa` đọc nhiễu điện chung của hai micro (trễ 0) ra 88°, độ tin
112–125. LEARN biến chỗ ấy thành ghế thứ hai ở giây 3,6, khi mới chỉ có A nói.

Sửa: `doa` dò theo quyết định **thô** của `vad` đầu vào, không theo quyết định có phần kéo dài.

Luật giữ "nhiều lần dò nhất", đếm trong cửa sổ, đổi ghế giữ theo từng lượt nói (giữ A: mất −2,7 dB, rò −0,8 dB). Đếm dồn
từ lúc nhận ghế thì hết.

## 4. Kết quả

`make eval-bss` ở `dc92e9d`, 40 s trên 8 tiến trình. Mỗi ô: mất / rò cả bài / rò từ 0,5 s sau đổi lượt / thời điểm
LEARN tách (giây, và số mục tách được trên số mục).

**Bản ghép A/B của board B**, mỗi người được giữ một lượt:

| Giữ | Chia ở chính diện | LEARN |
|---|---|---|
| A (~48°) | −0,03 / −14,8 / −332 dB / — | −0,03 / −14,8 / −325 dB / 9,7 s (1/1) |
| B (~130°) | −0,17 / −23,3 / −126 dB / — | −0,17 / −23,3 / −126 dB / 9,7 s (1/1) |

**Cảnh dựng `standard`**:
- lấy 96 cảnh có người gây nhiễu là người nói;
- mỗi cảnh: ảnh người nói 8 s, rồi ảnh người gây nhiễu 8 s;
- giữ người nói ở góc trên nhãn của cảnh; trung bình trên các cảnh.

| RT60, vị trí hai người | Chia ở chính diện | LEARN |
|---|---|---|
| 0,2 s, hai phía (31) | 0,00 / −26,1 / −176 dB / — | 0,00 / −26,1 / −171 dB / 10,1 s (31/31) |
| 0,4 s, hai phía (31) | 0,00 / −26,1 / −148 dB / — | −0,01 / −25,5 / −133 dB / 10,1 s (31/31) |
| 0,6 s, hai phía (29) | −0,02 / −27,0 / −150 dB / — | −0,02 / −27,0 / −151 dB / 10,2 s (29/29) |
| 0,2 s, một phía (1) | 0,00 / −0,1 / −0,1 dB / — | 0,00 / −2,2 / −2,2 dB / 10,4 s |
| 0,4 s, một phía (1) | −0,01 / 0,0 / 0,0 dB / — | −0,01 / −3,2 / −3,4 dB / 11,0 s |
| 0,6 s, một phía (3) | 0,00 / −0,2 / −0,1 dB / — | 0,00 / −5,0 / −5,0 dB / 10,8 s (3/3) |

- **Hai người ở hai phía:** cổng gần như không làm mất gì của người được giữ. Rò sau 0,5 s từ lúc đổi lượt dưới
  −125 dB ở mọi RT60, tức người kia bị chặn hết. Rò cả bài −15 đến −27 dB nằm ở đầu mỗi lượt (§2).
- **Hai người cùng một phía:** chia ở chính diện không tách được, đúng như KẾ HOẠCH đã ghi. LEARN tách được, nhưng chỉ sau
  khi người thứ hai nói khoảng 2–3 s: người gây nhiễu bắt đầu ở giây 8, ghế thứ hai có ở giây 10,4–11,0. Trước đó người
  ấy lọt qua, nên số cả bài chỉ còn −2 đến −5 dB.
- Ở mọi cảnh, LEARN tách khoảng 2 s sau khi người thứ hai mở lời. `tinyai-signal` cần 10,4 s trên bộ dựng của nó, 22,4–37,4 s
  trên bản thu người thật.
- Trên bản ghép A/B, LEARN tách muộn hơn, ở giây 9,7. Hai người nối lời không nghỉ, nên lúc `doa` quét từ 48° sang
  130° có những lần dò rơi vào giữa, làm phép kiểm độ trũng chưa qua ngay. Trong lúc chờ, LEARN cắt như chế độ chia ở
  chính diện.

## 5. Bản thu thật, không nhãn

LEARN, mặc định của `contracts/afe.yaml`:

| Bản thu | Tách sau | Hai ghế cuối cùng |
|---|---|---|
| Hai người luân phiên 30 s | 2,8 s | 137° và 63° |
| Hai người nói đè 10 s | 4,1 s | 135° và 57° |
| Giọng cùng loa nhạc 9,6 s | 3,6 s | 134° (giọng) và 12° (nhạc) |

Ghế khớp với bản đồ góc của `doa` trên chính các bản thu ấy. Lúc nói đè, mỗi bước cổng giữ một phía, không tách được
hai giọng trong cùng một bước (KẾ HOẠCH §3.8, giới hạn).

## 6. Khớp với bản C

`make parity-host`, bộ vàng `contracts/golden/bss/`:
- 4 ca khớp từng bit: cổng, số ghế, hướng sau mọi bước, và hai lối ra của bước cuối, đều `max_abs 0`;
- ca đối chứng âm mang cổng của chế độ chia ở chính diện và báo đỏ: cổng lệch tới 1, SNR 5,9 dB.

Trên board B: chưa chạy 🔬.

## 7. Chi phí

Chưa đo trên board 🔬. Mỗi bước chỉ nhân 257 vạch với một hệ số; `vad` thứ hai cùng giá với `vad` chính. Mỗi
`recheck_s`, LEARN phân cụm trên histogram 181 ô.

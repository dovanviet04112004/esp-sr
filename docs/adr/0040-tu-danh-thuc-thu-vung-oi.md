# ADR-0040 — Từ đánh thức thử "vừng ơi", mẫu dương đọc bằng KhanhTTS-OmniVoice

- **Trạng thái**: Chấp nhận để thử (chủ repo 31/10: "train thử wake vừng ơi", sau khi nghe mẫu OmniVoice: "nghe ok").
  Điểm 3 thay bởi ADR-0043 (chủ repo, 2026-11-03)
- **Ngày**: 2026-10-31
- **Liên quan**: KẾ HOẠCH §1.2, §3.11; ADR-0011, ADR-0033; TASKS E11-T41; `docs/measurements/wake.md` §2, §3,
  `docs/measurements/tts_engines.md`

---

## Bối cảnh

Sản phẩm thức bằng "happy" (ADR-0033), một từ tiếng Anh. Chủ repo muốn một từ tiếng Việt, chọn "vừng ơi" trong hai cụm
nghe thử, và bỏ "Mi Na".

Không có kho giọng thật nào nói "vừng ơi" đứng riêng (`wake.md` §7, §9). Lần học wake từ tiếng tổng hợp trước đây dùng
VieNeu-TTS và F5-TTS cho "chào mi na" và không bắt được giọng thật: 0/60 câu trên board (`wake.md` §2, §3).

Ngày 31/10, mẫu thử của KhanhTTS-OmniVoice được chủ repo nghe và nhận. Bộ này là OmniVoice của k2-fsa tinh chỉnh trên
~1 500 giờ tiếng Việt và tiếng Anh, Apache 2.0, nhân bản giọng từ một mẩu 3–10 s. Theo bảng của chính tác giả, nó đọc
đúng chữ hơn VieNeu v3 Turbo (WER 3,75% so với 5,5%) và giữ giọng nhân bản giống hơn (0,888 so với 0,778).

## Các phương án

| Phương án | Được | Mất |
|---|---|---|
| Chờ bản thu thật của nhiều người | dữ liệu đúng nhất | chưa có người đọc, chưa có phiếu |
| VieNeu, F5 như "chào mi na" | đường đã có | đã trượt trên giọng thật |
| **KhanhTTS-OmniVoice nhân bản nhiều giọng học, thử trên board với giọng chủ repo** | chủ repo nghe nhận; đường cũ dùng lại được | vẫn là tiếng tổng hợp; phải thử thật mới biết |
| G-OmniVoice | WER 2,6% theo tác giả | cổng của Hugging Face cần chủ repo đồng ý chia thông tin |

## Quyết định

1. **Bộ TTS `omnivoice`**: `ml/tts/omnivoice/`, một dự án uv riêng như VieNeu và F5, ghim
   `kjanh/KhanhTTS-OmniVoice` theo commit. Bộ ấy đọc tiếng Việt, ra 24 kHz, float16 trên GPU. Nó nhận giọng nhân bản (mẩu
   tham chiếu và chữ của nó) và giọng thiết kế (giới, tuổi, độ cao). File pickle của repo không tải.
2. **Từ thử "vừng ơi"**, split `wake/vung1`, theo đường của "trợ lý" ở KẾ HOẠCH §3.11:
   - mẫu dương là `synth positives` của `omnivoice`, nhân bản từ các giọng học (KẾ HOẠCH §1.3) và 24 giọng thiết kế;
   - âm bản gần âm của `synth negatives` và cụm "{x} ơi" với phụ âm đầu gần "v" của `synth hard` cũng đọc bằng các
     giọng ấy; âm bản khó thêm câu thật của kho học có "ơi" hay "vừng";
   - không mẩu nào qua PhoWhisper (`synth.heard_back` tắt): chủ repo đã nghe mẫu và nhận, và bộ nghe ấy hay sai trên
     cụm ngắn đứng riêng (`tts_engines.md` §2). Mọi mẩu được giữ, trừ mẩu trùng byte;
   - âm bản học, `val_neg`, `test_neg` là lời nói thật như `happy1`.
3. **Thử thật trước khi nghĩ tới sản phẩm.** Model chỉ vào board khi chủ repo nói "vừng ơi" qua board và nó bắt được nhiều
   hơn "happy". "happy" vẫn là từ của sản phẩm tới lúc ấy.

## Hệ quả

- Mẫu dương toàn tổng hợp là giới hạn đã biết (`wake.md` §3). Phiên chủ repo nói "vừng ơi" qua board là phép đo quyết
  định 🔬.
- "vừng" là từ ít gặp trong lời nói, "ơi" rất hay gặp. Âm bản gần âm gồm "ơi" đứng riêng, "vừng" đứng riêng và các cụm cách
  một thành phần âm tiết.

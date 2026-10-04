// GENERATED FILE - DO NOT EDIT.
// Source: contracts/listen.yaml
// Regenerate: python3 tools/gen_contracts.py

#pragma once

#define GEN_LISTEN_VERSION 3
#define GEN_LISTEN_N_BANDS 80
#define GEN_LISTEN_MEL_CONFIG {.n_bands = 80, .f_min_hz = 20.0f, .f_max_hz = 7600.0f, .log_floor = 1e-06f}       // dsp_spec_mel_config_t
#define GEN_LISTEN_PITCH_CONFIG {.resample_hz = 4000.0f, .lowpass_cutoff_hz = 1000.0f, .lowpass_zeros = 1, .upsample_zeros = 5, .window_s = 0.025f, .min_f0_hz = 50.0f, .max_f0_hz = 400.0f, .soft_min_f0 = 10.0f, .penalty_factor = 0.1f, .delta_pitch = 0.005f, .nccf_ballast = 7000.0f, .normalization_left_s = 0.75f, .delta_window = 2, .pov_scale = 2.0f, .pitch_scale = 2.0f, .delta_pitch_scale = 10.0f}       // dsp_spec_pitch_config_t
#define GEN_LISTEN_UTTERANCE_GAP_HOPS 25
#define GEN_LISTEN_UTTERANCE_MIN_HOPS 16
#define GEN_LISTEN_UTTERANCE_LEAD_HOPS 78
#define GEN_LISTEN_WINDOW_HOPS 188

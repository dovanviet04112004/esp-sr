# GENERATED FILE - DO NOT EDIT.
# Source: contracts/listen.yaml
# Regenerate: python3 tools/gen_contracts.py

VERSION = 5
HASH = 0x354b1ae6
FEATURES = {'n_bands': 80, 'f_min_hz': 20.0, 'f_max_hz': 7600.0, 'log_floor': 1e-06}
PITCH = {'resample_hz': 4000.0,
 'lowpass_cutoff_hz': 1000.0,
 'lowpass_zeros': 1,
 'upsample_zeros': 5,
 'window_s': 0.025,
 'min_f0_hz': 50.0,
 'max_f0_hz': 400.0,
 'soft_min_f0': 10.0,
 'penalty_factor': 0.1,
 'delta_pitch': 0.005,
 'nccf_ballast': 7000.0,
 'ballast_window_s': 2.0,
 'normalization_left_s': 0.75,
 'delta_window': 2,
 'pov_scale': 2.0,
 'pitch_scale': 2.0,
 'delta_pitch_scale': 10.0}
N_BANDS = 80
UTTERANCE_GAP_S = 0.4
UTTERANCE_MIN_S = 0.25
UTTERANCE_LEAD_S = 2.0
UTTERANCE_GAP_HOPS = 25
UTTERANCE_MIN_HOPS = 16
UTTERANCE_LEAD_HOPS = 125
WINDOW_S = 3.75
WINDOW_HOPS = 234

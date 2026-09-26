"""Scale-invariant signal-to-distortion ratio (Le Roux et al., 2019) for gsc, bss and ns (KEHOACH 3.15)."""

from __future__ import annotations

import numpy as np


def as_pair(estimate: np.ndarray, reference: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Both signals as float64 vectors of one length, or ValueError."""
    est = np.asarray(estimate, dtype=np.float64)
    ref = np.asarray(reference, dtype=np.float64)
    if est.ndim != 1 or est.shape != ref.shape:
        raise ValueError(f"need two 1-D signals of one length, got {est.shape} and {ref.shape}")
    return est, ref


def si_sdr_db(estimate: np.ndarray, reference: np.ndarray) -> float:
    """SI-SDR in dB after removing both means; +inf when the estimate is a scaled copy of the reference."""
    est, ref = as_pair(estimate, reference)
    est = est - est.mean()
    ref = ref - ref.mean()
    ref_power = float(ref @ ref)
    if ref_power == 0.0:
        raise ValueError("the reference is silent, SI-SDR is undefined")
    target = (float(est @ ref) / ref_power) * ref
    noise = est - target
    noise_power = float(noise @ noise)
    if noise_power == 0.0:
        return float("inf")
    return float(10.0 * np.log10(float(target @ target) / noise_power))


def si_sdr_improvement_db(estimate: np.ndarray, mixture: np.ndarray, reference: np.ndarray) -> float:
    """How much the block raised SI-SDR over its own input, in dB."""
    return si_sdr_db(estimate, reference) - si_sdr_db(mixture, reference)

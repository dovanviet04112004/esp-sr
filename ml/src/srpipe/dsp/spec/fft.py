"""Real FFT pair of dsp_spec/fft.h: unscaled forward, 1 / n inverse, float32 throughout (KEHOACH 3.14, ADR-0020).

Mirrors firmware/components/dsp_spec/src/fft.c operation for operation: n real points packed as n / 2 complex ones in
bit-reversed order, decimation in time by radix-4 passes (one radix-2 stage first when n / 2 is not a power of 4),
then the split into n / 2 + 1 bins, k and n / 2 - k together. The inverse merges the bins back, conjugates, runs the
same passes and conjugates again, scaled by 1 / n. Twiddles are cos and sin in double, rounded once.
"""

from __future__ import annotations

import functools
import math

import numba
import numpy as np

MIN_POINTS = 64
MAX_POINTS = 2048


def check_points(n_points: int) -> None:
    """Refuse the lengths dsp_spec_fft_workspace_bytes refuses: a power of two from 64 to 2048."""
    if not (MIN_POINTS <= n_points <= MAX_POINTS) or n_points & (n_points - 1):
        raise ValueError(f"{n_points} points is not a power of two in [{MIN_POINTS}, {MAX_POINTS}]")


@functools.cache
def tables(n_points: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """cos and sin of 2 pi k / n_points for k < 3 n_points / 4, each in double rounded once, and the bit reversal of
    n_points / 2 indices: what dsp_spec_fft_init builds."""
    check_points(n_points)
    angles = [2.0 * math.pi * k / n_points for k in range(3 * n_points // 4)]
    cosines = np.array([math.cos(a) for a in angles], dtype=np.float32)
    sines = np.array([math.sin(a) for a in angles], dtype=np.float32)
    half = n_points // 2
    bits = half.bit_length() - 1
    reversed_index = np.array([int(f"{k:0{bits}b}"[::-1], 2) for k in range(half)], dtype=np.int64)
    return cosines, sines, reversed_index


@numba.njit
def _butterflies(zr: np.ndarray, zi: np.ndarray, cosines: np.ndarray, sines: np.ndarray) -> None:
    m = len(zr)
    stages = 0
    while (1 << stages) < m:
        stages += 1
    h = 1
    if stages % 2:
        for a in range(0, m, 2):
            ar, ai, br, bi = zr[a], zi[a], zr[a + 1], zi[a + 1]
            zr[a] = ar + br
            zi[a] = ai + bi
            zr[a + 1] = ar - br
            zi[a + 1] = ai - bi
        h = 2
    while 4 * h <= m:
        step = 2 * m // (4 * h)
        for j in range(h):
            c1, s1 = cosines[j * step], sines[j * step]
            c2, s2 = cosines[2 * j * step], sines[2 * j * step]
            c3, s3 = cosines[3 * j * step], sines[3 * j * step]
            for a in range(j, m, 4 * h):
                ar, ai = zr[a], zi[a]
                br, bi = zr[a + h], zi[a + h]
                cr, ci = zr[a + 2 * h], zi[a + 2 * h]
                dr, di = zr[a + 3 * h], zi[a + 3 * h]
                if j == 0:
                    pr, pi_, qr, qi, rr, ri = cr, ci, br, bi, dr, di
                else:
                    pr = c1 * cr + s1 * ci
                    pi_ = c1 * ci - s1 * cr
                    qr = c2 * br + s2 * bi
                    qi = c2 * bi - s2 * br
                    rr = c3 * dr + s3 * di
                    ri = c3 * di - s3 * dr
                t0r = ar + qr
                t0i = ai + qi
                t1r = ar - qr
                t1i = ai - qi
                t2r = pr + rr
                t2i = pi_ + ri
                t3r = pr - rr
                t3i = pi_ - ri
                zr[a] = t0r + t2r
                zi[a] = t0i + t2i
                zr[a + h] = t1r + t3i
                zi[a + h] = t1i - t3r
                zr[a + 2 * h] = t0r - t2r
                zi[a + 2 * h] = t0i - t2i
                zr[a + 3 * h] = t1r - t3i
                zi[a + 3 * h] = t1i + t3r
        h *= 4


@numba.njit
def _forward(
    x: np.ndarray, cosines: np.ndarray, sines: np.ndarray, reversed_index: np.ndarray, re: np.ndarray, im: np.ndarray
) -> None:
    rows, n_points = x.shape
    m = n_points // 2
    zr = np.empty(m, dtype=np.float32)
    zi = np.empty(m, dtype=np.float32)
    half = np.float32(0.5)
    for r in range(rows):
        for k in range(m):
            zr[reversed_index[k]] = x[r, 2 * k]
            zi[reversed_index[k]] = x[r, 2 * k + 1]
        _butterflies(zr, zi, cosines, sines)
        re[r, 0] = zr[0] + zi[0]
        im[r, 0] = np.float32(0.0)
        re[r, m] = zr[0] - zi[0]
        im[r, m] = np.float32(0.0)
        for k in range(1, m // 2 + 1):
            j = m - k
            even_r = (zr[k] + zr[j]) * half
            even_i = (zi[k] - zi[j]) * half
            odd_r = (zi[k] + zi[j]) * half
            odd_i = (zr[j] - zr[k]) * half
            c, s = cosines[k], sines[k]
            qr = c * odd_r + s * odd_i
            qi = c * odd_i - s * odd_r
            re[r, k] = even_r + qr
            im[r, k] = even_i + qi
            if j != k:
                re[r, j] = even_r - qr
                im[r, j] = qi - even_i


@numba.njit
def _inverse(
    re: np.ndarray, im: np.ndarray, cosines: np.ndarray, sines: np.ndarray, reversed_index: np.ndarray, x: np.ndarray
) -> None:
    rows, n_bins = re.shape
    m = n_bins - 1
    zr = np.empty(m, dtype=np.float32)
    zi = np.empty(m, dtype=np.float32)
    scale = np.float32(1.0 / (2 * m))
    for r in range(rows):
        zr[0] = re[r, 0] + re[r, m]
        zi[0] = -(re[r, 0] - re[r, m])
        for k in range(1, m // 2 + 1):
            j = m - k
            sum_r = re[r, k] + re[r, j]
            sum_i = im[r, k] - im[r, j]
            diff_r = re[r, k] - re[r, j]
            diff_i = im[r, k] + im[r, j]
            c, s = cosines[k], sines[k]
            pr = c * diff_r - s * diff_i
            pi_ = s * diff_r + c * diff_i
            zr[reversed_index[k]] = sum_r - pi_
            zi[reversed_index[k]] = -(sum_i + pr)
            if j != k:
                zr[reversed_index[j]] = sum_r + pi_
                zi[reversed_index[j]] = -(pr - sum_i)
        _butterflies(zr, zi, cosines, sines)
        for k in range(m):
            x[r, 2 * k] = zr[k] * scale
            x[r, 2 * k + 1] = -(zi[k] * scale)


def forward(x: np.ndarray) -> np.ndarray:
    """Spectrum of n real samples into n / 2 + 1 complex64 bins, DC first, unscaled; over the last axis."""
    x = np.asarray(x, dtype=np.float32)
    n_points = x.shape[-1]
    check_points(n_points)
    rows = np.ascontiguousarray(x.reshape(-1, n_points))
    re = np.empty((len(rows), n_points // 2 + 1), dtype=np.float32)
    im = np.empty_like(re)
    _forward(rows, *tables(n_points), re, im)
    out = np.empty(re.shape, dtype=np.complex64)
    out.real = re
    out.imag = im
    return out.reshape(*x.shape[:-1], n_points // 2 + 1)


def inverse(bins: np.ndarray, n_points: int) -> np.ndarray:
    """Real float32 signal of n_points from n_points / 2 + 1 bins, scaled by 1 / n_points; over the last axis.

    The imaginary parts of the DC and Nyquist bins are ignored, as in dsp_spec_fft_inverse.
    """
    check_points(n_points)
    bins = np.asarray(bins, dtype=np.complex64)
    if bins.shape[-1] != n_points // 2 + 1:
        raise ValueError(f"{bins.shape[-1]} bins do not describe {n_points} points")
    rows = bins.reshape(-1, n_points // 2 + 1)
    x = np.empty((len(rows), n_points), dtype=np.float32)
    _inverse(np.ascontiguousarray(rows.real), np.ascontiguousarray(rows.imag), *tables(n_points), x)
    return x.reshape(*bins.shape[:-1], n_points)

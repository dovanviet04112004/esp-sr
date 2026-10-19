"""bss of dsp_afe: a gate on doa's direction over the mean of both microphones (KEHOACH 3.8).

Mirror of firmware/components/dsp_afe/src/bss.c in float32, after tinyai-signal's micarray separation: each counted doa
angle becomes a delay and sets the kept seat's gate, one factor for the whole mean; logistic by series in double.
Mode 0 splits two seats at broadside; mode 1 clusters the last decision_s of speech into up to seats_max seats.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from srpipe.dsp.afe.ns_omlsa import PI, cos_series, exp_series
from srpipe.generated import afe, array, grid

f32 = np.float32
ANGLES = 181  # every whole degree doa reports, 0 .. 180
SEATS_CAP = 4  # seats one separator holds; seats_max may not pass it
MODE_FIXED, MODE_LEARN = 0, 1
KEEP_ANGLE, KEEP_MOST = 0, 1
LOGISTIC_REACH = 40.0  # past this the logistic is 0 or 1 in float32
EXP_SPLIT = 8.0  # e^y as (e^(y/8))^8, the series kept within |x| <= 5
EXP_SERIES_REACH = 5.0
GATE_START = f32(0.5)
RISING = range(ANGLES - 1, -1, -1)  # degrees by rising delay: cos falls as the angle grows


@dataclass(frozen=True)
class BssConfig:
    """Field for field as dsp_afe_bss_config_t."""

    mode: int = afe.BSS_MODE
    keep_deg: float = afe.BSS_KEEP_DEG
    keep_rule: int = afe.BSS_KEEP_RULE
    gate_slope: float = afe.BSS_GATE_SLOPE
    conf_min: int = afe.BSS_CONF_MIN
    seats_max: int = afe.BSS_SEATS_MAX
    decision_s: float = afe.BSS_DECISION_S
    recheck_s: float = afe.BSS_RECHECK_S
    first_speech_s: float = afe.BSS_FIRST_SPEECH_S
    kmeans_rounds: int = afe.BSS_KMEANS_ROUNDS
    confirm: int = afe.BSS_CONFIRM
    occupancy_min: float = afe.BSS_OCCUPANCY_MIN
    valley_max: float = afe.BSS_VALLEY_MAX
    valley_width: float = afe.BSS_VALLEY_WIDTH
    min_gap: float = afe.BSS_MIN_GAP
    boundary_share_min: float = afe.BSS_BOUNDARY_SHARE_MIN
    boundary_follow: float = afe.BSS_BOUNDARY_FOLLOW
    track_tau_s: float = afe.BSS_TRACK_TAU_S
    track_gate: float = afe.BSS_TRACK_GATE
    seat_forget_s: float = afe.BSS_SEAT_FORGET_S
    update_every_hops: int = afe.DOA_UPDATE_EVERY_HOPS
    spacing_m: float = array.SPACING_M
    speed_of_sound_m_s: float = array.SPEED_OF_SOUND_M_S


def update_s(cfg: BssConfig) -> float:
    """Seconds of speech between two doa searches."""
    return grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ * cfg.update_every_hops


def check(cfg: BssConfig) -> None:
    """Raise unless dsp_afe_bss_workspace_bytes would accept cfg."""
    c = {k: float(f32(v)) if isinstance(v, float) else v for k, v in cfg.__dict__.items()}
    ok = (
        c["mode"] in (MODE_FIXED, MODE_LEARN)
        and c["keep_rule"] in (KEEP_ANGLE, KEEP_MOST)
        and 0.0 <= c["keep_deg"] <= ANGLES - 1
        and c["gate_slope"] > 0.0
        and 0 <= c["conf_min"] <= 255
        and 2 <= c["seats_max"] <= SEATS_CAP
        and c["decision_s"] > 0.0
        and c["recheck_s"] > 0.0
        and 0.0 < c["first_speech_s"] <= c["decision_s"]
        and c["kmeans_rounds"] >= 1
        and c["confirm"] >= 1
        and 0.0 <= c["occupancy_min"] < 1.0
        and c["valley_max"] > 0.0
        and 0.0 < c["valley_width"] < 0.5
        and c["min_gap"] > 0.0
        and 0.0 <= c["boundary_share_min"] <= 0.5
        and 0.0 <= c["boundary_follow"] <= 1.0
        and c["track_tau_s"] > 0.0
        and c["track_gate"] > 0.0
        and c["seat_forget_s"] > 0.0
        and c["update_every_hops"] >= 1
        and c["spacing_m"] > 0.0
        and c["speed_of_sound_m_s"] > 0.0
    )
    if not ok or update_s(cfg) / c["track_tau_s"] > EXP_SERIES_REACH:
        raise ValueError(f"bad bss configuration {cfg}")


def rounded(x: float) -> int:
    """x rounded half up, as floor(x + 0.5) in double on the C side; at least 1."""
    return max(1, math.floor(x + 0.5))


def logistic(y: float) -> float:
    """1 / (1 + e^-y) in double, e^-y by its series as (e^(-y/8))^8 with y kept within +-LOGISTIC_REACH."""
    y = min(max(y, -LOGISTIC_REACH), LOGISTIC_REACH)
    e = exp_series(-y / EXP_SPLIT)
    e = e * e
    e = e * e
    e = e * e
    return 1.0 / (1.0 + e)


def seat_gates(delay: np.float32, cuts: list, slope: float) -> list:
    """The gate of every seat by rising delay for one delay: the share above each cut, differenced; they sum to 1."""
    above = [f32(1.0)] + [f32(logistic(2.0 * slope * (float(delay) - float(c)))) for c in cuts] + [f32(0.0)]
    return [f32(above[j] - above[j + 1]) for j in range(len(cuts) + 1)]


def nearest(value: np.float32, centres: list) -> int:
    """Index of the centre closest to value, the first on a tie."""
    best, at = None, 0
    for i, c in enumerate(centres):
        d = abs(f32(value - c))
        if best is None or d < best:
            best, at = d, i
    return at


def sort_rising(c: list) -> list:
    """Insertion sort, as the C side does on at most SEATS_CAP centres."""
    out = list(c)
    for i in range(1, len(out)):
        v, j = out[i], i - 1
        while j >= 0 and out[j] > v:
            out[j + 1] = out[j]
            j -= 1
        out[j + 1] = v
    return out


class Bss:
    """One separator; the gate starts at one half on both outputs and LEARN starts with no seats."""

    def __init__(self, cfg: BssConfig | None = None) -> None:
        self.cfg = cfg or BssConfig()
        check(self.cfg)
        c = self.cfg
        self.learn = c.mode == MODE_LEARN
        tau_max = float(f32(c.spacing_m)) * grid.SAMPLE_RATE_HZ / float(f32(c.speed_of_sound_m_s))
        self.delay = [f32(tau_max * cos_series(d * PI / 180.0)) for d in range(ANGLES)]
        self.keep_delay = f32(tau_max * cos_series(float(f32(c.keep_deg)) * PI / 180.0))
        self.slope = float(f32(c.gate_slope))
        side = 1 if self.keep_delay > 0 else 0
        self.broadside = [seat_gates(self.delay[d], [f32(0.0)], self.slope)[side] for d in range(ANGLES)]
        speech_s = update_s(c)
        hop_s = grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ
        self.window = rounded(float(f32(c.decision_s)) / speech_s)
        self.first = min(self.window, rounded(float(f32(c.first_speech_s)) / speech_s))
        self.recheck_hops = rounded(float(f32(c.recheck_s)) / hop_s)
        self.forget_hops = rounded(float(f32(c.seat_forget_s)) / hop_s)
        self.rate = f32(1.0 - exp_series(-speech_s / float(f32(c.track_tau_s))))
        self.occupancy, self.valley_max, self.valley_width = (
            f32(c.occupancy_min),
            f32(c.valley_max),
            f32(c.valley_width),
        )
        self.min_gap, self.half_gap = f32(c.min_gap), f32(f32(0.5) * f32(c.min_gap))
        self.share_lo = f32(c.boundary_share_min)
        self.share_hi = f32(f32(1.0) - self.share_lo)
        self.follow, self.track_gate = f32(c.boundary_follow), f32(c.track_gate)
        self.reset()

    def reset(self) -> None:
        """Back to where init leaves it: no angles counted, no seats, the gate at one half."""
        self.ring = [0] * self.window
        self.head = self.filled = 0
        self.hist = [0] * ANGLES
        self.hop = self.since = 0
        self.seats = 0
        self.centres = [f32(0.0)] * SEATS_CAP
        self.offsets = [f32(0.0)] * (SEATS_CAP - 1)
        self.seen = [0] * SEATS_CAP
        self.votes = [0] * SEATS_CAP
        self.pending = self.agree = 0
        self.gate = GATE_START

    def process(
        self, x0: np.ndarray, x1: np.ndarray, angle_deg: int, confidence: int, update: bool
    ) -> tuple[np.ndarray, np.ndarray]:
        """Count doa's angle when it searched this hop, learn, and return the kept and the other share of the mean."""
        counted = bool(update) and 0 <= angle_deg < ANGLES and confidence >= self.cfg.conf_min
        if self.learn:
            if counted:
                self._count(angle_deg)
                if self.seats >= 2:
                    self._track(self.delay[angle_deg])
            self._forget()
            self.since += 1
            if self.since >= self.recheck_hops and self.filled >= self.first:
                self.since = 0
                self._cluster()
        if counted:
            self.gate = self._gate(angle_deg)
        self.hop += 1
        mean = (f32(0.5) * (np.asarray(x0, dtype=np.complex64) + np.asarray(x1, dtype=np.complex64))).astype(
            np.complex64
        )
        other = f32(f32(1.0) - self.gate)
        return (mean * self.gate).astype(np.complex64), (mean * other).astype(np.complex64)

    def directions(self) -> tuple[int, int]:
        """Whole degrees of the kept seat and of the nearest other one; -1 without two seats."""
        if self.seats < 2:
            return -1, -1
        keep = self._keep_seat()
        centres = self.centres[: self.seats]
        others = [i for i in range(self.seats) if i != keep]
        other = others[nearest(centres[keep], [centres[i] for i in others])]
        return nearest(centres[keep], self.delay), nearest(centres[other], self.delay)

    def _count(self, angle: int) -> None:
        if self.filled == self.window:
            self.hist[self.ring[self.head]] -= 1
        else:
            self.filled += 1
        self.ring[self.head] = angle
        self.hist[angle] += 1
        self.head = (self.head + 1) % self.window

    def _track(self, delay: np.float32) -> None:
        c = self.centres
        j = nearest(delay, c[: self.seats])
        if abs(f32(delay - c[j])) <= self.track_gate:
            c[j] = f32(c[j] + f32(self.rate * f32(delay - c[j])))
            for i in range(self.seats - 1):
                if f32(c[i + 1] - c[i]) < self.min_gap:
                    mid = f32(f32(0.5) * f32(c[i] + c[i + 1]))
                    c[i], c[i + 1] = f32(mid - self.half_gap), f32(mid + self.half_gap)
            self.seen[j] = self.hop
            self.votes[j] += 1

    def _forget(self) -> None:
        i = 0
        while i < self.seats and self.seats >= 2:
            if self.hop - self.seen[i] < self.forget_hops:
                i += 1
                continue
            last = self.seats - 1
            if 0 < i < last:
                self.offsets[i - 1] = f32(0.0)
            for j in range(i, last):
                self.centres[j], self.seen[j] = self.centres[j + 1], self.seen[j + 1]
                self.votes[j] = self.votes[j + 1]
            for j in range(i if i < last else last - 1, last - 1):
                self.offsets[j] = self.offsets[j + 1]
            self.seats -= 1
        if self.seats < 2:
            self.seats = 0

    def _density(self, at: np.float32, width: np.float32) -> int:
        return sum(self.hist[d] for d in RISING if self.hist[d] > 0 and abs(f32(self.delay[d] - at)) < width)

    def _kmeans(self, k: int) -> tuple[list, list] | None:
        """k centres and the offsets of their k - 1 boundaries off the midpoints, or None when the window does not
        hold k seats."""
        total = self.filled
        seeds = []
        for i in range(k):
            target = total * (10 * (k - 1) + 80 * i) // (100 * (k - 1))
            run = 0
            for d in RISING:
                run += self.hist[d]
                if self.hist[d] > 0 and run > target:
                    seeds.append(self.delay[d])
                    break
        c = seeds
        for _ in range(self.cfg.kmeans_rounds):
            sums, counts = [f32(0.0)] * k, [0] * k
            for d in RISING:
                n = self.hist[d]
                if n == 0:
                    continue
                j = nearest(self.delay[d], c)
                sums[j] = f32(sums[j] + f32(f32(n) * self.delay[d]))
                counts[j] += n
            c = sort_rising([f32(sums[j] / f32(counts[j])) if counts[j] > 0 else c[j] for j in range(k)])
        squares, counts = [f32(0.0)] * k, [0] * k
        for d in RISING:
            n = self.hist[d]
            if n == 0:
                continue
            j = nearest(self.delay[d], c)
            off = f32(self.delay[d] - c[j])
            squares[j] = f32(squares[j] + f32(f32(n) * f32(off * off)))
            counts[j] += n
        least = f32(self.occupancy * f32(total))
        if any(f32(n) < least for n in counts):
            return None
        spread = [f32(np.sqrt(f32(squares[j] / f32(counts[j])))) if counts[j] > 1 else f32(0.0) for j in range(k)]
        offsets = []
        for j in range(k - 1):
            gap = f32(c[j + 1] - c[j])
            mid = f32(f32(0.5) * f32(c[j] + c[j + 1]))
            width = f32(self.valley_width * gap)
            peaks = f32(f32(0.5) * f32(self._density(c[j], width) + self._density(c[j + 1], width)))
            if gap < self.min_gap or not f32(self._density(mid, width)) < f32(self.valley_max * peaks):
                return None
            a, b = spread[j], spread[j + 1]
            both = f32(a + b)
            bound = mid
            if both > 0:
                share = f32(a / both)
                if self.share_lo <= share <= self.share_hi:
                    bound = f32(f32(f32(c[j] * b) + f32(c[j + 1] * a)) / both)
                else:
                    w = self.share_lo if share < self.share_lo else self.share_hi
                    bound = f32(c[j] + f32(w * gap))
            offsets.append(f32(bound - mid))
        return c, offsets

    def _cluster(self) -> None:
        found, fit = 1, None
        for k in range(2, self.cfg.seats_max + 1):
            got = self._kmeans(k)
            if got is None:
                break
            found, fit = k, got
        current = max(self.seats, 1)
        if found > current:
            self.agree = self.agree + 1 if found == self.pending else 1
            self.pending = found
            if self.agree >= self.cfg.confirm:
                centres, offsets = fit
                self.seats = found
                self.centres[:found] = centres
                self.offsets[: found - 1] = offsets
                self.seen[:found] = [self.hop] * found
                self.votes[:found] = self._window_votes()
                self.pending = self.agree = 0
            return
        self.pending = self.agree = 0
        if found == current and current >= 2:
            _, offsets = fit
            for j in range(current - 1):
                self.offsets[j] = f32(self.offsets[j] + f32(self.follow * f32(offsets[j] - self.offsets[j])))

    def _keep_seat(self) -> int:
        centres = self.centres[: self.seats]
        if self.cfg.keep_rule == KEEP_ANGLE:
            return nearest(self.keep_delay, centres)
        votes = self.votes[: self.seats]
        return votes.index(max(votes))

    def _window_votes(self) -> list:
        """Updates of the window nearest each seat: where the votes of a seat just taken start."""
        counts = [0] * self.seats
        for d in RISING:
            if self.hist[d] > 0:
                counts[nearest(self.delay[d], self.centres[: self.seats])] += self.hist[d]
        return counts

    def _gate(self, angle: int) -> np.float32:
        if self.seats < 2:
            return self.broadside[angle]
        c = self.centres
        cuts = [f32(f32(f32(0.5) * f32(c[j] + c[j + 1])) + self.offsets[j]) for j in range(self.seats - 1)]
        return seat_gates(self.delay[angle], cuts, self.slope)[self._keep_seat()]

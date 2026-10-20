"""bss of dsp_afe: a gate on doa's direction over the mean of both microphones (KEHOACH 3.8).

Mirror of firmware/components/dsp_afe/src/bss.c in float32, after tinyai-signal's micarray separation: each counted doa
angle becomes a delay and sets the kept seat's gate, one factor for the whole mean; logistic by series in double.
Mode 0 splits two seats at broadside; mode 1 finds seats as peaks of the delay density of the last decision_s of speech,
up to seats_max, each holding one number from the recheck that takes it to the hop it is forgotten.
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
BINS_PER_GAP_CAP = 64  # finer bins than this buy nothing at doa's 2 deg grid
MODE_FIXED, MODE_LEARN = 0, 1
KEEP_ANGLE, KEEP_MOST = 0, 1
LOGISTIC_REACH = 40.0  # past this the logistic is 0 or 1 in float32
EXP_SPLIT = 8.0  # e^y as (e^(y/8))^8, the series kept within |x| <= 5
GATE_START = f32(0.5)
RISING = range(ANGLES - 1, -1, -1)  # degrees by rising delay: cos falls as the angle grows


@dataclass(frozen=True)
class BssConfig:
    """Field for field as dsp_afe_bss_config_t."""

    mode: int = afe.BSS_MODE
    keep_rule: int = afe.BSS_KEEP_RULE
    keep_deg: float = afe.BSS_KEEP_DEG
    gate_slope: float = afe.BSS_GATE_SLOPE
    conf_min: int = afe.BSS_CONF_MIN
    seats_max: int = afe.BSS_SEATS_MAX
    decision_s: float = afe.BSS_DECISION_S
    recheck_s: float = afe.BSS_RECHECK_S
    first_speech_s: float = afe.BSS_FIRST_SPEECH_S
    confirm: int = afe.BSS_CONFIRM
    occupancy_min: float = afe.BSS_OCCUPANCY_MIN
    min_gap: float = afe.BSS_MIN_GAP
    bins_per_gap: int = afe.BSS_BINS_PER_GAP
    smooth_bins: int = afe.BSS_SMOOTH_BINS
    prominence: float = afe.BSS_PROMINENCE
    core_share: float = afe.BSS_CORE_SHARE
    seat_follow: float = afe.BSS_SEAT_FOLLOW
    boundary_share_min: float = afe.BSS_BOUNDARY_SHARE_MIN
    boundary_follow: float = afe.BSS_BOUNDARY_FOLLOW
    seat_forget_s: float = afe.BSS_SEAT_FORGET_S
    update_every_hops: int = afe.DOA_UPDATE_EVERY_HOPS
    spacing_m: float = array.SPACING_M
    speed_of_sound_m_s: float = array.SPEED_OF_SOUND_M_S
    history_s: float = afe.BSS_HISTORY_S


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
        and c["confirm"] >= 1
        and 0.0 <= c["occupancy_min"] < 1.0
        and c["min_gap"] > 0.0
        and 1 <= c["bins_per_gap"] <= BINS_PER_GAP_CAP
        and 0 <= c["smooth_bins"] <= c["bins_per_gap"]
        and 0.0 < c["prominence"] <= 1.0
        and 0.0 < c["core_share"] <= 0.5
        and 0.0 <= c["seat_follow"] <= 1.0
        and 0.0 <= c["boundary_share_min"] <= 0.5
        and 0.0 <= c["boundary_follow"] <= 1.0
        and c["seat_forget_s"] > 0.0
        and c["update_every_hops"] >= 1
        and c["spacing_m"] > 0.0
        and c["speed_of_sound_m_s"] > 0.0
        and c["history_s"] >= 0.0
    )
    if not ok:
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
        self.side = 1 if self.keep_delay > 0 else 0
        self.rise = [seat_gates(self.delay[d], [f32(0.0)], self.slope)[1] for d in range(ANGLES)]
        speech_s = update_s(c)
        hop_s = grid.HOP_SAMPLES / grid.SAMPLE_RATE_HZ
        self.window = rounded(float(f32(c.decision_s)) / speech_s)
        self.first = min(self.window, rounded(float(f32(c.first_speech_s)) / speech_s))
        self.recheck_hops = rounded(float(f32(c.recheck_s)) / hop_s)
        self.forget_hops = rounded(float(f32(c.seat_forget_s)) / hop_s)
        self.history = math.floor(float(f32(c.history_s)) / hop_s + 0.5)
        self.occupancy = f32(c.occupancy_min)
        self.min_gap, self.half_gap = f32(c.min_gap), f32(f32(0.5) * f32(c.min_gap))
        self.share_lo = f32(c.boundary_share_min)
        self.share_hi = f32(f32(1.0) - self.share_lo)
        self.follow, self.seat_follow = f32(c.boundary_follow), f32(c.seat_follow)
        self.prominence = f32(c.prominence)
        self.width = float(self.min_gap) / c.bins_per_gap
        self.lo = -float(self.delay[0])
        self.n_bins = math.floor(2.0 * float(self.delay[0]) / self.width) + 1
        self.bin_of = [
            min(self.n_bins - 1, math.floor((float(self.delay[d]) - self.lo) / self.width)) for d in range(ANGLES)
        ]
        self.core = float(self.min_gap) * float(f32(c.core_share))
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
        self.number = [0] * SEATS_CAP
        self.agree = 0
        self.candidates: list = []
        self.gate = GATE_START
        self.shares = [GATE_START, GATE_START] + [f32(0.0)] * (SEATS_CAP - 2)
        self.last_angle = -1
        self.held = [-1] * (self.history + 1)

    def process(
        self, x0: np.ndarray, x1: np.ndarray, angle_deg: int, confidence: int, update: bool
    ) -> tuple[np.ndarray, np.ndarray]:
        """Count doa's angle when it searched this hop, learn, and return the kept and the other share of the mean."""
        counted = bool(update) and 0 <= angle_deg < ANGLES and confidence >= self.cfg.conf_min
        if self.learn:
            if counted:
                self._count(angle_deg)
                if self.seats >= 2:
                    self._mark(self.delay[angle_deg])
            self._forget()
            self.since += 1
            if self.since >= self.recheck_hops and self.filled >= self.first:
                self.since = 0
                self._cluster()
        if counted:
            self.gate, self.shares = self._gates(angle_deg)
            self.last_angle = angle_deg
        self.held[self.hop % len(self.held)] = self.last_angle
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

    def numbered(self) -> tuple[list, list]:
        """Gate and whole degrees of every seat number, SEATS_CAP each: the gates the last search set, summing to 1;
        -1 degrees for a number no seat holds, and below two seats, where 0 is the side past 90 deg and 1 the other."""
        angles = [-1] * SEATS_CAP
        if self.seats >= 2:
            for j in range(self.seats):
                angles[self.number[j]] = nearest(self.centres[j], self.delay)
        return list(self.shares), angles

    def numbered_back(self, back: int) -> tuple[list, int]:
        """Gate of every seat number for the angle held back hops ago under the seats held now, and the kept seat's
        number: the delayed output's gates; before any angle, the gates a separator starts with."""
        if not 0 <= back <= self.history:
            raise ValueError(f"back {back} is past the {self.history} hops of history kept")
        at = self.hop - 1 - back
        angle = self.held[at % len(self.held)] if at >= 0 else -1
        kept = self.number[self._keep_seat()] if self.seats >= 2 else self.side
        if angle < 0:
            return [GATE_START, GATE_START] + [f32(0.0)] * (SEATS_CAP - 2), kept
        return self._gates(angle)[1], kept

    def _count(self, angle: int) -> None:
        if self.filled == self.window:
            self.hist[self.ring[self.head]] -= 1
        else:
            self.filled += 1
        self.ring[self.head] = angle
        self.hist[angle] += 1
        self.head = (self.head + 1) % self.window

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
                self.votes[j], self.number[j] = self.votes[j + 1], self.number[j + 1]
            for j in range(i if i < last else last - 1, last - 1):
                self.offsets[j] = self.offsets[j + 1]
            self.seats -= 1
        if self.seats < 2:
            self.seats = 0

    def _mark(self, delay: np.float32) -> None:
        """Count a reading for the seat whose region holds it: what keeps that seat from being forgotten."""
        c, j = self.centres, 0
        for i in range(self.seats - 1):
            if delay > f32(f32(f32(0.5) * f32(c[i] + c[i + 1])) + self.offsets[i]):
                j = i + 1
        self.seen[j] = self.hop
        self.votes[j] += 1

    def _density(self) -> list:
        """The window's counts binned by delay and smoothed with the integer triangular kernel."""
        counts = [0] * self.n_bins
        for d in range(ANGLES):
            counts[self.bin_of[d]] += self.hist[d]
        r = self.cfg.smooth_bins
        s = [0] * self.n_bins
        for i in range(self.n_bins):
            for k in range(-r, r + 1):
                if 0 <= i + k < self.n_bins:
                    s[i] += (r + 1 - abs(k)) * counts[i + k]
        return s

    @staticmethod
    def _col(s: list, p: int, step: int) -> int:
        """Lowest density between bin p and the nearest higher bin one way, 0 when no higher bin lies that way."""
        low, i = s[p], p
        while 0 <= i + step < len(s):
            i += step
            if s[i] > s[p]:
                return low
            low = min(low, s[i])
        return 0

    @staticmethod
    def _saddle(s: list, a: int, b: int) -> int:
        """The bin of the lowest density between bins a and b, the middle one of those tied lowest."""
        lo, hi = min(a, b), max(a, b)
        low = min(s[lo : hi + 1])
        tied = [i for i in range(lo, hi + 1) if s[i] == low]
        return tied[len(tied) // 2]

    def _candidates(self, s: list) -> list:
        """(centre, peak bin) of every peak that stands out by prominence, the higher kept within min_gap, and whose
        core holds occupancy_min of the window; the centre is the core's mean delay; rising."""
        n = self.n_bins
        standing = []
        for p in range(n):
            if s[p] == 0 or (p > 0 and s[p] <= s[p - 1]) or (p < n - 1 and s[p] < s[p + 1]):
                continue
            saddle = max(self._col(s, p, -1), self._col(s, p, 1))
            if f32(s[p] - saddle) >= f32(self.prominence * f32(s[p])):
                standing.append(p)
        chosen: list = []
        for p in sorted(standing, key=lambda q: (-s[q], q)):
            if all(abs(p - q) >= self.cfg.bins_per_gap for q in chosen):
                chosen.append(p)
        out = []
        for p in sorted(chosen):
            peak = self.lo + (p + 0.5) * self.width
            mass, acc = 0, f32(0.0)
            for d in RISING:
                if self.hist[d] > 0 and abs(float(self.delay[d]) - peak) <= self.core:
                    mass += self.hist[d]
                    acc = f32(acc + f32(f32(self.hist[d]) * self.delay[d]))
            if mass > 0 and not f32(mass) < f32(self.occupancy * f32(self.filled)):
                out.append((f32(acc / f32(mass)), p))
        return out

    def _target(self, j: int, s: list, a: int, b: int) -> np.float32:
        """Offset off the midpoint of seats j and j + 1 of the density's lowest point between their peaks a and b, each
        seat keeping boundary_share_min of the gap."""
        c = self.centres
        cut = f32(self.lo + (self._saddle(s, a, b) + 0.5) * self.width)
        gap = f32(c[j + 1] - c[j])
        cut = max(cut, f32(c[j] + f32(self.share_lo * gap)))
        cut = min(cut, f32(c[j] + f32(self.share_hi * gap)))
        return f32(cut - f32(f32(0.5) * f32(c[j] + c[j + 1])))

    def _space(self) -> None:
        """Keep the seats rising and at least min_gap apart."""
        c = self.centres
        for i in range(self.seats - 1):
            if f32(c[i + 1] - c[i]) < self.min_gap:
                mid = f32(f32(0.5) * f32(c[i] + c[i + 1]))
                c[i], c[i + 1] = f32(mid - self.half_gap), f32(mid + self.half_gap)

    def _agrees(self, fresh: list, paired: bool) -> bool:
        """Whether fresh centres repeat the last recheck's within half min_gap, in order when paired."""
        if not fresh or not self.candidates:
            return False
        if paired:
            return len(fresh) == len(self.candidates) and all(
                abs(f32(u - q)) < self.half_gap for u, q in zip(fresh, self.candidates, strict=True)
            )
        return all(any(abs(f32(u - q)) < self.half_gap for q in self.candidates) for u in fresh)

    def _cluster(self) -> None:
        s = self._density()
        cands = self._candidates(s)
        if self.seats < 2:
            self._first_split(s, cands)
            return
        held = self.centres[: self.seats]
        match = [-1] * self.seats
        new = []
        for ci, (c, _) in enumerate(cands):
            j = nearest(c, held)
            dist = abs(f32(c - held[j]))
            if not dist < self.min_gap:
                new.append(ci)
            elif match[j] < 0 or dist < abs(f32(cands[match[j]][0] - held[j])):
                match[j] = ci
        for j in range(self.seats):
            if match[j] >= 0:
                self.centres[j] = f32(
                    self.centres[j] + f32(self.seat_follow * f32(cands[match[j]][0] - self.centres[j]))
                )
        self._space()
        room = self.cfg.seats_max - self.seats
        vacant = [j for j in range(self.seats) if match[j] < 0]
        if new and (room > 0 or vacant):
            fresh = [cands[ci][0] for ci in new]
            self.agree = self.agree + 1 if self._agrees(fresh, paired=False) else 1
            self.candidates = fresh
            if self.agree >= self.cfg.confirm:
                bins = [cands[m][1] if m >= 0 else -1 for m in match]
                if room > 0:
                    self._take([cands[ci] for ci in new[:room]], s, bins)
                else:
                    self._move([cands[ci] for ci in new], vacant, s, bins)
                self.agree, self.candidates = 0, []
            return
        self.agree, self.candidates = 0, []
        for j in range(self.seats - 1):
            if match[j] >= 0 and match[j + 1] >= 0:
                target = self._target(j, s, cands[match[j]][1], cands[match[j + 1]][1])
                self.offsets[j] = f32(self.offsets[j] + f32(self.follow * f32(target - self.offsets[j])))

    def _first_split(self, s: list, cands: list) -> None:
        """Seats from two or more candidates seen alike on confirm rechecks in a row, numbered by rising delay; with
        more candidates than seats, only once the ones left out stand below (1 - prominence) of the lowest one kept."""
        if len(cands) < 2:
            self.agree, self.candidates = 0, []
            return
        if len(cands) > self.cfg.seats_max:
            order = sorted(range(len(cands)), key=lambda i: (-s[cands[i][1]], i))
            tall, rest = order[: self.cfg.seats_max], order[self.cfg.seats_max :]
            lowest = s[cands[tall[-1]][1]]
            if f32(s[cands[rest[0]][1]]) > f32(f32(f32(1.0) - self.prominence) * f32(lowest)):
                self.agree, self.candidates = 0, []
                return
            cands = [cands[i] for i in sorted(tall)]
        fresh = [c for c, _ in cands]
        self.agree = self.agree + 1 if self._agrees(fresh, paired=True) else 1
        self.candidates = fresh
        if self.agree < self.cfg.confirm:
            return
        self.seats = len(cands)
        for j, (c, _) in enumerate(cands):
            self.centres[j], self.number[j], self.seen[j] = c, j, self.hop
        self.votes[: self.seats] = self._window_votes()
        for j in range(self.seats - 1):
            self.offsets[j] = self._target(j, s, cands[j][1], cands[j + 1][1])
        self.agree, self.candidates = 0, []

    def _move(self, fresh: list, vacant: list, s: list, held_bins: list) -> None:
        """With every seat taken, move each seat no peak of its own sits near to the nearest fresh (centre, bin)
        candidate, keeping its number: a seat placed off its talker, or a talker gone and another come."""
        for c, b in fresh:
            if not vacant:
                break
            j = vacant[nearest(c, [self.centres[v] for v in vacant])]
            vacant.remove(j)
            self.centres[j], self.seen[j], held_bins[j] = c, self.hop, b
        rows = sorted(range(self.seats), key=lambda j: float(self.centres[j]))
        order = [(self.centres[j], self.number[j], self.seen[j], self.votes[j], held_bins[j]) for j in rows]
        for j, (c, n, seen, votes, _) in enumerate(order):
            self.centres[j], self.number[j], self.seen[j], self.votes[j] = c, n, seen, votes
        self._space()
        for j in range(self.seats - 1):
            a, b = order[j][4], order[j + 1][4]
            self.offsets[j] = self._target(j, s, a, b) if a >= 0 and b >= 0 else f32(0.0)

    def _take(self, fresh: list, s: list, held_bins: list) -> None:
        """Seat fresh (centre, bin) candidates beside the held seats, which keep centre, number and counts; fresh ones
        take the smallest free numbers; a boundary between two seats with peaks in the window sits at its low point."""
        rows = [(self.centres[j], self.number[j], self.seen[j], self.votes[j], held_bins[j]) for j in range(self.seats)]
        used = [r[1] for r in rows]
        free = [n for n in range(self.cfg.seats_max) if n not in used]
        rows += [(c, free[i], self.hop, 0, b) for i, (c, b) in enumerate(fresh)]
        for i in range(1, len(rows)):
            row, k = rows[i], i - 1
            while k >= 0 and rows[k][0] > row[0]:
                rows[k + 1] = rows[k]
                k -= 1
            rows[k + 1] = row
        self.seats = len(rows)
        for j, (c, n, seen, votes, _) in enumerate(rows):
            self.centres[j], self.number[j], self.seen[j], self.votes[j] = c, n, seen, votes
        for j in range(self.seats - 1):
            a, b = rows[j][4], rows[j + 1][4]
            self.offsets[j] = self._target(j, s, a, b) if a >= 0 and b >= 0 else f32(0.0)

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

    def _gates(self, angle: int) -> tuple[np.float32, list]:
        """The kept seat's gate and the gate of every number for this angle."""
        shares = [f32(0.0)] * SEATS_CAP
        if self.seats < 2:
            rise = self.rise[angle]
            shares[0], shares[1] = f32(f32(1.0) - rise), rise
            return shares[self.side], shares
        c = self.centres
        cuts = [f32(f32(f32(0.5) * f32(c[j] + c[j + 1])) + self.offsets[j]) for j in range(self.seats - 1)]
        gates = seat_gates(self.delay[angle], cuts, self.slope)
        for j in range(self.seats):
            shares[self.number[j]] = gates[j]
        return gates[self._keep_seat()], shares

import numpy as np
import pytest

from srpipe.generated import speaker
from srpipe.scenes import speaker as survey
from srpipe.tasks.speaker import probe

OWNER_CFG = {"owner": {"enrol": {"date": "20260928", "distance_cm": "100"}}}


def window(spk: str, group: str = "near_1m", accepted: bool = False) -> survey.Window:
    return survey.Window(spk, group, np.zeros(4, np.float32), accepted)


def owner_windows() -> list[survey.Window]:
    enrol = [window("spk_001", "20260928 100", True) for _ in range(speaker.ENROL_COMMANDS + 2)]
    return enrol + [window("spk_001", "20261007 300", True) for _ in range(3)]


def test_the_roster_enrols_the_first_impostors_with_a_window_to_spare_outside_calibration(monkeypatch) -> None:
    impostors = [window("calibrated")]
    impostors += [window("b") for _ in range(speaker.ENROL_COMMANDS + 1)]
    impostors += [window("short") for _ in range(speaker.ENROL_COMMANDS)]
    impostors += [window("a") for _ in range(speaker.ENROL_COMMANDS + 3)]
    impostors += [window("c") for _ in range(speaker.ENROL_COMMANDS + 1)]
    monkeypatch.setattr(probe.survey, "owner_windows", lambda cfg, paths: owner_windows())
    monkeypatch.setattr(probe.survey, "impostor_windows", lambda cfg, paths, workers: impostors)
    monkeypatch.setattr(probe.quant, "drawn", lambda q, windows: ([0], len(windows) - 1))

    picked, enrolments = probe.chain_windows(OWNER_CFG, {}, {}, 1, 2)

    kinds = [kind for kind, _ in picked]
    assert kinds.count(probe.ENROL) == speaker.ENROL_COMMANDS
    assert [w.spk for kind, w in picked if kind == probe.MEMBER] == ["a", "b"]
    assert [{w.spk for w in theirs} for theirs in enrolments] == [{"a"}, {"b"}]
    assert [len(theirs) for theirs in enrolments] == [speaker.ENROL_COMMANDS] * 2
    member_test = next(w for kind, w in picked if kind == probe.MEMBER and w.spk == "a")
    assert all(member_test is not w for w in enrolments[0])
    assert [w.spk for kind, w in picked if kind == probe.IMPOSTOR] == ["short", "c"]


def test_a_roster_with_too_few_impostors_to_enrol_is_refused(monkeypatch) -> None:
    impostors = [window("calibrated")] + [window("a") for _ in range(speaker.ENROL_COMMANDS + 1)]
    monkeypatch.setattr(probe.survey, "owner_windows", lambda cfg, paths: owner_windows())
    monkeypatch.setattr(probe.survey, "impostor_windows", lambda cfg, paths, workers: impostors)
    monkeypatch.setattr(probe.quant, "drawn", lambda q, windows: ([0], len(windows) - 1))
    with pytest.raises(ValueError, match="the roster wants 2"):
        probe.chain_windows(OWNER_CFG, {}, {}, 1, 2)

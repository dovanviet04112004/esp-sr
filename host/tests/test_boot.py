"""srhost.boot on boot logs as board B prints them, with no board."""

from __future__ import annotations

from srhost import boot

KEPT = (
    b"I (3630) ai_speaker: speaker: 149 frames x 72 bands to 192 dims\r\n"
    b"I (4164) svc_listen: commands v1: 10 commands, 19 readings\r\n"
    b"I (4195) svc_listen: speaker check on, roster v1792129438 (ESP_OK), 2 voiceprints of this model\r\n"
    b"I (4195) app_boot: listening: every utterance vad finds, delta1 300, delta2 30, delta3 65535 (KEHOACH 5.4)\r\n"
)
SEEDED = b"I (120) app_boot: command thresholds 200, 0, 60 seeded: ESP_OK\r\n" + KEPT.replace(b"300", b"200")


def test_a_boot_that_kept_the_thresholds_passes_and_shows_them() -> None:
    lines = boot.boot_lines(KEPT)
    assert lines[0].startswith("commands v1") and lines[-1].startswith("listening:")
    assert any("2 voiceprints" in line for line in lines)
    assert boot.judged(KEPT) is None


def test_a_boot_that_seeded_the_thresholds_fails() -> None:
    assert boot.boot_lines(SEEDED)[0].startswith("command thresholds 200, 0, 60 seeded")
    assert "seeded" in boot.judged(SEEDED)


def test_a_boot_that_does_not_listen_fails() -> None:
    log = b"W (900) app_boot: no models in models_0 (ESP_ERR_NOT_FOUND), running without them\r\n"
    assert boot.boot_lines(log) == ["no models in models_0 (ESP_ERR_NOT_FOUND), running without them"]
    assert "not listening" in boot.judged(log)

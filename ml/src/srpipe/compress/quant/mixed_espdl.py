"""Rung 3 of the path to the chip (KEHOACH 3.14): the convolutions a quantised graph loses most at, by ESP-PPQ's
per-layer error, which the ladder's rows put at 16 bits through ptq_espdl's int16_ops."""

from __future__ import annotations

import torch


def ranked_layers(graph, calib: list[torch.Tensor]) -> list[tuple[str, float]]:
    """The graph's convolutions worst first, each with the noise-to-signal power ratio at the graph's outputs when
    only it is quantised, over the batches of calib."""
    from esp_ppq.quantization.analyse.layerwise import layerwise_error_analyse

    # The analysis stops after batch number steps, so steps one short of calib runs all of it.
    errors = layerwise_error_analyse(
        graph, calib, running_device="cpu", method="snr", steps=len(calib) - 1, verbose=False
    )
    return sorted(((name, float(error)) for name, error in errors.items()), key=lambda row: -row[1])


def int16_rows(ranked: list[tuple[str, float]], tops: list[int]) -> dict[str, list[str]]:
    """Each row's int16 operations: the first k of ranked, a row for each k of tops."""
    return {f"int16_top{k}": [name for name, _ in ranked[:k]] for k in tops}

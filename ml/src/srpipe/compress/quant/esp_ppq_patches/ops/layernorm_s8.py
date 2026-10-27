"""LayerNormS8 (espdl_layernorm_s8.cpp): each int8 LayerNorm as ai_engine's op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

LAYERNORM_S8 = "LayerNormS8"


def layernorm_s8_from(op) -> bool:
    """op renamed LayerNormS8 when it is an int8 LayerNormalization over the last axis, one grid each for its input,
    output, gamma and beta, with no mean or deviation output."""
    from esp_ppq.IR.quantize import QuantableOperation

    if op.type != "LayerNormalization" or not isinstance(op, QuantableOperation) or len(op.outputs) != 1:
        return False
    configs = [*op.input_quant_config, *op.output_quant_config]
    if op.attributes.get("axis", -1) != -1 or any(c.num_of_bits != 8 or c.scale.numel() != 1 for c in configs):
        return False
    op.type = LAYERNORM_S8
    return True


@contextlib.contextmanager
def layernorm_s8() -> Iterator[None]:
    """Each int8 LayerNormalization over the last axis exported as LayerNormS8 (KEHOACH 3.14 step 2.5): esp-dl's kernel
    takes each mean in double, which the S3 runs in software, and rescales gamma, beta and its rounding per value. The
    op takes layernorm_as_espdl's float32 steps in order, the mean as a float division where that gives the same.
    ReDimNet2 b0 (KEHOACH 3.17) on board B: the stem's norm took 65 ms of 1.17 s a window.
    """
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def renaming(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        for op in graph.operations.values():
            layernorm_s8_from(op)
        return graph

    EspdlExporter.prepare_graph = renaming
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare

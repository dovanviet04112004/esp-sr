"""BandChain (espdl_band_chain.cpp): each run of SwiftF0's single-frame BandBlocks, every one reading the one before
alone, as one op."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from .band_block import BAND_BLOCK

BAND_CHAIN = "BandChain"


def single_frame(op) -> bool:
    return op is not None and op.type == BAND_BLOCK and op.attributes["kernel"][1] == 1


def chain_of(graph, op) -> list | None:
    """The single-frame BandBlocks from op on, each reading the output of the one before alone, none of those
    outputs but the last a graph output, when there are two or more; None otherwise."""
    if not single_frame(op):
        return None
    chain = [op]
    while True:
        y = chain[-1].outputs[0]
        if y.name in graph.outputs or len(y.dest_ops) != 1 or not single_frame(y.dest_ops[0]):
            break
        chain.append(y.dest_ops[0])
    return chain if len(chain) > 1 else None


def band_chain_from(graph, op) -> bool:
    """The chain chain_of finds from op as one BandChain in op's place: x, then every block's weights and biases in
    turn, giving the last block's output; attributes each block's band taps, dilation and three shifts, in order.
    The other blocks and the maps between them go."""
    from esp_ppq.core import OperationQuantizationConfig

    chain = chain_of(graph, op)
    if chain is None:
        return False
    head, rest = chain[0], chain[1:]
    attributes = {
        "band_taps": [b.attributes["kernel"][0] for b in chain],
        "dilations": [b.attributes["dilation"] for b in chain],
        "shifts": [s for b in chain for s in b.attributes["shifts"]],
        "in_channels": head.attributes["in_channels"],
        "quant_type": head.attributes["quant_type"],
    }
    configs = list(head.config.input_quantization_config)
    params = []
    for block in rest:
        for v in block.inputs[1:]:
            v.dest_ops.remove(block)
            params.append(v)
        configs += block.config.input_quantization_config[1:]
        del block.inputs[1:]
    y = chain[-1].outputs[0]
    chain[-1].outputs.remove(y)
    out_configs = chain[-1].config.output_quantization_config
    middles = [b.outputs[0] for b in chain[:-1]]
    head.outputs.clear()
    for block in rest:
        graph.remove_operation(block)
    for v in middles:
        v.source_op = None
        graph.remove_variable(v)
    for v in params:
        head.inputs.append(v)
        v.dest_ops.append(head)
    head.outputs.append(y)
    y.source_op = head
    head.type = BAND_CHAIN
    head.attributes.clear()
    head.attributes.update(attributes)
    head.config = OperationQuantizationConfig(configs, out_configs)
    return True


@contextlib.contextmanager
def band_chain() -> Iterator[None]:
    """Each run of SwiftF0's single-frame residual blocks exported as one BandChain (KEHOACH 3.14 step 2.5): as
    BandBlocks, each block reads its input map from PSRAM and writes its output there. The op gathers a column into
    internal RAM once and runs every block on it there. Run after band_block. SwiftF0 (KEHOACH 3.11) on board B:
    blocks 1 to 4 took 9.0 ms of a 27.4 ms 16-frame window.
    """
    from esp_ppq.parser.espdl_exporter import EspdlExporter

    prepare = EspdlExporter.prepare_graph

    def joining(self, graph, *args, **kwargs):
        graph = prepare(self, graph, *args, **kwargs)
        for op in list(graph.topological_sort()):
            if op.name in graph.operations:
                band_chain_from(graph, op)
        return graph

    EspdlExporter.prepare_graph = joining
    try:
        yield
    finally:
        EspdlExporter.prepare_graph = prepare

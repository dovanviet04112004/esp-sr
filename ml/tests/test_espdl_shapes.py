"""esp-dl's shapes replayed over an exported .info: a weight left channels-first over channels-last data, a Reshape of
another size and a grouped convolution that multiplies channels are refused, each beside the same graph done right;
an op the replay does not know leaves its output unchecked."""

from __future__ import annotations

from srpipe.compress.quant import espdl_shapes


def info(nodes: list[str], declared: list[str] = (), values: dict[str, str] | None = None, x: str = "1x148x24") -> str:
    """An .info as ESP-PPQ's exporter prints it, its one input x."""
    tails = [
        f"%{name}, shape: [{len(v.split(','))}], exponents: [0], \nvalue: array([{v}])"
        for name, v in (values or {}).items()
    ]
    return "\n".join(
        ["graph main_graph (", f"  %x[INT8, {x}], exponents: [-4]", ") initializers ("]
        + [f"  {d}" for d in declared]
        + [") {"]
        + [f"  {n}" for n in nodes]
        + ["}"]
        + tails
    )


def problems(text: str) -> list[str]:
    return espdl_shapes.problems(espdl_shapes.parse(text))


def test_a_weight_left_channels_first_is_refused_and_one_vector_passes() -> None:
    nodes = ["%y = Mul[quant_type = 'S8'](%w, %x)"]
    assert problems(info(nodes, ["%w[INT8, 24x1]"])) == ["y: Mul broadcasts [24, 1] with [1, 148, 24]"]
    assert problems(info(nodes, ["%w[INT8, 24]"])) == []


def test_a_reshape_of_another_size_is_refused() -> None:
    nodes = ["%y = Reshape[allowzero = 0, quant_type = 'S8'](%x, %s)"]
    assert problems(info(nodes, ["%s[INT64, 4]"], {"s": "1, 37, 4, 12"})) == [
        "y: Reshape of [1, 148, 24] to [1, 37, 4, 12]"
    ]
    assert problems(info(nodes, ["%s[INT64, 4]"], {"s": "1, -1, 4, 6"})) == []


def test_a_grouped_convolution_that_multiplies_channels_breaks_the_shapes_after_it() -> None:
    def graph(group: int, weight: str) -> str:
        nodes = [
            f"%y = Conv[dilations = [1, 1], group = {group}, kernel_shape = [1, 1], pads = [0, 0, 0, 0], "
            f"strides = [1, 1]](%x, %w)",
            "%z = Reshape[allowzero = 0](%y, %s)",
        ]
        return info(nodes, [f"%w[INT8, {weight}]", "%s[INT64, 3]"], {"s": "1, 8, 192"}, x="1x8x8x12")

    assert problems(graph(12, "1x1x1x24")) == ["z: Reshape of [1, 8, 8, 12] to [1, 8, 192]"]
    assert problems(graph(1, "1x1x12x24")) == []


def test_an_op_the_replay_does_not_know_leaves_its_output_unchecked() -> None:
    nodes = ["%h = GRU[hidden_size = 8](%x)", "%y = Reshape[allowzero = 0](%h, %s)"]
    assert problems(info(nodes, ["%s[INT64, 2]"], {"s": "1, 5"})) == []

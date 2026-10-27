"""The S3's constants and the graph helpers every patch shares: shapes as the export lays them, exponents, configs."""

from __future__ import annotations

# esp-dl runs convolutions channels last, so time, the first spatial axis of every conv streamed here, is axis 1.
CONV_TIME_AXIS = 1

CHANNEL_STEP = 16  # dl_base_conv2d.cpp: S3 vector path, 16 channels a step

DATA_CACHE_BYTES = 0x10000  # CONFIG_ESP32S3_DATA_CACHE_SIZE of the firmware apps

ELEMENTWISE_OPS = frozenset(
    {
        "Add",
        "Sub",
        "Mul",
        "Div",
        "Sqrt",
        "Relu",
        "Tanh",
        "Sigmoid",
        "QuantizeLinear",
        "DequantizeLinear",
        "RequantizeLinear",
    }
)

REDUCE_OPS = frozenset({"ReduceMean", "ReduceSum", "ReduceMax", "ReduceMin"})


def laid_shape(var, info) -> list[int]:
    """var's shape as the export lays it in the chip's memory."""
    perm = info.get_var_permute(var.name)
    return [var.shape[i] for i in perm] if perm else list(var.shape)


def linked(graph, op, inputs: list, outputs: list):
    """op, quantised and with no variables yet, registered in graph and linked to inputs and outputs."""
    graph.operations[op.name] = op
    for var in inputs:
        op.inputs.append(var)
        var.dest_ops.append(op)
    for var in outputs:
        op.outputs.append(var)
        var.source_op = op
    return op


def int8_tensor(config) -> bool:
    return config.num_of_bits == 8 and config.scale.numel() == 1


def exponent_of(var, info) -> int | None:
    found = info.get_var_exponents(var.name)
    return found[0] if found and len(found) == 1 else None


def behind_reshapes(graph, var) -> tuple:
    """(the op making var, the Reshapes passed) going back from var through Reshapes, each variable on the way read
    by one op alone and no graph output; (None, []) when one is not."""
    passed = []
    while var.name not in graph.outputs and len(var.dest_ops) == 1 and var.source_op is not None:
        if var.source_op.type != "Reshape":
            return var.source_op, passed
        passed.append(var.source_op)
        var = var.source_op.inputs[0]
    return None, []


def read_once(graph, var) -> bool:
    return var.name not in graph.outputs and len(var.dest_ops) == 1


WORD_BYTES = 4


def config_of(var):
    """var's quantisation config as the op making it gives it, or as one reading it takes it; None when no
    quantised op does."""
    from esp_ppq.IR.quantize import QuantableOperation

    if isinstance(var.source_op, QuantableOperation):
        return var.source_op.output_quant_config[var.source_op.outputs.index(var)]
    for reader in var.dest_ops:
        if isinstance(reader, QuantableOperation):
            return reader.input_quant_config[reader.inputs.index(var)]
    return None

"""The registry of ESP-PPQ fixes the ctc and speaker branches opt into: every fix is undone when its block ends, so
other branches run ESP-PPQ as is, and an unknown name is refused."""

from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("esp_ppq")

from srpipe.compress.quant import esp_ppq_patches  # noqa: E402


def test_every_patch_is_undone_when_its_block_ends() -> None:
    from esp_ppq.api import espdl_interface, interface
    from esp_ppq.executor.base import OPERATION_FORWARD_TABLE
    from esp_ppq.parser import espdl_exporter
    from esp_ppq.parser.espdl.espdl_streaming import StreamingTable
    from esp_ppq.quantization.optim import QuantizeFusionPass

    from srpipe.compress.quant.esp_ppq_patches import layout

    pattern, add, fuse = espdl_exporter.InsertRequantNodePattern, StreamingTable.add, QuantizeFusionPass.optimize
    simplify, formatted = espdl_interface.simplify, interface.format_graph
    prepare, reset = espdl_exporter.EspdlExporter.prepare_graph, espdl_exporter.reset_graph_layout
    merge = layout.column_merge
    norms = {platform: table.get("RMSNormalization") for platform, table in OPERATION_FORWARD_TABLE.items()}
    gelus = {platform: table.get("Gelu") for platform, table in OPERATION_FORWARD_TABLE.items()}
    with esp_ppq_patches.applied(list(esp_ppq_patches.PATCHES)):
        assert espdl_exporter.InsertRequantNodePattern is not pattern and StreamingTable.add is not add
        assert QuantizeFusionPass.optimize is not fuse and espdl_interface.simplify is not simplify
        assert interface.format_graph is not formatted
        assert espdl_exporter.EspdlExporter.prepare_graph is not prepare
        assert espdl_exporter.reset_graph_layout is not reset and layout.column_merge is not merge
        s3 = [p for p in OPERATION_FORWARD_TABLE if p.name in ("ESPDL_S3_INT8", "ESPDL_S3_INT16")]
        assert len(s3) == 2 and all(OPERATION_FORWARD_TABLE[p]["RMSNormalization"] is not norms[p] for p in s3)
    assert espdl_exporter.InsertRequantNodePattern is pattern and StreamingTable.add is add
    assert QuantizeFusionPass.optimize is fuse and espdl_interface.simplify is simplify
    assert interface.format_graph is formatted
    assert espdl_exporter.EspdlExporter.prepare_graph is prepare and espdl_exporter.reset_graph_layout is reset
    assert layout.column_merge is merge
    assert all(table.get("RMSNormalization") is norms[p] for p, table in OPERATION_FORWARD_TABLE.items())
    assert all(table.get("Gelu") is gelus[p] for p, table in OPERATION_FORWARD_TABLE.items())


def test_an_unknown_patch_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown ESP-PPQ patches"), esp_ppq_patches.applied(["no_such_patch"]):
        pass

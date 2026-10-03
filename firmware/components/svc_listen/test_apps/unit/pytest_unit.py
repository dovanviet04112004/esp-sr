"""Run svc_listen's suite on board B over one round of Gate 3 sessions that make listen-unit wrote to the board."""

import pytest
from pytest_embedded_idf.dut import IdfDut


@pytest.mark.esp32s3
def test_svc_listen(dut: IdfDut) -> None:
    # A round holds minutes of sessions, each hop through the front end and each window through pitch and the net.
    dut.expect_unity_test_output(timeout=1800)

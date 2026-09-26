"""Run the dsp_spec suite on board B, correctness first and then the timings E6-T3 records."""

import pytest
from pytest_embedded_idf.dut import IdfDut


@pytest.mark.esp32s3
def test_dsp_spec(dut: IdfDut) -> None:
    dut.expect_unity_test_output(timeout=120)

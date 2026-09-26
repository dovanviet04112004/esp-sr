"""Run the drv_audio suite on board B and fail when any case does."""

import pytest
from pytest_embedded_idf.dut import IdfDut


@pytest.mark.esp32s3
def test_drv_audio(dut: IdfDut) -> None:
    dut.expect_unity_test_output(timeout=60)

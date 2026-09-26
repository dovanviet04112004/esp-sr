"""Run the sys_storage suite on board B, twenty self-inflicted power cuts first, and fail when any case does."""

import pytest
from pytest_embedded_idf.dut import IdfDut


@pytest.mark.esp32s3
def test_sys_storage(dut: IdfDut) -> None:
    dut.expect_unity_test_output(timeout=180)

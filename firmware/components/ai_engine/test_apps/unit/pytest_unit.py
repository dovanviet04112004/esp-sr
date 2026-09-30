"""Run the ai_engine suite on board B: both model slots are rewritten with test images and left erased."""

import pytest
from pytest_embedded_idf.dut import IdfDut


@pytest.mark.esp32s3
def test_ai_engine(dut: IdfDut) -> None:
    dut.expect_unity_test_output(timeout=180)

"""Run the ai_engine suite on board B: both model slots are rewritten with test images and left erased."""

import pytest
from pytest_embedded_idf.dut import IdfDut


@pytest.mark.esp32s3
def test_ai_engine(dut: IdfDut) -> None:
    # The Gate 3 case runs every board window of the record through the chip, minutes of it.
    dut.expect_unity_test_output(timeout=900)

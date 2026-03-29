from io import StringIO

import pytest

from ffc_ddw_sum_et.io import (
    DfManager,
    NumericTV,
    ScalarTV,
    Table2DManager,
    TextDataParser,
    numeric_type_set,
    scalar_type_set,
)
from ffc_ddw_sum_et.parameters.base.job_stage_p import JobStageProcessingTimeManager


def test_io_public_api_exports_stable_symbols() -> None:
    assert DfManager.__name__ == "DfManager"
    assert Table2DManager.__name__ == "Table2DManager"
    assert TextDataParser.__name__ == "TextDataParser"
    assert ScalarTV.__name__ == "ScalarTV"
    assert NumericTV.__name__ == "NumericTV"
    assert scalar_type_set == {int, float, str, bool}
    assert numeric_type_set == {int, float}


@pytest.mark.parametrize("dtype", [int, float])
def test_job_stage_processing_time_manager_accepts_numeric_dtypes(dtype: type) -> None:
    manager = JobStageProcessingTimeManager.from_text_stream(
        StringIO("1 2\n3 4\n"),
        row_count=2,
        dtype=dtype,
    )

    assert isinstance(manager, JobStageProcessingTimeManager)
    assert manager.df.shape == (2, 2)


@pytest.mark.parametrize(
    ("dtype", "message"),
    [
        (bool, "Boolean dtype is not supported for processing times."),
        (str, "Expected dtype to be a numeric type"),
    ],
)
def test_job_stage_processing_time_manager_rejects_non_numeric_dtypes(
    dtype: type,
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        JobStageProcessingTimeManager.from_text_stream(
            StringIO("1 2\n3 4\n"),
            row_count=2,
            dtype=dtype,
        )

"""Tests for ``JobBatchCpOption`` validation."""

from __future__ import annotations

import pytest

from ffc_ddw_sum_et.algorithm.job_batch_cp.option import JobBatchCpOption


class TestJobBatchCpOption:
    def test_minimal_option(self) -> None:
        opt = JobBatchCpOption(job_sequence=("j0", "j1"))
        assert opt.job_sequence == ("j0", "j1")
        assert opt.batch_size == 1
        assert opt.num_batches is None
        assert opt.pf_method == "PF1"

    def test_empty_job_sequence_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            JobBatchCpOption(job_sequence=())

    def test_duplicate_job_sequence_raises(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            JobBatchCpOption(job_sequence=("j0", "j1", "j0"))

    def test_batch_size_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            JobBatchCpOption(job_sequence=("j0",), batch_size=0)

    def test_num_batches_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="num_batches"):
            JobBatchCpOption(job_sequence=("j0",), num_batches=0)

    def test_pf_method_none_raises(self) -> None:
        with pytest.raises(ValueError, match="pf_method"):
            JobBatchCpOption(job_sequence=("j0",), pf_method=None)

    def test_time_factor_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="time_factor"):
            JobBatchCpOption(job_sequence=("j0",), time_factor=0)

    def test_horizon_multiplier_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="horizon_multiplier"):
            JobBatchCpOption(job_sequence=("j0",), horizon_multiplier=0)

    def test_all_pf_methods_accepted(self) -> None:
        for method in ("PF0", "PF1", "PF2", "MPF23"):
            opt = JobBatchCpOption(job_sequence=("j0",), pf_method=method)
            assert opt.pf_method == method


class TestProportionalBatchTl:
    """``batch_tl_mode="proportional"`` needs its own multiplier.

    Without the XOR-style check the mode silently degrades to "no per-batch
    limit at all" (``resolve_per_step_tl`` returns ``None`` for it), which lets
    the first batch consume the whole pass.
    """

    def test_proportional_without_multiplier_raises(self) -> None:
        with pytest.raises(ValueError, match="destroyed_op_tl_multiplier"):
            JobBatchCpOption(job_sequence=("j0",), batch_tl_mode="proportional")

    def test_proportional_with_multiplier_accepted(self) -> None:
        opt = JobBatchCpOption(
            job_sequence=("j0",),
            batch_tl_mode="proportional",
            destroyed_op_tl_multiplier=0.005,
        )
        assert opt.batch_tl_mode == "proportional"
        assert opt.destroyed_op_tl_multiplier == 0.005

    def test_non_positive_multiplier_raises(self) -> None:
        with pytest.raises(ValueError, match="destroyed_op_tl_multiplier"):
            JobBatchCpOption(job_sequence=("j0",), destroyed_op_tl_multiplier=0.0)

    def test_multiplier_defaults_to_none(self) -> None:
        assert JobBatchCpOption(job_sequence=("j0",)).destroyed_op_tl_multiplier is None

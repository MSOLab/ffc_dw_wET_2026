"""Tests for ``JobContribCpOption`` and ``resolve_jd_count_target``."""

from __future__ import annotations

import pytest

from ffc_ddw_sum_et.algorithm.job_contrib_cp.option import JobContribCpOption
from ffc_ddw_sum_et.orchestration.value_resolver import resolve_jd_count_target


class TestResolveJdCountTarget:
    def test_absolute_int(self) -> None:
        assert resolve_jd_count_target(5, 100) == 5

    def test_absolute_str(self) -> None:
        assert resolve_jd_count_target("5", 100) == 5

    def test_ratio_n(self) -> None:
        assert resolve_jd_count_target("0.05n", 100) == 5

    def test_ratio_ceil(self) -> None:
        assert resolve_jd_count_target("0.03n", 100) == 3

    def test_ratio_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_jd_count_target("0n", 100)

    def test_ratio_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_jd_count_target("-0.1n", 100)

    def test_absolute_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_jd_count_target(0, 100)

    def test_absolute_zero_str_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_jd_count_target("0", 100)

    def test_absolute_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_jd_count_target(-1, 100)

    def test_exceeds_n_saturates(self) -> None:
        assert resolve_jd_count_target(200, 100) == 100

    def test_invalid_str_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_jd_count_target("abc", 100)

    def test_empty_ratio_raises(self) -> None:
        with pytest.raises(ValueError):
            resolve_jd_count_target("n", 100)

    def test_tiny_ratio_ceil_to_1(self) -> None:
        assert resolve_jd_count_target("0.001n", 100) == 1


class TestJobContribCpOption:
    def test_minimal_option(self) -> None:
        opt = JobContribCpOption(jd_count_target=1)
        assert opt.jd_count_target == 1
        assert opt.pf_method == "PF1"

    def test_pf_method_none_raises(self) -> None:
        with pytest.raises(ValueError):
            JobContribCpOption(jd_count_target=1, pf_method=None)

    def test_jd_count_target_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="jd_count_target"):
            JobContribCpOption(jd_count_target=0)

    def test_jd_count_target_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="jd_count_target"):
            JobContribCpOption(jd_count_target=-1)

    def test_all_pf_methods_accepted(self) -> None:
        for method in ("PF0", "PF1", "PF2", "MPF23"):
            opt = JobContribCpOption(jd_count_target=1, pf_method=method)
            assert opt.pf_method == method

    def test_time_factor_defaults_to_1(self) -> None:
        opt = JobContribCpOption(jd_count_target=1)
        assert opt.time_factor == 1

    def test_time_factor_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="time_factor"):
            JobContribCpOption(jd_count_target=1, time_factor=0)

    def test_horizon_multiplier_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="horizon_multiplier"):
            JobContribCpOption(jd_count_target=1, horizon_multiplier=0)

    def test_solver_log_path_getter_defaults_to_none(self) -> None:
        opt = JobContribCpOption(jd_count_target=1)
        assert opt.solver_log_path_getter is None

    def test_solver_log_path_getter_accepts_callable(self) -> None:
        def my_getter(s: str) -> str:
            return s

        opt = JobContribCpOption(jd_count_target=1, solver_log_path_getter=my_getter)
        assert opt.solver_log_path_getter is my_getter


class TestDestroySetXor:
    """Exactly one of jd_count_target / destroy_job_ids selects the destroy set.

    Reading the option alone must answer "what decides which jobs are
    destroyed?", so neither "both" nor "neither" is allowed.
    """

    def test_neither_raises(self) -> None:
        with pytest.raises(ValueError, match="Exactly one"):
            JobContribCpOption()

    def test_both_raises(self) -> None:
        with pytest.raises(ValueError, match="Exactly one"):
            JobContribCpOption(jd_count_target=1, destroy_job_ids=("j0",))

    def test_jd_count_target_alone_is_accepted(self) -> None:
        opt = JobContribCpOption(jd_count_target=2)
        assert opt.jd_count_target == 2
        assert opt.destroy_job_ids is None

    def test_destroy_job_ids_alone_is_accepted(self) -> None:
        opt = JobContribCpOption(destroy_job_ids=("j0", "j2"))
        assert opt.destroy_job_ids == ("j0", "j2")
        assert opt.jd_count_target is None

    def test_empty_destroy_job_ids_raises(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            JobContribCpOption(destroy_job_ids=())

    def test_duplicate_destroy_job_ids_raises(self) -> None:
        with pytest.raises(ValueError, match="duplicates"):
            JobContribCpOption(destroy_job_ids=("j0", "j1", "j0"))

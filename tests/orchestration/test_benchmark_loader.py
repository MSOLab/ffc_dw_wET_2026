from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ffc_ddw_sum_et.orchestration.benchmark_loader import BenchmarkLoader


def _write_pra_file(path: Path) -> None:
    """Write a minimal valid PRA2017 HFSDDW file: 2 jobs x 2 stages x 1 machine each."""
    path.write_text(
        "HFSDDW\n"
        "2\t2\t2\n"
        "0\t3\t1\t5\n"
        "0\t4\t1\t2\n"
        "LBCmax: 7\n"
        "RELDUE\n"
        "-1\t5\t1\t2\n"
        "-1\t6\t1\t1\n"
        "DDW\n"
        "3\t7\n"
        "4\t8\n"
    )


def _write_index_csv(path: Path, rows: list[tuple[int, str]]) -> None:
    lines = ["insIndex,ffc_ddw_sum_et_filename"]
    for idx, name in rows:
        lines.append(f'{idx:04d},"{name}"')
    path.write_text("\n".join(lines) + "\n")


def test_load_all_with_ins_index(tmp_path: Path) -> None:
    file_name = "Instance_2_2_1_0,2_0,2_10_Rep0.txt"
    _write_pra_file(tmp_path / file_name)
    csv_path = tmp_path / "match.csv"
    _write_index_csv(csv_path, [(0, file_name)])

    loader = BenchmarkLoader(directory=tmp_path, ins_index_source=csv_path)
    instances = loader.load_all(ins_index=0)

    assert len(instances) == 1
    assert instances[0].name == Path(file_name).stem


def test_load_all_with_ins_index_partially_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    file_name = "Instance_2_2_1_0,2_0,2_10_Rep0.txt"
    _write_pra_file(tmp_path / file_name)
    csv_path = tmp_path / "match.csv"
    _write_index_csv(csv_path, [(0, file_name)])

    loader = BenchmarkLoader(directory=tmp_path, ins_index_source=csv_path)
    with caplog.at_level(logging.WARNING):
        instances = loader.load_all(ins_index=[0, 99])

    assert len(instances) == 1
    assert any("99" in record.message for record in caplog.records)


def test_load_all_with_ins_index_all_missing(tmp_path: Path) -> None:
    csv_path = tmp_path / "match.csv"
    _write_index_csv(csv_path, [])

    loader = BenchmarkLoader(directory=tmp_path, ins_index_source=csv_path)
    with pytest.raises(FileNotFoundError):
        loader.load_all(ins_index=[42])


def test_load_all_with_file_pattern(tmp_path: Path) -> None:
    keep_name = "Instance_2_2_1_0,2_0,2_10_Rep0.txt"
    _write_pra_file(tmp_path / keep_name)
    (tmp_path / "notes.md").write_text("ignore me")

    loader = BenchmarkLoader(directory=tmp_path)
    instances = loader.load_all(file_pattern=".txt")

    assert len(instances) == 1
    assert instances[0].name == Path(keep_name).stem


def test_load_all_no_files_raises(tmp_path: Path) -> None:
    loader = BenchmarkLoader(directory=tmp_path)
    with pytest.raises(FileNotFoundError):
        loader.load_all()


def test_load_all_skips_parse_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    good_name = "Instance_2_2_1_0,2_0,2_10_Rep0.txt"
    _write_pra_file(tmp_path / good_name)
    (tmp_path / "broken.txt").write_text("NOT A VALID HEADER\n")

    loader = BenchmarkLoader(directory=tmp_path)
    with caplog.at_level(logging.ERROR):
        instances = loader.load_all()

    assert [i.name for i in instances] == [Path(good_name).stem]
    assert any("broken" in record.message for record in caplog.records)

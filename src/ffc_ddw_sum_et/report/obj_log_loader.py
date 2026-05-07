"""Convert ``<instance>_obj_log.json`` (+ manifest) into a tabular form
consumable by the vendored hybridflowshop chart writers.

The on-disk obj_log shape is documented in
``ffcddw_single_instance_runner._save_obj_log``. Each series
(``obj_value`` / ``obj_bound``) is a flat ``time -> value`` mapping plus a
``notes`` mapping marking the *endpoint* of every controller step. The
endpoint label format is ``"<step_idx>-<subroutine_name>"`` (set by routix
``_get_call_context_of_current_method``).

This module re-bundles that flat trajectory into one :class:`CallSegment`
per controller step so the chart code can treat each subroutine call as an
atomic interval with a ``global_start_sec``, ``global_end_sec``, and a list
of progress points.

Failure policy: if ``<instance>_instance_result.yaml`` is missing, or the
note label does not match ``^\\d+-(.+)$``, raise. We never emit "best
effort" rows because partial averages mislead the resulting chart.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from routix.io import ArtifactLayout, load_yaml

logger = logging.getLogger(__name__)

_STEP_LABEL_RE = re.compile(r"^(\d+)-(.+)$")


@dataclass(frozen=True)
class ProgPoint:
    """One point in a series trajectory (controller-frame seconds)."""

    global_sec: float
    value: float


@dataclass(frozen=True)
class CallSegment:
    """One controller-step's contribution to a single series."""

    call_index: int  # 1-based, taken from the step-label prefix
    subroutine_name: str
    prefixed_subroutine_name: str
    global_start_sec: float
    global_end_sec: float
    points: tuple[ProgPoint, ...]

    @property
    def elapsed_sec(self) -> float:
        return self.global_end_sec - self.global_start_sec


@dataclass(frozen=True)
class InstanceProgression:
    """Decoded trajectory for one instance, both series."""

    instance_id: str
    job_cnt: int
    stage_cnt: int
    timelimit_sec: float
    obj_value_calls: tuple[CallSegment, ...]
    obj_bound_calls: tuple[CallSegment, ...]


def _parse_step_label(label: str) -> tuple[int, str]:
    match = _STEP_LABEL_RE.match(label)
    if match is None:
        raise ValueError(
            f"obj_log note label does not match '<idx>-<subroutine_name>': {label!r}"
        )
    return int(match.group(1)), match.group(2)


def _build_calls_for_series(
    data: dict[str, float],
    notes: dict[str, str],
) -> tuple[CallSegment, ...]:
    if not notes:
        return ()

    sorted_data = sorted(
        ((float(k), float(v)) for k, v in data.items()), key=lambda x: x[0]
    )
    sorted_endpoints = sorted(
        ((float(k), str(v)) for k, v in notes.items()), key=lambda x: x[0]
    )

    calls: list[CallSegment] = []
    prev_end = 0.0
    cursor = 0
    for end_sec, label in sorted_endpoints:
        idx, sub_name = _parse_step_label(label)
        seg_points: list[ProgPoint] = []
        # Each data point belongs to the segment whose interval
        # (prev_end, end_sec] contains its timestamp. _save_obj_log writes
        # both data[end] and notes[end], so the endpoint always shows up.
        while cursor < len(sorted_data) and sorted_data[cursor][0] <= end_sec:
            t, v = sorted_data[cursor]
            if t > prev_end:
                seg_points.append(ProgPoint(global_sec=t, value=v))
            cursor += 1

        calls.append(
            CallSegment(
                call_index=idx,
                subroutine_name=sub_name,
                prefixed_subroutine_name=label,
                global_start_sec=prev_end,
                global_end_sec=end_sec,
                points=tuple(seg_points),
            )
        )
        prev_end = end_sec

    return tuple(calls)


def _load_obj_log_json(obj_log_path: Path) -> dict[str, Any]:
    with open(obj_log_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_series_block(
    payload: dict[str, Any], series_key: str, source: Path
) -> tuple[dict[str, float], dict[str, str]]:
    """Return ``(data, notes)`` for ``series_key`` from a raw obj_log payload.

    Tolerates the series being absent (returns empty mappings) but raises
    ``ValueError`` on shape drift — non-mapping series block, non-mapping
    ``data`` / ``notes``. Per the module-level "raise loudly on drift"
    policy: a partial / mistyped payload should not silently produce
    misleading rows downstream.
    """
    block = payload.get(series_key)
    if block is None:
        return {}, {}
    if not isinstance(block, dict):
        raise ValueError(
            f"obj_log[{series_key!r}] in {source} is {type(block).__name__}, "
            "expected mapping"
        )
    data = block.get("data", {})
    notes = block.get("notes", {})
    if not isinstance(data, dict):
        raise ValueError(
            f"obj_log[{series_key!r}]['data'] in {source} is "
            f"{type(data).__name__}, expected mapping"
        )
    if not isinstance(notes, dict):
        raise ValueError(
            f"obj_log[{series_key!r}]['notes'] in {source} is "
            f"{type(notes).__name__}, expected mapping"
        )
    return data, notes


def _read_manifest(manifest_path: Path) -> dict[str, Any]:
    raw = load_yaml(manifest_path)
    if not isinstance(raw, dict):
        raise ValueError(f"instance_result manifest is not a mapping: {manifest_path}")
    return raw


def load_instance_progression(
    obj_log_path: Path,
    manifest_path: Path,
    *,
    instance_id: str | None = None,
) -> InstanceProgression:
    """Read both files and decode them into an :class:`InstanceProgression`.

    ``instance_id`` defaults to the manifest's ``instance_name`` field.
    """
    payload = _load_obj_log_json(obj_log_path)
    manifest = _read_manifest(manifest_path)

    if instance_id is None:
        instance_id = str(manifest["instance_name"])

    job_cnt = int(manifest["job_count"])
    stage_cnt = int(manifest["stage_count"])
    timelimit_sec = float(manifest["timelimit"])

    obj_value_calls = _build_calls_for_series(
        *_extract_series_block(payload, "obj_value", obj_log_path)
    )
    obj_bound_calls = _build_calls_for_series(
        *_extract_series_block(payload, "obj_bound", obj_log_path)
    )

    return InstanceProgression(
        instance_id=instance_id,
        job_cnt=job_cnt,
        stage_cnt=stage_cnt,
        timelimit_sec=timelimit_sec,
        obj_value_calls=obj_value_calls,
        obj_bound_calls=obj_bound_calls,
    )


def iter_scenario_instance_progressions(
    layout: ArtifactLayout,
    scenario_name: str,
    *,
    instance_names: Iterable[str] | None = None,
) -> list[InstanceProgression]:
    """Load every instance under ``scenario_name`` that has both files.

    When ``instance_names`` is omitted, the function discovers instances by
    scanning the scenario directory for sub-directories that hold an
    ``<instance>_obj_log.json``. Instances missing the obj_log are silently
    skipped; instances that have the obj_log but lack the manifest raise.
    """
    # Read-side path lookup: layout.scenario_dir() is a write-side registration
    # API that raises on the second call for the same scenario. Post-run
    # reporting runs after the scenario was already registered during the
    # actual run, so we use the non-registering private accessor instead.
    scenario_dir = layout._scenario_path(scenario_name)
    if not scenario_dir.exists():
        return []

    if instance_names is None:
        candidates = [p.name for p in sorted(scenario_dir.iterdir()) if p.is_dir()]
    else:
        candidates = list(instance_names)

    results: list[InstanceProgression] = []
    for ins in candidates:
        obj_log_path = layout.artifact_path(
            "obj_log_json", scenario_name=scenario_name, instance_name=ins
        )
        if not obj_log_path.exists():
            logger.debug("Skipping %s: no obj_log_json", ins)
            continue
        manifest_path = layout.artifact_path(
            "instance_result_manifest",
            scenario_name=scenario_name,
            instance_name=ins,
        )
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"obj_log_json present but manifest missing for {ins}: {manifest_path}"
            )
        results.append(load_instance_progression(obj_log_path, manifest_path))
    return results


def _calls_for_series(
    progression: InstanceProgression, series: str
) -> tuple[CallSegment, ...]:
    if series == "obj_value":
        return progression.obj_value_calls
    if series == "obj_bound":
        return progression.obj_bound_calls
    raise ValueError(f"unknown series: {series!r}")


def build_endpoint_df(
    progressions: list[InstanceProgression],
    *,
    series: str = "obj_value",
) -> pd.DataFrame:
    """One row per (instance, controller step). ``rpd_f`` is left NaN here —
    callers join a baseline DataFrame to fill it.
    """
    rows: list[dict[str, Any]] = []
    for prog in progressions:
        if prog.timelimit_sec <= 0:
            raise ValueError(
                f"non-positive timelimit_sec for instance {prog.instance_id}: "
                f"{prog.timelimit_sec}"
            )
        for call in _calls_for_series(prog, series):
            if not call.points:
                continue
            endpoint_value = call.points[-1].value
            rows.append(
                {
                    "instance_id": prog.instance_id,
                    "job_cnt": prog.job_cnt,
                    "stage_cnt": prog.stage_cnt,
                    "subroutine_name": call.subroutine_name,
                    "prefixed_subroutine_name": call.prefixed_subroutine_name,
                    "call_index": call.call_index,
                    "global_end_sec": call.global_end_sec,
                    "norm_time": call.global_end_sec / prog.timelimit_sec,
                    "obj_value": endpoint_value,
                }
            )
    return pd.DataFrame(rows)


def build_raw_progression_df(
    progressions: list[InstanceProgression],
    *,
    series: str = "obj_value",
) -> pd.DataFrame:
    """One row per (instance, controller step, point). Used for the line view."""
    rows: list[dict[str, Any]] = []
    for prog in progressions:
        if prog.timelimit_sec <= 0:
            raise ValueError(
                f"non-positive timelimit_sec for instance {prog.instance_id}: "
                f"{prog.timelimit_sec}"
            )
        for call in _calls_for_series(prog, series):
            for point in call.points:
                rows.append(
                    {
                        "instance_id": prog.instance_id,
                        "job_cnt": prog.job_cnt,
                        "stage_cnt": prog.stage_cnt,
                        "subroutine_name": call.subroutine_name,
                        "prefixed_subroutine_name": call.prefixed_subroutine_name,
                        "call_index": call.call_index,
                        "global_sec": point.global_sec,
                        "norm_time": point.global_sec / prog.timelimit_sec,
                        "obj_value": point.value,
                    }
                )
    return pd.DataFrame(rows)

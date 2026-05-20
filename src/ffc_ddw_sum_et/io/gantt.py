"""Gantt chart plotters.

``GanttPlotter`` renders a non-preemptive ``FFcSchedule``-shaped input
keyed on ``(job, stage, machine)`` maps. ``PreemptiveGanttPlotter``
renders a single-stage preemptive schedule where each ``(job, machine)``
may appear in multiple disjoint ``[start, end)`` segments. Both share
``PlotterBase`` for figure lifecycle, drawing primitives, machine-lane
layout, and axes finalization.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping, Sequence

import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

logger = logging.getLogger(__name__)


class PlotterBase:
    """Shared figure lifecycle, drawing primitives, and axes layout."""

    fig: Figure | None
    ax: Axes | None

    cmap_name = "tab20"
    machine_height = 1.0
    bar_height = 0.8
    bar_alpha = 0.5
    grid_alpha = 0.3
    figsize = (12, 8)
    default_title = "FFc-DDW Schedule Gantt Chart"

    def __init__(self) -> None:
        self.fig = None
        self.ax = None

    def _ensure_figure(self) -> None:
        if self.fig is None or self.ax is None:
            self.fig, self.ax = plt.subplots(figsize=self.figsize)

    def close(self) -> None:
        if self.fig is not None:
            plt.close(self.fig)
            self.fig = None
            self.ax = None

    def create_job_to_color_map(
        self, job_list: Sequence[str]
    ) -> dict[str, tuple[float, float, float, float]]:
        cmap = plt.get_cmap(self.cmap_name)
        n_jobs = max(len(job_list) - 1, 1)
        return {job: cmap(i / n_jobs) for i, job in enumerate(job_list)}

    @staticmethod
    def create_machine_lanes(
        keys: Sequence[tuple[str, str, str]],
        stage_list: Sequence[str],
        machine_list_per_stage: Mapping[str, Sequence[str]],
    ) -> tuple[list[tuple[str, str]], list[str]]:
        """Build the ``(stage, machine)`` lane order and their y-axis labels.

        ``keys`` is a sequence of ``(job, stage, machine)`` triples
        consulted only when a stage has no explicit machine list to
        fall back on.
        """
        machine_lanes: list[tuple[str, str]] = []
        machine_labels: list[str] = []
        for stage in stage_list:
            machines = (
                machine_list_per_stage.get(stage) if machine_list_per_stage else None
            )
            if not machines:
                machines = sorted({mc for (_, stg, mc) in keys if stg == stage})
            for mc in machines:
                machine_lanes.append((stage, mc))
                machine_labels.append(f"{stage}-{mc}")
        return machine_lanes, machine_labels

    def set_x_horizon(
        self,
        earliest: int,
        latest: int,
        force_start: int | None = None,
        force_end: int | None = None,
    ) -> None:
        assert self.ax is not None
        if force_start is not None:
            earliest = force_start
        if force_end is not None:
            latest = force_end
        self.ax.set_xlim(earliest, latest + 1)

    def draw_operation_bar(
        self,
        job: str,
        s_time: int,
        e_time: int,
        color: tuple[float, float, float, float],
        y: float,
        show_label: bool = True,
        show_duration: bool = True,
        highlight: bool = False,
    ) -> None:
        assert self.ax is not None
        duration = e_time - s_time
        linewidth = 3.0 if highlight else 1.0
        alpha = 1.0 if highlight else self.bar_alpha
        self.ax.add_patch(
            patches.Rectangle(
                (s_time, y),
                duration,
                self.bar_height,
                edgecolor="black",
                facecolor=color,
                alpha=alpha,
                linewidth=linewidth,
            )
        )
        if show_label and duration > 0:
            self.ax.text(
                (s_time + e_time) / 2,
                y + self.bar_height / 2,
                job,
                ha="center",
                va="center",
                color="black",
                fontsize=8,
            )
        if show_duration and duration > 0:
            self.ax.text(
                (s_time + e_time) / 2,
                y + self.bar_height - 0.05,
                str(duration),
                ha="center",
                va="bottom",
                color="gray",
                fontsize=7,
            )

    def _finalize_axes(
        self, machine_labels: Sequence[str], title: str | None = None
    ) -> None:
        assert self.ax is not None
        lane_count = len(machine_labels)
        self.ax.set_yticks([y + 0.4 for y in range(lane_count)])
        self.ax.set_yticklabels(list(machine_labels))
        self.ax.set_ylim(
            -self.machine_height / 2,
            lane_count + (self.bar_height - self.machine_height / 2),
        )
        self.ax.set_xlabel("Time")
        self.ax.set_title(title or self.default_title)
        self.ax.grid(True, axis="x", linestyle="--", alpha=self.grid_alpha)
        self.ax.invert_yaxis()
        plt.tight_layout()


class GanttPlotter(PlotterBase):
    """Non-preemptive Gantt renderer keyed on ``(job, stage, machine)``."""

    def display(
        self,
        start_time_map: Mapping[tuple[str, str, str], int],
        end_time_map: Mapping[tuple[str, str, str], int],
        job_list: Sequence[str] | None = None,
        stage_list: Sequence[str] | None = None,
        machine_list_per_stage: Mapping[str, Sequence[str]] | None = None,
        all_job_list: Sequence[str] | None = None,
        highlight_op_set: set[tuple[str, str]] | None = None,
        force_start: int | None = None,
        force_end: int | None = None,
    ) -> None:
        self.plot(
            start_time_map,
            end_time_map,
            job_list=job_list,
            stage_list=stage_list,
            machine_list_per_stage=machine_list_per_stage,
            all_job_list=all_job_list,
            highlight_op_set=highlight_op_set,
            force_start=force_start,
            force_end=force_end,
        )
        plt.show()
        self.close()

    def export(
        self,
        file_path: Path,
        start_time_map: Mapping[tuple[str, str, str], int],
        end_time_map: Mapping[tuple[str, str, str], int],
        job_list: Sequence[str] | None = None,
        stage_list: Sequence[str] | None = None,
        machine_list_per_stage: Mapping[str, Sequence[str]] | None = None,
        all_job_list: Sequence[str] | None = None,
        highlight_op_set: set[tuple[str, str]] | None = None,
        force_start: int | None = None,
        force_end: int | None = None,
        title: str | None = None,
    ) -> None:
        self._ensure_figure()
        assert self.ax is not None
        assert self.fig is not None
        self.ax.clear()
        try:
            self.plot(
                start_time_map,
                end_time_map,
                job_list=job_list,
                stage_list=stage_list,
                machine_list_per_stage=machine_list_per_stage,
                all_job_list=all_job_list,
                highlight_op_set=highlight_op_set,
                force_start=force_start,
                force_end=force_end,
                title=title,
            )
            self.fig.savefig(file_path, bbox_inches="tight", dpi=300)
            logger.info("Gantt chart saved to %s", file_path)
        finally:
            self.close()

    def plot(
        self,
        start_time_map: Mapping[tuple[str, str, str], int],
        end_time_map: Mapping[tuple[str, str, str], int],
        job_list: Sequence[str] | None = None,
        stage_list: Sequence[str] | None = None,
        machine_list_per_stage: Mapping[str, Sequence[str]] | None = None,
        all_job_list: Sequence[str] | None = None,
        highlight_op_set: set[tuple[str, str]] | None = None,
        force_start: int | None = None,
        force_end: int | None = None,
        title: str | None = None,
    ) -> None:
        self._ensure_figure()
        assert self.ax is not None
        earliest, latest = GanttPlotter.compute_horizon(start_time_map, end_time_map)
        self.set_x_horizon(
            earliest, latest, force_start=force_start, force_end=force_end
        )

        if not job_list:
            _job_list = sorted({j for (j, _, _) in start_time_map})
        else:
            _job_list = list(job_list)
        if not stage_list:
            _stage_list = sorted({i for (_, i, _) in start_time_map})
        else:
            _stage_list = list(stage_list)

        _machine_list_per_stage: dict[str, Sequence[str]] = {
            stage: [] for stage in _stage_list
        }
        for stage in _stage_list:
            if machine_list_per_stage is None or not machine_list_per_stage.get(stage):
                _machine_list_per_stage[stage] = sorted(
                    {mc for (_, stg, mc) in start_time_map if stg == stage}
                )
            else:
                _machine_list_per_stage[stage] = list(machine_list_per_stage[stage])

        if all_job_list:
            job_to_color = self.create_job_to_color_map(list(all_job_list))
        else:
            job_to_color = self.create_job_to_color_map(_job_list)

        machine_lanes, machine_labels = PlotterBase.create_machine_lanes(
            list(start_time_map.keys()), _stage_list, _machine_list_per_stage
        )
        machine_to_y = {
            mc: self.machine_height * idx for idx, mc in enumerate(machine_lanes)
        }
        self.draw_operation_bars(
            start_time_map=start_time_map,
            end_time_map=end_time_map,
            job_to_color=job_to_color,
            machine_to_y=machine_to_y,
            job_list=_job_list,
            highlight_op_set=highlight_op_set,
        )

        self._finalize_axes(machine_labels, title=title)

    @staticmethod
    def compute_horizon(
        start_time_map: Mapping[tuple[str, str, str], int],
        end_time_map: Mapping[tuple[str, str, str], int],
    ) -> tuple[int, int]:
        if not start_time_map or not end_time_map:
            raise ValueError("start_time_map and end_time_map must not be empty.")
        return min(start_time_map.values()), max(end_time_map.values())

    def draw_operation_bars(
        self,
        start_time_map: Mapping[tuple[str, str, str], int],
        end_time_map: Mapping[tuple[str, str, str], int],
        job_to_color: Mapping[str, tuple[float, float, float, float]],
        machine_to_y: Mapping[tuple[str, str], float],
        job_list: Sequence[str],
        highlight_op_set: set[tuple[str, str]] | None = None,
    ) -> None:
        for (job, stage, machine), s_time in start_time_map.items():
            if job_list and job not in job_list:
                continue
            if (stage, machine) not in machine_to_y:
                continue
            e_time = end_time_map[(job, stage, machine)]
            y = machine_to_y[(stage, machine)]
            color = job_to_color[job]
            is_highlight = (
                highlight_op_set is not None and (job, stage) in highlight_op_set
            )
            self.draw_operation_bar(
                job=job,
                s_time=s_time,
                e_time=e_time,
                color=color,
                y=y,
                highlight=is_highlight,
            )


class PreemptiveGanttPlotter(PlotterBase):
    """Gantt renderer for a single-stage preemptive schedule.

    Each segment is ``(job, stage, machine, start, end)``. A single
    ``(job, machine)`` pair may appear in multiple disjoint segments;
    each one is drawn as its own rectangle.
    """

    default_title = "FFc-DDW MCF Preemptive Schedule Gantt Chart"

    def display(
        self,
        segments: Sequence[tuple[str, str, str, int, int]],
        *,
        stage_id: str,
        machines: Sequence[str],
        jobs: Sequence[str] | None = None,
        all_jobs: Sequence[str] | None = None,
        highlight_op_set: set[tuple[str, str]] | None = None,
        force_start: int | None = None,
        force_end: int | None = None,
    ) -> None:
        self.plot(
            segments,
            stage_id=stage_id,
            machines=machines,
            jobs=jobs,
            all_jobs=all_jobs,
            highlight_op_set=highlight_op_set,
            force_start=force_start,
            force_end=force_end,
        )
        plt.show()
        self.close()

    def export(
        self,
        file_path: Path,
        segments: Sequence[tuple[str, str, str, int, int]],
        *,
        stage_id: str,
        machines: Sequence[str],
        jobs: Sequence[str] | None = None,
        all_jobs: Sequence[str] | None = None,
        highlight_op_set: set[tuple[str, str]] | None = None,
        force_start: int | None = None,
        force_end: int | None = None,
        title: str | None = None,
    ) -> None:
        self._ensure_figure()
        assert self.ax is not None
        assert self.fig is not None
        self.ax.clear()
        try:
            self.plot(
                segments,
                stage_id=stage_id,
                machines=machines,
                jobs=jobs,
                all_jobs=all_jobs,
                highlight_op_set=highlight_op_set,
                force_start=force_start,
                force_end=force_end,
                title=title,
            )
            self.fig.savefig(file_path, bbox_inches="tight", dpi=300)
            logger.info("Preemptive Gantt chart saved to %s", file_path)
        finally:
            self.close()

    def plot(
        self,
        segments: Sequence[tuple[str, str, str, int, int]],
        *,
        stage_id: str,
        machines: Sequence[str],
        jobs: Sequence[str] | None = None,
        all_jobs: Sequence[str] | None = None,
        highlight_op_set: set[tuple[str, str]] | None = None,
        force_start: int | None = None,
        force_end: int | None = None,
        title: str | None = None,
    ) -> None:
        self._ensure_figure()
        assert self.ax is not None

        earliest, latest = PreemptiveGanttPlotter.compute_horizon(segments)
        self.set_x_horizon(
            earliest, latest, force_start=force_start, force_end=force_end
        )

        if not jobs:
            _job_list = sorted({seg[0] for seg in segments})
        else:
            _job_list = list(jobs)

        _machine_list_per_stage: Mapping[str, Sequence[str]] = {
            stage_id: list(machines)
        }
        keys_for_lanes = [(seg[0], seg[1], seg[2]) for seg in segments]

        if all_jobs:
            job_to_color = self.create_job_to_color_map(list(all_jobs))
        else:
            job_to_color = self.create_job_to_color_map(_job_list)

        machine_lanes, machine_labels = PlotterBase.create_machine_lanes(
            keys_for_lanes, [stage_id], _machine_list_per_stage
        )
        machine_to_y = {
            mc: self.machine_height * idx for idx, mc in enumerate(machine_lanes)
        }
        self.draw_segment_bars(
            segments=segments,
            job_to_color=job_to_color,
            machine_to_y=machine_to_y,
            job_list=_job_list,
            highlight_op_set=highlight_op_set,
        )

        self._finalize_axes(machine_labels, title=title)

    @staticmethod
    def compute_horizon(
        segments: Sequence[tuple[str, str, str, int, int]],
    ) -> tuple[int, int]:
        if not segments:
            raise ValueError("segments must not be empty.")
        starts = [seg[3] for seg in segments]
        ends = [seg[4] for seg in segments]
        return min(starts), max(ends)

    def draw_segment_bars(
        self,
        segments: Sequence[tuple[str, str, str, int, int]],
        job_to_color: Mapping[str, tuple[float, float, float, float]],
        machine_to_y: Mapping[tuple[str, str], float],
        job_list: Sequence[str],
        highlight_op_set: set[tuple[str, str]] | None = None,
    ) -> None:
        job_filter = set(job_list) if job_list else None
        for job, stage, machine, s_time, e_time in segments:
            if job_filter is not None and job not in job_filter:
                continue
            if (stage, machine) not in machine_to_y:
                continue
            y = machine_to_y[(stage, machine)]
            color = job_to_color[job]
            is_highlight = (
                highlight_op_set is not None and (job, stage) in highlight_op_set
            )
            self.draw_operation_bar(
                job=job,
                s_time=s_time,
                e_time=e_time,
                color=color,
                y=y,
                highlight=is_highlight,
            )

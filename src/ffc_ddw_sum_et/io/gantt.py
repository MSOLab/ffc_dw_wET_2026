"""Gantt chart plotter ported from hybridflowshop/painter/gantt.py."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path

import matplotlib.patches as patches
import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure


class GanttPlotter:
    fig: Figure | None
    ax: Axes | None

    cmap_name = "tab20"
    machine_height = 1.0
    bar_height = 0.8
    bar_alpha = 0.5
    grid_alpha = 0.3
    figsize = (12, 8)

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
            )
            self.fig.savefig(file_path, bbox_inches="tight", dpi=300)
            logging.debug("Gantt chart saved to %s", file_path)
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
    ) -> None:
        self._ensure_figure()
        assert self.ax is not None
        self.set_x_horizon(
            start_time_map, end_time_map, force_start=force_start, force_end=force_end
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

        machine_lanes, machine_labels = GanttPlotter.create_machine_lanes(
            start_time_map, _stage_list, _machine_list_per_stage
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

        self.ax.set_yticks([y + 0.4 for y in range(len(machine_lanes))])
        self.ax.set_yticklabels(machine_labels)
        self.ax.set_ylim(
            -self.machine_height / 2,
            len(machine_lanes) + (self.bar_height - self.machine_height / 2),
        )
        self.ax.set_xlabel("Time")
        self.ax.set_title("FFc-DDW Schedule Gantt Chart")
        self.ax.grid(True, axis="x", linestyle="--", alpha=self.grid_alpha)
        self.ax.invert_yaxis()
        plt.tight_layout()

    @staticmethod
    def compute_horizon(
        start_time_map: Mapping[tuple[str, str, str], int],
        end_time_map: Mapping[tuple[str, str, str], int],
    ) -> tuple[int, int]:
        if not start_time_map or not end_time_map:
            raise ValueError("start_time_map and end_time_map must not be empty.")
        return min(start_time_map.values()), max(end_time_map.values())

    def set_x_horizon(
        self,
        start_time_map: Mapping[tuple[str, str, str], int],
        end_time_map: Mapping[tuple[str, str, str], int],
        force_start: int | None = None,
        force_end: int | None = None,
    ) -> None:
        assert self.ax is not None
        earliest, latest = GanttPlotter.compute_horizon(start_time_map, end_time_map)
        if force_start is not None:
            earliest = force_start
        if force_end is not None:
            latest = force_end
        self.ax.set_xlim(earliest, latest + 1)

    def create_job_to_color_map(
        self, job_list: Sequence[str]
    ) -> dict[str, tuple[float, float, float, float]]:
        cmap = plt.get_cmap(self.cmap_name)
        n_jobs = max(len(job_list) - 1, 1)
        return {job: cmap(i / n_jobs) for i, job in enumerate(job_list)}

    @staticmethod
    def create_machine_lanes(
        start_time_map: Mapping[tuple[str, str, str], int],
        stage_list: Sequence[str],
        machine_list_per_stage: Mapping[str, Sequence[str]],
    ) -> tuple[list[tuple[str, str]], list[str]]:
        machine_lanes: list[tuple[str, str]] = []
        machine_labels: list[str] = []
        for stage in stage_list:
            machines = (
                machine_list_per_stage.get(stage) if machine_list_per_stage else None
            )
            if not machines:
                machines = sorted(
                    {mc for (_, stg, mc) in start_time_map if stg == stage}
                )
            for mc in machines:
                machine_lanes.append((stage, mc))
                machine_labels.append(f"{stage}-{mc}")
        return machine_lanes, machine_labels

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

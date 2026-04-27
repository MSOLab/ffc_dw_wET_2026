"""Preemptive last-stage schedule derived from an MCF flow solution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

__all__ = ["MCFPreemptiveSchedule"]


@dataclass(frozen=True, slots=True, kw_only=True)
class MCFPreemptiveSchedule:
    """Preemptive last-stage assignment derived from an MCF flow solution.

    Each segment is ``(machine_id, job_id, start_t, end_t)`` representing
    the half-open interval ``[start_t, end_t)`` during which
    ``machine_id`` processes ``job_id`` without interruption. A single
    job may appear in multiple segments, possibly on different machines,
    but at any instant at most one segment per machine and at most one
    segment per job.

    ``FFcSchedule`` cannot represent preemption (it stores one
    ``(start, end)`` per ``(stage, job)``), so this type is kept as a
    sibling, diagnostic-only solution object. It is **not** a valid
    drop-in for ``compute_weighted_earliness_tardiness``
    or any dispatcher / model builder.
    """

    stage_id: str
    machines: tuple[str, ...]
    segments: tuple[tuple[str, str, int, int], ...]

    @classmethod
    def from_flow_dict(
        cls,
        flow: Mapping[str, Mapping[int, int]],
        stage_id: str,
        machines: Sequence[str],
    ) -> MCFPreemptiveSchedule:
        """Construct a preemptive schedule from an MCF ``x[j][t]`` dict.

        ``flow[j][t]`` is the flow on arc ``(job j, time t)`` from the
        parallel-machine preemptive MCF formulation
        (``ParallelMachinePreemptionMcf.get_variable_value_dict``). Arc
        capacity is 1, so every present entry represents one unit of
        processing for job ``j`` during the time slot ``t`` (modeled as
        the half-open interval ``[t - 1, t)``).

        Machines are interchangeable in the MCF formulation, so a
        heuristic assignment is applied: at each time slot, jobs that
        ran in the previous slot keep their machine when possible, and
        the rest are assigned to the machine whose last-used time is the
        smallest (ties broken by machine id).
        """
        machines_tuple = tuple(machines)
        machine_count = len(machines_tuple)

        time_to_jobs: dict[int, list[str]] = {}
        for job_id, t_map in flow.items():
            for t, units in t_map.items():
                if units <= 0:
                    continue
                if units != 1:
                    raise ValueError(
                        f"MCF flow for job {job_id} at t={t} is {units}; expected 0 or 1."
                    )
                time_to_jobs.setdefault(t, []).append(job_id)

        raw_segments: list[tuple[str, str, int, int]] = []
        prev_assignment: dict[str, str] = {}
        machine_last_t: dict[str, int] = dict.fromkeys(machines_tuple, -1)

        for t in sorted(time_to_jobs):
            jobs_here = time_to_jobs[t]
            if len(jobs_here) > machine_count:
                raise ValueError(
                    f"MCF flow assigns {len(jobs_here)} jobs at t={t} but only "
                    f"{machine_count} last-stage machines are available."
                )

            free: set[str] = set(machines_tuple)
            assigned_now: dict[str, str] = {}

            # First pass: continuity — reuse the previous slot's machine if free.
            for job_id in jobs_here:
                prev_mc = prev_assignment.get(job_id)
                if prev_mc is not None and prev_mc in free:
                    assigned_now[job_id] = prev_mc
                    free.remove(prev_mc)

            # Second pass: deterministic fill for the rest.
            remaining = sorted(j for j in jobs_here if j not in assigned_now)
            for job_id in remaining:
                mc = min(free, key=lambda m: (machine_last_t[m], m))
                assigned_now[job_id] = mc
                free.remove(mc)

            for job_id, mc in assigned_now.items():
                raw_segments.append((mc, job_id, t - 1, t))
                machine_last_t[mc] = t

            prev_assignment = assigned_now

        # Merge adjacent same-(machine, job) unit segments.
        raw_segments.sort(key=lambda seg: (seg[0], seg[2]))
        merged: list[tuple[str, str, int, int]] = []
        for mc, job_id, start, end in raw_segments:
            if (
                merged
                and merged[-1][0] == mc
                and merged[-1][1] == job_id
                and merged[-1][3] == start
            ):
                prev = merged[-1]
                merged[-1] = (prev[0], prev[1], prev[2], end)
            else:
                merged.append((mc, job_id, start, end))

        return cls(
            stage_id=stage_id,
            machines=machines_tuple,
            segments=tuple(merged),
        )

    def to_gantt_segments(self) -> list[tuple[str, str, str, int, int]]:
        """Emit ``(job, stage, machine, start, end)`` tuples for plotting.

        Symmetrical with :meth:`from_flow_dict`; yields one tuple per
        internal segment, reordering the fields into the ``(job, stage,
        machine, start, end)`` shape the Gantt plotter consumes.
        """
        return [
            (job_id, self.stage_id, mc, start, end)
            for (mc, job_id, start, end) in self.segments
        ]

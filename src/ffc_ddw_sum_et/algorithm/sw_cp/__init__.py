"""SW-CP algorithm package: sliding-window CP refinement."""

from .cp_model import SwCpBuildResult, SwCpModelBuilder
from .dispatcher import SwCpDispatcher
from .option import SwCpOption
from .partition import (
    JobMcType,
    OperationPartition,
    build_operation_partition,
    build_stage_2_batch_list,
    validate_and_get_batch_count,
)
from .step_log import SwCpStepEntry
from .visual import REGION_COLORS, render_partition_gantt_svg

__all__ = [
    "JobMcType",
    "OperationPartition",
    "SwCpBuildResult",
    "SwCpDispatcher",
    "SwCpModelBuilder",
    "SwCpOption",
    "SwCpStepEntry",
    "REGION_COLORS",
    "build_operation_partition",
    "build_stage_2_batch_list",
    "render_partition_gantt_svg",
    "validate_and_get_batch_count",
]

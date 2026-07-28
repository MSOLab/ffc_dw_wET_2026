"""Canonical field name constants for schedule serialization (JSON and YAML)."""

# top-level fields
INSTANCE_NAME = "instanceName"
OBJ_VALUE = "objValue"
OBJ_BOUND = "objBound"
JOBS = "jobs"
STAGES = "stages"
MACHINES_PER_STAGE = "machinesPerStage"
OPERATIONS = "operations"

# preemptive-only top-level fields
STAGE_ID = "stageId"
ALL_JOBS = "allJobs"
SEGMENTS = "segments"

# optional metadata
HIGHLIGHT_JOBS = "highlightJobs"

# operation / segment item fields
OP_JOB = "job"
OP_STAGE = "stage"
OP_MACHINE = "machine"
OP_START = "start"
OP_END = "end"

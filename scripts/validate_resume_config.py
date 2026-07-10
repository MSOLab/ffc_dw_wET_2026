"""Dry-run validator for a RunMode.RESUME experiment config.

Answers "will `uv run main.py` actually resume where I think it will?" without
starting the experiment. Replicates the checks main.main() performs between
loading the config and constructing the runners:

1. ``_resolve_resume_dir``      -- resolves an explicit scenario dir or ``latest:<name>``
2. flow-prefix validation       -- derives ``flow_resume_idx`` per scenario
3. the ``flow_resume_idx >= step_cnt`` guard (main.py:140) -- catches a
   ``resume_dir`` pointed at a *case* run, which would skip every step

All four helpers are imported from ``main`` (as ``entrypoint``) rather than
re-implemented, so this stays in sync with the real entrypoint.

With ``--check-artifacts`` it additionally verifies that every instance the run
would load has a base incumbent under ``resume_dir`` -- the failure that
otherwise surfaces as a RuntimeError from
``FFcDDWMultiInstanceRunner._load_resume_data`` after startup.

Usage:
    uv run python scripts/validate_resume_config.py metadata/20260710/sw_cp_tl_kappa_0.005.yaml
    uv run python scripts/validate_resume_config.py <config> --check-artifacts

Exit code is 0 when the config would resume cleanly, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from routix.dynamic_data_object import DynamicDataObject
from routix.io import load_yaml
from routix.subroutine_flow_validator import SubroutineFlowValidator
from routix.type_defs import RunMode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main as entrypoint  # noqa: E402
from ffc_ddw_sum_et.orchestration import (  # noqa: E402
    SUBROUTINE_FLOW_CACHE_FN,
    BenchmarkLoader,
    FFcDDWSubroutineController,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="validate_resume_config")
    parser.add_argument("config", type=Path, help="experiment config YAML")
    parser.add_argument(
        "--check-artifacts",
        action="store_true",
        help=(
            "also verify each instance has a base incumbent "
            "(<ins>_solution.json + <ins>_instance_result.yaml) under resume_dir"
        ),
    )
    return parser.parse_args()


def _print_prefix_mismatch(exc: ValueError) -> None:
    """Render routix's prefix-mismatch payload as the differing step params.

    ``validate_subroutine_flow_prefix`` raises ``ValueError(dict)`` carrying the
    mismatch index plus both fully-defaulted step dicts, which are far too wide
    to eyeball. Print only the keys that actually differ.
    """
    payload = exc.args[0] if exc.args else None
    if not isinstance(payload, dict) or "index" not in payload:
        print(f"   {exc}")
        return
    idx = payload["index"]
    base_step = payload.get("resume_element") or {}
    cur_step = payload.get("current_element") or {}
    method = cur_step.get("method", "?")
    print(f"   step [{idx}] {method}: base flow and scenario flow disagree")
    for key in sorted(set(base_step) | set(cur_step)):
        base_val, cur_val = base_step.get(key), cur_step.get(key)
        if base_val != cur_val:
            print(f"     {key}: base={base_val!r} scenario={cur_val!r}")
    print(
        f"   -> steps [0..{idx - 1}] match, so resume cannot reuse step [{idx}]. "
        "Either point resume_dir at a base run whose prefix matches, or align "
        "the scenario's step params with the base."
    )


def _check_artifacts(config: dict, resume_dir: Path) -> list[str]:
    """Return the names of instances missing base artifacts under resume_dir."""
    ins_index_source = config.get("ins_index_source")
    loader = BenchmarkLoader(
        Path(config["benchmark_dir"]),
        ins_index_source=Path(ins_index_source) if ins_index_source else None,
    )
    instances = loader.load_all(ins_index=config.get("ins_index"))
    print(f"  instances to run: {len(instances)}")
    missing = []
    for instance in instances:
        name = instance.name
        inst_dir = resume_dir / name
        if (
            not (inst_dir / f"{name}_solution.json").is_file()
            or not (inst_dir / f"{name}_instance_result.yaml").is_file()
        ):
            missing.append(name)
    return missing


def main() -> int:
    args = _parse_args()
    config = entrypoint._load_config(args.config)
    mode = entrypoint._parse_run_mode(config.get("run_mode", "FULL_RUN"))

    print(f"config    : {args.config}")
    print(f"run_mode  : {mode.name}")
    if mode != RunMode.RESUME:
        print("\nNot a RESUME config -- nothing to validate.")
        return 0

    entrypoint._validate_scenario_uniqueness(config.get("scenarios", []))

    base_output_dir = Path(config.get("output_dir", "output"))
    resume_dir = entrypoint._resolve_resume_dir(config, base_output_dir)
    print(f"output_dir: {base_output_dir}")
    print(f"resume_dir: {resume_dir}")
    print(f"  (from resume_dir: {config['resume_dir']!r})")

    base_flow = load_yaml(resume_dir / SUBROUTINE_FLOW_CACHE_FN)
    validator = SubroutineFlowValidator(FFcDDWSubroutineController)

    failures: list[str] = []
    for i, sc in enumerate(config.get("scenarios", [])):
        name = sc.get("name", f"scenario_{i + 1}")
        flow = sc["subroutine_flow"]
        try:
            idx = validator.validate_subroutine_flow_prefix(
                DynamicDataObject.from_obj(base_flow),
                DynamicDataObject.from_obj(flow),
            )
        except ValueError as exc:
            failures.append(name)
            print(f"\nscenario {name!r}: PREFIX MISMATCH")
            _print_prefix_mismatch(exc)
            continue
        step_cnt = len(flow)
        print(f"\nscenario {name!r}: flow_resume_idx={idx} of {step_cnt} steps")
        for j, step in enumerate(flow):
            print(f"   [{j}] {'SKIP  ' if j < idx else 'RERUN '} {step['method']}")
        if idx >= step_cnt:
            failures.append(name)
            print(
                f"   ERROR: would run no steps -- the {step_cnt}-step flow is fully "
                "covered by the base flow. Point resume_dir at the base (prefix) "
                "run, not a run of the case scenarios themselves."
            )

    if args.check_artifacts:
        print("\nbase incumbent artifacts:")
        missing = _check_artifacts(config, resume_dir)
        if missing:
            shown = ", ".join(missing[:5])
            more = f" (+{len(missing) - 5} more)" if len(missing) > 5 else ""
            print(f"  ERROR: {len(missing)} missing under {resume_dir}: {shown}{more}")
            failures.append("missing base artifacts")
        else:
            print("  all instances have a base incumbent")

    if failures:
        print(f"\nFAIL: {len(failures)} problem(s): {', '.join(failures)}")
        return 1
    print("\nOK: config would resume cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

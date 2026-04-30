"""ffc_ddw_sum_et `ArtifactLayout` extension.

Layers project-specific artifact kinds (mcf_lb_*, gantt_png, report_xlsx, ...)
on top of routix's default layout schema. Kinds are declared in the overlay
yaml at `metadata/artifact_layout/ffc_ddw_sum_et_v1.yaml` and registered via
`ArtifactLayout.register_kind`.
"""

from __future__ import annotations

from pathlib import Path

from routix.io import ArtifactLayout, RunRoot, load_yaml

DEFAULT_OVERLAY_PATH = (
    Path(__file__).resolve().parents[3]
    / "metadata"
    / "artifact_layout"
    / "ffc_ddw_sum_et_v1.yaml"
)


class FFcArtifactLayout(ArtifactLayout):
    """`ArtifactLayout` with ffc_ddw_sum_et project kinds preloaded."""

    def __init__(
        self,
        *,
        run_root: Path,
        run_id: str,
        overlay_path: Path | None = None,
    ) -> None:
        super().__init__(run_root=run_root, run_id=run_id)
        self._overlay_path = (
            Path(overlay_path) if overlay_path is not None else DEFAULT_OVERLAY_PATH
        )
        self._apply_overlay(self._overlay_path)

    def _apply_overlay(self, overlay_path: Path) -> None:
        if not overlay_path.exists():
            raise FileNotFoundError(
                f"ffc artifact_layout overlay not found: {overlay_path}"
            )
        data = load_yaml(overlay_path)
        for entry in data.get("artifacts", []):
            kwargs: dict = {
                "scope": entry["scope"],
                "file_template": entry["file_template"],
            }
            if "zone" in entry:
                kwargs["zone"] = entry["zone"]
            self.register_kind(entry["kind"], **kwargs)


def init_ffc_artifact_layout(run_root: RunRoot) -> FFcArtifactLayout:
    """Build an `FFcArtifactLayout` from a `RunRoot`."""
    return FFcArtifactLayout(run_root=run_root.path, run_id=run_root.run_id)


def restore_layout_from_run_dir(run_root: RunRoot) -> ArtifactLayout:
    """Restore an `ArtifactLayout` from a previously stamped run directory.

    If `<run_id>_artifact_layout.yaml` is present in the run dir, build a
    base `ArtifactLayout` straight from it (the stamp already contains every
    kind that was registered, so no overlay re-apply is needed). Otherwise
    fall back to a fresh `FFcArtifactLayout` with the default schema +
    ffc overlay.
    """
    stamped = run_root.path / f"{run_root.run_id}_artifact_layout.yaml"
    if stamped.exists():
        return ArtifactLayout(
            run_root=run_root.path,
            run_id=run_root.run_id,
            schema_path=stamped,
        )
    return init_ffc_artifact_layout(run_root)

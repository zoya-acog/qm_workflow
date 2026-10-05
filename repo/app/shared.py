"""Shared UI state, formatting, and run-row helpers (imported by every page)."""

import html
import json
import logging
from pathlib import Path

import ipywidgets as widgets

from helpers import status_badge_html, status_category

DEFAULT_RUNS_DIR = "/mnt/own6d/qe_workflow/data/runs"
_SYSTEM_PARAM_KEYS = (
    "input_dft", "vdw_corr", "xdm_a1", "xdm_a2", "ecutwfc", "ecutrho",
    "occupations", "smearing", "degauss",
)


def _run_log(msg: str) -> None:
    logging.getLogger("advQMcalc_test").info(msg)


class _Nav:
    """Small indirection so page builders can trigger navigation before the
    router itself exists yet (pages are built before the sidebar wiring)."""

    def __init__(self):
        self.show = lambda key: None


def _nav_button(label: str) -> widgets.Button:
    b = widgets.Button(description=label, layout=widgets.Layout(width="auto"))
    b.add_class("advqm-navbtn")
    return b


def _fmt_energy(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "–"


# Display names for calculation types; the backend keeps the original keys.
CALC_TYPE_LABELS = {
    "scf": "Single Point energy",
    "relax": "Fixed Cell Optimization",
    "vc-relax": "Variable Cell Optimization",
}


def _fmt_calc_type(v) -> str:
    return _fmt(CALC_TYPE_LABELS.get(v, v))


def _fmt(v) -> str:
    return str(v) if v not in (None, "") else "–"


def _list_all_run_rows(base: Path, only_successful: bool = False) -> list[dict]:
    """List every run under base — not just the latest.

    cli.py's own _aggregate_crystal_results (via _iter_per_cif_runs) has a
    "single-cif runs_dir" heuristic: if every child directory looks like a
    timestamp, it assumes the whole runs_dir represents one CIF's run
    history and returns only the single latest one, discarding every other
    run. That matches a different intended layout (runs_dir per CIF) but not
    how this app actually uses DEFAULT_RUNS_DIR — one shared flat folder for
    every run of every CIF ever submitted — so in practice it was silently
    hiding every run except the most recent system-wide. This frontend-only
    replacement (no cli.py change) walks every directory that directly
    contains a state.json, with no collapsing, keeping the exact same row
    shape _aggregate_crystal_results produced so every existing consumer
    (Runs, Results) keeps working unchanged.
    """
    rows: list[dict] = []
    if not base.is_dir():
        return rows
    for state_file in base.rglob("state.json"):
        run_path = state_file.parent
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            continue
        crystal = state.get("tasks", {}).get("crystal", {})
        crystal_status = crystal.get("status")
        if only_successful and crystal_status != "qe_energy_extracted":
            continue
        qe_output_block = crystal.get("qe_output") or {}
        qe_results_block = crystal.get("qe_results") or {}
        system_params = crystal.get("system_params") or {}
        result_file = run_path / "results" / "crystal_result.json"
        rows.append({
            "cif": state.get("cif") or run_path.name,
            "run_id": run_path.name,
            "crystal_status": crystal_status,
            "slurm_state": crystal.get("slurm_state"),
            "job_id": crystal.get("job_id"),
            "kpoints": crystal.get("kpoints"),
            "pseudo_dir": crystal.get("pseudo_dir"),
            "qe_output_file": qe_output_block.get("qe_output_file"),
            "energy_ry": qe_results_block.get("energy_ry"),
            "input_dft": system_params.get("input_dft"),
            "vdw_corr": system_params.get("vdw_corr"),
            "result_file": str(result_file) if result_file.exists() else None,
            "run_path": str(run_path),
        })
    return rows


def _effective_status_html(r: dict) -> str:
    """Status badge, distinguishing a genuine SCF non-convergence from a
    generic failure. QE reports this in the .out text as "convergence NOT
    achieved" — the backend's status field lumps it into the same generic
    "failed" bucket as everything else (qe_failed_validation), so this
    frontend-only check re-inspects the actual output for the specific
    marker rather than needing a cli.py change to add a new status value.

    QE has a hard default limit on SCF steps (electron_maxstep); a run that
    hits it isn't necessarily wrong, it may just need more iterations — for
    now this is surfaced as a distinct status so it's not confused with a
    real error, but resubmitting with a higher step count or extending from
    the last SCF step isn't wired up in the UI yet.
    """
    status = r.get("crystal_status")
    if not r.get("job_id") and status_category(status) in ("warning", "neutral"):
        # No SLURM job was ever submitted for this run — if its log records
        # an ERROR, it failed during setup (not Pending on SLURM).
        run_path = r.get("run_path")
        if run_path:
            try:
                err = _last_run_error(Path(run_path))
            except Exception:
                err = None
            if err:
                title = html.escape(err)
                return (
                    '<span class="advqm-badge advqm-badge-danger" '
                    f'title="{title}">Setup Failed</span>'
                )
    if status_category(status) == "danger":
        out_file = r.get("qe_output_file")
        if out_file:
            try:
                text = Path(out_file).read_text(errors="ignore")
                if "convergence NOT achieved" in text:
                    return '<span class="advqm-badge advqm-badge-danger">Convergence Failed</span>'
            except Exception:
                pass
    return status_badge_html(status)


def _last_run_error(run_dir: Path, log_name: str = "advQMcalc.log") -> str | None:
    """Return the last ERROR message from a run's log file, if any.

    Used to surface pre-submit backend failures (which log an error and
    return without raising) instead of leaving the run looking Pending.
    """
    run_dir = Path(run_dir)
    logs_dir = run_dir / "logs"
    log_file = logs_dir / log_name
    if not log_file.is_file():
        candidates = sorted(logs_dir.glob("*.log")) if logs_dir.is_dir() else []
        log_file = candidates[-1] if candidates else None
    if log_file is None or not log_file.is_file():
        return None
    try:
        lines = log_file.read_text(errors="ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if "ERROR" in line:
            msg = line.split("ERROR", 1)[1].strip(" -:|[]")
            return msg or line.strip()
    return None




CALC_TYPE_LABELS = {
    "scf": "Single Point energy",
    "relax": "Fixed Cell Optimization",
    "vc-relax": "Variable Cell Optimization",
}

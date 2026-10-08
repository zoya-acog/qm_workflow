"""Shared UI state, formatting, and run-row helpers (imported by every page)."""

import base64
import html
import io
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv

from helpers import _STATUS_MAP, status_badge_html, status_category

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


# 1 Ry = 13.605693122994 eV; 1 eV = 96.48533212331002 kJ/mol.
RY_TO_KJ_MOL = 13.605693122994 * 96.48533212331002


def ry_to_kj_mol(v):
    """Convert an energy in Ry to kJ/mol (None if not numeric)."""
    return v * RY_TO_KJ_MOL if isinstance(v, (int, float)) else None


_QE_TOTAL_ENERGY = re.compile(r"^!\s+total energy\s+=\s+(-?\d+\.\d+)\s+Ry", re.M)


def energy_vs_step(out_path) -> list[float]:
    """Total energy (kJ/mol) after each optimisation step of a QE output.

    QE prints one "!    total energy" line per ionic step of a relax or
    vc-relax run. Returns [] when the file is missing or has fewer than two
    steps (a single-point run has nothing to plot).
    """
    try:
        text = Path(out_path).read_text(errors="ignore")
    except (OSError, TypeError):
        return []
    energies = [float(m) * RY_TO_KJ_MOL for m in _QE_TOTAL_ENERGY.findall(text)]
    return energies if len(energies) >= 2 else []


_PWO2XSF = Path(__file__).resolve().parent / "tools" / "pwo2xsf.sh"
_TRAJ_CACHE: dict[tuple[str, float], bytes | None] = {}


def _run_pwo2xsf(option: str, out_path: str) -> str:
    """Run QE's pwo2xsf.sh (writes XSF to stdout) in a scratch dir; "" on failure."""
    with tempfile.TemporaryDirectory(prefix="pwo2xsf_") as tmp:
        try:
            res = subprocess.run(
                ["sh", str(_PWO2XSF), option, str(out_path)],
                cwd=tmp, capture_output=True, text=True, timeout=60,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError):
            return ""
    return res.stdout if res.returncode == 0 else ""


def trajectory_xsf(out_path) -> bytes | None:
    """Structure file extracted from a QE output with pwo2xsf.

    Relax / vc-relax runs give an animated XSF (AXSF) with every ionic step;
    a single-point run has only one structure, so it gets the input structure.
    Returns None when nothing could be extracted. Cached per file and mtime.
    """
    try:
        key = (str(out_path), Path(out_path).stat().st_mtime)
    except (OSError, TypeError):
        return None
    if key in _TRAJ_CACHE:
        return _TRAJ_CACHE[key]
    text = _run_pwo2xsf("--animxsf", out_path)
    if text.count("PRIMCOORD") < 2:
        text = _run_pwo2xsf("--inicoor", out_path)
    data = text.encode() if "PRIMCOORD" in text else None
    _TRAJ_CACHE[key] = data
    return data


def energy_plot_html(energies: list[float], label: str) -> str:
    """Card with a Total Energy vs Optimisation Step plot (PNG embedded as base64).

    Style follows the plot cards in AutoMD-OXtalS: the name sits above the
    figure and the figure itself carries no title.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.ticker import MaxNLocator
    except ImportError:
        return '<div class="advqm-muted">Plotting needs matplotlib, which is not installed.</div>'

    steps = list(range(len(energies)))
    fig, ax = plt.subplots(figsize=(7.2, 3.6), dpi=130)
    ax.plot(steps, energies, marker="o", markersize=4, linewidth=1.6, color="#1565c0")
    ax.set_xlabel("Optimisation Step", fontsize=10, color="#374151")
    ax.set_ylabel("Total Energy (kJ/mol)", fontsize=10, color="#374151")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))
    ax.ticklabel_format(axis="y", useOffset=False, style="plain")
    ax.grid(True, color="#e5e7eb", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=9, colors="#374151")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    img = base64.b64encode(buf.getvalue()).decode("ascii")
    return (
        '<div style="border:1px solid #e5e7eb; border-radius:10px; background:#fff; overflow:hidden;">'
        f'<div style="padding:10px 12px 4px; font-size:0.86rem; font-weight:700; color:#12344d;">'
        f'{html.escape(label)}</div>'
        f'<img src="data:image/png;base64,{img}" style="width:100%; max-width:760px; display:block; background:#fff;"/>'
        "</div>"
    )


def grid_columns(widths: list[str]) -> str:
    """CSS grid-template-columns for a table, from percentage widths. Header and
    rows use the same string so their columns line up exactly."""
    return " ".join(f"minmax(0,{w.rstrip('%')}fr)" for w in widths)


def table_head_html(labels: list[str], widths: list[str], min_width: str | None = None, info: dict | None = None) -> str:
    """Header row of a row-list table: same grid as the rows beneath it."""
    info = info or {}
    cells = "".join(
        f"<span>{html.escape(label)}{info.get(label, '')}</span>" for label in labels
    )
    return (
        f'<div class="advqm-rowlist-head" style="display:grid; '
        f'grid-template-columns:{grid_columns(widths)};'
        f'{f" min-width:{min_width};" if min_width else ""}">{cells}</div>'
    )


def cif_list_text(names: list[str], compact: bool = False) -> str:
    """One name for a single CIF; "{a.cif, b.cif, ..., z.cif}" for a batch.
    compact=True shortens a batch to "{a.cif,...}" to save space."""
    if len(names) == 1:
        return names[0]
    if compact:
        return "{" + names[0] + ",...}"
    shown = names if len(names) <= 4 else names[:2] + ["..."] + names[-1:]
    return "{" + ", ".join(shown) + "}"


def batch_status_text(rows: list[dict]) -> str:
    cats = [status_category(r.get("crystal_status")) for r in rows]
    n = len(rows)
    done, failed = cats.count("success"), cats.count("danger")
    running = n - done - failed
    if done == n:
        return f"Finished ({done}/{n})"
    if failed == n:
        return f"Failed ({failed}/{n})"
    parts = []
    if running:
        parts.append(f"{running} In Progress")
    if done:
        parts.append(f"{done} Finished")
    if failed:
        parts.append(f"{failed} Failed")
    return ", ".join(parts)


def batch_status_html(rows: list[dict]) -> str:
    """Batch status as one coloured pill per state, stacked, e.g.
    "Finished (1/3)" with "Pending (2/3)" below it."""
    n = len(rows)
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r.get("crystal_status") or "").lower()
        label, css = _STATUS_MAP.get(key, ((r.get("crystal_status") or "Unknown").replace("_", " ").title(), "neutral"))
        counts[(label, css)] = counts.get((label, css), 0) + 1
    order = {"success": 0, "info": 1, "warning": 2, "danger": 3, "neutral": 4}
    pills = "".join(
        f'<div style="margin-bottom:3px;"><span class="advqm-badge advqm-badge-{css}">'
        f"{html.escape(label)} ({count}/{n})</span></div>"
        for (label, css), count in sorted(counts.items(), key=lambda kv: (order.get(kv[0][1], 9), kv[0][0]))
    )
    return f"<div>{pills}</div>"


def group_rows(rows: list[dict]) -> list[list[dict]]:
    """Group runs submitted together (same array job id) into one list; every
    other run stays on its own. Order follows each group's first appearance."""
    groups: list[list[dict]] = []
    by_array: dict[str, list[dict]] = {}
    for r in rows:
        aid = r.get("array_job_id") or r.get("batch_id")
        if aid:
            if aid not in by_array:
                by_array[aid] = []
                groups.append(by_array[aid])
            by_array[aid].append(r)
        else:
            groups.append([r])
    return groups


def group_cif_names(rows: list[dict]) -> list[str]:
    return [
        Path(r.get("cif")).name if r.get("cif") else "?"
        for r in sorted(rows, key=lambda r: (r.get("array_task_id") or 0, r.get("cif") or ""))
    ]


def labeled_field(w, style: str = ""):
    """Show an ipyvuetify field's name as a plain label ABOVE the input box.

    Moves the widget's floating label out of the input (label becomes empty) and
    puts the same text in a small bold caption above it, matching the field
    labels in AutoMD-OXtalS (0.86rem / 600 / #374151). Widget and logic are
    otherwise untouched; fields without a label are returned as-is.
    """
    text = getattr(w, "label", "") or ""
    if not text:
        return w
    w.label = ""
    caption = ipv.Html(
        tag="div",
        children=[ipv.Html(
            tag="span", children=[text],
            style_="font-size:0.86rem; font-weight:600; color:#374151;",
        )],
        style_="display:flex; align-items:center; margin:8px 0 2px 0;",
    )
    return ipv.Html(tag="div", children=[caption, w], style_=style)


def _fmt_energy(v) -> str:
    """Format a QE energy (stored in Ry) for display in kJ/mol."""
    kj = ry_to_kj_mol(v)
    return f"{kj:,.2f}" if kj is not None else "–"


# Display names for calculation types; the backend keeps the original keys.
CALC_TYPE_LABELS = {
    "scf": "Single Point Energy",
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
            "array_job_id": crystal.get("array_job_id"),
            "batch_id": state.get("batch_id"),
            "array_task_id": crystal.get("array_task_id"),
            "run_label": state.get("run_label"),
            "cif_dir": state.get("cif_dir"),
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
    "scf": "Single Point Energy",
    "relax": "Fixed Cell Optimization",
    "vc-relax": "Variable Cell Optimization",
}

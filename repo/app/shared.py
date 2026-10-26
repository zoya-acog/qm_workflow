"""Shared UI state, formatting, and run-row helpers (imported by every page)."""

import base64
import fcntl
import html
import io
import json
import logging
import re
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv

from helpers import (
    _STATUS_MAP,
    _process_single_cif,
    _query_slurm_job,
    build_namespace,
    load_run_state,
    resume_target,
    status_badge_html,
    status_category,
)

from advQMcalc_test.site_config import SITE

DEFAULT_RUNS_DIR = SITE["app"]["runs_dir"]
# Every runs folder the app has been used with (the New Calculation page lets the
# user pick any "Runs Directory"). The Dashboard and Results read all of them, not
# just the default one, so a batch submitted to another folder still shows up.
_RUNS_DIRS_FILE = Path(DEFAULT_RUNS_DIR).parent / ".voila_runs_dirs.json"


def _stored_runs_dirs() -> list[str]:
    try:
        data = json.loads(_RUNS_DIRS_FILE.read_text())
        return [str(d) for d in data] if isinstance(data, list) else []
    except Exception:
        return []


def remember_runs_dir(path) -> None:
    """Add a runs folder to the list the Dashboard/Results read (best effort)."""
    try:
        p = str(Path(path).resolve())
        if p == str(Path(DEFAULT_RUNS_DIR).resolve()):
            return
        dirs = _stored_runs_dirs()
        if p in dirs:
            return
        dirs.append(p)
        tmp = _RUNS_DIRS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(dirs, indent=2))
        tmp.replace(_RUNS_DIRS_FILE)
    except Exception:
        pass


def known_runs_dirs() -> list[Path]:
    out, seen = [], set()
    for d in [DEFAULT_RUNS_DIR, *_stored_runs_dirs()]:
        p = Path(d)
        if p.is_dir() and str(p.resolve()) not in seen:
            seen.add(str(p.resolve()))
            out.append(p)
    return out


_SYSTEM_PARAM_KEYS = (
    "input_dft", "vdw_corr", "xdm_a1", "xdm_a2", "ecutwfc", "ecutrho",
    "occupations", "smearing", "degauss",
)


def _run_log(msg: str) -> None:
    logging.getLogger("advQMcalc_test").info(msg)


def runs_base_for(run_path: Path) -> Path:
    """The runs folder a run lives under."""
    rp = Path(run_path).resolve()
    for d in known_runs_dirs():
        try:
            if d.resolve() in rp.parents:
                return d
        except OSError:
            continue
    return Path(DEFAULT_RUNS_DIR)


def needs_slurm_check(run_path: Path) -> bool:
    """True for a run that was submitted to SLURM but whose final result is not
    saved yet (pending, running, or finished but not processed), or that is
    marked ENDED (finished while sacct could not be asked). Runs that never
    reached SLURM are left alone: re-checking them would only repeat a setup
    that already failed."""
    crystal = (load_run_state(Path(run_path)) or {}).get("tasks", {}).get("crystal", {})
    if not crystal.get("job_id"):
        return False
    return (crystal.get("slurm_state") == "ENDED"
            or status_category(crystal.get("status")) in ("info", "warning"))


@contextmanager
def _run_lock(run_path: Path):
    """Yields True when this process may update the run, False when another
    refresh (another page or another user) is updating it right now."""
    try:
        f = open(Path(run_path) / ".refresh.lock", "w")
    except OSError:
        yield False
        return
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        f.close()


def _recheck_ended(rp: Path, crystal: dict) -> None:
    # "ENDED" means the job left the queue while sacct could not be asked, so
    # its final SLURM state was never recorded. Ask sacct again and store the
    # real state (COMPLETED, FAILED, ...) when it answers.
    try:
        real = _query_slurm_job(crystal["job_id"])
    except Exception:
        return
    if real in ("ENDED", "UNKNOWN", "PENDING", "RUNNING"):
        return
    try:
        sf = rp / "state.json"
        st = json.loads(sf.read_text())
        st["tasks"]["crystal"]["slurm_state"] = real
        sf.write_text(json.dumps(st, indent=2))
    except Exception:
        pass


def _monitor_run(rp: Path, st: dict) -> None:
    # The same resume logic as the Resume button, used only to monitor: it asks
    # SLURM for the job state and, once the job has finished, validates the QE
    # output and extracts the energy. It never submits anything.
    crystal = st.get("tasks", {}).get("crystal", {})
    runs_dir, run_id = resume_target(runs_base_for(rp), rp.parent.name, rp)
    ns = build_namespace(
        resume=True, run_id=run_id, run_path=None, runs_dir=str(runs_dir),
        calc_type=st.get("calc_type") or "scf", kpoints=crystal.get("kpoints"),
        pseudo_dir=crystal.get("pseudo_dir"),
        pp_map=None,
        force_pp_cleanup=False,
        defer_submit=True,
        **{key: (crystal.get("system_params") or {}).get(key)
           for key in _SYSTEM_PARAM_KEYS},
    )
    _process_single_cif(rp, runs_dir, ns)


# SLURM states that end a job, and the run status the backend gives each.
_FINAL_STATUS = {"COMPLETED": "completed", "FAILED": "failed", "CANCELLED": "cancelled",
                 "TIMEOUT": "timeout", "OUT_OF_MEMORY": "out_of_memory",
                 "NODE_FAIL": "node_fail", "BOOT_FAIL": "boot_fail", "PREEMPTED": "preempted"}


def _array_task_state(task_id: str) -> str | None:
    """State of an array task that SLURM keeps no record of by itself.

    Tasks that never started (still queued, or cancelled while queued) appear
    in sacct only on the whole array's line ("104304_[1-3]  CANCELLED"), so a
    query for "104304_1" returns nothing and the backend reports UNKNOWN. Read
    the array's lines and use the one that covers this task."""
    array_id, _, index = str(task_id).partition("_")
    if not index.isdigit():
        return None
    try:
        out = subprocess.run(
            ["sacct", "-j", array_id, "-X", "-P", "-n", "-o", "JobID,State"],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except Exception:
        return None
    n = int(index)
    for line in out.splitlines():
        jid, _, state = line.partition("|")
        if "_" not in jid or not state.strip():
            continue
        spec = jid.split("_", 1)[1].strip("[]").split("%")[0]
        for part in spec.split(","):
            lo, _, hi = part.partition("-")
            if lo.isdigit() and lo and int(lo) <= n <= int(hi or lo):
                return state.split()[0]
    return None


def _resolve_unknown_array_task(rp: Path) -> None:
    """After a status check that ended with UNKNOWN for an array task, save
    the state SLURM records for the array (PENDING or how it ended)."""
    sf = rp / "state.json"
    try:
        st = json.loads(sf.read_text())
    except Exception:
        return
    crystal = st.get("tasks", {}).get("crystal", {})
    job_id = str(crystal.get("job_id") or "")
    if crystal.get("slurm_state") != "UNKNOWN" or "_" not in job_id:
        return
    state = _array_task_state(job_id)
    if state is None:
        return
    crystal["slurm_state"] = state
    if state == "PENDING":
        crystal["status"] = "pending"
    elif state in _FINAL_STATUS and state != "COMPLETED":
        # Never ran, so there is no QE output to validate.
        crystal["status"] = _FINAL_STATUS[state]
    else:
        return  # RUNNING / COMPLETED: the backend's next check records these
    sf.write_text(json.dumps(st, indent=2))


def refresh_run_statuses(run_paths: list[Path]) -> None:
    """Ask SLURM for the current state of the given runs and save it (used by
    Refresh on the Dashboard and Runs pages, and when a run is opened). Runs
    are checked one at a time; a run another refresh is already updating is
    skipped."""
    for rp in map(Path, run_paths):
        if not needs_slurm_check(rp):
            continue
        with _run_lock(rp) as ok:
            if not ok:
                continue
            st = load_run_state(rp) or {}
            crystal = st.get("tasks", {}).get("crystal", {})
            try:
                if crystal.get("slurm_state") == "ENDED":
                    _recheck_ended(rp, crystal)
                if status_category(crystal.get("status")) in ("info", "warning"):
                    _monitor_run(rp, st)
                    _resolve_unknown_array_task(rp)
            except Exception as e:
                _run_log(f"Status refresh failed for {rp.name}: {e}")


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


def energy_plot_html(energies: list[float], label: str | None = None) -> str:
    """Total Energy vs Optimisation Step plot (PNG embedded as base64).

    With a label: a card in the AutoMD-OXtalS style (name above the figure, no
    title on the figure). Without one: just the image, for a container that
    already shows a title (the Results plot dialog).
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
    if label is None:
        return f'<img src="data:image/png;base64,{img}" style="width:100%; display:block; background:#fff;"/>'
    return (
        '<div style="border:1px solid #e5e7eb; border-radius:10px; background:#fff; overflow:hidden;">'
        f'<div style="padding:10px 12px 4px; font-size:0.86rem; font-weight:700; color:#12344d;">'
        f'{html.escape(label)}</div>'
        f'<img src="data:image/png;base64,{img}" style="width:100%; max-width:760px; display:block; background:#fff;"/>'
        "</div>"
    )


def loading_html(text: str = "Loading…", page: bool = False) -> str:
    """Spinner with a short message, shown while a page or table is being built.
    page=True centres it in the content area (used while switching pages)."""
    cls = "advqm-loading advqm-loading-page" if page else "advqm-loading"
    return (
        f'<div class="{cls}"><div class="advqm-spinner"></div>'
        f"<span>{html.escape(text)}</span></div>"
    )


def grid_columns(widths: list[str]) -> str:
    """CSS grid-template-columns for a table, from percentage widths. Header and
    rows use the same string so their columns line up exactly."""
    return " ".join(f"minmax(0,{w.rstrip('%')}fr)" for w in widths)


def table_head_html(labels: list[str], widths: list[str], min_width: str | None = None, info: dict | None = None) -> str:
    """Header row of a row-list table: same grid as the rows beneath it."""
    info = info or {}
    def _cell(label: str) -> str:
        icon = info.get(label)
        if not icon:
            return f"<span>{html.escape(label).replace(chr(10), '<br>')}</span>"
        # The info icon follows the label's last word inline (e.g. after "Type"
        # on a two-line "Calculation / Type" header) and is never clipped.
        return (
            '<span style="overflow:visible; line-height:1.25; white-space:normal;">'
            f'{html.escape(label).replace(chr(10), "<br>")}'
            f'<span style="white-space:nowrap;">&nbsp;{icon.strip()}</span>'
            "</span>"
        )

    cells = "".join(_cell(label) for label in labels)
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
    cats = [effective_status(r)[1] for r in rows]
    n = len(rows)
    done, failed = cats.count("success"), cats.count("danger")
    running = n - done - failed
    if done == n:
        return f"Completed ({done}/{n})"
    if failed == n:
        return f"Failed ({failed}/{n})"
    parts = []
    if running:
        parts.append(f"{running} In Progress")
    if done:
        parts.append(f"{done} Completed")
    if failed:
        parts.append(f"{failed} Failed")
    return ", ".join(parts)


def batch_status_html(rows: list[dict]) -> str:
    """Batch status as one coloured pill per state, stacked, e.g.
    "Completed (1/3)" with "Pending (2/3)" below it."""
    n = len(rows)
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        label, css = effective_status(r)
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


def info_icon_html(text: str, end: bool = False) -> str:
    """ⓘ with hover text that appears immediately (CSS, see .advqm-tip).

    end=True opens the text to the left, for icons near the right edge.
    """
    tip = html.escape(text, quote=True).replace("\n", "&#10;")
    cls = "advqm-tip advqm-tip-end" if end else "advqm-tip"
    return f'<span class="{cls}" data-tip="{tip}" aria-label="{tip}">&#9432;</span>'


def labeled_field(w, style: str = "", info: str | None = None):
    """Show an ipyvuetify field's name as a plain label ABOVE the input box.

    Moves the widget's floating label out of the input (label becomes empty) and
    puts the same text in a small bold caption above it, matching the field
    labels in AutoMD-OXtalS (0.86rem / 600 / #374151). Widget and logic are
    otherwise untouched; fields without a label are returned as-is.
    info: optional hover text shown on an ⓘ icon next to the caption.
    """
    text = getattr(w, "label", "") or ""
    if not text:
        return w
    w.label = ""
    caption_children = [ipv.Html(
        tag="span", children=[text],
        style_="font-size:0.86rem; font-weight:600; color:#374151;",
    )]
    if info:
        caption_children.append(ipv.Html(
            tag="span", children=["ⓘ"], class_="advqm-tip advqm-tip-field",
            attributes={"data-tip": info, "aria-label": info},
            style_="margin-left:5px;",
        ))
    caption = ipv.Html(
        tag="div",
        children=caption_children,
        style_="position:relative; display:flex; align-items:center; margin:8px 0 2px 0;",
    )
    return ipv.Html(tag="div", children=[caption, w], style_=style)


def _fmt_energy(v) -> str:
    """Format a QE energy (stored in Ry) for display in kJ/mol."""
    kj = ry_to_kj_mol(v)
    return f"{kj:,.2f}" if kj is not None else "–"


# Display names for calculation types; the backend keeps the original keys.
# Hover text explaining QE's calculation-type keywords (Runs and Results tables).
CALC_TYPE_HELP = (
    "scf = Single Point Energy\nrelax = Fixed Cell Optimization\n"
    "vc-relax = Variable Cell Optimization"
)

CALC_TYPE_LABELS = {
    "scf": "Single Point Energy",
    "relax": "Fixed Cell Optimization",
    "vc-relax": "Variable Cell Optimization",
}


def _fmt_calc_type(v) -> str:
    return _fmt(CALC_TYPE_LABELS.get(v, v))


def list_rows_all_dirs(only_successful: bool = False) -> list[dict]:
    """Run rows from the default runs folder plus every remembered one."""
    rows, seen = [], set()
    for base in known_runs_dirs():
        for r in _list_all_run_rows(base, only_successful=only_successful):
            if r.get("run_path") not in seen:
                seen.add(r.get("run_path"))
                rows.append(r)
    return rows


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
            "calc_type": state.get("calc_type"),
            "submitted_by": state.get("submitted_by"),
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


def effective_status(r: dict) -> tuple[str, str]:
    """(label, category) of a run as the user should see it.

    Beyond the saved status: a run with no SLURM job whose log records an error
    failed during setup (shown as "Failed"), and a failed QE run whose output says
    "convergence NOT achieved" is reported as "Convergence Failed".
    """
    status = r.get("crystal_status")
    if not r.get("job_id") and status_category(status) in ("warning", "neutral"):
        run_path = r.get("run_path")
        if run_path:
            try:
                if _last_run_error(Path(run_path)):
                    return "Failed", "danger"
            except Exception:
                pass
    if status_category(status) == "danger":
        out_file = r.get("qe_output_file")
        if out_file:
            try:
                if "convergence NOT achieved" in Path(out_file).read_text(errors="ignore"):
                    return "Convergence Failed", "danger"
            except Exception:
                pass
    key = (status or "").lower()
    return _STATUS_MAP.get(key, ((status or "Unknown").replace("_", " ").title(), "neutral"))


def _effective_status_html(r: dict) -> str:
    """Status badge for one run (see effective_status); a setup failure shows
    its error on hover."""
    label, css = effective_status(r)
    if label == "Failed" and not r.get("job_id") and r.get("run_path"):
        err = _last_run_error(Path(r["run_path"])) or ""
        return f'<span class="advqm-badge advqm-badge-danger" title="{html.escape(err)}">Failed</span>'
    if label == "Convergence Failed":
        return '<span class="advqm-badge advqm-badge-danger">Convergence Failed</span>'
    return status_badge_html(r.get("crystal_status"))


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

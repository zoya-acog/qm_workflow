"""Runs monitoring table with monitor/resume support."""

import html
import threading
import time
import uuid
import zipfile
from pathlib import Path

import traitlets

import ipywidgets as widgets
import ipyvuetify as ipv

from helpers import (
    _process_single_cif,
    _reset_failed_job,
    _submit_array_batch,
    build_namespace,
    load_run_state,
    resume_target,
    status_category,
)
from shared import (
    CALC_TYPE_LABELS,
    batch_status_html,
    grid_columns,
    table_head_html,
    group_cif_names,
    group_rows,
    labeled_field,
    _Nav,
    _SYSTEM_PARAM_KEYS,
    _effective_status_html,
    _fmt,
    _fmt_calc_type,
    _fmt_energy,
    _last_run_error,
    _list_all_run_rows,
    _run_log,
    known_runs_dirs,
    DEFAULT_RUNS_DIR,
)


_ROW_COL_WIDTHS = ["9%", "14%", "9%", "13%", "16%", "10%", "11%", "6%", "6%", "6%"]
_TABLE_MIN_WIDTH = "1100px"  # below this the table scrolls sideways


_ROW_COL_LABELS = ["Job ID", "Run ID", "User Name", "CIF File", "Path", "Calculation\nType", "Status", "", "", ""]


# ⓘ next to the Calculation Type header: the table shows QE's own keywords, the
# tooltip says what each one means.
_CALC_TYPE_INFO = (
    ' <span style="cursor:help; color:#1565c0; font-weight:700;" '
    'title="scf = Single Point Energy&#10;relax = Fixed Cell Optimization'
    '&#10;vc-relax = Variable Cell Optimization">&#9432;</span>'
)


_CIF_CELL_MAX = 20


def _cif_cell_text(names: list[str]) -> str:
    """CIF File column text, limited to 20 characters of content.

    One CIF: its name, cut at 20 characters. Several CIFs: the names inside
    curly braces, "{a.cif, b.cif, ...}"; when the names exceed 20 characters
    they are cut and "..." is appended before the closing brace.
    """
    if len(names) == 1:
        return names[0][:_CIF_CELL_MAX]
    inner = ", ".join(names)
    if len(inner) > _CIF_CELL_MAX:
        inner = inner[:_CIF_CELL_MAX] + "..."
    return "{" + inner + "}"


# Log zips are served by Voila at voila/files/downloads/run_logs_<hex>.zip (the
# Dockerfile's file_whitelist allows exactly these names); temporary, cleaned after 1 h.
_LOG_DIR = Path(__file__).resolve().parent.parent / "downloads"


def _run_qe_out(run_path: Path) -> Path | None:
    """The run's QE output: next to its input once copied back, else (a QE job that
    failed before the copy) the newest work_<jobid>/ copy. None if QE never wrote one."""
    st = load_run_state(run_path) or {}
    qe_in = st.get("tasks", {}).get("crystal", {}).get("qe_input")
    if not qe_in:
        return None
    out = (run_path / qe_in).with_suffix(".out")
    if out.is_file():
        return out
    work = sorted(out.parent.glob(f"work_*/{out.name}"), key=lambda p: p.stat().st_mtime)
    return work[-1] if work else None


def _run_logs_zip(rows: list[dict]) -> Path | None:
    """Zip advQMcalc.log + the QE .out of each run in a Runs-table row (a batch row
    gets one folder per CIF). None when no run has either file."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in _LOG_DIR.glob("run_logs_*.zip"):
        try:
            if now - old.stat().st_mtime > 3600:
                old.unlink()
        except OSError:
            pass
    files = []
    for r in rows:
        rp = Path(r.get("run_path") or "")
        prefix = f"{Path(r.get('cif') or rp.name).stem}/" if len(rows) > 1 else ""
        log = rp / "logs" / "advQMcalc.log"
        if log.is_file():
            files.append((log, prefix + log.name))
        out = _run_qe_out(rp)
        if out:
            files.append((out, prefix + out.name))
    if not files:
        return None
    dest = _LOG_DIR / f"run_logs_{uuid.uuid4().hex}.zip"
    tmp = dest.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in files:
            zf.write(src, arc)
    tmp.replace(dest)
    return dest


def _run_error_text(run_path: Path, crystal: dict, max_lines: int = 3) -> str | None:
    """The actual error of a failed run, in up to 3 lines: QE's own error block
    from the .out, else error lines from the SLURM job log (Docker/MPI/node)."""
    lines: list[str] = []
    out = _run_qe_out(run_path)
    if out:
        text = out.read_text(errors="ignore").splitlines()
        for i, ln in enumerate(text):
            if "Error in routine" in ln:  # QE: "Error in routine X (n):" + message lines, closed by %%%%
                for nxt in text[i:i + 6]:
                    if nxt.strip().startswith("%%%%"):
                        break
                    if nxt.strip():
                        lines.append(nxt.strip())
                break
        if not lines:
            lines = [ln.strip() for ln in text if "convergence NOT achieved" in ln][:1]
    if not lines and crystal.get("job_script"):
        slurm_out = (run_path / crystal["job_script"]).with_suffix(".slurm.out")
        if slurm_out.is_file():
            keys = ("error", "segmentation fault", "killed", "cancelled", "time limit", "denied")
            lines = [ln.strip() for ln in slurm_out.read_text(errors="ignore").splitlines()
                     if any(k in ln.lower() for k in keys)
                     and "bind memory" not in ln.lower()]  # Open MPI binding warning: harmless, on every job
    return "\n".join(lines[:max_lines]) or None


class _DownloadTrigger(ipv.VuetifyTemplate):
    """Invisible helper: bumping `nonce` makes the browser download `url` as `filename`."""

    url = traitlets.Unicode("").tag(sync=True)
    filename = traitlets.Unicode("logs.zip").tag(sync=True)
    nonce = traitlets.Int(0).tag(sync=True)
    template = traitlets.Unicode("""
<template><span></span></template>
<script>
module.exports = {
  watch: {
    nonce() {
      const a = document.createElement('a');
      a.href = new URL(this.url, document.baseURI).href;
      a.download = this.filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
    },
  },
};
</script>
""").tag(sync=True)


def _logs_download_cell(rows: list[dict]) -> widgets.VBox:
    """Download icon (same look as the row's other icons): zips the run log(s) and
    QE output(s) on click and hands the zip to the browser."""
    first = rows[0]
    rid = first.get("run_label") or first.get("run_id") or "run"
    trigger = _DownloadTrigger(filename=f"logs_{rid}.zip")
    tip = "Download QE output and workflow log"
    btn = widgets.Button(icon="download", tooltip=tip, layout=widgets.Layout(width="100%"))

    def on_click(_b=None) -> None:
        btn.disabled, btn.icon = True, "spinner"

        def work() -> None:
            try:
                path = _run_logs_zip(rows)
                if path is None:
                    btn.tooltip = "No log or QE output found for this run"
                else:
                    btn.tooltip = tip
                    trigger.url = f"voila/files/downloads/{path.name}"
                    trigger.nonce += 1
            except Exception as e:
                btn.tooltip = f"Could not prepare the download: {e}"
            finally:
                btn.disabled, btn.icon = False, "download"

        threading.Thread(target=work, daemon=True).start()

    btn.on_click(on_click)
    return widgets.VBox([btn, trigger], layout=widgets.Layout(width="100%"))


def _run_row_widget(rows: list[dict], on_open, on_load_results) -> widgets.HBox:
    """One table row: a single run, or one multi-CIF batch (rows sharing an array job)."""
    first = rows[0]
    batch = len(rows) > 1
    group_row = {**first, "batch_rows": rows} if batch else first

    names = group_cif_names(rows)
    cif_text = _cif_cell_text(names)
    cif_html = widgets.HTML(
        f'<span title="{html.escape(", ".join(names))}">{html.escape(cif_text)}</span>',
        layout=widgets.Layout(width="100%"),
    )
    cif_html.add_class("advqm-rowcell-cif")

    job_id = first.get("array_job_id") if batch else first.get("job_id")
    label = first.get("run_label")
    run_id_text = label or (
        f"{first.get('run_id')} (+{len(rows) - 1} more)" if batch else first.get("run_id")
    )
    cif_dir = first.get("cif_dir")

    open_btn = widgets.Button(
        icon="wrench", tooltip="Monitor this run" if not batch else "Monitor all runs in this batch",
        layout=widgets.Layout(width="100%"),
    )
    open_btn.on_click(lambda _b, row=group_row: on_open(row))
    finished = [r for r in rows if status_category(r.get("crystal_status")) == "success"]
    load_results_btn = widgets.Button(
        icon="line-chart",
        disabled=not finished,
        tooltip="View results" if finished else "Available once a run finishes successfully",
        layout=widgets.Layout(width="100%"),
    )
    load_results_btn.on_click(lambda _b, row=group_row: on_load_results(row))
    status_html = (
        widgets.HTML(batch_status_html(rows), layout=widgets.Layout(width="100%"))
        if batch
        else widgets.HTML(_effective_status_html(first), layout=widgets.Layout(width="100%"))
    )
    # Long paths wrap inside their own cell (no truncation) so they never
    # run over the Calculation Type column.
    path_html = widgets.HTML(
        f'<span title="{html.escape(cif_dir or "")}">{html.escape(cif_dir or "")}</span>',
        layout=widgets.Layout(width="100%"),
    )
    path_html.add_class("advqm-rowcell-wrap")
    calc_html = widgets.HTML(html.escape(_fmt(first.get("calc_type"))), layout=widgets.Layout(width="100%"))
    cells = [
        widgets.HTML(html.escape(_fmt(job_id)), layout=widgets.Layout(width="100%")),
        widgets.HTML(html.escape(_fmt(run_id_text)), layout=widgets.Layout(width="100%")),
        widgets.HTML(html.escape(first.get("submitted_by") or "–"), layout=widgets.Layout(width="100%")),
        cif_html,
        path_html,
        calc_html,
        status_html,
        open_btn,
        load_results_btn,
        _logs_download_cell(rows),
    ]
    row_box = widgets.HBox(cells, layout=widgets.Layout(
        width="100%", min_width=_TABLE_MIN_WIDTH,
        display="grid", grid_template_columns=grid_columns(_ROW_COL_WIDTHS),
    ))
    row_box.add_class("advqm-rowlist-row")
    return row_box


def _build_runs_page(nav: _Nav):
    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">Runs</div>'
        '<div class="advqm-page-sub">Jobs &amp; Their Current Status</div></div>'
        "</div>"
    )
    new_run_btn = widgets.Button(description="New Calculation", button_style="success", icon="plus")
    new_run_btn.on_click(lambda _: nav.show("new_run"))

    search_w = ipv.TextField(
        label="Search by CIF Name or Job ID", v_model="", outlined=True, dense=True,
    )
    refresh_btn = widgets.Button(description="Refresh", icon="refresh")

    # ── Filter by user / jump straight to a run's results ───────────────────
    # Same field style as the search box above (caption + outlined input).
    _ALL_USERS = "__all__"
    user_filter_w = ipv.Select(
        label="Filter by User / Jump to Results", items=[], v_model=_ALL_USERS,
        outlined=True, dense=True,
    )

    col_head = widgets.HTML(table_head_html(
        _ROW_COL_LABELS, _ROW_COL_WIDTHS, _TABLE_MIN_WIDTH,
        info={"Calculation\nType": _CALC_TYPE_INFO},
    ))
    # As wide as the rows, so the card (header + rows) is what scrolls sideways.
    rows_box = widgets.VBox([], layout=widgets.Layout(min_width=_TABLE_MIN_WIDTH))
    rows_wrap = widgets.VBox([col_head, rows_box])
    rows_wrap.add_class("advqm-card")
    rows_wrap.add_class("advqm-rowlist")
    rows_wrap.add_class("advqm-runs-table")  # headers and values centred

    current_rows = []

    # ── Monitor / Resume — the run in focus is picked by clicking a row's
    # wrench icon (see on_open below), not a separate dropdown; this label
    # just reflects whatever's currently selected. ──────────────────────────
    selected_run = {"path": None, "paths": [], "label": None}
    selected_label_w = widgets.HTML('<span class="advqm-muted">No run selected — click the ⚙ icon on a run above.</span>')
    job_status = widgets.Label(value="")
    resume_btn = widgets.Button(description="Resume", button_style="success", icon="play")
    resume_target_w = widgets.Dropdown(
        options=[
            ("Latest run for this CIF", "latest"),
            ("Selected CIF file", "cif"),
            ("Selected run folder path", "run_path"),
        ],
        value="cif",
        description="Resume target",
        layout=widgets.Layout(width="100%"),
    )
    # CIF checkboxes (shown for "Selected CIF file"): the CIFs of the job opened with
    # the gear icon; tick the ones to resume (each resumes its own run of that job).
    cif_checks: dict[str, widgets.Checkbox] = {}
    cif_target: dict[str, Path] = {}
    cif_list_box = widgets.VBox(
        [], layout=widgets.Layout(max_height="220px", overflow_y="auto", width="100%",
                                  padding="4px 0")
    )
    cif_select_all_btn = widgets.Button(description="Select all", layout=widgets.Layout(width="auto"))
    cif_clear_btn = widgets.Button(description="Clear", layout=widgets.Layout(width="auto"))
    cif_title_w = widgets.HTML('<div style="font-weight:600; margin:6px 0 2px;">CIF files to resume</div>')
    cif_picker = widgets.VBox([
        cif_title_w, widgets.HBox([cif_select_all_btn, cif_clear_btn]), cif_list_box,
    ])

    def _toggle_cif_picker(*_):
        cif_picker.layout.display = "" if resume_target_w.value == "cif" else "none"

    resume_target_w.observe(_toggle_cif_picker, names="value")
    _toggle_cif_picker()
    resubmit_failed_w = widgets.Checkbox(
        value=False, description="Resubmit if SLURM job failed",
    )
    force_pp_resume_w = widgets.Checkbox(
        value=False, description="Force pseudopotential cleanup",
    )
    resume_note_w = widgets.HTML(
        '<span class="advqm-muted">Resume reuses the saved run settings. '
        "If a SLURM job was already submitted, resume will not submit a new job; "
        "forced cleanup rewrites the QE input only. \"Resubmit if SLURM job failed\" clears the saved job of "
        "failed/cancelled/timed-out runs and submits them again. With \"Selected CIF file\", tick the CIF "
        "files of the selected job to resume.</span>"
    )
    state_out = widgets.HTML()

    def _cif_name_of(cif) -> str:
        return Path(cif or "?").name

    def _rebuild_cif_checks(job_runs: dict[str, Path]) -> None:
        """Checkbox list for ONE job: the CIF files of the run(s) opened with the
        gear icon (a multi-CIF batch lists all its CIFs), all ticked by default.
        Each CIF resumes its own run from that job."""
        cif_target.clear()
        cif_target.update(job_runs)
        cif_checks.clear()
        for name in sorted(job_runs, key=str.lower):
            crystal = (load_run_state(job_runs[name]) or {}).get("tasks", {}).get("crystal", {})
            state_txt = crystal.get("slurm_state") or crystal.get("status") or "not submitted"
            cif_checks[name] = widgets.Checkbox(
                value=True, description=f"{name}  —  {state_txt}", indent=False,
                style={"description_width": "initial"},
                layout=widgets.Layout(width="100%", margin="0"),
            )
        cif_list_box.children = list(cif_checks.values()) or [
            widgets.HTML('<span class="advqm-muted">Click the ⚙ icon on a run to list its CIF files.</span>')
        ]

    def _ticked_runs() -> dict[str, Path]:
        return {name: cif_target[name] for name, cb in cif_checks.items() if cb.value}

    def _set_all_ticks(value: bool) -> None:
        for cb in cif_checks.values():
            cb.value = value

    cif_select_all_btn.on_click(lambda _: _set_all_ticks(True))
    cif_clear_btn.on_click(lambda _: _set_all_ticks(False))

    def _select_run(rp: Path, label: str, paths: list | None = None) -> None:
        selected_run["path"] = rp
        selected_run["paths"] = list(paths) if paths else [rp]
        selected_run["label"] = label
        selected_label_w.value = f'<b>Selected run:</b> {label}'
        job_runs: dict[str, Path] = {}
        for p in selected_run["paths"]:
            name = _cif_name_of((load_run_state(p) or {}).get("cif"))
            if name in job_runs:  # same CIF twice in one job: tell them apart by run folder
                name = f"{name} ({p.name})"
            job_runs[name] = p
        _rebuild_cif_checks(job_runs)
        show_state()

    def show_state(_=None):
        paths = selected_run["paths"] or ([selected_run["path"]] if selected_run["path"] else [])
        # Plain HTML value (not an Output widget), so the auto status check can
        # redraw it safely from its background thread.
        state_out.value = "".join(_one_state_html(rp, header=len(paths) > 1) for rp in paths)

    def _one_state_html(rp, header=False) -> str:
        st = load_run_state(rp)
        if st is None:
            return f'<div class="advqm-muted">No state.json found for {html.escape(str(rp))}</div>'
        crystal = st.get("tasks", {}).get("crystal", {})
        qe_output = crystal.get("qe_output") or {}
        qe_results = crystal.get("qe_results") or {}
        try:
            setup_error = _last_run_error(rp)
        except Exception:
            setup_error = None
        rows = [
            ("CIF", _fmt(Path(st.get("cif") or "?").name if st.get("cif") else None)),
            ("Calculation Type", _fmt_calc_type(st.get("calc_type"))),
            ("Status", _fmt(crystal.get("status"))),
            ("Job ID", _fmt(crystal.get("job_id"))),
            ("SLURM State", _fmt(crystal.get("slurm_state"))),
            ("Setup Error", setup_error or "None"),
            ("k-points", _fmt(crystal.get("kpoints"))),
            ("Pseudopotential Dir", _fmt(crystal.get("pseudo_dir"))),
            ("DFT Functional", _fmt((crystal.get("system_params") or {}).get("input_dft"))),
            ("vdW Correction", _fmt((crystal.get("system_params") or {}).get("vdw_corr"))),
            ("GPU", _fmt(
                f'{crystal["gpu"]["count"]} x {crystal["gpu"]["type"]}'
                if crystal.get("gpu") else "No"
            )),
            ("Submitted At", _fmt(crystal.get("submitted_at"))),
            ("User Name", _fmt(st.get("submitted_by"))),
            ("Energy (kJ/mol)", _fmt_energy(qe_results.get("energy_ry"))),
            ("Error found", (_run_error_text(rp, crystal) if status_category(crystal.get("status")) == "danger" else None) or "None"),
        ]
        lines = "".join(
            f'<div style="padding:4px 0; border-bottom:1px solid #f1f3f7;">'
            f'<span style="color:#667085; font-weight:600;">{label}:</span> '
            f'<span style="color:#1f2937;">{html.escape(str(value)).replace(chr(10), "<br>")}</span></div>'
            for label, value in rows
        )
        title = (
            f'<div style="font-weight:700; margin:10px 0 4px;">'
            f'{html.escape(_fmt(Path(st.get("cif") or "?").name))} · {html.escape(rp.name)}</div>'
            if header else ""
        )
        return f'<div style="font-size:0.85rem;">{title}{lines}</div>'

    def _auto_check(paths: list[Path]) -> None:
        """Opening a run (⚙) checks SLURM for its unfinished runs and saves the
        result, like Refresh does but for just these runs (never submits)."""
        unfinished = [
            rp for rp in paths
            if status_category(((load_run_state(rp) or {}).get("tasks", {})
                                .get("crystal", {})).get("status")) in ("info", "warning")
        ]
        if not unfinished:
            return
        job_status.value = "Checking SLURM status…"

        def work() -> None:
            try:
                for rp in unfinished:
                    _resolve_one_stale({"run_path": str(rp), "run_id": rp.name}, _base_for(rp))
            finally:
                job_status.value = ""
                show_state()
                refresh()

        threading.Thread(target=work, daemon=True).start()

    def _scan_dirs() -> list[Path]:
        """Folders the table reads: the default runs folder plus every folder a
        batch was submitted to (picked as Runs Directory on New Calculation)."""
        return known_runs_dirs()

    def _base_for(rp: Path) -> Path:
        """The runs folder a given run lives under."""
        rp_res = Path(rp).resolve()
        for d in _scan_dirs():
            try:
                if d.resolve() in rp_res.parents:
                    return d
            except OSError:
                continue
        return Path(DEFAULT_RUNS_DIR)

    def _resume_one(rp: Path, mode: str, force_pp: bool) -> None:
        label = rp.parent.name
        base = _base_for(rp)
        sel_runs_dir, sel_run_id = resume_target(base, label, rp)
        if mode == "latest":
            runs_dir, run_id, run_path = sel_runs_dir, None, None
        elif mode == "run_path":
            runs_dir, run_id, run_path = sel_runs_dir, None, str(rp)
        else:
            runs_dir, run_id, run_path = sel_runs_dir, sel_run_id, None
        st = load_run_state(rp) or {}
        crystal = st.get("tasks", {}).get("crystal", {})
        kpoints = crystal.get("kpoints")
        system_params = crystal.get("system_params") or {}
        ns = build_namespace(
            resume=True, run_id=run_id, run_path=run_path, runs_dir=str(runs_dir),
            calc_type=st.get("calc_type") or "scf", kpoints=kpoints,
            pseudo_dir=crystal.get("pseudo_dir"),
            pp_map=None,
            force_pp_cleanup=force_pp,
            **{key: system_params.get(key) for key in _SYSTEM_PARAM_KEYS},
        )
        _process_single_cif(rp, runs_dir, ns)

    def on_resume(_=None):
        mode = resume_target_w.value
        force_pp = bool(force_pp_resume_w.value)
        if mode == "cif":
            ticked = _ticked_runs()
            if not ticked:
                job_status.value = "Tick at least one CIF file to resume."
                return
            targets = [(name, rp, "run_path") for name, rp in ticked.items()]
        else:
            paths = selected_run["paths"] or ([selected_run["path"]] if selected_run["path"] else [])
            if not paths:
                job_status.value = "No run selected — click the ⚙ icon on a run above."
                return
            targets = [(rp.name, rp, mode) for rp in paths]
        resubmit = bool(resubmit_failed_w.value)
        job_status.value = (
            f"Resuming {len(targets)} run(s) ({mode}, "
            f"{'force PP cleanup' if force_pp else 'keeping saved PP files'}"
            f"{', resubmit failed' if resubmit else ''})..."
        )
        resume_btn.disabled = True

        notes: list[str] = []

        def work():
            try:
                if resubmit:
                    # Failed/cancelled/timed-out jobs: clear the saved job and
                    # submit them again together as one new SLURM array.
                    reset_dirs, remaining = [], []
                    for name, rp, run_mode in targets:
                        try:
                            if _reset_failed_job(rp):
                                reset_dirs.append(rp)
                                continue
                        except Exception as e:
                            _run_log(f"Could not reset {name}: {e}")
                        remaining.append((name, rp, run_mode))
                    if reset_dirs:
                        try:
                            array_id = _submit_array_batch(reset_dirs, _base_for(reset_dirs[0]), 0)
                            _run_log(f"Resubmitted {len(reset_dirs)} failed run(s) as SLURM array job {array_id}")
                            notes.append(f"Resubmitted {len(reset_dirs)} failed run(s) as SLURM array job {array_id}.")
                        except Exception as e:
                            _run_log(f"Resubmission failed: {e}")
                            notes.append(f"Resubmission failed: {e}")
                    else:
                        _run_log("No failed SLURM jobs among the selected runs; resuming normally")
                        notes.append("None of the ticked runs has a failed SLURM job, so nothing was resubmitted.")
                    targets_to_resume = remaining
                else:
                    targets_to_resume = targets
                for name, rp, run_mode in targets_to_resume:
                    try:
                        _resume_one(rp, run_mode, force_pp)
                    except Exception as e:
                        _run_log(f"Resume failed for {name}: {e}")
                        notes.append(f"Resume failed for {name}: {e}")
                notes.append(f"Resume finished for {len(targets_to_resume)} run(s).")
            finally:
                job_status.value = " ".join(notes) or "Done."
                resume_btn.disabled = False

        threading.Thread(target=work, daemon=True).start()

    resume_btn.on_click(on_resume)

    # Monitor only: run details + automatic SLURM status check (no Resume controls).
    monitor_body = widgets.VBox([selected_label_w, job_status, state_out])
    monitor_acc = widgets.Accordion(children=[monitor_body])
    monitor_acc.set_title(0, "Monitor")
    monitor_acc.selected_index = None

    selected_run_path_str = {"value": None}

    def on_open(row: dict) -> None:
        batch = row.get("batch_rows") or [row]
        paths = [Path(r["run_path"]) for r in batch if r.get("run_path")]
        if not paths:
            return
        if len(paths) > 1:
            label = f"batch of {len(paths)} runs (job {_fmt(row.get('array_job_id'))})"
        else:
            label = f"{_fmt(row.get('cif'))} @ {_fmt(row.get('run_id'))}"
        _select_run(paths[0], label, paths)
        monitor_acc.selected_index = 0
        selected_run_path_str["value"] = row.get("run_path")
        render_rows()
        _auto_check(paths)

    def on_load_results(row: dict) -> None:
        nav.show("results", row=row)

    def _refresh_user_filter():
        users = sorted({r["submitted_by"] for r in current_rows if r.get("submitted_by")})
        items = (
            [{"text": "All users", "value": _ALL_USERS}]
            + [{"text": u, "value": u} for u in users]
            + [{"text": "Unknown", "value": "__unknown__"}]
        )
        prev = user_filter_w.v_model
        user_filter_w.items = items
        user_filter_w.v_model = prev if prev in [i["value"] for i in items] else _ALL_USERS

    def _filtered_by_user(rows: list[dict]) -> list[dict]:
        selected = user_filter_w.v_model
        if selected in (None, _ALL_USERS):
            return rows
        if selected == "__unknown__":
            return [r for r in rows if not r.get("submitted_by")]
        return [r for r in rows if r.get("submitted_by") == selected]

    def render_rows(*_):
        filt = (search_w.v_model or "").strip().lower()
        rows = _filtered_by_user(current_rows)
        rows = [
            r
            for r in rows
            if not filt
            or filt in str(r.get("cif", "")).lower()
            or filt in str(r.get("job_id", "")).lower()
            or filt in str(r.get("run_id", "")).lower()
            or filt in str(r.get("run_label") or "").lower()
            or filt in str(r.get("array_job_id") or "").lower()
        ]
        if not rows:
            empty = widgets.HTML('<div class="advqm-muted" style="padding:16px;">No runs found.</div>')
            rows_box.children = [empty]
            return
        # One table row per multi-CIF batch (shared array job id); every other
        # run (single CIF, or runs from before arrays) stays one row each.
        groups = group_rows(rows)
        children = []
        for grp in groups:
            children.append(_run_row_widget(grp, on_open, on_load_results))
            if selected_run_path_str["value"] and any(
                r.get("run_path") == selected_run_path_str["value"] for r in grp
            ):
                children.append(monitor_acc)
        rows_box.children = children

    def refresh(_=None):
        nonlocal current_rows
        dirs = _scan_dirs()
        rows, seen = [], set()
        try:
            for d in dirs:
                for r in _list_all_run_rows(d, only_successful=False):
                    if r.get("run_path") not in seen:
                        seen.add(r.get("run_path"))
                        rows.append(r)
        except Exception as e:
            current_rows = []
            rows_box.children = [
                widgets.HTML(f'<div class="advqm-muted" style="padding:16px;">Aggregation failed: {e}</div>')
            ]
            return
        for r in rows:
            st = load_run_state(Path(r["run_path"])) if r.get("run_path") else None
            r["calc_type"] = (st or {}).get("calc_type")
            r["submitted_by"] = (st or {}).get("submitted_by") or None
        current_rows = rows
        _refresh_user_filter()
        render_rows()

    def _resolve_one_stale(r: dict, base: Path) -> None:
        # Re-runs the same resume/monitor logic as clicking the ⚙ icon's
        # Resume button, but silently in the background — the pipeline only
        # ever checks SLURM status once, synchronously, right after submit
        # (cli.py's _process_single_cif, TASK 6), so anything still
        # Pending/Running in state.json may just never have been looked at
        # again. This re-checks it for real instead of trusting a stale
        # snapshot.
        rp = Path(r["run_path"]) if r.get("run_path") else None
        if rp is None:
            return
        label = rp.parent.name
        try:
            runs_dir, run_id = resume_target(base, label, rp)
        except Exception:
            return
        st = load_run_state(rp) or {}
        crystal = st.get("tasks", {}).get("crystal", {})
        kpoints = crystal.get("kpoints")
        ns = build_namespace(
            resume=True, run_id=run_id, run_path=None, runs_dir=str(runs_dir),
            calc_type=st.get("calc_type") or "scf", kpoints=kpoints,
            pseudo_dir=crystal.get("pseudo_dir"),
            pp_map=None,
            force_pp_cleanup=False,
            # Refresh only monitors. Runs that are prepared but not yet submitted
            # (waiting for their batch's SLURM array) must not be sent to SLURM
            # one by one here.
            defer_submit=True,
            **{key: (crystal.get("system_params") or {}).get(key)
               for key in _SYSTEM_PARAM_KEYS},
        )
        try:
            _process_single_cif(rp, runs_dir, ns)
        except Exception as e:
            _run_log(f"Auto-refresh failed for {r.get('run_id')}: {e}")

    def refresh_and_resolve_stale(_=None):
        refresh_btn.disabled = True
        refresh_btn.description = "Refreshing…"
        refresh()
        stale = [r for r in current_rows if status_category(r.get("crystal_status")) in ("info", "warning")]

        def work():
            for r in stale:
                _resolve_one_stale(r, _base_for(Path(r["run_path"])) if r.get("run_path") else None)
            refresh()
            refresh_btn.disabled = False
            refresh_btn.description = "Refresh"

        if not stale:
            refresh_btn.disabled = False
            refresh_btn.description = "Refresh"
            return

        threading.Thread(target=work, daemon=True).start()

    refresh_btn.on_click(refresh_and_resolve_stale)
    search_w.observe(render_rows, names="v_model")
    user_filter_w.observe(render_rows, names="v_model")

    refresh()

    return widgets.VBox(
        [
            header,
            widgets.HBox(
                [new_run_btn, refresh_btn],
                layout=widgets.Layout(padding="0 0 12px 0", grid_gap="10px"),
            ),
            labeled_field(search_w),
            labeled_field(user_filter_w),
            rows_wrap,
        ]
    )




# © 2017-26. Aganitha Cognitive Solutions Private Limited. Proprietary and Confidential.

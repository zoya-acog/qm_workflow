"""
app_src.py
----------
advQMcalc_test — QM Crystal Workflow.
Thin UI over the existing backend. Handles:
  1. Run new QE crystal calculations from uploaded CIF files (via SLURM)
  2. Monitor / resume existing workflow runs
  3. Aggregate per-CIF results into a summary table

Built on ipywidgets (ipywidgets 8 / py3.12 stack).

Layout: a dark left sidebar (New Run / Runs / Results) next to a
single main content area whose contents are swapped on navigation — the
closest approximation of a multi-page dashboard achievable inside Voila.
"""

import base64
import io
import json
import logging
import re
import sys
import os
import threading
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv
from IPython.display import HTML, display

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── Locate helpers ────────────────────────────────────────────────────────────
_APP_DIR = None
for _root in [Path.cwd(), Path.cwd().parent, *Path.cwd().parents]:
    _candidate = _root / "app" / "helpers.py"
    if _candidate.is_file():
        _APP_DIR = _root / "app"
        break
if _APP_DIR is not None:
    sys.path.insert(0, str(_APP_DIR))
    sys.path.insert(0, str(_APP_DIR.parent))

from helpers import (
    UPLOAD_DIR,
    build_namespace,
    attach_log_handler,
    save_uploads,
    enumerate_runs,
    load_run_state,
    resume_target,
    status_badge_html,
    status_category,
    _process_single_cif,
    _query_slurm_job,
    _runs_dir_for_cif,
)

DEFAULT_RUNS_DIR = "/mnt/own6d/qe_workflow/data/runs"


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


def _stamp_submitted_by(runs_dir: Path, submitted_by: str) -> None:
    """Record who submitted the most-recently-created run under runs_dir.

    Frontend-only enrichment of state.json (no cli.py changes) — mirrors the
    pattern already used to add calc_type to the Runs table via a post-hoc
    load_run_state read, just as a write this time.
    """
    try:
        pairs = enumerate_runs(runs_dir)
        if not pairs:
            return
        _, latest_run_path = max(pairs, key=lambda p: p[1].name)
        state_file = latest_run_path / "state.json"
        state = json.loads(state_file.read_text())
        state["submitted_by"] = submitted_by or None
        state_file.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


# ── New Run page ─────────────────────────────────────────────────────────────
def _build_new_run_page(nav: _Nav):
    upload = widgets.FileUpload(accept=".cif", multiple=True)
    # These use ipyvuetify (not plain ipywidgets) — its Material text fields
    # handle box-sizing/width correctly inside percentage-width flex columns,
    # which plain ipywidgets <input> elements were not doing reliably (fields
    # were getting cut off on the right / causing horizontal scroll).
    calc_type_w = ipv.Select(label="Calculation Type", items=["scf", "relax"],
                              v_model="scf", outlined=True, dense=True)
    runs_dir_w = widgets.Text(value=DEFAULT_RUNS_DIR, layout=widgets.Layout(width="100%"))
    log_name_w = widgets.Text(value="advQMcalc.log", layout=widgets.Layout(width="100%"))
    force_pp_w = widgets.Checkbox(value=False, description="Force pseudopotential cleanup")
    kpoints_w = ipv.TextField(label="K-Points Grid (optional)", v_model="3 3 2 0 0 0",
                               placeholder="e.g. 3 3 2 0 0 0 (auto if empty)", outlined=True, dense=True)
    ksep_w = ipv.TextField(label="K-Point Separation", v_model="0.03", outlined=True, dense=True)
    pseudo_dir_w = ipv.TextField(label="Pseudopotential Directory",
                                  v_model="/mnt/own6d/qe_workflow/data/pseudos",
                                  placeholder="/path/to/pseudo", outlined=True, dense=True)
    pp_map_w = widgets.Text(value="", placeholder="/path/to/pp_map.json", layout=widgets.Layout(width="100%"))
    walltime_w = ipv.TextField(label="Walltime", v_model="12:00:00", outlined=True, dense=True)
    ntasks_w = ipv.TextField(label="Tasks", v_model="8", outlined=True, dense=True)
    qe_cmd_w = ipv.TextField(label="QE Command", v_model="pw.x", outlined=True, dense=True)
    submitted_by_w = ipv.TextField(label="Submitted By *", v_model="",
                                    placeholder="Your name/initials", outlined=True, dense=True)

    run_btn = widgets.Button(description="Submit Run", button_style="success", icon="play")
    reset_btn = widgets.Button(description="Cancel", icon="undo")
    sample_btn = widgets.Button(description="Try Sample", icon="refresh")
    run_log = widgets.Output()
    attach_log_handler(run_log)

    def on_sample_clicked(b: widgets.Button) -> None:
        calc_type_w.v_model = "scf"
        runs_dir_w.value = DEFAULT_RUNS_DIR
        log_name_w.value = "advQMcalc.log"
        kpoints_w.v_model = "3 3 2 0 0 0"
        ksep_w.v_model = "0.03"
        pseudo_dir_w.v_model = "/mnt/own6d/qe_workflow/data/pseudos"
        pp_map_w.value = ""
        walltime_w.v_model = "12:00:00"
        ntasks_w.v_model = "8"
        qe_cmd_w.v_model = "pw.x"
        force_pp_w.value = False
        submitted_by_w.v_model = ""
        run_log.clear_output()
        run_log.append_stdout("Sample values loaded. Upload a .cif file and click Submit Run.\n")

    sample_btn.on_click(on_sample_clicked)

    def on_run_clicked(b: widgets.Button) -> None:
        if not (submitted_by_w.v_model or "").strip():
            _run_log("Submitted By is required — enter your name/initials before submitting.")
            return
        run_btn.disabled = True
        try:
            staged = save_uploads(upload, UPLOAD_DIR)
        except Exception as e:
            _run_log(f"Upload error: {e}")
            run_btn.disabled = False
            return
        if not staged:
            _run_log("No CIF files selected for upload.")
            run_btn.disabled = False
            return
        base_runs = Path(runs_dir_w.value)
        multi = len(staged) > 1
        try:
            ntasks_val = int(ntasks_w.v_model)
            ksep_val = float(ksep_w.v_model)
        except (TypeError, ValueError) as e:
            _run_log(f"Invalid Tasks/K-Point Separation value: {e}")
            run_btn.disabled = False
            return
        common = dict(
            calc_type=calc_type_w.v_model or "scf",
            runs_dir=str(base_runs),
            log_name=log_name_w.value,
            force_pp_cleanup=force_pp_w.value,
            pseudo_dir=(pseudo_dir_w.v_model or "").strip() or None,
            pp_map=pp_map_w.value.strip() or None,
            slurm_walltime=walltime_w.v_model,
            slurm_ntasks=ntasks_val,
            qe_command=qe_cmd_w.v_model,
            kpoints=(kpoints_w.v_model or "").strip() or None,
            kpoint_separation=ksep_val,
        )

        submitted_by = submitted_by_w.v_model.strip()

        def work() -> None:
            _run_log(f"Starting run for {len(staged)} uploaded CIF file(s)")
            for cif in staged:
                ns = build_namespace(cif=[str(cif)], **common)
                runs_dir = _runs_dir_for_cif(base_runs, cif, multi)
                try:
                    _process_single_cif(cif, runs_dir, ns)
                    _stamp_submitted_by(runs_dir, submitted_by)
                except Exception as e:
                    _run_log(f"Run failed for {cif.name}: {e}")
            _run_log("Workflow batch finished.")

        def on_done() -> None:
            run_btn.disabled = False

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def _check() -> None:
            while t.is_alive():
                pass
            on_done()

        threading.Thread(target=_check, daemon=True).start()

    def on_reset_clicked(b: widgets.Button) -> None:
        upload.value = {}
        run_log.clear_output()
        run_log.append_stdout("Ready.\n")

    run_btn.on_click(on_run_clicked)
    reset_btn.on_click(on_reset_clicked)

    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">New Calculation Run</div>'
        '<div class="advqm-page-sub">Configure and submit a Quantum ESPRESSO workflow</div></div>'
        "</div>"
    )

    upload_card = widgets.VBox(
        [widgets.HTML('<div class="advqm-card-title">\U0001F4C4 Crystal Structure Files</div>'), upload],
    )
    upload_card.add_class("advqm-card")

    def _ipv_row(*widgets_):
        # Vuetify's outlined text field floats its label up into the top
        # border on focus/when filled — with zero top padding on the column,
        # that label had nowhere to go and was getting clipped by whatever
        # sits directly above it. padding-top gives it room without touching
        # the horizontal spacing/design.
        return ipv.Row(children=[
            ipv.Col(children=[w], style_="flex:1; min-width:0; padding:10px 8px 0 8px;") for w in widgets_
        ], style_="margin:0 -8px;")

    params_card = widgets.VBox(
        [
            widgets.HTML('<div class="advqm-card-title">⚙️ Calculation Parameters</div>'),
            _ipv_row(calc_type_w, pseudo_dir_w),
            _ipv_row(kpoints_w, ksep_w),
            _ipv_row(submitted_by_w),
        ]
    )
    params_card.add_class("advqm-card")

    slurm_card = widgets.VBox(
        [
            widgets.HTML('<div class="advqm-card-title">\U0001F5A5️ SLURM Configuration</div>'),
            _ipv_row(walltime_w, ntasks_w, qe_cmd_w),
        ]
    )
    slurm_card.add_class("advqm-card")

    advanced = widgets.Accordion(
        children=[
            widgets.VBox(
                [
                    widgets.HTML("<b>Runs Directory</b>"), runs_dir_w,
                    widgets.HTML("<b>Log File Name</b>"), log_name_w,
                    widgets.HTML("<b>PP Map (optional)</b>"), pp_map_w,
                    force_pp_w,
                ]
            )
        ]
    )
    advanced.set_title(0, "Advanced settings")
    advanced.selected_index = None

    actions = widgets.HBox([sample_btn, run_btn, reset_btn])

    return widgets.VBox(
        [header, upload_card, params_card, slurm_card, advanced, actions, run_log]
    )


# ── Runs page (table + monitor/resume) ──────────────────────────────────────
_ROW_COL_WIDTHS = ["20%", "12%", "9%", "12%", "13%", "14%", "12%", "8%"]
_ROW_COL_LABELS = ["Cif file", "Run id", "Job id", "Calculation type", "Energy (ry)", "Status", "", ""]


def _run_row_widget(r: dict, on_open, on_load_results) -> widgets.HBox:
    cif_path = r.get("cif")
    cif_name = Path(cif_path).name if cif_path else _fmt(cif_path)
    cif_html = widgets.HTML(
        f'<span title="{_fmt(cif_path)}">{cif_name}</span>',
        layout=widgets.Layout(width=_ROW_COL_WIDTHS[0]),
    )
    cif_html.add_class("advqm-rowcell-cif")
    open_btn = widgets.Button(
        icon="wrench", tooltip="Monitor & Resume this run",
        layout=widgets.Layout(width=_ROW_COL_WIDTHS[6]),
    )
    open_btn.on_click(lambda _b, row=r: on_open(row))
    is_finished = status_category(r.get("crystal_status")) == "success"
    load_results_btn = widgets.Button(
        description="Load Results", icon="line-chart",
        disabled=not is_finished,
        tooltip="View this run's results" if is_finished else "Available once the run finishes successfully",
        layout=widgets.Layout(width=_ROW_COL_WIDTHS[7]),
    )
    load_results_btn.on_click(lambda _b, row=r: on_load_results(row))
    cells = [
        cif_html,
        widgets.HTML(_fmt(r.get("run_id")), layout=widgets.Layout(width=_ROW_COL_WIDTHS[1])),
        widgets.HTML(_fmt(r.get("job_id")), layout=widgets.Layout(width=_ROW_COL_WIDTHS[2])),
        widgets.HTML(_fmt(r.get("calc_type")), layout=widgets.Layout(width=_ROW_COL_WIDTHS[3])),
        widgets.HTML(_fmt_energy(r.get("energy_ry")), layout=widgets.Layout(width=_ROW_COL_WIDTHS[4])),
        widgets.HTML(_effective_status_html(r), layout=widgets.Layout(width=_ROW_COL_WIDTHS[5])),
        open_btn,
        load_results_btn,
    ]
    row_box = widgets.HBox(cells, layout=widgets.Layout(width="100%"))
    row_box.add_class("advqm-rowlist-row")
    return row_box


def _build_runs_page(nav: _Nav):
    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">Runs</div>'
        '<div class="advqm-page-sub">All workflow runs and their current status</div></div>'
        "</div>"
    )
    new_run_btn = widgets.Button(description="New Run", button_style="success", icon="plus")
    new_run_btn.on_click(lambda _: nav.show("new_run"))

    runs_dir_w = widgets.Text(value=DEFAULT_RUNS_DIR, layout=widgets.Layout(width="60%"))
    search_w = widgets.Text(placeholder="Search by CIF name or job ID...", layout=widgets.Layout(width="60%"))
    refresh_btn = widgets.Button(description="Refresh", icon="refresh")

    # ── Filter by user / jump straight to a run's results ───────────────────
    user_filter_w = widgets.Dropdown(layout=widgets.Layout(width="calc(40% - 8px)"))
    job_w = widgets.Dropdown(layout=widgets.Layout(width="calc(40% - 8px)"))
    load_selected_btn = widgets.Button(
        description="Load Results", button_style="primary", icon="line-chart",
        layout=widgets.Layout(width="calc(20% - 8px)"),
    )

    col_head = widgets.HTML(
        '<div class="advqm-rowlist-head">'
        + "".join(
            f'<span style="width:{w};">{label}</span>'
            for w, label in zip(_ROW_COL_WIDTHS, _ROW_COL_LABELS)
        )
        + "</div>"
    )
    rows_box = widgets.VBox([])
    rows_wrap = widgets.VBox([col_head, rows_box])
    rows_wrap.add_class("advqm-card")
    rows_wrap.add_class("advqm-rowlist")

    current_rows = []

    # ── Monitor / Resume — the run in focus is picked by clicking a row's
    # wrench icon (see on_open below), not a separate dropdown; this label
    # just reflects whatever's currently selected. ──────────────────────────
    selected_run = {"path": None, "label": None}
    selected_label_w = widgets.HTML('<span class="advqm-muted">No run selected — click the ⚙ icon on a run above.</span>')
    job_status = widgets.Label(value="—")
    query_btn = widgets.Button(description="Query SLURM", icon="search")
    resume_btn = widgets.Button(description="Resume", button_style="success", icon="play")
    state_out = widgets.Output()

    def _select_run(rp: Path, label: str) -> None:
        selected_run["path"] = rp
        selected_run["label"] = label
        selected_label_w.value = f'<b>Selected run:</b> {label}'
        show_state()

    def show_state(_=None):
        state_out.clear_output()
        rp = selected_run["path"]
        with state_out:
            if not rp:
                return
            st = load_run_state(rp)
            if st is None:
                print(f"No state.json found for {rp}")
                return
            crystal = st.get("tasks", {}).get("crystal", {})
            qe_output = crystal.get("qe_output") or {}
            qe_results = crystal.get("qe_results") or {}
            error_markers = qe_output.get("qe_error_markers") or []
            rows = [
                ("CIF", _fmt(Path(st.get("cif") or "?").name if st.get("cif") else None)),
                ("Calculation Type", _fmt(st.get("calc_type"))),
                ("Status", _fmt(crystal.get("status"))),
                ("Job ID", _fmt(crystal.get("job_id"))),
                ("SLURM State", _fmt(crystal.get("slurm_state"))),
                ("K-Points", _fmt(crystal.get("kpoints"))),
                ("Pseudopotential Dir", _fmt(crystal.get("pseudo_dir"))),
                ("Submitted At", _fmt(crystal.get("submitted_at"))),
                ("Submitted By", _fmt(st.get("submitted_by"))),
                ("Energy (Ry)", _fmt_energy(qe_results.get("energy_ry"))),
                ("JOB DONE seen", _fmt(qe_output.get("qe_job_done"))),
                ("Error markers found", _fmt(", ".join(error_markers)) if error_markers else "None"),
            ]
            lines = "".join(
                f'<div style="padding:4px 0; border-bottom:1px solid #f1f3f7;">'
                f'<span style="color:#667085; font-weight:600;">{label}:</span> '
                f'<span style="color:#1f2937;">{value}</span></div>'
                for label, value in rows
            )
            display(HTML(f'<div style="font-size:0.85rem;">{lines}</div>'))

    def on_query(_=None):
        rp = selected_run["path"]
        if not rp:
            job_status.value = "No run selected — click the ⚙ icon on a run above."
            return
        st = load_run_state(rp) or {}
        jid = st.get("tasks", {}).get("crystal", {}).get("job_id")
        if not jid:
            job_status.value = "No SLURM job_id recorded for this run"
            return
        try:
            s = _query_slurm_job(str(jid))
            job_status.value = f"Job {jid}: {s}"
        except Exception as e:
            job_status.value = f"Query failed: {e}"

    def on_resume(_=None):
        rp = selected_run["path"]
        if not rp:
            job_status.value = "No run selected — click the ⚙ icon on a run above."
            return
        label = rp.parent.name
        base = Path(runs_dir_w.value)
        try:
            runs_dir, run_id = resume_target(base, label, rp)
        except Exception as e:
            job_status.value = f"Resume setup failed: {e}"
            return
        st = load_run_state(rp) or {}
        kpoints = st.get("tasks", {}).get("crystal", {}).get("kpoints")
        ns = build_namespace(
            resume=True, run_id=run_id, runs_dir=str(runs_dir),
            calc_type=st.get("calc_type") or "scf", kpoints=kpoints,
        )
        resume_btn.disabled = True

        def work():
            try:
                _process_single_cif(rp, runs_dir, ns)
            except Exception as e:
                _run_log(f"Resume failed: {e}")
            finally:
                resume_btn.disabled = False

        t = threading.Thread(target=work, daemon=True)
        t.start()

        def _check():
            while t.is_alive():
                pass
            resume_btn.disabled = False

        threading.Thread(target=_check, daemon=True).start()

    query_btn.on_click(on_query)
    resume_btn.on_click(on_resume)

    monitor_body = widgets.VBox(
        [selected_label_w, widgets.HBox([job_status, query_btn, resume_btn]), state_out]
    )
    monitor_acc = widgets.Accordion(children=[monitor_body])
    monitor_acc.set_title(0, "Monitor & Resume a run")
    monitor_acc.selected_index = None

    selected_run_path_str = {"value": None}

    def on_open(row: dict) -> None:
        rp = Path(row["run_path"]) if row.get("run_path") else None
        if rp is None:
            return
        label = f"{_fmt(row.get('cif'))} @ {_fmt(row.get('run_id'))}"
        _select_run(rp, label)
        monitor_acc.selected_index = 0
        selected_run_path_str["value"] = row.get("run_path")
        render_rows()

    def on_load_results(row: dict) -> None:
        nav.show("results", row=row)

    def _refresh_user_filter():
        users = sorted({r["submitted_by"] for r in current_rows if r.get("submitted_by")})
        options = [("All users", None)] + [(u, u) for u in users] + [("Unknown", "__unknown__")]
        prev = user_filter_w.value
        user_filter_w.options = options
        user_filter_w.value = prev if prev in [o[1] for o in options] else None

    def _filtered_by_user(rows: list[dict]) -> list[dict]:
        selected = user_filter_w.value
        if selected is None:
            return rows
        if selected == "__unknown__":
            return [r for r in rows if not r.get("submitted_by")]
        return [r for r in rows if r.get("submitted_by") == selected]

    def _refresh_job_dropdown(*_):
        rows = _filtered_by_user(current_rows)
        options = [
            (f"{Path(r.get('cif') or '?').name} · {r.get('run_id')} · {r.get('crystal_status') or 'pending'}", r)
            for r in sorted(rows, key=lambda r: r.get("run_id") or "", reverse=True)
        ]
        job_w.options = options
        job_w.value = options[0][1] if options else None

    def on_load_selected(_=None):
        row = job_w.value
        if not row:
            return
        on_load_results(row)

    def render_rows(*_):
        filt = (search_w.value or "").strip().lower()
        rows = _filtered_by_user(current_rows)
        rows = [
            r
            for r in rows
            if not filt
            or filt in str(r.get("cif", "")).lower()
            or filt in str(r.get("job_id", "")).lower()
            or filt in str(r.get("run_id", "")).lower()
        ]
        if not rows:
            empty = widgets.HTML('<div class="advqm-muted" style="padding:16px;">No runs found.</div>')
            rows_box.children = [empty]
            return
        children = []
        for r in rows:
            children.append(_run_row_widget(r, on_open, on_load_results))
            if selected_run_path_str["value"] and r.get("run_path") == selected_run_path_str["value"]:
                children.append(monitor_acc)
        rows_box.children = children

    def refresh(_=None):
        nonlocal current_rows
        base = Path(runs_dir_w.value)
        if not base.is_dir():
            current_rows = []
            rows_box.children = [
                widgets.HTML(f'<div class="advqm-muted" style="padding:16px;">Runs dir does not exist: {base}</div>')
            ]
            return
        try:
            rows = _list_all_run_rows(base, only_successful=False)
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
        _refresh_job_dropdown()
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
        kpoints = st.get("tasks", {}).get("crystal", {}).get("kpoints")
        ns = build_namespace(
            resume=True, run_id=run_id, runs_dir=str(runs_dir),
            calc_type=st.get("calc_type") or "scf", kpoints=kpoints,
        )
        try:
            _process_single_cif(rp, runs_dir, ns)
        except Exception as e:
            _run_log(f"Auto-refresh failed for {r.get('run_id')}: {e}")

    def refresh_and_resolve_stale(_=None):
        refresh_btn.disabled = True
        refresh_btn.description = "Refreshing…"
        refresh()
        base = Path(runs_dir_w.value)
        stale = [r for r in current_rows if status_category(r.get("crystal_status")) in ("info", "warning")]

        def work():
            for r in stale:
                _resolve_one_stale(r, base)
            refresh()
            refresh_btn.disabled = False
            refresh_btn.description = "Refresh"

        if not stale:
            refresh_btn.disabled = False
            refresh_btn.description = "Refresh"
            return

        threading.Thread(target=work, daemon=True).start()

    refresh_btn.on_click(refresh_and_resolve_stale)
    search_w.observe(render_rows, names="value")
    user_filter_w.observe(_refresh_job_dropdown, names="value")
    user_filter_w.observe(render_rows, names="value")
    load_selected_btn.on_click(on_load_selected)

    refresh()

    filter_card = widgets.VBox(
        [
            widgets.HTML('<div class="advqm-card-title">Filter by user / jump to results</div>'),
            widgets.HBox(
                [user_filter_w, job_w, load_selected_btn],
                layout=widgets.Layout(width="100%", justify_content="space-between"),
            ),
        ]
    )
    filter_card.add_class("advqm-card")

    return widgets.VBox(
        [
            header,
            widgets.HBox([new_run_btn]),
            widgets.HBox([runs_dir_w, refresh_btn]),
            search_w,
            filter_card,
            rows_wrap,
        ]
    )


def _parse_scf_history(out_path: Path) -> dict:
    """Parse SCF (and, for relax/vc-relax, ionic-step) convergence history
    out of a QE .out file.

    Real .out files under this project have been observed with every line
    duplicated (interleaved sub-run writes), so this scans in file order and
    plots by occurrence index rather than trusting "iteration #" as a unique
    key — the same "don't assume uniqueness" approach already used by
    _extract_qe_total_energy for the total-energy line.

    For relax/vc-relax, QE runs a full SCF cycle at every ionic (geometry)
    step, and — usefully — its SCF iteration counter resets back to 1 each
    time a new ionic step starts. That reset is used here purely as a
    boundary marker to split the flat accuracy/energy stream into per-ionic-
    step segments, so a relax run can show "energy vs ionic step" (the
    number that actually answers "did the geometry optimization converge")
    instead of one long, visually confusing sawtooth of concatenated SCF
    cycles.
    """
    text = out_path.read_text(errors="ignore")
    iters = [int(m.group(1)) for m in re.finditer(r"iteration #\s*(\d+)\s+ecut=", text)]
    accuracy = [float(m.group(1)) for m in re.finditer(r"estimated scf accuracy\s*<\s*([\d.eE+-]+)\s*Ry", text)]
    energies = [float(m.group(1)) for m in re.finditer(r"total energy\s*=\s*([-\d.]+)\s*Ry", text)]
    converged = "convergence has been achieved" in text
    job_done = "JOB DONE" in text

    # Split into ionic steps wherever the SCF iteration counter resets to 1
    # (after the very first entry). Each segment's last accuracy/energy
    # value is that ionic step's converged SCF result.
    ionic_energies: list[float] = []
    if iters and energies and len(iters) == len(energies):
        seg_start = 0
        for i in range(1, len(iters)):
            if iters[i] == 1 and iters[i - 1] != 1:
                ionic_energies.append(energies[i - 1])
                seg_start = i
        ionic_energies.append(energies[-1])

    return {
        "accuracy": accuracy,
        "final_energy": energies[-1] if energies else None,
        "converged": converged,
        "job_done": job_done,
        "ionic_energies": ionic_energies,
    }


def _render_scf_chart(history: dict, title: str = "SCF convergence history") -> str:
    accuracy = history.get("accuracy") or []
    if not accuracy:
        return '<div class="advqm-muted" style="padding:8px 0;">No SCF accuracy values found in this run\'s output.</div>'

    fig, ax = plt.subplots(figsize=(7, 3.2))
    x = list(range(1, len(accuracy) + 1))
    ax.plot(x, accuracy, color="#1565c0", linewidth=1.8, marker="o", markersize=3)
    ax.set_yscale("log")
    ax.set_xlabel("SCF step")
    ax.set_ylabel("Estimated accuracy (Ry)")
    ax.set_title(title)
    ax.grid(True, which="both", linestyle="--", alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img src="data:image/png;base64,{b64}" style="max-width:100%; border-radius:8px;">'


def _render_ionic_chart(history: dict) -> str:
    """Energy-vs-ionic-step chart — the meaningful convergence view for a
    relax/vc-relax run, as opposed to the raw (multi-step) SCF accuracy plot.
    """
    energies = history.get("ionic_energies") or []
    if len(energies) < 2:
        return ""

    fig, ax = plt.subplots(figsize=(7, 3.0))
    x = list(range(1, len(energies) + 1))
    ax.plot(x, energies, color="#1c7a3d", linewidth=1.8, marker="o", markersize=4)
    ax.set_xlabel("Ionic step")
    ax.set_ylabel("Total energy (Ry)")
    ax.set_title("Geometry optimization progress")
    ax.grid(True, linestyle="--", alpha=0.3)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130)
    plt.close(fig)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f'<img src="data:image/png;base64,{b64}" style="max-width:100%; border-radius:8px;">'


# ── Results / Aggregation page ───────────────────────────────────────────────
def _build_results_page(nav: _Nav, preselect_row: dict | None = None):
    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">Results</div>'
        '<div class="advqm-page-sub">SCF convergence for a run — use the '
        '"Load Results" button on the Runs page to open one</div></div>'
        "</div>"
    )

    result_out = widgets.Output()

    def on_load(row: dict | None) -> None:
        result_out.clear_output()
        with result_out:
            if not row:
                display(HTML(
                    '<div class="advqm-muted" style="padding:8px 0;">'
                    'No run selected — go to the Runs page and click "Load Results" '
                    'on a finished run.</div>'
                ))
                return
            out_file = row.get("qe_output_file")
            if not out_file or not Path(out_file).is_file():
                display(HTML('<div class="advqm-muted" style="padding:8px 0;">'
                              'This run has no QE output file yet — the calculation may still be running.</div>'))
                return
            history = _parse_scf_history(Path(out_file))
            calc_type = row.get("calc_type") or "scf"
            energy = history.get("final_energy")
            is_relax = calc_type in ("relax", "vc-relax")
            ionic_energies = history.get("ionic_energies") or []

            if is_relax and len(ionic_energies) >= 2:
                ionic_html = _render_ionic_chart(history)
                scf_html = _render_scf_chart(
                    history, title="SCF accuracy (all ionic steps, concatenated)"
                )
                chart_block = (
                    f'<div>{ionic_html}</div>'
                    f'<div style="margin-top:20px;">'
                    f'<div class="advqm-muted" style="margin-bottom:6px;">'
                    f'Raw SCF accuracy across every ionic step below — each geometry '
                    f'step restarts its own SCF cycle, so this sawtooths by design; '
                    f'the geometry-optimization chart above is the one that answers '
                    f'"did this converge?" for a {calc_type} run.</div>{scf_html}</div>'
                )
                steps_label = "Ionic Steps"
                steps_value = len(ionic_energies)
            else:
                chart_block = _render_scf_chart(history)
                steps_label = "SCF Steps"
                steps_value = len(history.get("accuracy") or [])

            display(
                HTML(
                    f"""
                    <div class="advqm-card">
                      <div class="advqm-card-title">📄 {_fmt(Path(row.get('cif') or '?').name)}</div>
                      <div class="advqm-detail-grid">
                        <div><b>Run ID</b><br>{_fmt(row.get('run_id'))}</div>
                        <div><b>Calculation Type</b><br>{_fmt(calc_type)}</div>
                        <div><b>Status</b><br>{_effective_status_html(row)}</div>
                        <div><b>Final Energy</b><br>{_fmt_energy(energy)} Ry</div>
                        <div><b>{steps_label}</b><br>{steps_value}</div>
                        <div><b>Submitted By</b><br>{_fmt(row.get('submitted_by'))}</div>
                      </div>
                      <div style="margin-top:16px;">{chart_block}</div>
                    </div>
                    """
                )
            )

    on_load(preselect_row)

    return widgets.VBox([header, result_out])


# ── App factory ──────────────────────────────────────────────────────────────
def app(app_mode: bool = True, debug: bool = False):
    """
    Build the advQMcalc_test app shell.

    Returns:
        HubApp instance with .form ready to display
    """
    from template_widgets import HubApp

    my_app = HubApp(app_name="advQMcalc_test")

    nav = _Nav()

    pages = {
        "new_run": _build_new_run_page(nav),
        "runs": _build_runs_page(nav),
        "results": _build_results_page(nav),
    }

    nav_specs = [
        ("new_run", "New Run", "plus"),
        ("runs", "Runs", "list"),
        ("results", "Results", "bar-chart"),
    ]

    nav_buttons = {}
    for key, label, icon in nav_specs:
        b = _nav_button(label)
        b.icon = icon
        nav_buttons[key] = b

    main_area = widgets.VBox([])
    main_area.add_class("advqm-main")

    def show_page(key: str, row: dict | None = None) -> None:
        for k, b in nav_buttons.items():
            if k == key:
                b.add_class("advqm-navbtn-active")
            else:
                b.remove_class("advqm-navbtn-active")
        main_area.children = [pages[key]]
        if key == "runs":
            pages["runs"] = _build_runs_page(nav)
            main_area.children = [pages["runs"]]
        elif key == "results":
            pages["results"] = _build_results_page(nav, preselect_row=row)
            main_area.children = [pages["results"]]

    nav.show = show_page

    for key, b in nav_buttons.items():
        b.on_click(lambda _btn, k=key: show_page(k))

    sidebar = widgets.VBox(list(nav_buttons.values()))
    sidebar.add_class("advqm-sidebar")

    shell = widgets.HBox([sidebar, main_area])
    shell.add_class("advqm-shell")

    show_page("new_run")

    my_app.content.children = [shell]
    return my_app

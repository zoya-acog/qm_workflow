"""Runs monitoring table with monitor/resume support."""

import html
import threading
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv
from IPython.display import HTML, display

from helpers import (
    _process_single_cif,
    _query_slurm_job,
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
    cif_list_text,
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
    DEFAULT_RUNS_DIR,
)


_ROW_COL_WIDTHS = ["10%", "16%", "20%", "18%", "12%", "12%", "5%", "7%"]
_TABLE_MIN_WIDTH = "1100px"  # below this the table scrolls sideways


_ROW_COL_LABELS = ["Job ID", "Run ID", "CIF File", "Path", "Calculation Type", "Status", "", ""]


# ⓘ next to the Calculation Type header: the table shows QE's own keywords, the
# tooltip says what each one means.
_CALC_TYPE_INFO = (
    ' <span style="cursor:help; color:#1565c0; font-weight:700;" '
    'title="scf = Single Point Energy&#10;relax = Fixed Cell Optimization'
    '&#10;vc-relax = Variable Cell Optimization">&#9432;</span>'
)


def _run_row_widget(rows: list[dict], on_open, on_load_results) -> widgets.HBox:
    """One table row: a single run, or one multi-CIF batch (rows sharing an array job)."""
    first = rows[0]
    batch = len(rows) > 1
    group_row = {**first, "batch_rows": rows} if batch else first

    names = group_cif_names(rows)
    cif_text = cif_list_text(names, compact=True)
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
        icon="wrench", tooltip="Monitor & Resume this run" if not batch else "Monitor & Resume all runs in this batch",
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
    cells = [
        widgets.HTML(html.escape(_fmt(job_id)), layout=widgets.Layout(width="100%")),
        widgets.HTML(html.escape(_fmt(run_id_text)), layout=widgets.Layout(width="100%")),
        cif_html,
        widgets.HTML(
            f'<span title="{html.escape(cif_dir or "")}">{html.escape(cif_dir or "")}</span>',
            layout=widgets.Layout(width="100%"),
        ),
        widgets.HTML(html.escape(_fmt(first.get("calc_type"))), layout=widgets.Layout(width="100%")),
        status_html,
        open_btn,
        load_results_btn,
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
        '<div class="advqm-page-sub">All workflow runs and their current status</div></div>'
        "</div>"
    )
    new_run_btn = widgets.Button(description="New Calculation", button_style="success", icon="plus")
    new_run_btn.on_click(lambda _: nav.show("new_run"))

    runs_dir_w = ipv.TextField(
        label="Runs Directory", v_model=DEFAULT_RUNS_DIR, outlined=True, dense=True,
        layout=widgets.Layout(width="60%"),
    )
    search_w = ipv.TextField(
        label="Search Runs", v_model="", placeholder="Search by CIF name or job ID...",
        outlined=True, dense=True, layout=widgets.Layout(width="60%"),
    )
    refresh_btn = widgets.Button(description="Refresh", icon="refresh")

    # ── Filter by user / jump straight to a run's results ───────────────────
    user_filter_w = widgets.Dropdown(layout=widgets.Layout(width="calc(40% - 8px)"))

    col_head = widgets.HTML(table_head_html(
        _ROW_COL_LABELS, _ROW_COL_WIDTHS, _TABLE_MIN_WIDTH,
        info={"Calculation Type": _CALC_TYPE_INFO},
    ))
    rows_box = widgets.VBox([])
    rows_wrap = widgets.VBox([col_head, rows_box])
    rows_wrap.add_class("advqm-card")
    rows_wrap.add_class("advqm-rowlist")

    current_rows = []

    # ── Monitor / Resume — the run in focus is picked by clicking a row's
    # wrench icon (see on_open below), not a separate dropdown; this label
    # just reflects whatever's currently selected. ──────────────────────────
    selected_run = {"path": None, "paths": [], "label": None}
    selected_label_w = widgets.HTML('<span class="advqm-muted">No run selected — click the ⚙ icon on a run above.</span>')
    job_status = widgets.Label(value="—")
    query_btn = widgets.Button(description="Query SLURM", icon="search")
    resume_btn = widgets.Button(description="Resume", button_style="success", icon="play")
    resume_target_w = widgets.Dropdown(
        options=[
            ("Latest run for this CIF", "latest"),
            ("Selected run ID", "run_id"),
            ("Selected run folder path", "run_path"),
        ],
        value="run_id",
        description="Resume target",
        layout=widgets.Layout(width="100%"),
    )
    force_pp_resume_w = widgets.Checkbox(
        value=False, description="Force pseudopotential cleanup",
    )
    resume_note_w = widgets.HTML(
        '<span class="advqm-muted">Resume reuses the saved run settings. '
        "If a SLURM job was already submitted, resume will not submit a new job; "
        "forced cleanup rewrites the QE input only.</span>"
    )
    state_out = widgets.Output()

    def _select_run(rp: Path, label: str, paths: list | None = None) -> None:
        selected_run["path"] = rp
        selected_run["paths"] = list(paths) if paths else [rp]
        selected_run["label"] = label
        selected_label_w.value = f'<b>Selected run:</b> {label}'
        show_state()

    def show_state(_=None):
        state_out.clear_output()
        paths = selected_run["paths"] or ([selected_run["path"]] if selected_run["path"] else [])
        with state_out:
            for rp in paths:
                _show_one_state(rp, header=len(paths) > 1)

    def _show_one_state(rp, header=False):
        if True:
            st = load_run_state(rp)
            if st is None:
                print(f"No state.json found for {rp}")
                return
            crystal = st.get("tasks", {}).get("crystal", {})
            qe_output = crystal.get("qe_output") or {}
            qe_results = crystal.get("qe_results") or {}
            error_markers = qe_output.get("qe_error_markers") or []
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
                ("JOB DONE seen", _fmt(qe_output.get("qe_job_done"))),
                ("Error markers found", _fmt(", ".join(error_markers)) if error_markers else "None"),
            ]
            lines = "".join(
                f'<div style="padding:4px 0; border-bottom:1px solid #f1f3f7;">'
                f'<span style="color:#667085; font-weight:600;">{label}:</span> '
                f'<span style="color:#1f2937;">{value}</span></div>'
                for label, value in rows
            )
            title = (
                f'<div style="font-weight:700; margin:10px 0 4px;">{html.escape(_fmt(Path((load_run_state(rp) or {}).get("cif") or "?").name))}'
                f' · {html.escape(rp.name)}</div>'
                if header else ""
            )
            display(HTML(f'<div style="font-size:0.85rem;">{title}{lines}</div>'))


    def on_query(_=None):
        paths = selected_run["paths"] or ([selected_run["path"]] if selected_run["path"] else [])
        if not paths:
            job_status.value = "No run selected — click the ⚙ icon on a run above."
            return
        msgs = []
        for rp in paths:
            st = load_run_state(rp) or {}
            jid = st.get("tasks", {}).get("crystal", {}).get("job_id")
            if not jid:
                msgs.append(f"{rp.name}: no SLURM job_id recorded")
                continue
            try:
                msgs.append(f"Job {jid}: {_query_slurm_job(str(jid))}")
            except Exception as e:
                msgs.append(f"Job {jid}: query failed ({e})")
        job_status.value = " | ".join(msgs)

    def _resume_one(rp: Path, mode: str, force_pp: bool) -> None:
        label = rp.parent.name
        base = Path(runs_dir_w.v_model)
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
        paths = selected_run["paths"] or ([selected_run["path"]] if selected_run["path"] else [])
        if not paths:
            job_status.value = "No run selected — click the ⚙ icon on a run above."
            return
        mode = resume_target_w.value
        force_pp = bool(force_pp_resume_w.value)
        job_status.value = (
            f"Resuming {len(paths)} run(s) ({mode}, "
            f"{'force PP cleanup' if force_pp else 'keeping saved PP files'})..."
        )
        resume_btn.disabled = True

        def work():
            try:
                for rp in paths:
                    try:
                        _resume_one(rp, mode, force_pp)
                    except Exception as e:
                        _run_log(f"Resume failed for {rp.name}: {e}")
            finally:
                resume_btn.disabled = False

        threading.Thread(target=work, daemon=True).start()

    query_btn.on_click(on_query)
    resume_btn.on_click(on_resume)

    monitor_body = widgets.VBox(
        [selected_label_w, widgets.HBox([job_status, query_btn, resume_btn]),
         resume_target_w, force_pp_resume_w, resume_note_w, state_out]
    )
    monitor_acc = widgets.Accordion(children=[monitor_body])
    monitor_acc.set_title(0, "Monitor & Resume a run")
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
        base = Path(runs_dir_w.v_model)
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
        base = Path(runs_dir_w.v_model)
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
    search_w.observe(render_rows, names="v_model")
    user_filter_w.observe(render_rows, names="value")

    refresh()

    filter_card = widgets.VBox(
        [
            widgets.HTML('<div class="advqm-card-title">Filter by user / jump to results</div>'),
            widgets.HBox(
                [user_filter_w],
                layout=widgets.Layout(width="100%", justify_content="space-between"),
            ),
        ]
    )
    filter_card.add_class("advqm-card")

    return widgets.VBox(
        [
            header,
            widgets.HBox(
                [new_run_btn], layout=widgets.Layout(padding="0 0 12px 0")
            ),
            widgets.HBox([labeled_field(runs_dir_w, "flex:1; min-width:0;"), refresh_btn]),
            labeled_field(search_w),
            filter_card,
            rows_wrap,
        ]
    )



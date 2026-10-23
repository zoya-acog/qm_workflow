"""Dashboard overview page."""

import html
import threading
from pathlib import Path

import ipywidgets as widgets

from shared import (
    DEFAULT_RUNS_DIR,
    _Nav,
    batch_status_html,
    cif_list_text,
    group_cif_names,
    group_rows,
    _effective_status_html,
    _fmt,
    effective_status,
    list_rows_all_dirs,
    needs_slurm_check,
    refresh_run_statuses,
)


def _build_dashboard_page(nav: _Nav):
    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">Dashboard</div></div>'
        "</div>"
    )
    new_run_btn = widgets.Button(description="New Calculation", button_style="success", icon="plus")
    new_run_btn.on_click(lambda _: nav.show("new_run"))
    # HTML widgets, not Output: Refresh redraws them from a background thread.
    stats_out = widgets.HTML()
    recent_out = widgets.HTML()
    refresh_btn = widgets.Button(description="Refresh", icon="refresh")

    def render(_=None):
        base = Path(DEFAULT_RUNS_DIR)
        rows = list_rows_all_dirs()
        # Same status as the Runs page shows (setup failures count as failed).
        cats = [effective_status(r)[1] for r in rows]
        n_success = cats.count("success")
        n_progress = sum(c in ("info", "warning") for c in cats)
        n_failed = cats.count("danger")

        stats_out.value = f"""
                <div class="advqm-stats-row">
                  <div class="advqm-stat-card"><div class="advqm-stat-num success">{n_success}</div>
                    <div class="advqm-stat-label">Completed</div></div>
                  <div class="advqm-stat-card"><div class="advqm-stat-num info">{n_progress}</div>
                    <div class="advqm-stat-label">In Progress</div></div>
                  <div class="advqm-stat-card"><div class="advqm-stat-num danger">{n_failed}</div>
                    <div class="advqm-stat-label">Failed</div></div>
                </div>"""
        if not rows:
            recent_out.value = (
                '<div class="advqm-card"><div class="advqm-card-title">Recent Runs</div>'
                f'<div class="advqm-muted">No runs found yet in {base}.</div></div>'
            )
            return
        # One entry per multi-CIF batch (shared array job id), newest first.
        groups = group_rows(sorted(rows, key=lambda r: r.get("run_id") or "", reverse=True))[:5]
        entries = []
        for grp in groups:
            first = grp[0]
            if len(grp) > 1:
                title = html.escape(cif_list_text(group_cif_names(grp)))
                run = first.get("run_label") or f"{first.get('run_id')} (+{len(grp) - 1} more)"
                job = first.get("array_job_id")
                status = batch_status_html(grp)
            else:
                title = html.escape(_fmt(first.get("cif")))
                run = first.get("run_label") or first.get("run_id")
                job = first.get("job_id")
                status = _effective_status_html(first)
            entries.append(
                f'<div class="advqm-list-row"><div><b>{title}</b>'
                f'<div class="advqm-muted">job {html.escape(_fmt(job))} &middot; '
                f'{html.escape(_fmt(run))} &middot; {html.escape(first.get("submitted_by") or "unknown user")}'
                f'</div></div>{status}</div>'
            )
        items = "".join(entries)
        recent_out.value = (
            f'<div class="advqm-card"><div class="advqm-card-title">Recent Runs</div>{items}</div>'
        )

    def refresh(_=None):
        # Show the saved statuses at once, then ask SLURM about unfinished runs
        # in the background and redraw with the up-to-date statuses.
        render()
        to_check = [Path(r["run_path"]) for r in list_rows_all_dirs()
                    if r.get("run_path") and needs_slurm_check(Path(r["run_path"]))]
        if not to_check:
            return
        refresh_btn.disabled = True
        refresh_btn.description = "Refreshing…"

        def work():
            try:
                refresh_run_statuses(to_check)
                render()
            finally:
                refresh_btn.disabled = False
                refresh_btn.description = "Refresh"

        threading.Thread(target=work, daemon=True).start()

    refresh_btn.on_click(refresh)
    render()
    return widgets.VBox([header, widgets.HBox([new_run_btn, refresh_btn]), stats_out, recent_out])


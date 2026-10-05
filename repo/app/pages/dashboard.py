"""Dashboard overview page."""

from pathlib import Path

import ipywidgets as widgets
from IPython.display import HTML, display

from helpers import status_category
from shared import (
    DEFAULT_RUNS_DIR,
    _Nav,
    _effective_status_html,
    _fmt,
    _list_all_run_rows,
)


def _build_dashboard_page(nav: _Nav):
    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">Dashboard</div>'
        '<div class="advqm-page-sub">Overview of your Quantum ESPRESSO calculation workflow</div></div>'
        "</div>"
    )
    new_run_btn = widgets.Button(description="New Calculation", button_style="success", icon="plus")
    new_run_btn.on_click(lambda _: nav.show("new_run"))
    stats_out = widgets.Output()
    recent_out = widgets.Output()
    refresh_btn = widgets.Button(description="Refresh", icon="refresh")

    def render(_=None):
        base = Path(DEFAULT_RUNS_DIR)
        stats_out.clear_output()
        recent_out.clear_output()
        rows = _list_all_run_rows(base) if base.is_dir() else []
        n_success = sum(status_category(r.get("crystal_status")) == "success" for r in rows)
        n_progress = sum(status_category(r.get("crystal_status")) in ("info", "warning") for r in rows)
        n_failed = sum(status_category(r.get("crystal_status")) == "danger" for r in rows)

        with stats_out:
            display(HTML(f"""
                <div class="advqm-stats-row">
                  <div class="advqm-stat-card"><div class="advqm-stat-num success">{n_success}</div>
                    <div class="advqm-stat-label">Completed</div></div>
                  <div class="advqm-stat-card"><div class="advqm-stat-num info">{n_progress}</div>
                    <div class="advqm-stat-label">In Progress</div></div>
                  <div class="advqm-stat-card"><div class="advqm-stat-num danger">{n_failed}</div>
                    <div class="advqm-stat-label">Failed</div></div>
                </div>"""))

        with recent_out:
            if not rows:
                display(HTML(
                    '<div class="advqm-card"><div class="advqm-card-title">Recent Runs</div>'
                    f'<div class="advqm-muted">No runs found yet in {base}.</div></div>'
                ))
                return
            recent = sorted(rows, key=lambda r: r.get("run_id") or "", reverse=True)[:5]
            items = "".join(
                f'<div class="advqm-list-row"><div><b>{_fmt(r.get("cif"))}</b>'
                f'<div class="advqm-muted">{_fmt(r.get("run_id"))} &middot; '
                f'job {_fmt(r.get("job_id"))}</div></div>{_effective_status_html(r)}</div>'
                for r in recent
            )
            display(HTML(
                f'<div class="advqm-card"><div class="advqm-card-title">Recent Runs</div>{items}</div>'
            ))

    refresh_btn.on_click(render)
    render()
    return widgets.VBox([header, widgets.HBox([new_run_btn, refresh_btn]), stats_out, recent_out])


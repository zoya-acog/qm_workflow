"""
app_src.py
----------
advQMcalc_test — QM Crystal Workflow (facade).

Page builders live in app/pages/ (one module per page), shared run utilities in
app/shared.py, and dashboard documentation in app/docs.py. This module keeps the
`from app_src import app` entrypoint used by app.ipynb and wires the pages into
the sidebar shell.
"""

import sys
from pathlib import Path

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

import ipywidgets as widgets

from docs import (
    _DOC_DESCRIPTION_HTML,
    _DOC_HOWTO_HTML,
    _DOC_REFERENCES_HTML,
    _doc_accordion,
)
from pages.dashboard import _build_dashboard_page
from pages.new_run import _build_new_run_page
from pages.results import _build_results_page
from pages.runs import _build_runs_page
from shared import _Nav, _nav_button


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
        "dashboard": _build_dashboard_page(nav),
        "new_run": _build_new_run_page(nav),
        "runs": _build_runs_page(nav),
        "results": _build_results_page(nav),
    }

    nav_specs = [
        ("dashboard", "Dashboard", "th-large"),
        ("new_run", "New Calculation", "plus"),
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
        elif key == "dashboard":
            pages["dashboard"] = _build_dashboard_page(nav)
            main_area.children = [pages["dashboard"]]

    nav.show = show_page

    for key, b in nav_buttons.items():
        b.on_click(lambda _btn, k=key: show_page(k))

    sidebar = widgets.VBox(list(nav_buttons.values()))
    sidebar.add_class("advqm-sidebar")

    shell = widgets.HBox([sidebar, main_area])
    shell.add_class("advqm-shell")

    docs = widgets.VBox([
        _doc_accordion("📖 Application Description", _DOC_DESCRIPTION_HTML),
        _doc_accordion("🧭 How to Use", _DOC_HOWTO_HTML),
        _doc_accordion("📚 References and Contacts", _DOC_REFERENCES_HTML),
    ])
    docs.add_class("advqm-docs")

    show_page("dashboard")

    my_app.content.children = [docs, shell]
    return my_app

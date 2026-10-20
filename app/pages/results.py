"""Results comparison table over completed runs with CSV download."""

import base64
import csv
import html
import io
import json
import re
import threading
import time
import types
import uuid
import zipfile
import shlex
import warnings
from functools import lru_cache
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv
import traitlets
from IPython.display import HTML, display

from helpers import load_run_state
from shared import (
    DEFAULT_RUNS_DIR, cif_list_text, energy_plot_html, energy_vs_step, group_cif_names, group_rows,
    grid_columns, loading_html, trajectory_xsf, RY_TO_KJ_MOL, _Nav, _fmt, _list_all_run_rows, list_rows_all_dirs, labeled_field, ry_to_kj_mol,
)


def _cif_result_metadata(row: dict) -> dict[str, str | None]:
    """Read the space group and unit-cell labels needed by the results table."""
    run_path = Path(row.get("run_path") or ".")
    source_cif = Path(row.get("cif") or "")
    candidates = [source_cif, run_path / "inputs" / "crystal" / source_cif.name]
    cif_path = next((p for p in candidates if p.is_file()), None)
    if cif_path is None:
        return {"space_group": "–", "cell_parameters": "–", "formula": None, "cif_path": None}

    tags: dict[str, str] = {}
    try:
        for line in cif_path.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped.startswith("_"):
                continue
            parts = stripped.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                tokens = shlex.split(parts[1], comments=True)
            except ValueError:
                continue
            if tokens:
                tags[parts[0].lower()] = " ".join(tokens)
    except OSError:
        return {"space_group": "–", "cell_parameters": "–", "formula": None, "cif_path": None}

    space_group = next((
        tags[key] for key in (
            "_symmetry_space_group_name_h-m",
            "_space_group_name_h-m_alt",
            "_space_group_name_h-m_ref",
            "_space_group_name_h-m",
        ) if tags.get(key)
    ), "–")

    def number(tag: str) -> str | None:
        value = tags.get(tag)
        if not value:
            return None
        match = re.match(
            r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(?:\(\d+\))?$",
            value,
        )
        return f"{float(match.group(1)):g}" if match else None

    lengths = [number(f"_cell_length_{axis}") for axis in "abc"]
    angles = [number(f"_cell_angle_{axis}") for axis in ("alpha", "beta", "gamma")]
    cell = "a × b × c: " + " × ".join(v or "–" for v in lengths) + " Å"
    if any(angles):
        labels = ("α", "β", "γ")
        cell += "; " + ", ".join(
            f"{label}={value}°" if value else f"{label}=–"
            for label, value in zip(labels, angles)
        )
    formula = next((
        tags[key] for key in (
            "_chemical_formula_sum",
            "_chemical_formula_moiety",
            "_chemical_formula_structural",
        ) if tags.get(key)
    ), None)
    return {"space_group": space_group, "cell_parameters": cell, "formula": formula,
            "cif_path": str(cif_path)}


def _formula_signature(formula: str | None) -> tuple[tuple[tuple[str, float], ...], float] | None:
    if not formula:
        return None
    matches = re.findall(r"([A-Z][a-z]?)(\d*(?:\.\d+)?)", formula)
    if not matches:
        return None
    counts: dict[str, float] = {}
    for element, count in matches:
        counts[element] = counts.get(element, 0.0) + (float(count) if count else 1.0)
    total = sum(counts.values())
    if total <= 0:
        return None
    return tuple(sorted(counts.items())), total


@lru_cache(maxsize=512)
def _cif_molecule_cached(cif_path: str, mtime: float) -> tuple[int, str] | None:
    """(atoms per molecule, molecule formula) found by pymatgen, or None.

    Bonded atoms are grouped into molecules (JmolNN covalent radii). A close
    contact can make bond detection cut a molecule differently (e.g. C6H6O and
    C6H5O in one cell), so the molecule is the one whose formula, times a whole
    number, gives the cell's formula. Hydrates, salts and co-crystals have no
    such single molecule; their unit is the CIF's formula unit (the cell divided
    by _cell_formula_units_Z, e.g. acid + 2 H2O). None when pymatgen is missing,
    the CIF does not parse, or neither applies; callers then fall back to the
    CIF's _chemical_formula_sum.
    """
    try:
        from pymatgen.analysis.graphs import StructureGraph
        from pymatgen.analysis.local_env import JmolNN
        from pymatgen.core import Structure

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # pymatgen CIF parser warnings
            structure = Structure.from_file(cif_path)
            make = (getattr(StructureGraph, "from_local_env_strategy", None)
                    or StructureGraph.with_local_env_strategy)
            molecules = make(structure, JmolNN()).get_subgraphs_as_molecules()
    except Exception:
        return None
    cell = structure.composition
    for molecule in sorted(molecules, key=len, reverse=True):
        comp = molecule.composition
        z = cell.num_atoms / comp.num_atoms
        if z.is_integer() and all(abs(cell[el] - z * comp[el]) < 1e-6 for el in cell):
            return len(molecule), comp.formula
    z_tag = re.search(r"(?im)^\s*_cell_formula_units_Z\s+(\d+)", Path(cif_path).read_text(errors="ignore"))
    z = int(z_tag.group(1)) if z_tag else 0
    if z > 0 and all(abs(n / z - round(n / z)) < 1e-6 for n in cell.values()):
        unit = cell / z
        return round(unit.num_atoms), unit.formula
    return None


def _cif_molecule(cif_path: str | None, run_path: str | None = None) -> tuple[int, str] | None:
    """Cached _cif_molecule_cached: in memory, and on disk in the run's results/
    folder (pymatgen takes ~1 s per CIF, too slow to redo for every page view)."""
    if not cif_path:
        return None
    try:
        mtime = Path(cif_path).stat().st_mtime
    except OSError:
        return None
    cache = Path(run_path) / "results" / "cif_molecule.json" if run_path else None
    if cache is not None:
        try:
            saved = json.loads(cache.read_text())
            if saved.get("cif") == cif_path and saved.get("mtime") == mtime and saved.get("v") == 3:
                return tuple(saved["molecule"]) if saved.get("molecule") else None
        except (OSError, ValueError, KeyError, TypeError):
            pass
    molecule = _cif_molecule_cached(cif_path, mtime)
    if cache is not None:
        try:
            cache.write_text(json.dumps({"v": 3, "cif": cif_path, "mtime": mtime, "molecule": molecule}))
        except OSError:
            pass  # read-only run folder: still correct, just not cached on disk
    return molecule


def _qe_cell_atom_count(row: dict) -> int | None:
    run_path = Path(row.get("run_path") or ".")
    state = load_run_state(run_path) or {}
    crystal = state.get("tasks", {}).get("crystal", {})
    input_rel = crystal.get("qe_input")
    candidates = [run_path / input_rel] if input_rel else []
    candidates.extend(sorted((run_path / "inputs" / "crystal").glob("*.in")))
    for qe_input in candidates:
        try:
            match = re.search(r"(?im)^\s*nat\s*=\s*(\d+)", qe_input.read_text(errors="ignore"))
        except OSError:
            continue
        if match:
            return int(match.group(1))
    return None


_MAX_TRAJ_BYTES = 10 * 1024 * 1024

# Hover text for the ⓘ on the "Download Output Structure Files" button.
_STRUCTURE_FILES_INFO = (
    "• Final Relaxed Coordinates for Fixed Cell Optimization\n"
    "• All (Intermediate & Final) Coordinates for Variable Cell Optimization"
)
# Cell Parameters wraps onto two lines and headers may wrap, so the table fits
# an ordinary laptop screen; narrower windows still scroll sideways.
_TABLE_MIN_WIDTH = "1050px"


def _download_suffix(groups: list[list[dict]]) -> str:
    """Run ID(s) appended to every Results download filename ("<name>_<run id>").

    Per selected job: the Run ID given at submission, else the default one (the
    run folder name, the same ID the Results dropdown shows). Several jobs are
    joined with "_"; more than three become "<first>_and_<n>_more".
    """
    ids = []
    for grp in groups:
        first = grp[0]
        rid = re.sub(r"[^A-Za-z0-9._-]+", "-", str(first.get("run_label") or first.get("run_id") or "run"))
        if rid not in ids:
            ids.append(rid)
    if len(ids) <= 3:
        return "_".join(ids) or "run"
    return f"{ids[0]}_and_{len(ids) - 1}_more"


def _trajectory_link_html(rows: list[dict], suffix: str) -> str:
    """"Download Output Structure Files" link: one .axsf for one run, a .zip for several.
    The downloaded file's name ends in "_<suffix>" (see _download_suffix)."""
    files: list[tuple[str, bytes]] = []
    for row in rows:
        out_file = row.get("qe_output_file")
        data = trajectory_xsf(out_file) if out_file else None
        if data:
            stem = f"{Path(row.get('cif') or 'run').stem}_{row.get('run_id') or 'run'}"
            files.append((f"{stem}.axsf", data))
    if not files:
        return ""
    if len(files) == 1:
        cif_stem = Path(rows[0].get("cif") or "run").stem if len(rows) == 1 else "structure_file"
        name, payload, mime = f"{cif_stem}_{suffix}.axsf", files[0][1], "chemical/x-xsf"
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in files:
                zf.writestr(fname, data)
        name, payload, mime = f"structure_files_{suffix}.zip", buf.getvalue(), "application/zip"
    if len(payload) > _MAX_TRAJ_BYTES:
        return (
            '<span class="advqm-muted">Structure files are too large to download here '
            "(over 10 MB); select fewer runs.</span>"
        )
    b64 = base64.b64encode(payload).decode("ascii")
    # ⓘ inside the button: hover shows _STRUCTURE_FILES_INFO; clicking the icon
    # itself does not start the download.
    info = (
        f'<span class="advqm-btn-info" title="{html.escape(_STRUCTURE_FILES_INFO)}" '
        'onclick="event.preventDefault(); event.stopPropagation();">&#9432;</span>'
    )
    return (
        '<a class="advqm-csv-download" '
        f'href="data:{mime};base64,{b64}" download="{html.escape(name)}">'
        f"Download Output Structure Files{info}</a>"
    )


# QE output zips are written here and served by Voila at voila/files/downloads/...
# (Voila's root is this app folder; `make run` allows exactly these names via
# --VoilaConfiguration.file_whitelist). Files are temporary handoffs to the browser.
_DOWNLOAD_DIR = Path(__file__).resolve().parent.parent / "downloads"
_DOWNLOAD_MAX_AGE_S = 3600


def _qe_output_zip(rows: list[dict]) -> Path | None:
    """Zip the QE .out file of each run into _DOWNLOAD_DIR; None if there are none.

    Entries are named "<CIF>_<run_id>.<calc>.out", e.g. G1_2026-10-09_092818.relax.out.
    """
    _DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in _DOWNLOAD_DIR.glob("qe_output_files_*.zip"):
        try:
            if now - old.stat().st_mtime > _DOWNLOAD_MAX_AGE_S:
                old.unlink()
        except OSError:
            pass
    files = []
    for row in rows:
        out = Path(row.get("qe_output_file") or "")
        if not out.is_file():
            continue
        stem = Path(row.get("cif") or "run").stem
        run_id = row.get("run_id") or "run"
        name = out.name
        arc = (f"{stem}_{run_id}{name[len(stem):]}" if name.startswith(stem + ".")
               else f"{stem}_{run_id}_{name}")
        files.append((out, arc))
    if not files:
        return None
    dest = _DOWNLOAD_DIR / f"qe_output_files_{uuid.uuid4().hex}.zip"
    tmp = dest.with_suffix(".part")
    used: set[str] = set()
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for out, arc in files:
            base, k = arc, 2
            while arc in used:
                arc, k = f"{k}_{base}", k + 1
            used.add(arc)
            zf.write(out, arc)
    tmp.replace(dest)
    return dest


class _ServerDownloadLink(ipv.VuetifyTemplate):
    """A download link styled like the CSV one that builds its file on click.

    Python builds the file (prepare), then bumps `nonce`; the template's watcher
    makes the browser download `url` as an ordinary file, so nothing large passes
    through the widget connection or sits in the page.
    """

    label = traitlets.Unicode("").tag(sync=True)
    filename = traitlets.Unicode("download.zip").tag(sync=True)
    busy = traitlets.Bool(False).tag(sync=True)
    message = traitlets.Unicode("").tag(sync=True)
    url = traitlets.Unicode("").tag(sync=True)
    nonce = traitlets.Int(0).tag(sync=True)
    template = traitlets.Unicode("""
<template>
  <span style="display:inline-flex; align-items:center; gap:10px;">
    <a class="advqm-csv-download" href="#" @click.prevent="if (!busy) prepare()"
       :style="busy ? 'opacity:0.65; cursor:wait;' : ''">
      {{ busy ? 'Preparing…' : label }}
    </a>
    <span v-if="message" class="advqm-muted">{{ message }}</span>
  </span>
</template>
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

    def __init__(self, build, **kwargs):
        super().__init__(**kwargs)
        self._build = build  # () -> Path | None, the file to download

    def vue_prepare(self, _data=None) -> None:
        self.busy, self.message = True, ""

        def work() -> None:
            try:
                path = self._build()
                if path is None:
                    self.message = "No QE output files found for the selected runs."
                else:
                    self.url = f"voila/files/downloads/{path.name}"
                    self.nonce += 1
            except Exception as e:
                self.message = f"Could not prepare the download: {e}"
            finally:
                self.busy = False

        threading.Thread(target=work, daemon=True).start()


# ⓘ next to a Results header (hover text); keyed by the header label.
_HEAD_INFO = {
    "Relative Energy per Molecule (kJ/mol)": (
        ' <span style="cursor:help; color:#1565c0; font-weight:700;" title="Valid when calculations '
        'are performed with same inputs (e.g. XC functionals, k-point spacing etc.)">&#9432;</span>'
    ),
}


def _csv_link_html(columns: list[str], rows: list[list[str]], suffix: str) -> str:
    """"Download CSV" link holding the given rows, in the order given (on-screen order)."""
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    # The CSV is embedded in a data URI; very large selections may exceed
    # browser URL limits.
    payload = base64.b64encode(buf.getvalue().encode("utf-8")).decode("ascii")
    return (
        '<a class="advqm-csv-download" '
        f'href="data:text/csv;charset=utf-8;base64,{payload}" '
        f'download="multi_cif_results_{suffix}.csv">Download CSV</a>'
    )


def _natural_key(name: str) -> list:
    """Sort key that orders "2.cif" before "10.cif" (digit runs compared as numbers)."""
    return [(0, int(tok)) if tok.isdigit() else (1, tok) for tok in re.split(r"(\d+)", name.lower()) if tok]


class _PlotDialog:
    """One pop-up window per Results page showing a run's energy plot.

    A dialog always opens in view, unlike a panel under the row, which sat at the
    left edge of the sideways-scrolling table, off screen when the user had
    scrolled right to reach the Plot column.
    """

    def __init__(self):
        self._title = ipv.Html(tag="span", children=[""],
                               style_="font-weight:700; font-size:0.95rem; color:#12344d;")
        close_btn = ipv.Btn(icon=True, children=[ipv.Icon(children=["mdi-close"])])
        close_btn.on_event("click", lambda *_: self.close())
        self._body = widgets.HTML()
        card = ipv.Card(children=[
            ipv.CardTitle(
                children=[self._title, ipv.Spacer(), close_btn],
                style_="padding:12px 12px 4px 20px;",
            ),
            ipv.CardText(children=[self._body], style_="padding:4px 20px 20px;"),
        ])
        self.widget = ipv.Dialog(v_model=False, max_width="840px", children=[card])

    def show(self, title: str, plot_html: str) -> None:
        self._title.children = [title]
        self._body.value = plot_html
        self.widget.v_model = True

    def close(self) -> None:
        self.widget.v_model = False


def _result_row_widget(row: dict, values: list[str], widths: list[str], on_plot) -> widgets.HBox:
    """One results row with a Plot icon; clicking it calls on_plot(title, plot_html)."""
    # "; " only occurs in Cell Parameters: put the angles on their own line so
    # that column can stay narrow.
    cells = [
        widgets.HTML(
            html.escape(str(v)).replace("; ", "<br>").replace(" Å", "&nbsp;Å"),
            layout=widgets.Layout(width="100%"),
        )
        for v, w in zip(values, widths)
    ]
    out_file = row.get("qe_output_file")
    energies = energy_vs_step(out_file) if out_file else []
    plot_btn = widgets.Button(
        icon="line-chart",
        disabled=not energies,
        tooltip=(
            "Plot total energy vs optimisation step"
            if energies else "Plot is available for relax / vc-relax runs"
        ),
        layout=widgets.Layout(width="100%"),
    )
    row_box = widgets.HBox(cells + [plot_btn], layout=widgets.Layout(
        width="100%", min_width=_TABLE_MIN_WIDTH,
        display="grid", grid_template_columns=grid_columns(widths),
    ))
    row_box.add_class("advqm-rowlist-row")
    row_box.add_class("advqm-results-row")

    def open_plot(_b=None) -> None:
        label = f"{Path(row.get('cif') or '?').name} · {row.get('run_id')}"
        on_plot(f"Total Energy vs Optimisation Step — {label}", energy_plot_html(energies))

    plot_btn.on_click(open_plot)
    return row_box


def _build_results_page(nav: _Nav, preselect_row: dict | None = None):
    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">Results</div></div>'
        "</div>"
    )

    try:
        completed_rows = list_rows_all_dirs(only_successful=True)
    except Exception:
        completed_rows = []
    completed_rows.sort(key=lambda r: (r.get("run_id") or "", r.get("cif") or ""), reverse=True)

    # One option per multi-CIF batch (shared array job id): the label shows a
    # single job id and "{a.cif, b.cif, ...}"; choosing it selects every run in it.
    groups_by_label: dict[str, list[dict]] = {}
    for grp in group_rows(completed_rows):
        first = grp[0]
        if len(grp) > 1:
            label = (
                f"{cif_list_text(group_cif_names(grp))} · "
                f"{first.get('run_label') or first.get('run_id')} · job {first.get('array_job_id')}"
            )
        else:
            cif_name = Path(first.get("cif") or "?").name
            label = f"{cif_name} · {first.get('run_id')} · job {first.get('job_id') or '–'}"
        label += f" · {first.get('submitted_by') or 'unknown user'}"
        if label in groups_by_label:
            label += f" · {Path(first.get('run_path') or '.').parent.name}"
        groups_by_label[label] = grp

    # What the job field's search matches: the label plus every CIF name of a
    # batch (the label abbreviates them), the user name, job IDs and Run ID.
    search_text = {
        label: " ".join([label] + [
            f"{Path(r.get('cif') or '').name} {r.get('submitted_by') or ''} {r.get('job_id') or ''} "
            f"{r.get('array_job_id') or ''} {r.get('run_label') or ''} {r.get('run_id') or ''}"
            for r in grp
        ]).lower()
        for label, grp in groups_by_label.items()
    }

    preselect_paths = set()
    if preselect_row:
        batch_rows = preselect_row.get("batch_rows") or [preselect_row]
        preselect_paths = {r.get("run_path") for r in batch_rows}
    selected_labels = [
        label for label, grp in groups_by_label.items()
        if any(r.get("run_path") in preselect_paths for r in grp)
    ]
    selection = {"labels": list(selected_labels)}
    select_holder = widgets.VBox([])

    def _make_select(v_model):
        # Searchable: typing filters the jobs by user name, job ID, Run ID or any
        # CIF file name (matched here in Python, so batches match on every CIF).
        sel = ipv.Autocomplete(
            label="Select Job to Display and Download Results",
            placeholder="Search by user name, job ID or CIF file",
            items=list(groups_by_label),
            v_model=v_model,
            no_filter=True,
            multiple=True,
            chips=True,
            clearable=True,
            outlined=True,
            dense=True,
            hide_details=True,  # no empty hint/error area under the field
            disabled=not completed_rows,
        )
        sel.observe(_on_select, names="v_model")

        # The typed text reaches Python as the Vue event "update:search-input"
        # (ipyvuetify does not sync search_input back as a trait).
        def _on_search(_widget, _event, text) -> None:
            words = (text or "").lower().split()
            chosen = list(sel.v_model or [])
            matches = [lb for lb in groups_by_label if all(w in search_text[lb] for w in words)]
            sel.items = chosen + [lb for lb in matches if lb not in chosen]  # keep chosen chips

        sel.on_event("update:search-input", _on_search)
        return sel

    def _on_select(change) -> None:
        selection["labels"] = list(change["new"] or [])
        # A multi-select keeps its menu open after each pick. Swapping in a
        # fresh widget (same selection) closes it, so no extra click is needed.
        # Done before rendering so the loading spinner is not hidden by the menu.
        select_holder.children = [labeled_field(_make_select(selection["labels"]))]
        render_comparison()

    downloads_html = widgets.HTML()
    csv_download = types.SimpleNamespace(value="")
    traj_download = types.SimpleNamespace(value="")
    shown_rows: list[dict] = []
    qe_out_download = _ServerDownloadLink(
        build=lambda: _qe_output_zip(list(shown_rows)),
        label="Download Quantum ESPRESSO Output Files",
        filename="qe_output_files.zip",
    )
    qe_out_holder = widgets.HBox([])
    downloads_row = widgets.HBox([downloads_html, qe_out_holder])
    downloads_row.add_class("advqm-downloads-row")

    def _sync_downloads() -> None:
        # Both links in one inline row so they sit right next to each other.
        downloads_html.value = (
            '<div style="display:flex; gap:16px; align-items:center;">'
            f"{csv_download.value}{traj_download.value}</div>"
        )
    comparison_note = widgets.HTML()
    comparison_table = widgets.VBox([])
    plot_dialog = _PlotDialog()

    # ── Pagination: only the current page's rows are built and shown; the CSV and
    # download buttons always cover every selected run. ──────────────────────
    # base = rows in the default order (relative energy); items = rows as shown,
    # after the CIF Name sort (None -> "asc" -> "desc" -> None on each click).
    table_state = {"base": [], "items": [], "head": None, "widths": None,
                   "columns": [], "suffix": "", "sort": None}
    sort_btn = widgets.Button(tooltip="Sort by CIF name", layout=widgets.Layout(width="100%"))
    sort_btn.add_class("advqm-sort-btn")

    def _results_head(columns: list[str], widths: list[str]) -> widgets.HBox:
        """Header row on the same grid as the rows; the CIF Name cell is the sort button."""
        cells = [sort_btn] + [
            widgets.HTML(f"<span>{html.escape(c)}{_HEAD_INFO.get(c, '')}</span>", layout=widgets.Layout(width="100%"))
            for c in columns[1:] + ["Plot"]
        ]
        head = widgets.HBox(cells, layout=widgets.Layout(
            width="100%", min_width=_TABLE_MIN_WIDTH,
            display="grid", grid_template_columns=grid_columns(widths),
        ))
        head.add_class("advqm-results-headrow")
        head.add_class("advqm-sticky-head")  # header stays visible while the rows scroll
        return head

    def _apply_sort() -> None:
        """Order the shown rows by the current sort; the CSV follows that order."""
        mode = table_state["sort"]
        items = list(table_state["base"])
        if mode:
            items.sort(key=lambda it: _natural_key(it[1][0]), reverse=(mode == "desc"))
        table_state["items"] = items
        # The arrow is drawn by CSS (smaller than the name, kept on its line):
        # advqm-sort-none / -asc / -desc on the button.
        sort_btn.description = "CIF Name"
        for state in ("none", "asc", "desc"):
            sort_btn.remove_class(f"advqm-sort-{state}")
        sort_btn.add_class(f"advqm-sort-{mode or 'none'}")
        csv_download.value = _csv_link_html(
            table_state["columns"], [values for _, values in items], table_state["suffix"],
        )
        _sync_downloads()

    def _on_sort(_b=None) -> None:
        table_state["sort"] = {None: "asc", "asc": "desc", "desc": None}[table_state["sort"]]
        _apply_sort()
        if pager_w.v_model != 1:
            pager_w.v_model = 1  # back to page 1 (observer redraws)
        else:
            show_table_page()

    sort_btn.on_click(_on_sort)
    page_size_w = ipv.Select(
        items=[10, 20, 50, 100], v_model=20, outlined=True, dense=True, hide_details=True,
        style_="width:96px; min-width:96px;",
    )
    pager_w = ipv.Pagination(length=1, v_model=1, total_visible=7, circle=False)
    range_w = widgets.HTML()
    pager_row = widgets.HBox([
        widgets.HBox([widgets.HTML('<span class="advqm-muted">Rows per page</span>'), page_size_w],
                     layout=widgets.Layout(align_items="center", grid_gap="8px")),
        range_w,
        pager_w,
    ])
    pager_row.add_class("advqm-pager")
    pager_row.layout.display = "none"

    def show_table_page(*_) -> None:
        items, size = table_state["items"], int(page_size_w.v_model or 20)
        pages = max(1, -(-len(items) // size))
        if pager_w.length != pages:
            pager_w.length = pages
        page = min(max(int(pager_w.v_model or 1), 1), pages)
        start = (page - 1) * size
        chunk = items[start:start + size]
        range_w.value = (
            f'<span class="advqm-muted">Showing {start + 1}–{start + len(chunk)} of {len(items)}</span>'
        )
        comparison_table.children = [table_state["head"]] + [
            _result_row_widget(row, values, table_state["widths"], plot_dialog.show)
            for row, values in chunk
        ]
        pager_row.layout.display = None if items else "none"

    def _on_page_size(*_) -> None:
        if pager_w.v_model != 1:
            pager_w.v_model = 1  # triggers show_table_page via its own observer
        else:
            show_table_page()

    pager_w.observe(show_table_page, names="v_model")
    page_size_w.observe(_on_page_size, names="v_model")

    def render_comparison(_=None) -> None:
        selected = selection["labels"]
        rows = [r for label in selected if label in groups_by_label for r in groups_by_label[label]]
        shown_rows[:] = rows
        suffix = _download_suffix([groups_by_label[label] for label in selected if label in groups_by_label])
        qe_out_download.filename = f"qe_output_files_{suffix}.zip"
        qe_out_download.message = ""
        if not rows:
            qe_out_holder.children = []
            downloads_row.layout.display = "none"  # nothing to download
            pager_row.layout.display = "none"
            csv_download.value = ""
            traj_download.value = ""
            _sync_downloads()
            comparison_note.value = ""
            comparison_table.children = [widgets.HTML(
                '<div class="advqm-muted" style="padding:12px 0 0;">'
                + ("Select one or more completed runs to view the comparison table."
                   if completed_rows else "No completed runs are available yet.")
                + "</div>"
            )]
            return

        # Building the table (CIF metadata, structure files) is slow for big
        # batches: show a spinner and hide the download buttons until the table
        # and all three buttons can appear together.
        downloads_row.layout.display = "none"
        pager_row.layout.display = "none"
        comparison_note.value = ""
        comparison_table.children = [widgets.HTML(loading_html(f"Loading results for {len(rows)} run(s)…"))]

        # Energies are compared per formula unit; formulas that cannot be
        # parsed show no ΔE.
        row_data = []
        for row in rows:
            metadata = _cif_result_metadata(row)
            # Molecule or formula unit from pymatgen (see _cif_molecule_cached); else the CIF formula tag.
            molecule = _cif_molecule(metadata.get("cif_path"), row.get("run_path"))
            formula = _formula_signature(molecule[1] if molecule else metadata.get("formula"))
            nat = _qe_cell_atom_count(row)
            # Z of the QE cell (cif2cell may write a smaller cell than the CIF's).
            formula_units = nat / formula[1] if nat and formula else None
            energy = row.get("energy_ry")
            per_formula_energy = (
                float(energy) / formula_units
                if isinstance(energy, (int, float)) and formula_units else None
            )
            row_data.append({
                "formula_units": formula_units,  # molecules in the QE cell
                "row": row,
                "metadata": metadata,
                "formula": formula[0] if formula else None,
                "per_formula_energy": per_formula_energy,  # Ry per formula unit
                # Convert Ry -> kJ/mol first; the relative energy is then taken in kJ/mol.
                "per_formula_kj": (
                    ry_to_kj_mol(per_formula_energy) if per_formula_energy is not None else None
                ),
            })
        formulas = {item["formula"] for item in row_data}
        can_compare = (
            len(formulas) == 1
            and None not in formulas
            and all(item["per_formula_energy"] is not None for item in row_data)
        )
        if can_compare:
            # Default (and only) order: relative energy, lowest to highest.
            row_data.sort(key=lambda item: item["per_formula_energy"])
        min_kj = min(item["per_formula_kj"] for item in row_data) if can_compare else None
        columns = [
            "CIF Name", "Space Group", "Cell Parameters", "k-points",
            "Energy (kJ/mol)", "No. of Molecule in the Unit Cell (Z)",
            "Relative Energy per Molecule (kJ/mol)", "SLURM State", "SLURM Job ID",
        ]
        widths = ["9%", "9%", "22%", "8%", "11%", "9%", "11%", "8%", "8%", "5%"]
        table_items = []
        for item in row_data:
            row = item["row"]
            metadata = item["metadata"]
            energy = row.get("energy_ry")
            relative = (
                item["per_formula_kj"] - min_kj
                if can_compare
                else None
            )
            values = [
                Path(row.get("cif") or "?").name,
                metadata["space_group"],
                metadata["cell_parameters"],
                row.get("kpoints") or "–",
                # Energy per formula unit (same basis as Relative Energy, so the two
                # columns subtract consistently); falls back to the whole-cell energy
                # only when the formula units cannot be determined.
                # Total energy of the whole cell, as printed by QE ("! total energy").
                f"{ry_to_kj_mol(float(energy)):.4f}" if isinstance(energy, (int, float)) else "–",
                # Molecules (formula units) in the cell = QE atom count / atoms per formula.
                (f"{item['formula_units']:g}" if float(item['formula_units']).is_integer()
                 else f"{item['formula_units']:.2f}") if item["formula_units"] else "–",
                f"{relative:.2f}" if relative is not None else "–",
                row.get("slurm_state") or "–",
                row.get("array_job_id") or row.get("job_id") or "–",
            ]
            table_items.append((row, values))

        traj_download.value = _trajectory_link_html(rows, suffix)
        comparison_note.value = (
            ""
            if can_compare else
            '<div class="advqm-muted" style="margin:8px 0;">'
            "Relative energy needs selected runs with the same parseable CIF formula and QE atom counts."
            "</div>"
        )
        # New selection: default order (relative energy), CSV regenerated to match.
        table_state.update(base=table_items, columns=columns, suffix=suffix, sort=None,
                           head=_results_head(columns, widths), widths=widths)
        _apply_sort()
        if pager_w.v_model != 1:
            pager_w.v_model = 1  # new selection: back to page 1 (observer redraws)
        else:
            show_table_page()
        qe_out_holder.children = [qe_out_download]
        downloads_row.layout.display = None

    select_holder.children = [labeled_field(_make_select(selection["labels"]))]
    render_comparison()
    comparison_card = widgets.VBox([
        select_holder,
        downloads_row,
        comparison_note,
        comparison_table,
        pager_row,
        plot_dialog.widget,
    ])
    comparison_card.add_class("advqm-card")
    comparison_table.add_class("advqm-rowlist")
    comparison_table.add_class("advqm-rowlist-scroll")  # own scroll box: scrollbars always on screen

    return widgets.VBox([header, comparison_card])




# © 2017-26. Aganitha Cognitive Solutions Private Limited. Proprietary and Confidential.

"""Results comparison table over completed runs with CSV download."""

import base64
import csv
import html
import io
import re
import types
import zipfile
import shlex
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv
from IPython.display import HTML, display

from helpers import load_run_state
from shared import (
    DEFAULT_RUNS_DIR, cif_list_text, energy_plot_html, energy_vs_step, group_cif_names, group_rows,
    grid_columns, table_head_html, trajectory_xsf, RY_TO_KJ_MOL, _Nav, _fmt, _list_all_run_rows, labeled_field, ry_to_kj_mol,
)


def _cif_result_metadata(row: dict) -> dict[str, str | None]:
    """Read the space group and unit-cell labels needed by the results table."""
    run_path = Path(row.get("run_path") or ".")
    source_cif = Path(row.get("cif") or "")
    candidates = [source_cif, run_path / "inputs" / "crystal" / source_cif.name]
    cif_path = next((p for p in candidates if p.is_file()), None)
    if cif_path is None:
        return {"space_group": "–", "cell_parameters": "–", "formula": None}

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
        return {"space_group": "–", "cell_parameters": "–", "formula": None}

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
    cell = "a, b, c: " + " × ".join(v or "–" for v in lengths) + " Å"
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
    return {"space_group": space_group, "cell_parameters": cell, "formula": formula}


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
_TABLE_MIN_WIDTH = "1900px"  # wide enough for every header; narrower windows scroll sideways


def _trajectory_link_html(rows: list[dict]) -> str:
    """"Download Trajectory" link: one .axsf for one run, a .zip for several."""
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
        name, payload, mime = files[0][0], files[0][1], "chemical/x-xsf"
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in files:
                zf.writestr(fname, data)
        name, payload, mime = "trajectories.zip", buf.getvalue(), "application/zip"
    if len(payload) > _MAX_TRAJ_BYTES:
        return (
            '<span class="advqm-muted">Trajectory is too large to download here '
            "(over 10 MB); select fewer runs.</span>"
        )
    b64 = base64.b64encode(payload).decode("ascii")
    return (
        '<a class="advqm-csv-download" '
        f'href="data:{mime};base64,{b64}" download="{html.escape(name)}">Download Structure Files</a>'
    )


def _result_row_widget(row: dict, values: list[str], widths: list[str]) -> widgets.VBox:
    """One results row with a Plot icon; clicking it opens/closes an accordion
    holding the Total Energy vs Optimisation Step graph under the row."""
    cells = [
        widgets.HTML(html.escape(str(v)), layout=widgets.Layout(width="100%"))
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
    slot = widgets.VBox([])
    row_box = widgets.HBox(cells + [plot_btn], layout=widgets.Layout(
        width="100%", min_width=_TABLE_MIN_WIDTH,
        display="grid", grid_template_columns=grid_columns(widths),
    ))
    row_box.add_class("advqm-rowlist-row")

    def toggle(_b=None) -> None:
        if slot.children:
            slot.children = []
            return
        label = f"{Path(row.get('cif') or '?').name} · {row.get('run_id')}"
        acc = widgets.Accordion(children=[widgets.HTML(energy_plot_html(energies, label))])
        acc.set_title(0, "Total Energy vs Optimisation Step")
        acc.selected_index = 0
        slot.children = [acc]

    plot_btn.on_click(toggle)
    return widgets.VBox([row_box, slot], layout=widgets.Layout(overflow="visible"))


def _build_results_page(nav: _Nav, preselect_row: dict | None = None):
    header = widgets.HTML(
        '<div class="advqm-page-header">'
        '<div><div class="advqm-page-title">Results</div>'
        '<div class="advqm-page-sub">Compare completed runs and download a CSV. '
        'Use Runs to monitor jobs.</div></div>'
        "</div>"
    )

    try:
        completed_rows = _list_all_run_rows(Path(DEFAULT_RUNS_DIR), only_successful=True)
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
        if label in groups_by_label:
            label += f" · {Path(first.get('run_path') or '.').parent.name}"
        groups_by_label[label] = grp

    preselect_paths = set()
    if preselect_row:
        batch_rows = preselect_row.get("batch_rows") or [preselect_row]
        preselect_paths = {r.get("run_path") for r in batch_rows}
    selected_labels = [
        label for label, grp in groups_by_label.items()
        if any(r.get("run_path") in preselect_paths for r in grp)
    ]
    completed_select = ipv.Select(
        label="Completed Runs to Compare",
        items=list(groups_by_label),
        v_model=selected_labels,
        multiple=True,
        chips=True,
        clearable=True,
        outlined=True,
        dense=True,
        disabled=not completed_rows,
    )
    downloads_html = widgets.HTML()
    csv_download = types.SimpleNamespace(value="")
    traj_download = types.SimpleNamespace(value="")

    def _sync_downloads() -> None:
        # Both links in one inline row so they sit right next to each other.
        downloads_html.value = (
            '<div style="display:flex; gap:16px; align-items:center;">'
            f"{csv_download.value}{traj_download.value}</div>"
        )
    comparison_note = widgets.HTML()
    comparison_table = widgets.VBox([])

    def render_comparison(_=None) -> None:
        selected = completed_select.v_model or []
        rows = [r for label in selected if label in groups_by_label for r in groups_by_label[label]]
        if not rows:
            csv_download.value = ""
            traj_download.value = ""
            _sync_downloads()
            comparison_note.value = ""
            comparison_table.children = [widgets.HTML(
                '<div class="advqm-muted" style="padding:12px 0;">'
                + ("Select one or more completed runs to view the comparison table."
                   if completed_rows else "No completed runs are available yet.")
                + "</div>"
            )]
            return

        # ponytail: normalize each QE cell to formula units before comparison;
        # ceiling: complex/disordered formulas may not parse, so show no ΔE.
        row_data = []
        for row in rows:
            metadata = _cif_result_metadata(row)
            formula = _formula_signature(metadata.get("formula"))
            nat = _qe_cell_atom_count(row)
            formula_units = nat / formula[1] if nat and formula else None
            energy = row.get("energy_ry")
            per_formula_energy = (
                float(energy) / formula_units
                if isinstance(energy, (int, float)) and formula_units else None
            )
            row_data.append({
                "row": row,
                "metadata": metadata,
                "formula": formula[0] if formula else None,
                "per_formula_energy": per_formula_energy,
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
        min_energy = min(item["per_formula_energy"] for item in row_data) if can_compare else None
        columns = [
            "CIF Name", "Space Group", "Cell Parameters", "k-points",
            "Energy (kJ/mol)", "Relative Energy (kJ/mol)", "SLURM State", "SLURM Job ID",
        ]
        widths = ["14%", "10%", "18%", "9%", "12%", "12%", "8%", "8%", "9%"]
        csv_buffer = io.StringIO(newline="")
        writer = csv.writer(csv_buffer)
        writer.writerow(columns)
        table_rows = []
        for item in row_data:
            row = item["row"]
            metadata = item["metadata"]
            energy = row.get("energy_ry")
            relative = (
                (item["per_formula_energy"] - min_energy) * RY_TO_KJ_MOL
                if can_compare
                else None
            )
            values = [
                Path(row.get("cif") or "?").name,
                metadata["space_group"],
                metadata["cell_parameters"],
                row.get("kpoints") or "–",
                f"{ry_to_kj_mol(float(energy)):.4f}" if isinstance(energy, (int, float)) else "–",
                f"{relative:.4f}" if relative is not None else "–",
                row.get("slurm_state") or "–",
                row.get("array_job_id") or row.get("job_id") or "–",
            ]
            writer.writerow(values)
            table_rows.append(_result_row_widget(row, values, widths))

        # ponytail: embed CSV bytes in a data URI; ceiling: very large
        # selections may exceed browser URL limits. Upgrade to a download route.
        csv_payload = base64.b64encode(csv_buffer.getvalue().encode("utf-8")).decode("ascii")
        csv_download.value = (
            '<a class="advqm-csv-download" '
            f'href="data:text/csv;charset=utf-8;base64,{csv_payload}" '
            'download="multi_cif_results.csv">Download CSV</a>'
        )
        traj_download.value = _trajectory_link_html(rows)
        _sync_downloads()
        comparison_note.value = (
            ""
            if can_compare else
            '<div class="advqm-muted" style="margin:8px 0;">'
            "Relative energy needs selected runs with the same parseable CIF formula and QE atom counts."
            "</div>"
        )
        head = widgets.HTML(table_head_html(columns + ["Plot"], widths, _TABLE_MIN_WIDTH))
        comparison_table.children = [head] + table_rows

    completed_select.observe(render_comparison, names="v_model")
    render_comparison()
    comparison_card = widgets.VBox([
        widgets.HTML('<div class="advqm-card-title">Multi-run results</div>'),
        labeled_field(completed_select),
        widgets.HTML(
            '<div class="advqm-muted" style="margin:8px 0;">'
            "Select completed runs with matching compositions. Relative energy is per mole of formula units."
            "</div>"
        ),
        downloads_html,
        comparison_note,
        comparison_table,
    ])
    comparison_card.add_class("advqm-card")
    comparison_table.add_class("advqm-rowlist")

    return widgets.VBox([header, comparison_card])



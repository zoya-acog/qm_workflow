"""Results comparison table over completed runs with CSV download."""

import base64
import csv
import html
import io
import re
import shlex
from pathlib import Path

import ipywidgets as widgets
import ipyvuetify as ipv
from IPython.display import HTML, display

from helpers import load_run_state
from shared import DEFAULT_RUNS_DIR, RY_TO_KJ_MOL, _Nav, _fmt, _list_all_run_rows, ry_to_kj_mol


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

    rows_by_label: dict[str, dict] = {}
    for row in completed_rows:
        cif_name = Path(row.get("cif") or "?").name
        label = f"{cif_name} · {row.get('run_id')} · job {row.get('job_id') or '–'}"
        if label in rows_by_label:
            label += f" · {Path(row.get('run_path') or '.').parent.name}"
        rows_by_label[label] = row

    selected_labels = [
        label for label, row in rows_by_label.items()
        if preselect_row and row.get("run_path") == preselect_row.get("run_path")
    ]
    completed_select = ipv.Select(
        label="Completed runs to compare",
        items=list(rows_by_label),
        v_model=selected_labels,
        multiple=True,
        chips=True,
        clearable=True,
        outlined=True,
        dense=True,
        disabled=not completed_rows,
    )
    csv_download = widgets.HTML()
    comparison_table = widgets.HTML()

    def render_comparison(_=None) -> None:
        selected = completed_select.v_model or []
        rows = [rows_by_label[label] for label in selected if label in rows_by_label]
        if not rows:
            csv_download.value = ""
            comparison_table.value = (
                '<div class="advqm-muted" style="padding:12px 0;">'
                + ("Select one or more completed runs to view the comparison table."
                   if completed_rows else "No completed runs are available yet.")
                + "</div>"
            )
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
            "CIF name", "Space group", "Cell parameters", "K-points",
            "Energy (kJ/mol)", "Relative energy (kJ/mol)", "SLURM state", "SLURM job id",
        ]
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
                row.get("job_id") or "–",
            ]
            writer.writerow(values)
            table_rows.append("<tr>" + "".join(
                f"<td>{html.escape(str(value))}</td>" for value in values
            ) + "</tr>")

        # ponytail: embed CSV bytes in a data URI; ceiling: very large
        # selections may exceed browser URL limits. Upgrade to a download route.
        csv_payload = base64.b64encode(csv_buffer.getvalue().encode("utf-8")).decode("ascii")
        csv_download.value = (
            '<a class="advqm-csv-download" '
            f'href="data:text/csv;charset=utf-8;base64,{csv_payload}" '
            'download="multi_cif_results.csv">Download CSV</a>'
        )
        comparison_table.value = (
            '<div class="advqm-muted" style="margin:8px 0;">'
            + (
                "Relative energy is per mole of formula units, referenced to the lowest selected run."
                if can_compare else
                "Relative energy needs selected runs with the same parseable CIF formula and QE atom counts."
            )
            + "</div>"
            '<div class="advqm-table-wrap"><table class="advqm-table advqm-results-table">'
            "<thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in columns)
            + "</tr></thead><tbody>" + "".join(table_rows) + "</tbody></table></div>"
        )

    completed_select.observe(render_comparison, names="v_model")
    render_comparison()
    comparison_card = widgets.VBox([
        widgets.HTML('<div class="advqm-card-title">Multi-run results</div>'),
        completed_select,
        widgets.HTML(
            '<div class="advqm-muted" style="margin:8px 0;">'
            "Select completed runs with matching compositions. Relative energy is per mole of formula units."
            "</div>"
        ),
        csv_download,
        comparison_table,
    ])
    comparison_card.add_class("advqm-card")

    return widgets.VBox([header, comparison_card])



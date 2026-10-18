"""QE input details the workflow adds on top of cif2cell's output.

The CLI that calls these also runs cif2cell and sbatch, so the tests enter at
the two backend functions that turn a CIF and settings into input values.
"""

import re

from advQMcalc_test.cli import _compute_kpoints_from_cif, _ensure_qe_required_sections
from conftest import DATA

CIF2CELL_LIKE = """&CONTROL
  prefix = 'benzene'
/
&SYSTEM
  ibrav = 0
  nat = 48
/
"""


def _nsteps(text: str) -> list[str]:
    return re.findall(r"(?m)^\s*nstep\s*=\s*(\S+)", text)


def test_optimizations_get_100_ionic_steps_and_scf_gets_none():
    for calc in ("relax", "vc-relax"):
        assert _nsteps(_ensure_qe_required_sections(CIF2CELL_LIKE, "/p", calc, "1 1 1 0 0 0", {})) == ["100"]
    assert _nsteps(_ensure_qe_required_sections(CIF2CELL_LIKE, "/p", "scf", "1 1 1 0 0 0", {})) == []


def test_existing_nstep_is_replaced_not_duplicated():
    text = CIF2CELL_LIKE.replace("  prefix", "  nstep = 20\n  prefix")
    assert _nsteps(_ensure_qe_required_sections(text, "/p", "relax", "1 1 1 0 0 0", {})) == ["100"]


def test_kpoint_grid_from_cell_lengths_and_spacing():
    # Benzene a, b, c = 6.914, 7.476, 9.563 A. n = round(1 / (length * spacing)):
    # spacing 0.03 -> 4.82, 4.46, 3.49 -> 5 4 3 (the grid our benzene runs used);
    # spacing 0.05 -> 2.89, 2.68, 2.09 -> 3 3 2.
    cif = DATA / "z" / "benzene_z4.cif"
    assert _compute_kpoints_from_cif(cif, 0.03) == "5 4 3 0 0 0"
    assert _compute_kpoints_from_cif(cif, 0.05) == "3 3 2 0 0 0"

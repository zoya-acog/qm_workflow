"""Z (molecules, or formula units, per cell) shown on the Results page.

The page computes Z inside a widget callback, so the tests enter at the
function it calls for each run's CIF. Each CIF is a real case that once gave
the wrong Z.
"""

import json

import pytest

pytest.importorskip("pymatgen", reason="Results Z needs pymatgen (make install)")
pytest.importorskip("ipyvuetify", reason="the Results page imports the widget stack (make install)")

from conftest import DATA  # noqa: E402
from pages.results import _cif_molecule  # noqa: E402

Z_DATA = DATA / "z"


@pytest.mark.parametrize("cif, atoms_per_unit, formula, z", [
    # Plain molecular crystal with a Z tag.
    ("benzene_z4.cif", 12, "H6 C6", 4),
    # Genarris: no Z tag, formula tag is the whole cell (gave Z = 1).
    ("genarris_001.cif", 13, "H6 C6 O1", 4),
    # Close contact splits one molecule two ways (C6H6O and C6H5O).
    ("genarris_016.cif", 13, "H6 C6 O1", 4),
    # Hydrate and salt: no single molecule; unit is the CIF's formula unit.
    ("cyanuric_acid_dihydrate.cif", 18, "H7 C3 N3 O5", 4),
    ("ammonium_oxalate_monohydrate.cif", 19, "H10 C2 N2 O5", 2),
])
def test_z_for_real_cifs(tmp_path, cif, atoms_per_unit, formula, z):
    path = str(Z_DATA / cif)
    unit = _cif_molecule(path)
    assert unit == (atoms_per_unit, formula)
    from pymatgen.core import Structure  # cell size, independent of the code under test
    assert len(Structure.from_file(path)) / unit[0] == z


def test_result_is_cached_in_the_run_folder(tmp_path):
    run = tmp_path / "run"
    (run / "results").mkdir(parents=True)
    path = str(Z_DATA / "genarris_016.cif")
    first = _cif_molecule(path, str(run))
    saved = json.loads((run / "results" / "cif_molecule.json").read_text())
    assert saved["cif"] == path and tuple(saved["molecule"]) == first


def test_unreadable_cif_gives_no_answer(tmp_path):
    bad = tmp_path / "bad.cif"
    bad.write_text("not a cif\n")
    assert _cif_molecule(str(bad)) is None
    assert _cif_molecule(str(tmp_path / "missing.cif")) is None

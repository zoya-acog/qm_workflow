"""Tests for what the app produces for a run, without QE or SLURM.

- Job scripts: written by RunContext.write_crystal_slurm_script, the call the
  CLI makes for every run, for the site described by a config file.
- QE input: the details the workflow adds to cif2cell's output.
- The web app's modules import, so a broken install fails here, not in the browser.

The CLI around these also runs cif2cell and sbatch, so the tests start one
step inside it.
"""

import importlib
import re
import subprocess
from pathlib import Path

import pytest

from advQMcalc_test import run_context
from advQMcalc_test.cli import _compute_kpoints_from_cif, _ensure_qe_required_sections
from advQMcalc_test.run_context import RunContext
from advQMcalc_test.site_config import load_site_config

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "config.example.yaml"  # pre-filled for DeltaHPC
SAMPLE_CIF = REPO / "app" / "samples" / "benzene.cif"  # COD 4501704, the Try Sample structure


# ─── Job scripts and site configuration ──────────────────────────────────────

EXPECTED = Path(__file__).resolve().parent / "expected"


def _script(tmp_path, monkeypatch, site_file=None, **kwargs) -> str:
    monkeypatch.delenv("ADVQM_SITE_CONFIG", raising=False)  # default site unless a file is given
    monkeypatch.setattr(run_context, "SITE", load_site_config(site_file))
    ctx = RunContext(tmp_path, tmp_path / "run")
    ctx.run_dir.mkdir()
    ctx.create_layout()
    (ctx.run_dir / "inputs/crystal/X.scf.in").write_text("&CONTROL\n/\n")
    path = ctx.write_crystal_slurm_script("inputs/crystal/X.scf.in", "X.scf", **kwargs)
    return path.read_text().replace(str(ctx.run_dir), "RUNDIR")


@pytest.mark.parametrize("name, kwargs", [
    ("reference_cpu", {}),
    ("reference_gpu", {"gpu": True}),
])
def test_scripts_match_reference_output(tmp_path, monkeypatch, name, kwargs):
    # Recorded with config.example.yaml (DeltaHPC); review any difference before updating.
    text = _script(tmp_path, monkeypatch, site_file=EXAMPLE, **kwargs)
    assert text == (EXPECTED / f"{name}.slurm").read_text()


def test_client_original_script_is_kept_line_for_line(tmp_path, monkeypatch):
    # client_0.0.16.slurm was written by the client's own advQMcalc_test 0.0.16 for
    # the same run. Every one of its lines must appear, in order, in the script this
    # version writes for their cluster (quoting of values aside).
    def lines(text):
        return [line.replace('"', "") for line in text.splitlines() if line.strip()]

    ours = iter(lines(_script(tmp_path, monkeypatch, site_file=EXAMPLE)))
    missing = [line for line in lines((EXPECTED / "client_0.0.16.slurm").read_text())
               if not any(line == candidate for candidate in ours)]
    assert missing == []


def test_gpu_job_runs_one_mpi_rank_per_gpu(tmp_path, monkeypatch):
    text = _script(tmp_path, monkeypatch, site_file=EXAMPLE, gpu=True, gpu_type="a100", gpus=2)
    assert "#SBATCH --gres=gpu:a100:2" in text
    assert "#SBATCH --ntasks=2" in text
    assert "srun pw.x -nk 2" in text


def test_module_site_loads_modules_and_uses_srun_in_scratch(tmp_path, monkeypatch):
    text = _script(tmp_path, monkeypatch, site_file=EXAMPLE, ntasks=16)
    assert "module load medsci\nmodule load quantum-espresso/7.1" in text
    assert 'srun pw.x -i "$infile.in"' in text
    assert 'workdir="/hpfs/userws/$USER/workdir/$SLURM_JOBID"' in text
    assert "trap 'cp \"$workdir/$infile.out\"" in text  # .out comes back even if QE fails
    assert "docker" not in text
    assert "--partition" not in text  # empty in the example: line left out
    assert "#SBATCH --ntasks=16" in text


def test_unknown_launcher_is_refused(tmp_path):
    site = tmp_path / "config.yaml"
    site.write_text("launcher: podman\n")
    with pytest.raises(ValueError, match="launcher"):
        load_site_config(site)


@pytest.mark.parametrize("site_file, kwargs", [
    (None, {}), (None, {"gpu": True}), (EXAMPLE, {}), (EXAMPLE, {"gpu": True}),
])
def test_generated_scripts_are_valid_bash(tmp_path, monkeypatch, site_file, kwargs):
    script = tmp_path / "job.sh"
    script.write_text(_script(tmp_path, monkeypatch, site_file=site_file, **kwargs))
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_example_config_loads_as_a_module_site():
    site = load_site_config(EXAMPLE)
    assert site["launcher"] == "srun"
    assert site["modules"] == ["medsci", "quantum-espresso/7.1"]
    assert site["app"]["contact"] == [{"name": "Your Name", "email": "you@example.com"}]


def test_json_config_still_accepted(tmp_path):
    site = tmp_path / "site.json"
    site.write_text('{"modules": ["qe/7.1"]}')
    assert load_site_config(site)["modules"] == ["qe/7.1"]


# ─── QE input ────────────────────────────────────────────────────────────────

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
    # Benzene (COD 4501704) a, b, c = 5.522, 5.4396, 7.6726 A. n = round(1 / (length * spacing)):
    # spacing 0.03 -> 6.04, 6.13, 4.34 -> 6 6 4;  spacing 0.05 -> 3.62, 3.68, 2.61 -> 4 4 3.
    assert _compute_kpoints_from_cif(SAMPLE_CIF, 0.03) == "6 6 4 0 0 0"
    assert _compute_kpoints_from_cif(SAMPLE_CIF, 0.05) == "4 4 3 0 0 0"


# ─── Web app ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("module", ["app_src", "pages.new_run", "pages.runs", "pages.results", "pages.dashboard"])
def test_app_modules_import(module):
    importlib.import_module(module)

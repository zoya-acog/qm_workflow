"""SLURM job scripts generated for a run, per site config.

Entered through RunContext.write_crystal_slurm_script, the call the CLI makes
for every run. The CLI around it also needs cif2cell and sbatch, so the tests
start one step inside it. The site config is swapped the way a site does it:
by loading its JSON file.
"""

import subprocess
from pathlib import Path

import pytest

from advQMcalc_test import run_context
from advQMcalc_test.run_context import RunContext
from advQMcalc_test.site_config import load_site_config
from conftest import REPO

GOLDEN = Path(__file__).resolve().parent / "golden"
DELTAHPC = REPO / "advQMcalc_test" / "site_configs" / "deltahpc.example.json"


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
    ("own_cpu", {}),
    ("own_gpu", {"gpu": True}),
])
def test_our_cluster_scripts_match_recorded_output(tmp_path, monkeypatch, name, kwargs):
    # Recorded from the scripts that ran on our cluster; review any diff before updating.
    assert _script(tmp_path, monkeypatch, **kwargs) == (GOLDEN / f"{name}.slurm").read_text()


def test_gpu_job_runs_one_mpi_rank_per_gpu(tmp_path, monkeypatch):
    text = _script(tmp_path, monkeypatch, gpu=True, gpu_type="nvidia_geforce_rtx_4090", gpus=2)
    assert "#SBATCH --gres=gpu:nvidia_geforce_rtx_4090:2" in text
    assert "#SBATCH --ntasks=2" in text
    assert "mpirun -np 2 pw.x -nk 2" in text


def test_module_site_loads_modules_and_uses_srun_in_scratch(tmp_path, monkeypatch):
    text = _script(tmp_path, monkeypatch, site_file=DELTAHPC, ntasks=16)
    assert "module load medsci\nmodule load quantum-espresso/7.1" in text
    assert 'srun pw.x -i "$infile.in"' in text
    assert 'workdir="/hpfs/userws/$USER/workdir/$SLURM_JOBID"' in text
    assert "trap 'cp \"$workdir/$infile.out\"" in text  # .out comes back even if QE fails
    assert "docker" not in text
    assert "--partition" not in text  # empty in the example: line left out
    assert "#SBATCH --ntasks=16" in text


def test_docker_site_rejects_srun(tmp_path, monkeypatch, tmp_path_factory):
    site = tmp_path_factory.mktemp("site") / "site.json"
    site.write_text('{"launcher": "srun"}')
    with pytest.raises(ValueError, match="needs launcher 'mpirun'"):
        _script(tmp_path, monkeypatch, site_file=site)


def test_unknown_launch_mode_is_refused(tmp_path):
    site = tmp_path / "site.json"
    site.write_text('{"launch_mode": "podman"}')
    with pytest.raises(ValueError, match="launch_mode"):
        load_site_config(site)


@pytest.mark.parametrize("site_file, kwargs", [
    (None, {}), (None, {"gpu": True}), (DELTAHPC, {}), (DELTAHPC, {"gpu": True}),
])
def test_generated_scripts_are_valid_bash(tmp_path, monkeypatch, site_file, kwargs):
    script = tmp_path / "job.sh"
    script.write_text(_script(tmp_path, monkeypatch, site_file=site_file, **kwargs))
    result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

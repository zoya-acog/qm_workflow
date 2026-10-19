"""SLURM job scripts generated for a run, per site config.

Entered through RunContext.write_crystal_slurm_script, the call the CLI makes
for every run. The CLI around it also needs cif2cell and sbatch, so the tests
start one step inside it. The site config is swapped the way a site does it:
by loading its config file.
"""

import subprocess
from pathlib import Path

import pytest

from advQMcalc_test import run_context
from advQMcalc_test.run_context import RunContext
from advQMcalc_test.site_config import load_site_config
from conftest import REPO

EXPECTED = Path(__file__).resolve().parent / "expected"
DELTAHPC = REPO / "advQMcalc_test" / "site_configs" / "deltahpc.example.yaml"
EXAMPLE = REPO / "config.example.yaml"


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
    # Recorded with the DeltaHPC example config; review any difference before updating.
    text = _script(tmp_path, monkeypatch, site_file=DELTAHPC, **kwargs)
    assert text == (EXPECTED / f"{name}.slurm").read_text()


def test_client_original_script_is_kept_line_for_line(tmp_path, monkeypatch):
    # client_0.0.16.slurm was written by the client's own advQMcalc_test 0.0.16 for
    # the same run. Every one of its lines must appear, in order, in the script this
    # version writes for their cluster (quoting of values aside).
    def lines(text):
        return [line.replace('"', "") for line in text.splitlines() if line.strip()]

    ours = iter(lines(_script(tmp_path, monkeypatch, site_file=DELTAHPC)))
    missing = [line for line in lines((EXPECTED / "client_0.0.16.slurm").read_text())
               if not any(line == candidate for candidate in ours)]
    assert missing == []


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
    site = tmp_path / "config.yaml"
    site.write_text("launch_mode: podman\n")
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


def test_example_config_loads_as_a_module_site():
    site = load_site_config(EXAMPLE)
    assert (site["launch_mode"], site["launcher"]) == ("module", "srun")
    assert site["modules"] == ["quantum-espresso/7.1"]
    assert site["app"]["contact"] == [{"name": "Your Name", "email": "you@example.com"}]


def test_json_config_still_accepted(tmp_path):
    site = tmp_path / "site.json"
    site.write_text('{"launch_mode": "module", "modules": ["qe/7.1"]}')
    assert load_site_config(site)["modules"] == ["qe/7.1"]

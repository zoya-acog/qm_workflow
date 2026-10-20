"""Cluster-specific settings for the generated SLURM job scripts.

The built-in DEFAULT_SITE is neutral (QE from a module, no partitions or
paths). A site describes itself in a config file (config.yaml, or JSON) named
by the ADVQM_SITE_CONFIG environment variable; its keys override the defaults
(nested "cpu"/"gpu" sections are merged key by key). See config.example.yaml.

Keys
  launch_mode    "docker": run QE from a Docker image (development cluster).
                 "module": run a QE installed on the cluster, loaded with
                 `module load` (e.g. DeltaHPC's quantum-espresso/7.1).
  launcher       "mpirun": `mpirun -np <ranks> pw.x` (inside the image for docker).
                 "srun":   `srun pw.x`; SLURM starts the MPI ranks itself.
  modules        Modules loaded before QE runs (module mode), e.g.
                 ["medsci", "quantum-espresso/7.1"].
  qe_bin         QE executable name or absolute path.
  data_root      Directory mounted into the Docker container (docker mode).
  workdir        Where QE runs. "$indir" is the run's input folder; any shell
                 variables ($USER, $SLURM_JOBID) are expanded by the job.
                 When it is outside $indir the .out file is copied back even
                 if QE fails.
  local_scratch  QE's outdir (wavefunctions, restart files); node-local is best.
  account, qos   Optional #SBATCH --account / --qos (empty: not written).
  cpu, gpu       Per-kind settings:
                   partition, nodelist, exclude  (empty: line not written)
                   cpus_per_task  OpenMP threads per MPI rank
                   image, image_tar              (docker mode only)
                   extra_sbatch   list of extra "#SBATCH ..." lines
  app            Web app defaults: upload_dir, runs_dir, pseudo_dir, and
                 gpu_types (the New Run "GPU Type" choices; each value is the
                 SLURM GRES name, e.g. "a100").
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

ENV_VAR = "ADVQM_SITE_CONFIG"

# Neutral defaults: QE from a module launched with srun, nothing site-specific.
# Every real value (modules, partitions, paths) comes from the site's config.yaml.
DEFAULT_SITE: dict = {
    "name": "default",
    "launch_mode": "module",
    "launcher": "srun",
    "modules": [],
    "qe_bin": "pw.x",
    "data_root": "",
    "workdir": "$indir/work_$SLURM_JOBID",
    "local_scratch": "/tmp/qe_scratch_$SLURM_JOBID",
    "account": "",
    "qos": "",
    "cpu": {
        "partition": "",
        "nodelist": "",
        "exclude": "",
        "cpus_per_task": 1,
        "image": "",
        "image_tar": "",
        "extra_sbatch": [],
    },
    "gpu": {
        "partition": "",
        "nodelist": "",
        "exclude": "",
        "cpus_per_task": 4,
        "image": "",
        "image_tar": "",
        "extra_sbatch": [],
    },
    "app": {
        "upload_dir": "uploads",
        "runs_dir": "runs",
        "pseudo_dir": "",
        "gpu_types": [],
    },
}

_VALID = {"launch_mode": ("docker", "module"), "launcher": ("mpirun", "srun")}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_config_file(path: Path) -> dict:
    """Settings from a .yaml/.yml file (PyYAML, imported only when needed) or .json file."""
    text = path.read_text()
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(text) or {}
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"site config {path}: expected key: value settings at the top level")
    return data


def load_site_config(path: str | os.PathLike | None = None) -> dict:
    """DEFAULT_SITE overridden by the config file at `path` (or $ADVQM_SITE_CONFIG)."""
    path = path or os.environ.get(ENV_VAR)
    site = copy.deepcopy(DEFAULT_SITE)
    if path:
        site = _merge(site, _read_config_file(Path(path)))
    for key, allowed in _VALID.items():
        if site[key] not in allowed:
            raise ValueError(f"site config {key}={site[key]!r}; expected one of {allowed}")
    return site


SITE = load_site_config()

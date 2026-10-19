# Backend changes since the version you shared

`advQMcalc_test/` is your backend. This page lists every change we made to it
for the web app, and why, so you can review them without reading a diff.

| | Version | `cli.py` | `run_context.py` |
|---|---|---|---|
| Your copy | `__init__.py` 0.0.16, `pyproject.toml` 0.0.17 | 1,373 lines | fixed SLURM template for your cluster |
| This copy | **0.0.18** in both | 1,638 lines | job script built from a site config |

Your `cli.py` has Windows (CRLF) line endings and ours Unix (LF) ones, so a
plain `diff` marks every line as changed. Compare with
`diff --strip-trailing-cr` to see the real changes (about 280 lines): 5
functions edited, 5 added, 3 constants, 1 new file. No function or option was
removed; every command-line option you had still works the same way.

## 1. Your job script, now built from settings

Your `run_context.py` wrote a fixed 22-line SLURM script for your cluster.
That script is now produced from a **site config**
(`site_config.py` defaults, overridden by the JSON file named in
`ADVQM_SITE_CONFIG`). With `advQMcalc_test/site_configs/deltahpc.example.json`:

```json
"launch_mode": "module", "launcher": "srun",
"modules": ["medsci", "quantum-espresso/7.1"],
"workdir": "/hpfs/userws/$USER/workdir/$SLURM_JOBID"
```

the generated script contains **all 22 of your lines, in the same order**
(`--nodes`, `--ntasks`, `--mem-per-cpu`, `--time`, both `module load` lines,
the `/hpfs` scratch, `srun pw.x`, copying the output back), plus:

| Added | Why |
|---|---|
| `#SBATCH --cpus-per-task` and `OMP_NUM_THREADS` | Optional OpenMP threads per MPI rank (default 1: no change) |
| `trap` that copies `<name>.out` back on exit | With `set -e`, your script skipped the copy when `pw.x` failed; now the output, and the error in it, always comes back |
| `outdir` = `$workdir/tmp`, `disk_io = 'none'` | QE's temporary files in their own folder, removed at the end; avoids "File cannot be deleted" errors we saw on network storage |
| `&IONS ion_dynamics='bfgs'` for relax/vc-relax, `&CELL cell_dynamics='bfgs'` for vc-relax | QE stops vc-relax with "bad line in namelist &cell" when `&CELL` is missing, so vc-relax did not run before |

Partition, account, QOS, node list and extra `#SBATCH` lines are optional
settings; empty values are left out, as in your script. Check: after a first
run, compare `<runs_dir>/<run>/jobs/crystal/*.slurm` with your own script.

New file: `site_config.py` (the settings and their defaults). The built-in
defaults describe **our** cluster, so always start with your own config file
(see `docs/installation.md`).

## 2. New command-line options

| Option | What it does |
|---|---|
| `--array` | With several CIFs, submit them as **one** SLURM array job instead of one job each |
| `--array-max-concurrent N` | At most N array tasks run at once (`--array=1-M%N`); 0 = no limit |
| `--gpu`, `--gpus N`, `--gpu-type T` | Run on N GPUs (`--gres=gpu[:T]:N`), one MPI rank per GPU, `pw.x -nk N` |
| `--slurm-mem-per-cpu` | Memory per CPU (was fixed at 4G; still the default) |

New functions behind them: `_submit_array_batch`, `_validate_gpu_options`.
`write_crystal_slurm_script` gained `gpu`, `gpu_type`, `gpus` arguments
(defaults keep CPU behaviour).

## 3. QE input

- **`nstep = 100`** in `&CONTROL` for relax and vc-relax (constant
  `QE_DEFAULT_NSTEP` in `_ensure_qe_required_sections`). QE's default of 50
  steps often ends an optimization before it converges. scf inputs are
  unchanged.
- **Cell lengths with uncertainties** (`_extract_cell_lengths_from_cif`):
  values like `5.1047(3)`, common in CIFs, made `float()` fail and stopped the
  automatic k-point grid. The bracketed uncertainty is now ignored.

## 4. Job status and resubmitting

- **`sacct` unavailable** (`_job_state_with_fallback`): your code read the
  state only from `sacct` and marked the run failed when that call failed.
  Now it falls back to `squeue`. A job that has left the queue with no
  accounting record shows **ENDED** (treated like COMPLETED: results are
  collected if the output is there). A failed status check no longer marks a
  submitted run as failed; it is retried on the next refresh.
- **Resubmit** (`_reset_failed_job`, states in `_RESUBMITTABLE_SLURM_STATES`):
  a FAILED, CANCELLED or TIMEOUT job can be resubmitted with the same settings.

## 5. Parts that only run on our cluster

Our cluster allows QE only inside Docker containers. That code is in the
backend but **does nothing with `launch_mode: "module"`**, which is your setup:

- running `pw.x` inside a Docker image and loading the image on each node;
- the image-file check in `_validate_gpu_options`;
- `_drop_legacy_security_label` (with `_LEGACY_SECURITY_LABEL`): removes a
  Docker label from old job scripts before resubmitting. It changes nothing in
  scripts without that label.

## Not changed

`config.py`, `logging_utils.py`, CIF staging, cif2cell conversion,
pseudopotential matching, result collection and the CSV/JSON summaries are as
in your copy. The backend still has no third-party dependencies.

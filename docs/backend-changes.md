# Backend changes

This release of `advQMcalc_test` (version **0.0.18**) builds on the version
you provided (0.0.16) and adds four capabilities needed by the web
application. Existing command-line usage is unchanged: every option in 0.0.16
behaves as before.

| Change | Benefit |
|---|---|
| [SLURM job arrays](#1-slurm-job-arrays) | A folder of CIFs is submitted as a single job |
| [Geometry optimization namelists](#2-geometry-optimization-namelists-ions-and-cell) | Variable-cell optimization (vc-relax) now runs |
| [GPU execution](#3-gpu-execution) | Calculations can run on NVIDIA GPUs |
| [Site configuration](#4-site-configuration) | One code base runs on any SLURM cluster without code changes |

## 1. SLURM job arrays

When several CIFs are processed together, each structure previously became a
separate SLURM job. With `--array`, all structures are prepared first and then
submitted as **one array job**: one job ID, one entry in the queue, and one
task per structure.

| Option | Description |
|---|---|
| `--array` | Submit all structures as a single SLURM array job |
| `--array-max-concurrent N` | Run at most *N* structures at the same time (`--array=1-M%N`); `0` means no limit |

Each structure keeps its own run directory, state and results, so outputs are
organized exactly as for individual submissions.

## 2. Geometry optimization namelists (`&IONS` and `&CELL`)

Quantum ESPRESSO requires an `&IONS` namelist for `relax` and both `&IONS`
and `&CELL` for `vc-relax`. Without `&CELL`, a variable-cell optimization
stops immediately with *"bad line in namelist &cell"*.

The job script now adds these namelists before Quantum ESPRESSO starts:

| Calculation | Added |
|---|---|
| `relax` | `&IONS ion_dynamics = 'bfgs'` |
| `vc-relax` | `&IONS ion_dynamics = 'bfgs'` and `&CELL cell_dynamics = 'bfgs'` |
| `scf` | nothing |

Existing `&IONS` sections are left untouched. The BFGS algorithm matches
Quantum ESPRESSO's own default; it is now stated explicitly so the input
documents the method used.

## 3. GPU execution

Calculations can run on NVIDIA GPUs with a GPU-enabled build of
Quantum ESPRESSO.

| Option | Description |
|---|---|
| `--gpu` | Run on GPU instead of CPU |
| `--gpus N` | Number of GPUs per job (default 1) |
| `--gpu-type T` | Request a specific GPU type, e.g. `a100` (optional) |

Each GPU is driven by one MPI rank, the configuration recommended for
Quantum ESPRESSO on GPUs. The job requests `--gres=gpu[:T]:N` and runs
`pw.x -nk N`, which distributes k-points across the GPUs. CPU runs are
unaffected when `--gpu` is not given.

## 4. Site configuration

In 0.0.16, the SLURM job script was written for one cluster: module names,
scratch path and launcher were fixed in `run_context.py`. These settings now
come from a **site configuration** (`site_config.py`), which each site
overrides with a YAML file, `config.yaml` (or any file named in the
`ADVQM_SITE_CONFIG` environment variable).

| Setting | Example |
|---|---|
| How QE is provided | `modules: [medsci, quantum-espresso/7.1]` |
| MPI launcher | `launcher: srun` |
| Working directory | `workdir: /hpfs/userws/$USER/workdir/$SLURM_JOBID` |
| SLURM resources | partition, account, QOS, node list, threads per rank (empty values are omitted) |
| Application paths | runs, uploads and pseudopotential directories |

`config.example.yaml` is pre-filled with these values and reproduces the
0.0.16 job script: the generated script contains every line of the original,
in the same order. The built-in defaults are neutral (no partitions or paths),
so every real value comes from your `config.yaml` (see
[installation.md](installation.md)).

## Reviewing the changes

The 0.0.16 sources use Windows (CRLF) line endings and this release uses Unix
(LF) line endings, so a plain `diff` reports every line as changed. To see
only the actual changes:

```bash
diff -r --strip-trailing-cr <path-to-0.0.16>/advQMcalc_test advQMcalc_test
```

The backend has no third-party dependencies, except PyYAML for reading
`config.yaml`.

# Installing and running on your own cluster

The setup path for a site that runs the app directly on its own machines,
without administrator rights. Each step ends with a check you can run; most ways this
goes wrong are quiet (a run that never starts, a Results page with no energy),
so run the check before moving on.

**What you are installing.** One Python environment holds the web app and the
backend that builds Quantum ESPRESSO (QE) inputs and SLURM job scripts. QE
itself is **not** installed here: jobs use your cluster's QE (e.g.
`module load quantum-espresso/7.1`).

| Step | Runs where |
|---|---|
| Web app, CIF → QE input (cif2cell), job script, `sbatch` | the machine serving the app |
| `pw.x` (the calculation) | a compute node, through SLURM |

The Python environment is needed **only on the machine serving the app**. Job
scripts run `pw.x` from your QE module and use no Python, so compute nodes
need nothing from this installation.

**No SLURM token is needed.** The app submits and monitors jobs with `sbatch`,
`squeue` and `sacct`, as the `advQMcalc_test` command line does, so it runs on
a SLURM login node (or any host where those commands work).

---

## 1. Prerequisites

| | What | Notes |
|---|---|---|
| OS | Linux x86-64 | |
| Python | 3.10 or newer, with `venv` | no root needed |
| Scheduler | SLURM; `sbatch` and `squeue` work **on the machine serving the app** | the app submits with `sbatch` |
| QE | `pw.x` 7.1 on the compute nodes (module or install) | GPU runs need a GPU-enabled build |
| Storage | a folder visible at the **same path** from the app machine and the compute nodes | runs and pseudopotentials live here |

### Check

```bash
python3 --version          # 3.10+
sbatch --version && squeue -u $USER | head -1
srun -N1 -n1 bash -lc 'module load quantum-espresso/7.1 && which pw.x'
```

## 2. Install the software

From a copy of this repository on shared storage, in its top folder:

```bash
make install               # .venv with the app, backend, pymatgen, cif2cell, tests
```

`make install` also registers the widget front-ends (ipyvuetify,
ipywidgets) and the Jupyter kernel `python3` in the environment. Without the
widgets every page fails with "Script error for jupyter-vue"; without the
kernel the app does not start.

> **The UI package versions are coupled.** ipyvuetify 1.8 needs the
> notebook 6 / ipywidgets 7 stack pinned in `requirements.txt`. Newer
> notebook or ipywidgets versions install cleanly and then show an empty page,
> so do not upgrade them separately.

### Check

```bash
.venv/bin/jupyter nbextension list 2>&1 | grep -E "jupyter-vuetify|jupyter-js-widgets"   # both 'enabled'
.venv/bin/jupyter kernelspec list | grep python3
.venv/bin/python -c "import pymatgen, advQMcalc_test; print('ok')"
which cif2cell || ls .venv/bin/cif2cell
make test                  # no QE or SLURM needed
```

## 3. Choose the storage paths

| Setting | Holds | Must be visible from compute nodes |
|---|---|---|
| `app.runs_dir` | one folder per run: inputs, job script, QE output, results | **yes** (the job writes here, the app reads it back) |
| `app.pseudo_dir` | `.UPF` pseudopotential files | **yes** (QE reads them) |
| `app.upload_dir` | CIFs uploaded through the browser | no |
| `workdir` | where QE runs during a job (scratch) | compute node only |

### Pseudopotentials

Pseudopotentials are not distributed with the application. Place your `.UPF`
files in a single directory on shared storage, accessible at the same path from
both the application host and the compute nodes, and set this path as
`pseudo_dir` in `config.yaml`. The verification run in step 6 requires
pseudopotentials for carbon (C) and hydrogen (H).

### Check

```bash
ls /shared/qe_workflow/pseudos/*.UPF | head
srun -N1 -n1 ls /shared/qe_workflow/runs     # same path from a compute node
```

## 4. Write the configuration

All site settings live in one file, `config.yaml`:

```bash
cp config.example.yaml config.yaml           # gitignored
```

`config.example.yaml` explains every setting next to its value. Fill in
everything under **Required**:

- `modules`: what a job loads to get `pw.x` (e.g. `quantum-espresso/7.1`).
- `launcher`: `srun` if your SLURM starts MPI programs directly; otherwise `mpirun`.
- `workdir`: a scratch path; `$USER` and `$SLURM_JOBID` are expanded by the job.
- `app`: the three paths from step 3, the GPU names your SLURM uses
  (`gpu_types`, e.g. `- {text: A100, value: a100}`), and the contacts
  shown in the app.

Then, under **SLURM resources**, the `cpu` / `gpu` `partition`, and `account` /
`qos` if your site requires them. Empty values are left out of the job script.

`config.example.yaml` is pre-filled with the DeltaHPC values (modules, `srun`,
`/hpfs` scratch); keys you remove fall back to neutral built-in defaults, so
keep the paths set. `make run` refuses to start without
`config.yaml`; to keep the file elsewhere, run `make run CONFIG=/path/to/config.yaml`.

### Check

```bash
ADVQM_SITE_CONFIG=$PWD/config.yaml .venv/bin/python -c \
  "from advQMcalc_test.site_config import SITE; print(SITE['modules'], SITE['app']['runs_dir'])"
```

## 5. Launch the app

```bash
make run                                     # http://127.0.0.1:8866
```

The app listens on 127.0.0.1 only. From your desktop, forward the port:
`ssh -N -L 8866:127.0.0.1:8866 <app-host>`, then open http://localhost:8866.
Change the port with `make run PORT=8900`.

`make run` stays in the foreground and stops when the terminal closes; run it
under `tmux` or `screen`, or as a systemd user service, to keep it up.

The app has **no login of its own**: the "User Name" field labels runs, it
does not identify users. To publish it beyond your workstation, put it behind
your own authenticating proxy.

### Check

The page shows **Application Description**, **How to Use** and **References
and Contacts** at the top, and the New Calculation form below it. Its
Pseudopotential Directory field shows your `pseudo_dir`.

## 6. Verify with a real run

1. Click **Try Sample** (benzene, Grimme-D3), then **Submit Run**.
2. **Runs**: the job appears and moves to COMPLETED within a few minutes on 8 cores.
3. **Results**: select the run. Expect Z = 2 and a total energy in kJ/mol;
   **Download Quantum ESPRESSO Output Files** gives the `.out` file ending in `JOB DONE.`

On the command line, the job script is in
`<runs_dir>/<run>/jobs/crystal/*.slurm`: it should contain your
`module load` lines and `srun pw.x`.

## 7. When it goes wrong

The Runs page shows the error of a failed run, and its download button gives
the workflow log and the QE output. Setup problems (modules, partition,
scratch) are in the SLURM job log,
`<runs_dir>/<run>/jobs/crystal/<name>.slurm.out`, next to the job script.

| Symptom | Cause | Fix |
|---|---|---|
| Every page shows "Script error for jupyter-vue" | widget extensions not registered | rerun `make install` |
| The app does not start; no kernel `python3` | kernel not registered | rerun `make install`; check `jupyter kernelspec list` |
| Empty page after upgrading packages | UI versions no longer match | reinstall from `requirements.txt` (step 2) |
| Submit Run fails with "sbatch not found" | the app machine has no SLURM client, or it is not on `PATH` | start the app on a login node, from a shell where `sbatch` works |
| Job FAILED at once; `.slurm.out` says `pw.x: command not found` | `modules` does not provide `pw.x` | fix `modules` in `config.yaml`; test with `module load …; which pw.x` |
| "No pseudopotential found for element 'X'" | no file named `X.…` or `X_….UPF` in `pseudo_dir` | add the file (step 3) |
| "Multiple pseudopotentials found for element 'X'" | two candidate files for one element | keep one per element in that folder |
| Job fails reading pseudopotentials | `pseudo_dir` not visible from compute nodes | move it to shared storage (step 3) |
| "cif2cell failed" | the CIF could not be converted | check the CIF in a viewer; partial occupancies and missing cell data are the usual causes |

## Appendix: what gets installed

Everything comes from `requirements.txt` into `.venv`; QE comes from your cluster.

| Package | Version | Purpose |
|---|---|---|
| voila | `>=0.4,<0.5` | serves the app |
| notebook | `==6.5.4` | UI stack (coupled, see step 2) |
| ipywidgets · widgetsnbextension · jupyterlab_widgets | `7.6.5` · `3.5.2` · `1.1.4` | UI stack |
| ipyvuetify · ipyvue | `1.8.10` · `>=1.9,<2` | UI components |
| ipykernel | latest | Jupyter kernel for the app |
| pandas · matplotlib | latest · `>=3.9,<4` | results tables, plots |
| cif2cell | latest | CIF → QE input |
| pymatgen · pymatgen-core | `==2026.9.24` · `==2026.10.16` | molecules per cell (Z); pinned to the tested versions |
| pyyaml | latest | reads `config.yaml` |
| advQMcalc_test | 0.0.18 (this repo) | backend |
| pytest | latest | `make test` |

From your cluster: Python 3.10 or newer, SLURM client commands on the app
machine, and Quantum ESPRESSO 7.1 (`pw.x`) on the compute nodes, GPU-enabled
for GPU runs.


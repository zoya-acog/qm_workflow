# Installing and running on your own cluster

The setup path for a site that runs the app directly on its own machines, with
no Docker and no root. Each step ends with a check you can run; most ways this
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

From a copy of this repository on shared storage:

```bash
cd repo
make install               # .venv with the app, backend, pymatgen, cif2cell, tests
```

`make install` also registers the widget front-ends (ipyvuetify,
ipywidgets) in the environment. Without them every page fails with
"Script error for jupyter-vue".

### Check

```bash
.venv/bin/jupyter nbextension list 2>&1 | grep -E "ipyvuetify|widgets" # both 'enabled OK'
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

The pseudopotential folder must exist and contain a `.UPF` file for every
element you will run; the app matches files to elements automatically.

### Check

```bash
ls /shared/qe_workflow/pseudos/*.UPF | head
srun -N1 -n1 ls /shared/qe_workflow/runs     # same path from a compute node
```

## 4. Write the site config

```bash
cp site.example.json site.json               # gitignored
```

Fill in:

- `modules`: what `module load` gives a job `pw.x` (e.g. `["quantum-espresso/7.1"]`).
- `launcher`: `srun` if your SLURM starts MPI programs directly; otherwise `mpirun`.
- `cpu` / `gpu` `partition`, and `account` / `qos` if your site requires them.
  Empty values are left out of the job script.
- `workdir`: a scratch path; `$USER` and `$SLURM_JOBID` are expanded by the job.
- `app`: the three paths from step 3, the GPU names your SLURM uses
  (`gpu_types`, e.g. `{"text": "A100", "value": "a100"}`), and the contacts
  shown in the app.

Keys you leave out keep their built-in default, which describes **our**
cluster, so set every path.

### Check

```bash
ADVQM_SITE_CONFIG=$PWD/site.json .venv/bin/python -c \
  "from advQMcalc_test.site_config import SITE; print(SITE['launch_mode'], SITE['app']['runs_dir'])"
```

## 5. Launch the app

```bash
ADVQM_SITE_CONFIG=$PWD/site.json make run    # http://127.0.0.1:8866
```

The app listens on 127.0.0.1 only. From your desktop, forward the port:
`ssh -L 8866:127.0.0.1:8866 <app-host>`, then open http://localhost:8866.
Change the port with `make run PORT=8900`.

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
`module load` lines and `srun pw.x`, and no `docker`.

## 7. When it goes wrong

| Symptom | Likely cause |
|---|---|
| Every page shows "Script error for jupyter-vue" | widget extensions not registered: rerun `make install` |
| Submit Run fails with "sbatch not found" | the app machine has no SLURM client, or it is not on `PATH` when the app starts |
| Job FAILED at once; `.slurm.out` says `pw.x: command not found` | `modules` in `site.json` does not provide `pw.x` |
| Job fails reading pseudopotentials | `pseudo_dir` not visible from compute nodes, or a missing element `.UPF` |
| Status shows **ENDED** instead of COMPLETED | SLURM accounting (`sacct`) is unavailable; the job left the queue. Check its `.out` file |
| Results show "–" for relative energy | the selected runs are different compounds, or one CIF could not be read |

## Appendix: what gets installed

`requirements.txt` lists it: Voila 0.4 with the notebook-6 / ipywidgets-7
widget stack (ipyvuetify needs it), pandas and matplotlib for tables and
plots, cif2cell (CIF → QE input), pymatgen (molecule detection for Z), the
`advQMcalc_test` backend, and pytest.

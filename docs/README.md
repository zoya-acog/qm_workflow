# The app: overview and how it works

qm_workflow is a web app for ranking crystal structures by energy. You give it
one CIF, or a folder of CIFs; it builds Quantum ESPRESSO (QE) inputs with the
same settings for every structure, runs them on the SLURM cluster (CPU or GPU),
and shows a table of total and relative energies per molecule.

Setup on a cluster is in [installation.md](installation.md). This page
describes what the app does once it is running.

| Document | Read it for |
|---|---|
| [installation.md](installation.md) | Installing, `config.yaml`, launching, a verification run, troubleshooting |
| **README.md** (this page) | What each page does and what happens behind it |
| [design.md](design.md) | Decisions and the options considered |
| [backend-changes.md](backend-changes.md) | Changes to the `advQMcalc_test` backend |
| [gpu_slurm.md](gpu_slurm.md) | Shared GPUs (off by default) |
| [status.md](status.md) | What works and what is open |
| [vision.md](vision.md) | Why the app exists and who it is for |

## The pieces

```
 browser ──► Voila app (app/)  ──calls in-process──►  advQMcalc_test (backend)
                 │   ▲                                   │  cif2cell → QE input
                 │   │ reads                             │  job script → sbatch
                 │   └──── run folders on shared storage ◄┘
                 │                     ▲
                 └── sacct / squeue    │ pw.x writes output
                                 SLURM compute nodes
```

- **Web app** (`app/`): a Voila notebook with four pages: New Calculation,
  Runs, Results, Dashboard. It runs on a host where `sbatch`, `squeue` and
  `sacct` work.
- **Backend** (`advQMcalc_test/`): converts a CIF to a QE input with cif2cell,
  writes the SLURM job script, submits it, and later checks the job and reads
  the QE output. It also works on its own from the command line.
- **Site configuration** (`config.yaml`): everything specific to a cluster:
  QE modules, MPI launcher, partitions, account/QOS, scratch folder, the GPU
  types offered in the form, and default paths.
- **Run folders**: one folder per structure under the Runs Directory. There is
  no database; every page reads these folders.

```
<runs_dir>/<run>/
  state.json            settings, SLURM job id, status, energies
  inputs/crystal/       the CIF copy and the QE input (.scf.in / .relax.in / ...)
  jobs/crystal/         the job script (.slurm) and SLURM's log (.slurm.out)
  outputs/              QE output (.out) and output structures
  results/              extracted energies, molecules per cell (Z)
  logs/                 the workflow log
```

## Pages

The top of every page has **Application Description**, **How to Use** and
**References and Contacts**. The left menu switches between the pages.

### New Calculation

The form for a new run, in four sections.

**Input**

- **Single CIF File**: upload one `.cif` from your computer. A new upload
  replaces the previous one.
- **Multiple CIF Files**: the absolute path of a folder on the server, plus a
  filename pattern (default `*.cif`). The app checks the folder before
  submitting and says what is wrong: not found, not a folder, empty, no
  matching files, or no permission (and at which folder access is blocked).
- **Try Sample** fills the form with benzene and Grimme-D3.

**⚙️ QE Settings**

| Field | Meaning |
|---|---|
| Calculation Type | scf (Single Point Energy), relax (Fixed Cell Optimization), vc-relax (Variable Cell Optimization) |
| Pseudopotential Directory | Folder with one `.UPF` file per element |
| k-point Separation (1/Å) | Builds the k-point grid from the cell lengths (preferred) |
| k-points Grid | A fixed grid instead, e.g. `3 3 2 0 0 0` |
| DFT Functional | Empty = taken from the pseudopotentials |
| vdW Correction, XDM a1 / a2 | Dispersion correction, e.g. DFT-D3 or XDM with its parameters |
| Wavefunction / Charge Density Cutoff (Ry) | Plane-wave cutoffs; charge density is usually 8–12 × the wavefunction cutoff |
| Occupations, Smearing, Degauss | Needed mainly for metallic systems or SCF convergence trouble |

**🛠️ Additional Settings** (open by default)

- **User Name** (required): labels the run; the app has no login.
- **Run ID** (optional), **Runs Directory**, **Log File Name**, **PP Map**
  (pseudopotential file per element, when the folder has more than one).

**🖥️ SLURM Configuration**

| Field | Meaning |
|---|---|
| Walltime, Tasks, Memory Per CPU | SLURM limits for each structure (CPU runs use Tasks MPI processes) |
| Max Structures Running at Once | Folder runs only: at most this many structures run at the same time. 0 = no limit; then free CPUs/GPUs decide |
| Use GPU, GPU Type, No. of GPUs | Run on GPUs; one QE process per GPU. GPU Type "Any" lets SLURM choose |
| GPU Sharing | Only when the site enables shards: share one GPU with other jobs, or reserve it. See [gpu_slurm.md](gpu_slurm.md) |

**Submit Run** checks the paths, prepares the CIFs, creates the runs and
submits them. Each submission gets its own collapsible log section below the
form ("Submission N · time · CIFs · job id"), with what was done and any error.

### Runs

All runs, newest first. A folder of CIFs submitted together is one row (one
SLURM array job).

- **Search** by CIF name or job id; **Filter by User**.
- **Refresh** asks SLURM for the current state of unfinished runs and saves it.
- Columns: Job ID, Run ID, User Name, CIF File, **Path to Input CIFs** (folder
  runs only), Calculation Type, Status.
- Row icons: **⚙ Monitor** (settings and status of the run, and Resume),
  **📈 Results** (once finished), **⬇ Logs** (zip of the workflow log, SLURM
  log and QE output).
- **Resume** re-checks the selected CIFs of a run and collects results. With
  *Resubmit if SLURM job failed* it submits a failed one again with the saved
  settings.

### Results

Select a run (or a batch) to see, per structure: total energy, molecules per
cell (Z), energy per molecule and **relative energy per molecule (kJ/mol)**,
lowest first. Relative energies are shown only when all selected structures
are the same compound. Downloads: CSV of the table, output structures
(relax / vc-relax), and the QE output files. The plot icon shows the energy
over the optimization steps.

### Dashboard

Number of runs Completed, In Progress and Failed, and the five most recent
runs (or batches) of all users. **Refresh** asks SLURM, as on the Runs page.

## What happens on Submit

For each CIF:

1. **Prepare the CIF.** Files whose name has spaces or special characters are
   copied under a safe name (letters, digits, `_`, `-`); a space group written
   with underscores (`P 1 2_1/c 1`) is copied as `P 1 21/c 1`, which cif2cell
   reads. The original file is never changed; the log notes each change.
2. **Create the run folder** and `state.json` with every setting.
3. **Build the QE input**: cif2cell converts the CIF; the backend adds the
   k-point grid, cutoffs, functional, dispersion, smearing, and `&IONS` /
   `&CELL` for relax and vc-relax.
4. **Write the job script** from the site configuration: partition, account,
   modules, launcher, GPU request.
5. **Submit**: one `sbatch` for a single CIF; for a folder, one SLURM **array
   job** with one task per CIF (`--array=1-N%M`, M = Max Structures Running at
   Once).

## Status

The app asks SLURM (`sacct`, falling back to `squeue`) on Refresh and when a
run is opened, then reads QE's output when the job has finished.

| Shown | Meaning |
|---|---|
| Pending | Prepared or submitted, waiting in the queue |
| Running | The job is running |
| Completed | QE finished; energies extracted |
| Failed | Setup error, QE error, cancelled, time limit, out of memory, node failure |

A batch row sums up its structures. Array tasks that never started (cancelled
while queued) take the array's state. When a job leaves the queue while
`sacct` cannot be reached, its SLURM state is saved as ENDED, and Refresh asks
`sacct` again later.

## Energy per molecule

Energy per molecule = QE cell energy ÷ Z, where Z is the number of molecules
(or formula units) in the cell, found with pymatgen. Relative energy compares
structures to the lowest one and is valid only between runs with the same
settings (functional, pseudopotentials, cutoffs, k-spacing, dispersion). The
rule for Z is in [design.md](design.md#z-and-relative-energy).

## Command line

The backend runs without the app; see the [main README](../README.md#command-line)
and `advQMcalc_test --help`.

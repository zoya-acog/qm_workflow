# App overview

A web app that ranks crystal structures by energy. Give it one CIF or a folder
of CIFs; it runs Quantum ESPRESSO on the SLURM cluster with the same settings
for every structure, and shows the relative energy per molecule.

Setup: [installation.md](installation.md).

## How it works

1. **New Calculation**: upload a CIF (or give a folder path), choose the QE
   and SLURM settings, click **Submit Run**.
2. The app prepares each CIF (unsafe file names and `2_1`-style space groups
   are fixed in a copy; the original is never changed), builds the QE input
   with cif2cell, and submits the job. A folder is one SLURM array job.
3. **Runs**: follow the status (Pending → Running → Completed / Failed).
   **Refresh** asks SLURM for the latest state.
4. **Results**: energies per structure, ranked by relative energy
   (kJ/mol), with CSV, structure and QE output downloads.

Each run is a folder under the Runs Directory (inputs, job script, QE output,
results); there is no database.

## Pages

| Page | What you do there |
|---|---|
| New Calculation | Input CIFs, ⚙️ QE Settings, 🛠️ Additional Settings (User Name, Run ID), 🖥️ SLURM Configuration (CPU/GPU, Max Structures Running at Once) |
| Runs | All runs, newest first; search, filter by user, Refresh, Monitor / Resume, download logs |
| Results | Ranked energies, plots and downloads |
| Dashboard | Completed / In Progress / Failed counts and the latest runs |

## More

| Document | For |
|---|---|
| [installation.md](installation.md) | Installing, `config.yaml`, launching, troubleshooting |
| [backend-changes.md](backend-changes.md) | Changes to the `advQMcalc_test` backend |

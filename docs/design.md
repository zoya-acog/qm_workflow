# Design

Decisions behind qm_workflow, with the options considered. Current state and
open work are in [status.md](status.md).

## Shape

A Voila app (`app/`) drives a backend package (`advQMcalc_test/`).
The app collects settings, calls the backend in-process to stage the CIF, run
cif2cell, write the QE input and a SLURM job script, and submit it. Each run
is a folder with `state.json`, inputs, job script, QE output and results. The
app reads those folders back for the Runs, Results and Dashboard pages; there
is no database.

The backend is the client's code. Our changes go in the app, deployment, tests
and docs; the backend is changed only by agreement.

## Site config, not code, describes a cluster

**Decision:** every cluster-specific value (MPI launcher, modules,
partitions, node lists, account/QOS, scratch and app paths, GPU names) lives in
one file, `config.yaml` (used by `make run`, or named by `ADVQM_SITE_CONFIG`).
`site_config.py` reads it over neutral built-in defaults; `config.example.yaml`
is pre-filled for DeltaHPC.

**Options:** separate code branches per site (rejected: fixes would have to be
made twice); environment variables per value (rejected: dozens of variables,
no nesting for CPU/GPU).

**Consequence:** a change to `config.example.yaml` or to the script generator changes
the job scripts;
`tests/expected/` records the reference scripts for the DeltaHPC example, and the
script the original advQMcalc_test 0.0.16 wrote; a test checks every line of that one is kept.

## How QE runs

Jobs load QE from the cluster's module system and start it with SLURM:
`module load quantum-espresso/7.1`, then `srun pw.x`, as the client's own
advQMcalc_test 0.0.16 scripts do. The modules, the launcher (`srun` or
`mpirun`) and the scratch directory are settings in `config.yaml`.

**QE 7.1** matches the client cluster. Benzene + DFT-D3 scf gives
−302.98707889 Ry on QE 7.1 (CPU and GPU) and 7.2 alike, so results are
comparable across these versions. GPU jobs run one MPI rank per GPU with
`-nk <GPUs>`.

## Z and relative energy

Energy per molecule = QE cell energy ÷ Z, where Z = atoms in the QE cell ÷
atoms per unit. The unit comes from pymatgen, in order:

1. the bonded molecule whose formula, times a whole number, equals the cell's
   formula (molecular crystals; Genarris files with no Z tag; cells where a
   close contact makes bond detection cut one molecule two ways);
2. otherwise the CIF's formula unit, cell ÷ `_cell_formula_units_Z`
   (hydrates, salts, co-crystals);
3. otherwise the CIF's `_chemical_formula_sum` (the old rule).

The CIF formula tag alone was wrong for Genarris files (it is the whole cell,
giving Z = 1). The result is cached per run in `results/cif_molecule.json`.

Relative energy is shown only when every selected run has the same unit
formula; it is valid only between runs with the same settings (functional,
pseudopotentials, cutoffs, k-spacing, dispersion).

## Job status

`sacct` on our cluster is often down. Status falls back to `squeue`; a job
that has left the queue without an accounting record shows **ENDED** rather
than failed.

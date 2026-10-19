# qm_workflow

A web app for crystal-structure energies: upload CIFs, and it builds Quantum ESPRESSO inputs, runs them on a SLURM cluster (CPU or GPU, one file or a whole folder as an array job), and ranks the structures by relative energy per molecule.

> **Start here:** [docs/installation.md](docs/installation.md): software, storage paths, site config, launching the app, and a verification run.
>
> **Deeper:** [docs/vision.md](docs/vision.md) · decisions in [docs/design.md](docs/design.md) · current state in [docs/status.md](docs/status.md).

## Using it

Open the app in a browser. The **How to Use** section at the top walks through New Calculation, Runs, Results and Dashboard; **Try Sample** fills in a benzene example.

## Developing it

```bash
make install   # .venv with the app, backend and test dependencies
make test      # pytest suite (no QE or SLURM needed)
make run       # app on http://127.0.0.1:8866
```

`make help` lists every target. The map of the code is in [AGENTS.md](AGENTS.md). The backend (`advQMcalc_test/`) is client-provided; what we changed in it, and why, is in [docs/backend-changes.md](docs/backend-changes.md).

## Running it

```bash
make install
cp config.example.yaml config.yaml        # fill in modules, partitions, paths
make run
```

The machine running the app must be able to run `sbatch`/`squeue` and see the runs and pseudopotential folders at the same paths as the compute nodes. QE comes from your cluster (e.g. `module load quantum-espresso/7.1`); there is no container in this distribution.

## Directory structure

```
├── README.md · AGENTS.md · Makefile      front doors · automation contract
├── requirements.txt · config.example.yaml  Python dependencies · configuration template
├── app/                                  Voila web app (app.ipynb → app_src.py, pages/)
├── advQMcalc_test/                       backend: CIF → QE input, SLURM jobs, results
├── tests/                                pytest suite, sample CIFs, recorded job scripts
└── docs/                                 installation · vision · design · status
```

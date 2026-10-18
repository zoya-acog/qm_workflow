# qm_workflow

A web app for crystal-structure energies: upload CIFs, and it builds Quantum ESPRESSO inputs, runs them on a SLURM cluster (CPU or GPU, one file or a whole folder as an array job), and ranks the structures by relative energy per molecule.

## Using it

Open the app (on our cluster: https://advqm-voila.own3.aganitha.ai/). The **How to Use** section at the top of the app walks through New Calculation, Runs, Results and Dashboard. "Try Sample" fills in a benzene example.

## Developing it

```bash
cd repo
make install   # .venv with the app, backend and test dependencies
make test      # pytest suite
make run       # app on http://localhost:8866
```

The map of the code is in [AGENTS.md](AGENTS.md); decisions are in [docs/design.md](docs/design.md). The backend (`repo/advQMcalc_test`) is client-provided and is not changed here.

## Running it

**Another cluster (no Docker):** copy `repo/advQMcalc_test/site_configs/deltahpc.example.json`, fill in partitions, modules and paths, then:

```bash
cd repo && make install
ADVQM_SITE_CONFIG=/path/to/site.json make run
```

The host running the app must be able to run `sbatch`/`squeue` and see the runs and pseudopotential folders.

**Our cluster (own3, Docker):** `cd repo && make rebuild` builds the image and replaces the `advqm-voila` container. QE runs in the `advqm-qe-cpu:7.1` / `advqm-qe-gpu:7.1` images; `make help` lists every target.

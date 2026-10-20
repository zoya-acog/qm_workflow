# qm_workflow

Web app (Voila) that turns crystal CIFs into Quantum ESPRESSO energy runs on a SLURM cluster and compares the results.

## Map

| Path | What it is |
|---|---|
| app/ | Voila web app: app.ipynb → app_src.py; pages/ (new_run, runs, results, dashboard), shared.py, helpers.py, docs.py (in-app help) |
| advQMcalc_test/ | Backend package: CIF → QE input (cif2cell), SLURM job scripts, state.json, results. **Client-provided** |
| advQMcalc_test/src/advQMcalc_test/site_config.py | Per-cluster settings (launch mode, partitions, paths); overridden by config.yaml (`make run`) or `ADVQM_SITE_CONFIG=<file>` |
| config.example.yaml | The one site configuration file, pre-filled for DeltaHPC: copy to config.yaml (gitignored); SLURM, QE modules, paths, GPU names, contacts |
| tests/ | pytest suite; tests/data/ holds real CIFs, tests/expected/ reference job scripts (incl. the client 0.0.16 original) |
| docs/ | installation.md (setup), backend-changes.md (changes to the client backend), vision.md, design.md (decisions), status.md (state and TODO) |

## Rules

- `app/` imports the backend; the backend never imports the app.
- **Do not modify `advQMcalc_test/`** (client code). Put new behavior in the app or outside the package; ask first if a backend change seems needed.
- Commands run from the repo root: `make install`, `make test`, `make run`.
- Submitting SLURM jobs uses shared cluster resources: ask before submitting. Test runs use benzene with DFT-D3.
- Built-in site defaults are neutral (no partitions, paths or images); every real value comes from `config.yaml`. `make test` compares job scripts with tests/expected/.
- Agents commit only when asked; never push to main without being asked.

## Deeper

- Setup on a cluster: docs/installation.md
- Architecture, site config, QE version, Z rule: docs/design.md
- Current state and open work: docs/status.md

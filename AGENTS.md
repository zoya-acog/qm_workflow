# qm_workflow

Web app (Voila) that turns crystal CIFs into Quantum ESPRESSO energy runs on a SLURM cluster and compares the results.

## Map

| Path | What it is |
|---|---|
| app/ | Voila web app: app.ipynb → app_src.py; pages/ (new_run, runs, results, dashboard), shared.py, helpers.py, docs.py (in-app help) |
| advQMcalc_test/ | Backend package: CIF → QE input (cif2cell), SLURM job scripts, state.json, results. **Client-provided** |
| advQMcalc_test/src/advQMcalc_test/site_config.py | Per-cluster settings (launch mode, partitions, paths); override with `ADVQM_SITE_CONFIG=<json>` |
| site.example.json | Template a site copies to site.json (gitignored); also sets contacts shown in the app |
| advQMcalc_test/site_configs/ | Example site configs (DeltaHPC) |
| tests/ | pytest suite; tests/data/ holds real CIFs, tests/golden/ recorded job scripts |
| docs/ | installation.md (setup), vision.md, design.md (decisions), status.md (state and TODO) |

## Rules

- `app/` imports the backend; the backend never imports the app.
- **Do not modify `advQMcalc_test/`** (client code). Put new behavior in the app or outside the package; ask first if a backend change seems needed.
- Commands run from the repo root: `make install`, `make test`, `make run`.
- Submitting SLURM jobs uses shared cluster resources: ask before submitting. Test runs use benzene with DFT-D3.
- The built-in site config is the developers' cluster; a site always runs with its own `site.json`. `make test` compares job scripts with tests/golden/.
- Agents commit only when asked; never push to main without being asked.

## Deeper

- Setup on a cluster: docs/installation.md
- Architecture, site config, QE version, Z rule: docs/design.md
- Current state and open work: docs/status.md

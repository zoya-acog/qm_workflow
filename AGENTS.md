# qm_workflow

Web app (Voila) that turns crystal CIFs into Quantum ESPRESSO energy runs on a SLURM cluster and compares the results.

## Map

| Path | What it is |
|---|---|
| repo/app/ | Voila web app: app.ipynb → app_src.py; pages/ (new_run, runs, results, dashboard), shared.py, helpers.py, docs.py (in-app help) |
| repo/advQMcalc_test/ | Backend package: CIF → QE input (cif2cell), SLURM job scripts, state.json, results. **Client-provided** |
| repo/advQMcalc_test/src/advQMcalc_test/site_config.py | Per-cluster settings (launch mode, partitions, paths); override with `ADVQM_SITE_CONFIG=<json>` |
| repo/site.example.json | Template a site copies to site.json (gitignored); also sets contacts shown in the app |
| repo/advQMcalc_test/site_configs/ | Example site configs (DeltaHPC) |
| repo/cpu/, repo/gpu/ | Dockerfiles for the QE 7.1 images used on our cluster |
| repo/Dockerfile, repo/deploy/ | App image and own3 container restart script (our cluster only) |
| repo/tests/ | pytest suite; tests/data/ holds real CIFs, tests/golden/ recorded job scripts |
| docs/ | vision.md, design.md (decisions), installation.md (client setup), status.md (state and TODO) |
| data/ (gitignored) | Live runs, uploads, pseudopotentials, image tars. Never commit; never delete |

## Rules

- `repo/app` imports the backend; the backend never imports the app.
- **Do not modify `repo/advQMcalc_test/`** (client code). Put new behavior in the app or outside the package; ask first if a backend change seems needed.
- Commands run from `repo/`: `make install`, `make test`, `make run`; on our cluster `make rebuild` redeploys the live app (ask first).
- Submitting SLURM jobs uses shared cluster resources: ask before submitting. Test runs use benzene with DFT-D3.
- Changing the default site config changes every job script; `make test` compares them with tests/golden/.
- Agents commit only when asked; never push to main without being asked.

## Deeper

- Architecture, site config, QE version, Z rule: docs/design.md
- Current state and open work: docs/status.md

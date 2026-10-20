# Status

## Working

- Single CIF and folder (SLURM array) runs; scf, relax, vc-relax; CPU and GPU.
- QE 7.1: benzene + DFT-D3 scf gives the same energy as QE 7.2 and 7.3.1, on CPU and GPU.
- One configuration file, `config.yaml`, pre-filled for DeltaHPC in `config.example.yaml`; QE from a module, `srun`; the generated job script keeps every line of the advQMcalc_test 0.0.16 script.
- Results: relative energy per molecule, Z from pymatgen, CSV / structure / QE output downloads, plots.
- `make test`: job scripts, site config, QE input (nstep, k-points) and an import check of the app; no QE or SLURM needed.
- Fresh install (`make install`, `make test`, `make run`) checked from a clean clone.

## Open

- Site: fill in `config.yaml` (paths, partitions, account/QOS, GPU names, contacts) and run the verification run in docs/installation.md.
- The app submits with `sbatch`, so it must run on a host where `sbatch`/`squeue` work (typically a login node); token-based (slurmrestd) submission is not implemented.
- GPU runs need a GPU-enabled QE module.
- QE 7.1 relax / vc-relax not yet run end to end on the target cluster.
- Multi-node runs: node count is fixed at 1.
- The New Run "QE command" field is ignored (QE comes from the site config).

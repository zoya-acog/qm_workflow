# Status

## Working

- Single CIF and folder (SLURM array) runs; scf, relax, vc-relax; CPU and GPU.
- QE 7.1 images on our cluster; scf benzene + D3 checked on CPU and RTX 5080.
- Site config with `docker` and `module` launch modes; DeltaHPC example config.
- Results: relative energy per molecule, Z from pymatgen, CSV / structure / QE output downloads, plots.
- `make test`: job scripts, QE input (nstep, k-points), Z on real CIFs.

## Open

- QE 7.1 not yet checked for relax / vc-relax, or on RTX 4090.
- Client (DeltaHPC): partitions/account/QOS, runs/uploads/pseudopotential paths, whether the UI host can run `sbatch`; GPU module to be enabled on their side.
- Multi-node runs: node count is fixed at 1.
- The New Run "QE command" field is ignored (QE comes from the site config).
- `skills-pack.lock.json`: add once `atk` / `skills-pack` is installed.
- Our cluster: own5 (Docker GPU device error), own10 (no docker access) and own3 (GTX 1080 Ti) are excluded from GPU jobs.

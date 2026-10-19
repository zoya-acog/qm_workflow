# Vision

## Why it exists

Ranking the polymorphs of a molecular crystal by energy means running the same
DFT calculation on many structures with identical settings, then comparing
energies per molecule. Done by hand, that is hours of input editing, job
scripts and output parsing per batch, and the comparison is easy to get wrong
(different settings, a wrong count of molecules per cell).

## Who it serves

Computational chemists (first, the client's solid-form team) who know which
structures and DFT settings they want but should not have to write QE inputs,
SLURM scripts or parsers to get a ranked table.

## What it does

From a CIF, or a folder of CIFs, to a ranked table of relative energies per
molecule: Quantum ESPRESSO inputs built consistently, jobs run on the
cluster's CPUs or GPUs, results and structures downloadable.

## What success looks like

- A user goes from CIFs to a ranked table without touching a terminal.
- Every structure in a comparison ran with the same settings, and the app
  refuses to compare runs that are not the same compound.
- The app installs on a new cluster by writing one site config, without code
  changes.

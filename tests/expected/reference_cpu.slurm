#!/bin/bash
#SBATCH --job-name=X.scf
#SBATCH --output=RUNDIR/jobs/crystal/X.scf.slurm.out
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --ntasks=8
#SBATCH --mem-per-cpu=4G
#SBATCH --time=2:00:00

set -e

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK
module load medsci
module load quantum-espresso/7.1
PW_BIN=pw.x

infile=$(basename "X.scf.in" .in)
indir="RUNDIR/inputs/crystal"
workdir="/hpfs/userws/$USER/workdir/$SLURM_JOBID"

mkdir -p "$workdir" && cd "$workdir" && echo "$workdir"
trap 'cp "$workdir/$infile.out" "$indir/" 2>/dev/null || true' EXIT
cp -r "$indir/$infile.in" "$workdir"

# QE's scratch/coordination files (*.save/, pwscf.para,
# pwscf.restart, ...) race across MPI ranks when written to the
# NFS-mounted workdir — NFS's weaker concurrent-unlink semantics
# cause "File cannot be deleted" Fortran errors (exit code 2)
# even when the calculation itself converges correctly and
# prints JOB DONE. disk_io='none' alone isn't enough: QE still
# writes/deletes small MPI coordination files at shutdown
# regardless of that setting. Point outdir at node-local scratch
# instead — only the small .out file (captured via the stdout
# redirect below) needs to survive, we don't use
# restart-from-checkpoint anywhere in this pipeline.
localscratch="/hpfs/userws/$USER/workdir/$SLURM_JOBID/tmp"
mkdir -p "$localscratch"
if grep -q "outdir" "$infile.in"; then
    sed -i "s#^\s*outdir.*#  outdir = '$localscratch'#" "$infile.in"
else
    sed -i "/&CONTROL/a\  outdir = '$localscratch'" "$infile.in"
fi
if grep -q "disk_io" "$infile.in"; then
    sed -i "s/^\s*disk_io.*/  disk_io = 'none'/" "$infile.in"
else
    sed -i "/&CONTROL/a\  disk_io = 'none'" "$infile.in"
fi

# Domain guidance: for relax/vc-relax, make ion_dynamics explicit
# rather than relying on QE's unstated default (which happens to
# already be 'bfgs', but an unstated default is fragile — it
# silently changes behavior if a future QE version's default
# changes, and it's undocumented to anyone reading the input
# file). Only applies to relax/vc-relax; scf has no &IONS block
# and shouldn't get one. Inserted right after &ELECTRONS's own
# closing "/" (matched via awk, not sed, since a QE input has
# several bare "/" lines — one per namelist — and only this one
# is unambiguous: it's the first bare "/" after &ELECTRONS opens).
# vc-relax additionally needs a &CELL namelist (QE otherwise
# fails with: bad line in namelist &cell).
vc=0
if grep -q "calculation = 'vc-relax'" "$infile.in"; then vc=1; fi
if grep -qE "calculation = '(vc-)?relax'" "$infile.in" && ! grep -q "&IONS" "$infile.in"; then
    awk -v vc="$vc" '
        /&ELECTRONS/ { in_electrons=1 }
        in_electrons && /^\/$/ {
            print
            print ""
            print "&IONS"
            print "  ion_dynamics = '"'"'bfgs'"'"'"
            print "/"
            if (vc == 1) {
                print ""
                print "&CELL"
                print "  cell_dynamics = '"'"'bfgs'"'"'"
                print "/"
            }
            in_electrons=0
            next
        }
        { print }
    ' "$infile.in" > "$infile.in.tmp" && mv "$infile.in.tmp" "$infile.in"
fi

srun pw.x -i "$infile.in" > "$infile.out"
rm -rf "$localscratch"
cp "$infile.out" "$indir"

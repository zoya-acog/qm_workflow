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

# QE temporary files go to a scratch directory and disk_io is 'none',
# which avoids file conflicts between MPI ranks on shared storage.
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

# relax / vc-relax: add &IONS (and &CELL for vc-relax) with BFGS after
# &ELECTRONS; QE requires &CELL for vc-relax.
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

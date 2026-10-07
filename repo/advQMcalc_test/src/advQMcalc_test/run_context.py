from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from shutil import copy2
from textwrap import dedent


CPU_IMAGE = "advqm-qe-cpu:7.2"
CPU_IMAGE_TAR = "/mnt/own6d/qe_workflow/data/gpu-build/advqm-qe-cpu-7.2.tar"
GPU_IMAGE = "advqm-qe-gpu:7.3.1"
GPU_IMAGE_TAR = "/mnt/own6d/qe_workflow/data/gpu-build/advqm-qe-gpu-7.3.1.tar"
DATA_ROOT = "/mnt/own6d/qe_workflow/data"



class RunContext:
    """
    Represents a single workflow run.
    Owns the run directory and state.json.
    """

    def __init__(self, base_dir: Path, run_dir: Path | None = None):
        self.base_dir = base_dir

        if run_dir is None:
            self.run_dir = self._generate_unique_run_dir(base_dir)
        else:
            self.run_dir = run_dir

        self.state_file = self.run_dir / "state.json"

    def _generate_unique_run_dir(self, base_dir: Path) -> Path:
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        candidate = base_dir / ts

        if not candidate.exists():
            return candidate

        for k in range(1, 1000):
            candidate_k = base_dir / f"{ts}_{k:02d}"
            if not candidate_k.exists():
                return candidate_k

        raise RuntimeError(f"Could not generate a unique run directory under {base_dir}")

    def initialize(self, initial_state: dict):
        self.run_dir.mkdir(parents=True, exist_ok=False)
        with open(self.state_file, "w") as f:
            json.dump(initial_state, f, indent=2)

    def load_state(self) -> dict:
        with open(self.state_file, "r") as f:
            return json.load(f)

    def save_state(self, state: dict):
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def create_layout(self):
        (self.run_dir / "inputs" / "crystal").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "inputs" / "monomer").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "jobs" / "crystal").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "jobs" / "monomer").mkdir(parents=True, exist_ok=True)
        (self.run_dir / "results").mkdir(exist_ok=True)
        (self.run_dir / "logs").mkdir(exist_ok=True)

    def stage_cif_for_crystal(self, source_cif: Path) -> Path:
        dest = self.run_dir / "inputs" / "crystal" / source_cif.name
        copy2(source_cif, dest)
        return dest

    def write_crystal_slurm_script(
        self,
        qe_input_rel: str,
        job_name: str,
        walltime: str = "2:00:00",
        ntasks: int = 8,
        nodes: int = 1,
        mem_per_cpu: str = "4G",
        qe_command: str = "pw.x",
        gpu: bool = False,
        gpu_type: str | None = None,
        gpus: int = 1,
    ) -> Path:
        """
        Write a SLURM script for the crystal QE calculation.

        Defaults requested:
          - #SBATCH --nodes=1
          - #SBATCH --mem-per-cpu=4G
          - #SBATCH --time=2:00:00
        """
        qe_input_path = self.run_dir / qe_input_rel
        if not qe_input_path.exists():
            raise RuntimeError(f"QE input file not found for SLURM generation: {qe_input_path}")

        script_path = self.run_dir / "jobs" / "crystal" / f"{job_name}.slurm"
        slurm_out_path = (script_path.parent / f"{job_name}.slurm.out").resolve()
        input_dir = qe_input_path.parent.resolve()

        pad = " " * 12  # keeps multi-line inserts aligned for dedent()
        # QE always runs from a docker image (DevOps policy: containers only).
        # Paths are mounted at identical locations so the generated QE input
        # (pseudo_dir, outdir) works unchanged, and MPI runs inside the
        # container as one job (plain `srun pw.x` gave uncoordinated copies).
        if gpu:
            image, image_tar = GPU_IMAGE, GPU_IMAGE_TAR
            gres = f"gpu:{gpu_type}:{gpus}" if gpu_type else f"gpu:{gpus}"
            partition_line = "#SBATCH --partition=pgpu"
            # own3: GTX 1080 Ti (cc6.1) has no kernels in the GPU image.
            # own10: our user is not in the docker group there.
            nodelist_line = f"#SBATCH --gres={gres}\n{pad}#SBATCH --exclude=own3,own10"
            cpus_per_task = 4
            ntasks = gpus
            device_arg = "--device nvidia.com/gpu=all "
            mpi_cmd = f"mpirun -np {gpus} pw.x -nk {gpus}"
            kind = "GPU"
        else:
            image, image_tar = CPU_IMAGE, CPU_IMAGE_TAR
            partition_line = "#SBATCH --partition=pzero"
            nodelist_line = "#SBATCH --nodelist=own9"
            cpus_per_task = 1
            device_arg = ""
            mpi_cmd = f"mpirun --bind-to none -np {ntasks} pw.x"
            kind = "CPU"
        pw_bin = "pw.x"  # QE comes from the image
        env_lines = "\n".join(
            [
                "export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK",
                f'IMG="{image}"',
                "# Load the image on this node once (flock: one loader per node).",
                'if ! docker image inspect "$IMG" >/dev/null 2>&1; then',
                f"    flock /tmp/advqm_docker_load.lock sh -c 'docker image inspect {image} >/dev/null 2>&1 || docker load -i {image_tar}'",
                "fi",
            ]
        ).replace("\n", "\n" + pad)
        launch = "\n".join(
            [
                f"docker run --rm {device_arg}\\",
                '    --label user="$(id -un)" \\',
                f'    --label description="advQMcalc_test QE {kind} job ($SLURM_JOB_NAME)" \\',
                "    --label security=ldap \\",
                '    --user "$(id -u):$(id -g)" -e HOME=/tmp -e OMP_NUM_THREADS \\',
                f'    -v {DATA_ROOT}:{DATA_ROOT} -v "$localscratch:$localscratch" \\',
                '    -w "$workdir" "$IMG" \\',
                f'    {mpi_cmd} -i "$infile.in" > "$infile.out"',
            ]
        ).replace("\n", "\n" + pad)

        script_text = dedent(
            f"""\
            #!/bin/bash
            #SBATCH --job-name={job_name}
            #SBATCH --output={slurm_out_path}
            {partition_line}
            #SBATCH --nodes={nodes}
            {nodelist_line}
            #SBATCH --cpus-per-task={cpus_per_task}
            #SBATCH --ntasks={ntasks}
            #SBATCH --mem-per-cpu={mem_per_cpu}
            #SBATCH --time={walltime}

            set -e

            {env_lines}
            # ponytail: retain the existing cluster-specific QE executable and
            # paths; ceiling: the UI's qe_command field is ignored here. Upgrade
            # by parameterizing this script with a validated qe_command value.
            PW_BIN={pw_bin}

            infile=$(basename "{qe_input_path.name}" .in)
            indir="{input_dir}"
            workdir="$indir/work_$SLURM_JOBID"

            mkdir -p "$workdir" && cd "$workdir" && echo "$workdir"
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
            localscratch="/tmp/qe_scratch_$SLURM_JOBID"
            mkdir -p "$localscratch"
            if grep -q "outdir" "$infile.in"; then
                sed -i "s#^\\s*outdir.*#  outdir = '$localscratch'#" "$infile.in"
            else
                sed -i "/&CONTROL/a\\  outdir = '$localscratch'" "$infile.in"
            fi
            if grep -q "disk_io" "$infile.in"; then
                sed -i "s/^\\s*disk_io.*/  disk_io = 'none'/" "$infile.in"
            else
                sed -i "/&CONTROL/a\\  disk_io = 'none'" "$infile.in"
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
                    /&ELECTRONS/ {{ in_electrons=1 }}
                    in_electrons && /^\/$/ {{
                        print
                        print ""
                        print "&IONS"
                        print "  ion_dynamics = '"'"'bfgs'"'"'"
                        print "/"
                        if (vc == 1) {{
                            print ""
                            print "&CELL"
                            print "  cell_dynamics = '"'"'bfgs'"'"'"
                            print "/"
                        }}
                        in_electrons=0
                        next
                    }}
                    {{ print }}
                ' "$infile.in" > "$infile.in.tmp" && mv "$infile.in.tmp" "$infile.in"
            fi

            {launch}
            rm -rf "$localscratch"
            cp "$infile.out" "$indir"
            """
        )

        script_path.write_text(script_text)
        return script_path

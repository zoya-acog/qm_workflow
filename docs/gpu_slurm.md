# Shared GPUs (SLURM shards)

GPU runs can share a GPU with other jobs instead of reserving a whole GPU.
This is **off by default**: with `gpu.shard` empty in `config.yaml`, every GPU
run reserves whole GPUs, exactly as before, and the web app does not show the
option.

## Background

SLURM offers two ways to request a GPU:

| Request | Meaning |
|---|---|
| `--gres=gpu:<type>:N` | N whole GPUs, reserved for this job only |
| `--gres=shard:<type>:S` | A share of **one** GPU's compute (S shards). Other jobs may use the same GPU at the same time |

Shards divide a GPU's **compute only**. GPU memory is not divided or limited:
all jobs on a GPU share its full memory, first come first served.

A shared GPU helps when runs are short or use only part of a GPU: a run that
reserves a whole GPU blocks it for everyone else.

## Enabling it

1. Check that your SLURM has shards configured on the GPU nodes:

   ```bash
   sinfo -p <gpu partition> -o "%N %G"
   ```

   The GRES column must list `shard:` entries, e.g.
   `gpu:a100:4,shard:a100:40000`. If it lists only `gpu:`, shards are not
   available; leave `gpu.shard` empty. Requesting shards on such a cluster
   makes `sbatch` fail with "Invalid generic resource specification".

2. Choose how many shards a shared run requests. The number of shards per GPU
   is set by your cluster admin (shown by `sinfo` above, divided by the GPUs
   per node). For example, with 10000 shards per GPU, `shard: 3000` asks for
   about 30% of a GPU's compute. Your admin may have a policy for this amount.

3. Set it in `config.yaml` and restart the app:

   ```yaml
   gpu:
     shard: 3000
   ```

## What a run requests

| No. of GPUs | GPU Sharing | Job script requests | QE processes |
|---|---|---|---|
| 1 | Shared GPU (default once enabled) | `--gres=shard:<type>:<shard>` | 1 |
| 1 | Whole GPU | `--gres=gpu:<type>:1` | 1 |
| 2 or more | Whole GPU (fixed) | `--gres=gpu:<type>:N` | N |

`<type>` is omitted when GPU Type is "Any". QE still runs one MPI process per
GPU (`pw.x -nk N`). A shard is a share of a single GPU, so a run with 2 or
more GPUs always reserves whole GPUs.

## Changes by component

### Site configuration (`site_config.py`, `config.yaml`)

New setting `gpu.shard`:

| Value | Effect |
|---|---|
| empty (`""`, the default) | Whole GPUs only; the GPU Sharing field is hidden |
| a number, e.g. `3000` | Shared GPUs available, and the default in the form |

### Backend (`advQMcalc_test`)

| File | Change |
|---|---|
| `run_context.py` | `write_crystal_slurm_script(..., gpu_shard=None)`. With a shard amount and 1 GPU, writes `--gres=shard:<type>:<shard>`; otherwise `--gres=gpu:<type>:N` as before. A shard with more than 1 GPU is refused |
| `cli.py` | New option `--gpu-shard N` (requires `--gpu` and `--gpus 1`). Recorded in the run's `state.json` as `tasks.crystal.gpu = {type, count, shard}` |
| Batches | No change: the array job copies the first run's `#SBATCH` lines, so it inherits the shard request |

Without `--gpu-shard`, job scripts are identical to before.

### Web app

**New Calculation → SLURM Configuration**

- Field **GPU Sharing** next to No. of GPUs: *Shared GPU* or *Whole GPU*,
  with an info icon. Shown only when `gpu.shard` is set; enabled only when
  Use GPU is ticked.
- 1 GPU: the user chooses (default Shared). 2 or more: Whole GPU, fixed.

**Runs → ⚙ → Monitor**: the GPU line shows how the GPU was requested, e.g.
`Shared (3000 shards), a100` or `1 x a100 (whole)`.

## Trade-offs

| | Shared GPU | Whole GPU |
|---|---|---|
| Starts | Sooner: can start on a GPU that is partly busy | Waits for a completely free GPU |
| Speed | Full speed when alone on the GPU; slower when another job uses it at the same time | Always full speed |
| GPU memory | Shared with the other jobs; a run can fail with out-of-memory if another job uses a lot | Whole GPU memory |
| Fairness | Leaves the rest of the GPU to others | Blocks the GPU for others |

Guidance:

- Small structures and scf runs: Shared GPU.
- Large structures, long relax / vc-relax runs: Whole GPU.
- Structures whose QE memory estimate ("Estimated max dynamical RAM" in the
  output) is close to the GPU's memory: Whole GPU, or 2 or more GPUs.

A run that fails with out-of-memory on a shared GPU can be resubmitted as
Whole GPU.

## Multi-CIF batches

Every CIF in a batch requests the same thing. With Shared GPU, several CIFs of
a batch can run on the same GPU, so a batch runs more CIFs at once (up to Max
Structures Running at Once) without needing that many free GPUs.

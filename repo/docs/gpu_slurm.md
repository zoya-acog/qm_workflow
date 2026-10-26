# Shared GPUs (SLURM shards)

GPU runs can share a GPU with other jobs instead of reserving a whole GPU.
This follows a request from DevOps for the own3 cluster: a short QE run that
reserves a whole GPU (`--gres=gpu:1`) blocks it for everyone else, even when it
uses only part of it.

## Background

SLURM offers two ways to request a GPU:

| Request | Meaning |
|---|---|
| `--gres=gpu:<type>:N` | N whole GPUs, reserved for this job only |
| `--gres=shard:<type>:S` | A share of **one** GPU's compute (S shards). Other jobs may use the same GPU at the same time |

Shards divide a GPU's **compute only**. GPU memory is not divided or limited:
all jobs on a GPU share its full memory, first come first served.

Shards available per GPU on own3 (`sinfo -o "%N %G"`):

| GPU | Memory | Shards per GPU | `shard:5000` is about |
|---|---|---|---|
| RTX 4090 (own4, own5, own6) | 24 GB | 16374 | 31% of the GPU |
| RTX 5080 (own7–own10) | 16 GB | 10742 | 47% of the GPU |

## What changed

### Before

Every GPU run requested whole GPUs: `--gres=gpu:<type>:N`.

### After

| No. of GPUs | GPU Sharing | Job script requests | QE processes |
|---|---|---|---|
| 1 | Shared GPU (default) | `--gres=shard:<type>:5000` | 1 |
| 1 | Whole GPU | `--gres=gpu:<type>:1` | 1 |
| 2 or more | Whole GPU (fixed) | `--gres=gpu:<type>:N` | N |

`<type>` is omitted when GPU Type is "Any". QE still runs one MPI process per
GPU (`pw.x -nk N`); sharing only lets other jobs use the rest of that GPU.

A shard is a share of a single GPU, so a run with 2 or more GPUs always
reserves whole GPUs.

## Changes by component

### Site configuration (`site_config.py`)

New setting `gpu.shard`: the number of shards a shared GPU run requests.

| Value | Effect |
|---|---|
| empty (`""`) | Whole GPUs only; the GPU Sharing field is hidden |
| a number, e.g. `5000` | Shared GPUs available, and the default in the form |

own3 (main branch): `shard: 5000`, the amount DevOps asked for.

The cluster admin sets this, not the user: it only works where SLURM has shard
GRES configured (`sinfo -o "%N %G"` lists `shard:` on the GPU nodes), and the
amount is a cluster policy. On a cluster without shards, requesting them makes
`sbatch` fail with "Invalid generic resource specification".

### Backend (`advQMcalc_test`)

| File | Change |
|---|---|
| `run_context.py` | `write_crystal_slurm_script(..., gpu_shard=None)`. With a shard amount and 1 GPU, writes `--gres=shard:<type>:<shard>`; otherwise `--gres=gpu:<type>:N` as before. A shard with more than 1 GPU is refused |
| `cli.py` | New option `--gpu-shard N` (requires `--gpu` and `--gpus 1`; checked in `_validate_gpu_options`). Passed to the job script, and recorded in the run's `state.json` as `tasks.crystal.gpu = {type, count, shard}` |
| Batches (`_submit_array_batch`) | No change. The array script copies the `#SBATCH` lines of the first run's job script, so it inherits the shard request |

Existing command-line use is unchanged: without `--gpu-shard`, job scripts are
identical to before.

### Frontend (`app/`)

**New Calculation → SLURM Configuration**

- New field **GPU Sharing** next to No. of GPUs: *Shared GPU* or
  *Whole GPU*, with an info icon explaining both.
- Shown only when the site config sets `gpu.shard`; enabled only when Use GPU
  is ticked.
- Linked to **No. of GPUs**:
  - 1 GPU: the user chooses; the default is Shared.
  - 2 or more: switches to Whole GPU and is disabled.
  - Back to 1: returns to Shared if it was switched automatically; a user's own
    choice of Whole is kept.
- On Submit, the shard amount is passed only for Use GPU + 1 GPU + Shared.
- Reset restores the default.

**Runs → ⚙ → Monitor**

The GPU line shows how the GPU was requested, for example:

- `Shared (5000 shards), nvidia_geforce_rtx_5080`
- `1 x nvidia_geforce_rtx_4090 (whole)`
- `2 x any (whole)`

Runs from before this change show as whole GPUs, which is what they used.

## Trade-offs

| | Shared GPU | Whole GPU |
|---|---|---|
| Starts | Sooner: can start on a GPU that is partly busy | Waits for a completely free GPU |
| Speed | Full speed when alone on the GPU; slower when another job uses it at the same time | Always full speed |
| GPU memory | Shared with the other job; a run can fail with out-of-memory if the other job uses a lot | Whole GPU memory |
| Fairness | Leaves the rest of the GPU to others | Blocks the GPU for others |

Guidance:

- Typical structures and scf runs: Shared GPU. The Compound 1–3 test runs
  need about 3–7.5 GB of memory and 2–6 minutes, well within a 16–24 GB GPU.
- Large structures or long relax / vc-relax runs: Whole GPU.
- Very large structures with many k-points: 2 or more GPUs (always whole).

A run that fails with out-of-memory on a shared GPU can be resubmitted, or
resubmitted as Whole GPU.

## Multi-CIF batches

Every CIF in a batch requests the same thing. With Shared GPU, several CIFs of
a batch can run on the same GPU, so a batch runs more CIFs at once (up to Max
Structures Running at Once) without needing that many free GPUs.

## Verification

Generated job scripts (benzene, SLURM submission replaced by a stub):

| Case | Result |
|---|---|
| 1 GPU, Shared, RTX 5080 | `--gres=shard:nvidia_geforce_rtx_5080:5000`, `--ntasks=1` |
| 1 GPU, Shared, Any | `--gres=shard:5000`, `--ntasks=1` |
| 1 GPU, Whole, RTX 4090 | `--gres=gpu:nvidia_geforce_rtx_4090:1`, `--ntasks=1` |
| 2 GPUs | `--gres=gpu:2`, `--ntasks=2` |
| 2 GPUs + shard (command line) | Refused: "applies to exactly 1 GPU" |
| Batch of 2, Shared, max 2 at once | `array.slurm`: `--array=1-2%2`, `--gres=shard:5000` |

Form behaviour (New Calculation): the default is Shared; 2 GPUs switches to
Whole (disabled); back to 1 restores Shared; Submit passes the shard only for
1 GPU + Shared.

Still to check on own3 after `make rebuild`: one real shared GPU run, to
confirm SLURM accepts the shard request and QE runs normally.

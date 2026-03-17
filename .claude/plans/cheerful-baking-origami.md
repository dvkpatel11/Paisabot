# Plan: Split requirements into dev vs prod + add CPU torch for dev

## Context
The user's dev machine has no NVIDIA GPU (has AMD). The current `requirements.txt` has no torch at all, and `requirements-gpu.txt` adds CUDA torch + transformers for prod. This means FinBERT sentiment code can't run locally. The user wants:
1. A dev requirements file that includes torch **CPU-only** + transformers so sentiment works locally
2. A prod requirements file that includes torch **CUDA** + transformers for GPU inference
3. The dev script (`run_dev.sh`) and Dockerfile to install the correct one automatically

## Changes

### 1. `requirements.txt` — no change
Stays as-is: core deps, no torch, no transformers.

### 2. `requirements-dev.txt` — new file
```
-r requirements.txt

# CPU-only torch for local dev (no NVIDIA GPU required)
--extra-index-url https://download.pytorch.org/whl/cpu
torch==2.10.0+cpu

# ML / NLP
huggingface_hub==0.36.2
safetensors==0.7.0
tokenizers==0.22.2
transformers==4.57.6
```

### 3. `requirements-gpu.txt` — rename to `requirements-prod.txt`
Same content but rename so the naming convention is clear: `dev` vs `prod`.
```
-r requirements.txt

# CUDA torch + ML deps for GPU FinBERT inference
torch==2.10.0
huggingface_hub==0.36.2
hf-xet==1.4.2
safetensors==0.7.0
tokenizers==0.22.2
transformers==4.57.6

# NVIDIA CUDA libraries
cuda-bindings==12.9.4
cuda-pathfinder==1.4.2
nvidia-cublas-cu12==12.8.4.1
nvidia-cuda-cupti-cu12==12.8.90
nvidia-cuda-nvrtc-cu12==12.8.93
nvidia-cuda-runtime-cu12==12.8.90
nvidia-cudnn-cu12==9.10.2.21
nvidia-cufft-cu12==11.3.3.83
nvidia-cufile-cu12==1.13.1.3; sys_platform == "linux"
nvidia-curand-cu12==10.3.9.90
nvidia-cusolver-cu12==11.7.3.90
nvidia-cusparse-cu12==12.5.8.93
nvidia-cusparselt-cu12==0.7.1
nvidia-nccl-cu12==2.27.5; sys_platform == "linux"
nvidia-nvjitlink-cu12==12.8.93
nvidia-nvshmem-cu12==3.4.5; sys_platform == "linux"
nvidia-nvtx-cu12==12.8.90
triton==3.6.0; sys_platform == "linux"
```

### 4. `scripts/run_dev.sh` — add pip install step
After venv activation (line 59), add:
```bash
# ── Install dev dependencies ───────────────────────────────
info "Installing dev dependencies..."
pip install -q -r requirements-dev.txt
```

### 5. `Dockerfile` — update to use `requirements-prod.txt`
```dockerfile
COPY requirements.txt requirements-prod.txt ./
RUN pip install --no-cache-dir --prefix=/install -r requirements-prod.txt
```

### 6. `CLAUDE.md` — update setup instructions
Update the setup section to reference the new file names.

## Files to modify
- `requirements-dev.txt` — **new** (CPU torch + transformers)
- `requirements-gpu.txt` → `requirements-prod.txt` — **rename + same content**
- `scripts/run_dev.sh` — add `pip install -r requirements-dev.txt`
- `Dockerfile` — change `requirements-gpu.txt` → `requirements-prod.txt`
- `CLAUDE.md` — update setup docs

## Verification
1. `pip install -r requirements-dev.txt` on Windows/Mac (no GPU) — should succeed, torch CPU installs
2. `python -c "import torch; print(torch.__version__)"` — prints version, no CUDA error
3. `docker build .` — Dockerfile installs prod deps with CUDA torch
4. `./scripts/run_dev.sh --skip-docker --skip-migrate --skip-seed` — installs dev deps automatically

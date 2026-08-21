#!/usr/bin/env bash
# vLLM を1本立てる。$1=モデルディレクトリ名 $2=served名 以降=追加オプション
# GMU / MML / MNS を環境変数で上書きできる。
set -x
MODEL_DIR="$1"; SERVED="$2"; shift 2
GMU="${GMU:-0.60}"; MML="${MML:-8192}"; MNS="${MNS:-8}"
docker rm -f vllm_bench >/dev/null 2>&1
docker run -d --name vllm_bench --gpus all --ipc host \
  -v /home/team_a/models:/models \
  -e TORCH_CUDA_ARCH_LIST="12.1" \
  -p 8080:8000 \
  --entrypoint vllm \
  2026_internship_dgx-spark-serve_vllm:latest \
  serve "/models/$MODEL_DIR" \
    --served-model-name "$SERVED" \
    --port 8000 --host 0.0.0.0 \
    --max-model-len "$MML" --max-num-seqs "$MNS" \
    --gpu-memory-utilization "$GMU" \
    --trust-remote-code "$@"

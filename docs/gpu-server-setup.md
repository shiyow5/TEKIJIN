# GPU サーバー準備手順（チーム共有）

チームメンバーが GPU サーバー（DGX Spark、Tailscale 経由）で TEKIJIN を立ち上げるための手順。

**基本方針**: GPU を使う **vLLM は 1 本だけ共有**する。backend / frontend は各メンバーが
**別ポート**で立て、共有 vLLM と共有 Postgres を指す。2 本目の vLLM を立てると同じ 35B
モデルをもう 1 コピー VRAM に載せることになり OOM するので避ける。

> ローカル（GPU 不要・スタブ LLM）で動かすだけなら、この手順ではなく
> [README の「アプリの起動（開発）」](../README.md#アプリの起動開発) を参照。

## 全体像

```
[GPUホスト internship-dgx1 / Tailscale 100.118.131.67]
  ├─ 共有Postgres  tekijin_app_pg :15432   ← 管理者が1回準備（seed+embed済み）
  ├─ 共有vLLM      tekijin_vllm   :18080   ← 管理者が1回準備（GPUを使うのはこれだけ）
  ├─ メンバーA: backend :18000 / frontend :13000
  ├─ メンバーB: backend :18001 / frontend :13001
  └─ メンバーC: backend :18002 / frontend :13002   … 各自ユニークポート
```

ポートは 13000 番台（frontend）／ 18000 番台（backend）／ 15432（PG）／ 18080（vLLM）で、
**メンバー間で衝突させない**こと。

---

## パートA：管理者が1回だけやる「サーバー準備」

### Step 0. 前提を揃える

- ホストに SSH できる（`ssh team_a@internship-dgx1`）。
- モデルが `/home/team_a/models/` にある（LLM: `Qwen3.6-35B-A3B-NVFP4`、埋め込み:
  `Nemotron-3-Embed-1B-BF16`）。
- vLLM イメージがある（`docker images | grep vllm`）。
- モデル名・パス・イメージ名は環境依存。以下は現行 DGX の値なので自環境に合わせて読み替える。

### Step 1. チームを Tailscale に入れる（アクセス権）

- Tailscale 管理コンソールで各メンバーを**同じ Tailnet に招待**する。
- 必要なら ACL で `internship-dgx1` の 13000–13010 / 18000–18010 番台への到達を許可。
- これで各メンバーは `http://100.118.131.67:<port>` に到達できる（**外部インターネットには
  出ない**）。

### Step 2. 共有 Postgres を用意（seed＋embed 済みの 1 台）

```bash
docker run -d --name tekijin_app_pg --restart unless-stopped \
  -e POSTGRES_USER=tekijin -e POSTGRES_PASSWORD=tekijin -e POSTGRES_DB=tekijin \
  -p 15432:5432 pgvector/pgvector:pg16

cd ~/TEKIJIN
export TEKIJIN_DATABASE_URL=postgresql+psycopg://tekijin:tekijin@localhost:15432/tekijin
make seed                 # 合成フィクスチャ投入（社員40/Q150/A150/文書30）
make setup-ml embed       # 密ベクトルを一度だけ全件計算・格納
```

> 合成データなので、チームで **1 つの DB を共有**してよい（アプリの書き込みは `api_`
> 接頭辞で名前空間分離される）。データを汚したくない人だけ、自分用 PG を別ポート
> （例 `:15433`）・別コンテナ名で立てる。

### Step 3. 共有 vLLM を 1 本立てる（＝ GPU サーバーの本体）

```bash
docker run -d --name tekijin_vllm --restart unless-stopped \
  --gpus all --ipc host \
  -v /home/team_a/models:/models \
  -e TORCH_CUDA_ARCH_LIST="12.1" \
  -p 18080:8000 \
  --entrypoint vllm \
  2026_internship_dgx-spark-serve_vllm:latest \
  serve /models/Qwen3.6-35B-A3B-NVFP4 \
    --served-model-name Qwen3.6-35B-A3B-NVFP4 \
    --host 0.0.0.0 --port 8000 \
    --max-model-len 8192 --max-num-seqs 8 \
    --gpu-memory-utilization 0.60 --trust-remote-code \
    --reasoning-parser qwen3 --enable-auto-tool-choice \
    --tool-call-parser hermes --quantization modelopt
```

- **`--tool-call-parser hermes --enable-auto-tool-choice` は必須**。無いと C1（構造化出力）が
  400 で落ちる。
- ロードに約 4.5 分。`docker logs -f tekijin_vllm` で `Application startup complete` を待つ。
- **これが GPU を使う唯一のプロセス**。以降メンバーは全員ここを共有する。
- `scripts/serve_vllm.sh` は同等のヘルパー（既定は別名 `vllm_bench` / ポート 8080 なので、
  共有運用では上記のように名前・ポートを固定して起動する）。

### Step 4. 起動確認

```bash
curl -s http://localhost:18080/v1/models     # モデルが返れば OK
```

---

## パートB：各メンバーがやる「自分の backend / frontend を立てる」

GPU を食う vLLM は立てない。共有の `:18080` を指すだけ。

### Step 5. `.env` を用意（ユニークポート）

リポジトリ直下 `~/TEKIJIN/.env`（メンバーB の例）:

```bash
TEKIJIN_APP_ENV=development                 # プロトタイプなので development が正（#108/#173）
TEKIJIN_STRICT_DURABILITY=true              # 本番想定の耐久性ガードを有効化（#180）
TEKIJIN_LLM_BACKEND=vllm
TEKIJIN_LLM_BASE_URL=http://100.118.131.67:18080/v1        # ← 共有 vLLM
TEKIJIN_CHECKPOINTER_BACKEND=postgres
TEKIJIN_DATABASE_URL=postgresql+psycopg://tekijin:tekijin@100.118.131.67:15432/tekijin
TEKIJIN_MAX_CONCURRENT_RUNS=8               # バックプレッシャ（vLLM max-num-seqs と揃える）
TEKIJIN_CORS_ORIGINS=["http://100.118.131.67:13001"]      # ← 自分のフロントの URL
```

- `TEKIJIN_STRICT_DURABILITY=true` は、`app_env=development` のままでも「memory チェックポインタ
  や Postgres 接続失敗を起動時に拒否」する耐久性ガードを効かせる（#180）。開発中に手軽さを
  優先するなら外してよい。
- `TEKIJIN_CORS_ORIGINS` に**自分のフロントの URL** を入れ忘れると、ブラウザが API 呼び出しを
  ブロックする。

### Step 6. backend を自分のポートで起動（例 `:18001`）

```bash
cd ~/TEKIJIN
TEKIJIN_PORT=18001 TEKIJIN_VENV_PY=~/tekijin-bench/.venv/bin/python \
  nohup deploy/start_backend.sh >~/backend_b.log 2>&1 </dev/null &
curl -s http://localhost:18001/health        # {"status":"ok",...} を確認
```

- `deploy/start_backend.sh` は foreground で `exec uvicorn` する（`TEKIJIN_PORT` /
  `TEKIJIN_VENV_PY` で上書き可）。**末尾に他コマンドを付けない**（ssh が閉じて起動失敗する）。
- 常駐させたいなら [`deploy/tekijin-backend.service`](../deploy/tekijin-backend.service)
  （systemd, 自動再起動）を使う。
- 💡 **GPU メモリ節約**: backend は埋め込み（Nemotron）を読み込む。GPU を vLLM に空けたい
  なら backend 側だけ埋め込みを CPU に寄せられる → 起動時に `CUDA_VISIBLE_DEVICES=""` を付ける
  （vLLM は別コンテナなので影響しない）。

### Step 7. frontend を自分のポートで起動（例 `:13001`）

`NEXT_PUBLIC_API_BASE_URL` は**ビルド時**に焼き込む（実行時の環境変数では届かない）。

```bash
# node:20-slim コンテナでビルド（自分の backend URL を焼き込む）
docker run --rm -v ~/TEKIJIN/frontend:/app -w /app \
  -e NEXT_PUBLIC_API_BASE_URL=http://100.118.131.67:18001 \
  node:20-slim bash -c "npm ci && npm run build"

# 別名・別ポートで起動
docker run -d --name tekijin_frontend_b --restart unless-stopped \
  --network host -v ~/TEKIJIN/frontend:/app -w /app \
  node:20-slim bash -c "npx next start -p 13001"
```

### Step 8. アクセス

ブラウザで **`http://100.118.131.67:13001`**（Tailnet に入っているメンバーなら誰でも）。

---

## パートC：共有 GPU の運用ルール

- **ポート・コンテナ名は全員ユニークに**（13000 番台 / 18000 番台で衝突させない）。
- **vLLM は 1 本共有が原則**。2 本目は同じ 35B をもう 1 コピー載せて `GMU 0.60 × 2` ＝ OOM。
  どうしても必要なら両方 `--gpu-memory-utilization` を 0.35〜0.40 に下げて別ポートで。
- **重い GPU ジョブは 1 つずつ**。vLLM の再ロードや別の学習を同時に走らせると、過去に
  ssh ごとホストが落ちた実績がある。
- **スループットは共有**: 全員のリクエストが同じ vLLM の `max-num-seqs=8` に入るので、混雑時は
  順番待ち。アプリ側はバックプレッシャ（#180）で **503 + Retry-After** を返して落ちないように
  してある。
- **停止作法**: GPU を空けたい人は**自分の backend / frontend だけ**止める。**共有 vLLM / PG は
  勝手に止めない**（他の人が使っている）。全体を止めるのは管理者が意図的に行うときだけ。

---

## トラブルシュート

| 症状 | 原因・対処 |
|---|---|
| C1 で 400（tool_choice 系エラー） | vLLM に `--tool-call-parser hermes --enable-auto-tool-choice` が無い |
| backend が起動直後に落ちる | `tail -30 ~/backend_*.log` で import/設定エラーを確認。`app_env` と `STRICT_DURABILITY` の整合（#180）／DB 接続を確認 |
| フロントは開くが API が全部失敗 | backend の `TEKIJIN_CORS_ORIGINS` に自分のフロント URL が入っているか。frontend のビルド時 `NEXT_PUBLIC_API_BASE_URL` が自分の backend を指しているか |
| `http://100.118.131.67:130xx` に繋がらない | Tailnet に入っているか（ACL 含む）。ポート衝突していないか（`docker ps` / `ss -ltnp`） |
| 応答が遅い | 共有 vLLM が混雑（`max-num-seqs=8`）。落ちてはいない。順番待ち |

## 関連

- [README](../README.md) / [.env.example](../.env.example)
- [`deploy/tekijin-backend.service`](../deploy/tekijin-backend.service)・[`deploy/start_backend.sh`](../deploy/start_backend.sh)（自動再起動・#180/#181）
- 耐久性の設計は Issue #180、開発フローは [.claude/skills/dev-flow/SKILL.md](../.claude/skills/dev-flow/SKILL.md)

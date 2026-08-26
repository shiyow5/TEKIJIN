# Slack 連携用トンネル（期間限定・**2026-08-29 に必ず撤去**）

Slack の Events API / Interactivity は **Slack 側から** バックエンドを叩きに来る。
DGX 上のバックエンド（`:18000`）は Tailscale 経由でしか到達できないため、
そのままでは届かない。この文書は、その一時的な公開経路の張り方と、
**プロジェクト終了後の撤去手順**を定める。

> ## ⚠️ 撤去期限: **2026-08-29**
>
> このトンネルは**ハッカソン期間中だけの一時的な公開経路**である。
> 期間終了後に放置すると、**認証付きとはいえ社内向けAPIがインターネットに
> 露出したまま**になる。下の「[撤去手順](#撤去手順必ず実施)」を **2026-08-29 に必ず実施**すること。
>
> 撤去はコードを消すことではない。**動いているトンネルのプロセスを止め、
> Slack App を無効化し、`.env` から認証情報を消す**ところまでを指す。

---

## 何を公開するのか

`:18000`（バックエンド）**全体**が公開される。`/slack/*` だけを選んで公開することはできない。

認証が不要な口は次の5つだけで、それ以外は全て `require_principal` で保護されている
（`/inbox` `/questions` `/knowledge` `/notifications` `/messages/*` 等はトークンが必要）。

| 口 | 保護 |
|---|---|
| `POST /auth/login` | ブルートフォース制限（`SlidingWindowLimiter`） |
| `POST /auth/logout` | — |
| `GET /slack/oauth/callback` | 署名付き JWT state（`exp` + `purpose` 検証） |
| `POST /slack/events` | Slack 署名検証（HMAC-SHA256 + ±5分のリプレイ窓） |
| `POST /slack/interactivity` | 同上 ＋ 担当者本人チェック |

さらに **FastAPI の既定で以下も無認証で公開される**（`app.py` で `docs_url` 等を無効化していないため）。
2026-08-26 にトンネル越しに実測し、4つとも 200 が返ることを確認した。

| 口 | 中身 |
|---|---|
| `GET /health` | バージョン・環境名 |
| `GET /docs` / `GET /redoc` | Swagger UI / ReDoc |
| `GET /openapi.json` | **APIスキーマ全体**（全エンドポイントと型） |

データは出ないが、攻撃面の下調べには十分な情報である。気になる場合は
`create_app()` の `FastAPI(...)` に `docs_url=None, redoc_url=None, openapi_url=None` を
渡して塞ぐ（本番だけ塞ぐなら `app_env` で分岐する）。

**実質の攻撃面は `/auth/login` だけ**である。ここが要注意で、リポジトリには
**平文の既定パスワードが2つ**あり、どちらも実際に通ってしまう。

| 設定 | 既定値 | 出典 | 影響 |
|---|---|---|---|
| `TEKIJIN_ADMIN_PASSWORD` | `tekijin-admin` | `config.py:25` | 管理者。全社員へのなりすましが可能 |
| `TEKIJIN_DEMO_USER_PASSWORD` | `tekijin-demo` | `config.py:370` | **社員40名全員の実パスワード** |

**トンネルを張る前に両方とも差し替えること。** 総当たりの話ではなく、
値がリポジトリに書いてあるので、URL を知った人がそのままログインできてしまう。

社員側は `.env` を変えるだけでは効かない。ログインは `employees.password_hash` を見るので、
**DBの既存行を更新する必要がある**（`make seed` は全テーブルを TRUNCATE するので使わないこと）。

```bash
cd <repo>/backend && PYTHONPATH=src python3 - <<'EOF'
import secrets
from sqlalchemy import text
from tekijin.auth.passwords import hash_password
from tekijin.config import get_settings
from tekijin.data.db import get_engine
new = secrets.token_urlsafe(12)
with get_engine(get_settings().database_url).begin() as c:
    c.execute(text("UPDATE employees SET password_hash = :h"), {"h": hash_password(new)})
print("new demo password:", new)   # .env の TEKIJIN_DEMO_USER_PASSWORD も同じ値に揃える
EOF
```

`.env` 側も揃えておかないと、**再 seed したときにリポジトリ既定値へ戻る**。

**`TEKIJIN_AUTH_SECRET` も併せて確認すること。** 既定値は `dev-insecure-change-me`
（`config.py:24`）で、これは **JWT の署名鍵**である。既定のままだと誰でも管理者トークンを
オフラインで偽造できるので、パスワードを変えても意味がない。

```bash
python3 -c 'import secrets; print("TEKIJIN_AUTH_SECRET=" + secrets.token_urlsafe(48))'
```

あわせて **`TEKIJIN_STRICT_AUTH=true`** を `.env` に入れる。DGX は別の理由で
`app_env=development` のままなので（#108/#173）、この安全弁を明示しない限り
**既定パスワードのまま起動できてしまう**。`config.py:384-389` がまさにこの設定を想定している。

> **順序に注意**: `TEKIJIN_STRICT_AUTH=true` は `TEKIJIN_AUTH_SECRET` と
> `TEKIJIN_ADMIN_PASSWORD` の**両方**が既定値でないことを要求する（`app.py:37-46`）。
> どちらかが既定のまま立てると**バックエンドが起動を拒否する**。
> 上の3つ（auth_secret / admin_password / demo password）を先に済ませてから立てること。
> 起動しないからと言って `false` に戻すのは最悪手で、公開鍵で管理者JWTを偽造できる状態のまま公開される。

---

## ⚠️ `shiyow.dev` にホスト名を足さないこと

**このゾーンは既に壊れやすい状態にある。** 2026-08-26 に実測した結果:

```
SNI=shiyow.dev        Host=dm-ai.shiyow.dev  -> 403
SNI=dm-ai.shiyow.dev  Host=shiyow.dev        -> 200
正常系 SNI=Host=shiyow.dev                   -> 200
```

apex の証明書は SAN が `shiyow.dev` **のみ**で、`*.shiyow.dev` を覆っていない。
同一ゾーンのホストは Cloudflare の同じ IP を共有し、ブラウザは HTTP/2 の
コネクションを使い回すため、**証明書が要求ホストを覆っていない組み合わせで 403 になる**。

これは 2026-07-13 に `yuruwollet.shiyow.dev` を追加して他サイトが 403 になった
事故と**同じ機序**であり、その状態が現在も続いている。

ここに `tekijin.shiyow.dev` を足すと、**同じ事故を繰り返す**可能性が高い。
Slack のサーバはブラウザではないので Slack→バックエンドの通信自体は壊れないが、
**OAuth コールバックは人間のブラウザが踏む**ため、`shiyow.dev` を開いている
利用者が 403 に当たり得る。

**したがって、既存ゾーンにホスト名を追加しない方式を採る。**

---

## 方式: Quick Tunnel（`trycloudflare.com`）

`cloudflared` の Quick Tunnel を使う。

| | |
|---|---|
| 費用 | 無料 |
| Cloudflare アカウント | **不要**（＝個人に紐づかない） |
| ゾーン/DNSレコード | **触らない**（`shiyow.dev` に影響しない） |
| URL | `https://<ランダム>.trycloudflare.com` |
| 撤去 | **プロセスを止めるだけ**（残骸が一切残らない） |

**トレードオフ**: URL は**プロセスを再起動すると変わる**。変わったら
Slack App 側の3つの URL を登録し直す必要がある。期間が短く、撤去を確実にしたい
今回の要件では、この不便さより「**ゾーンに触らない・アカウントを作らない・
撤去がプロセス停止で完結する**」ことを優先する。

> 名前付きトンネル（固定URL）にする場合は **`shiyow.dev` とは別のゾーン**を用意すること。
> 既存ゾーンへの追加は上記の理由で不可。

---

## 手順

**順序が重要**。公開してから設定を直すのではなく、**中身を安全にしてから公開する**。
`.env` はプロセス起動時にしか読まれないので、書き換えたら必ず再起動する。

### 1. 認証情報を先に固める（**公開する前に**）

上の「何を公開するのか」の3点を `.env`（`/home/team_a/TEKIJIN/.env`）に入れる。

- `TEKIJIN_AUTH_SECRET`（JWT署名鍵。既定 `dev-insecure-change-me` は論外）
- `TEKIJIN_ADMIN_PASSWORD`（既定 `tekijin-admin`）
- 社員40名のパスワード（DBの `password_hash` を更新 ＋ `TEKIJIN_DEMO_USER_PASSWORD` を揃える）
- `TEKIJIN_STRICT_AUTH=true`

### 2. Slack App の認証情報も、この時点で入れておく

後回しにすると、URL 登録時に Slack の `url_verification` が
**署名検証で 401 になり、Request URL の登録自体が通らない**（`verify.py` は
`signing_secret` が空なら必ず False を返す）。URL が決まる前でも、
`REDIRECT_URI` 以外の4つは先に入れられる。

```bash
TEKIJIN_SLACK_CLIENT_ID=...
TEKIJIN_SLACK_CLIENT_SECRET=...
TEKIJIN_SLACK_SIGNING_SECRET=...
TEKIJIN_SLACK_BOT_TOKEN=xoxb-...
TEKIJIN_SLACK_FRONTEND_URL=http://100.118.131.67:13000
```

> `docker-compose.yml` の `env_file` 設定は**このホストには効かない**。
> DGX のバックエンドは compose ではなく `deploy/start_backend.sh` の uvicorn で動いており、
> 設定は `config.py` の `env_file=<リポジトリルート>/.env` 経由で読まれる。
> このファイルは `deploy/deploy.sh` の `rsync --exclude` 対象なので、**デプロイしても消えない**。

### 3. 再起動して、まだ非公開のうちに効いたことを確かめる

```bash
bash deploy/deploy.sh
# 旧既定パスワードが拒否されること（ローカルからで十分）
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:18000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@tekijin.local","password":"tekijin-admin"}'    # 401 を期待
```

**ここで 200 が返るなら、絶対に公開しないこと。**

### 4. `cloudflared` を DGX に置く

DGX の `team_a` は **sudo を持たない**ので、パッケージではなく静的バイナリを `~/bin` に置く。

```bash
mkdir -p ~/bin
curl -fsSL -o ~/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
chmod +x ~/bin/cloudflared
~/bin/cloudflared --version
```

> DGX Spark は **arm64**。`cloudflared-linux-amd64` を落とすと `Exec format error` になる。

### 5. トンネルを起動する（＝ここで初めて公開される）

`deploy/start_tunnel.sh` を使う。**`setsid` でセッションから切り離す**ので、
ssh を抜けてもデプロイが走っても生き残る。

```bash
cd /home/team_a/TEKIJIN
bash deploy/start_tunnel.sh
```

割り当てられた URL はスクリプトが表示する。後から拾い直すなら:

```bash
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' ~/tunnel.log | tail -1
```

### 6. `REDIRECT_URI` を確定して、もう一度再起動する

URL は起動して初めて決まるので、これだけは後追いになる。

```bash
TEKIJIN_SLACK_REDIRECT_URI=https://<host>/slack/oauth/callback
```

を `.env` に入れて `bash deploy/deploy.sh`。

### 7. Slack App に URL を登録する

| Slack App の設定 | 値 |
|---|---|
| OAuth & Permissions → Redirect URLs | `https://<host>/slack/oauth/callback` |
| Event Subscriptions → Request URL | `https://<host>/slack/events` |
| Interactivity & Shortcuts → Request URL | `https://<host>/slack/interactivity` |

必要なスコープ:

| 種別 | スコープ | 用途 |
|---|---|---|
| Bot | `groups:write` | ペア用プライベートチャンネルの作成・招待 |
| Bot | `chat:write` | メッセージ投稿 |
| Bot | `groups:history` | Slack→TEKIJIN の流入（`message.groups` の購読も要る） |
| User | `identity.basic` | 「Sign in with Slack」で誰が連携したかを知る |

> Bot スコープを入れても **Event Subscriptions → Subscribe to bot events に
> `message.groups` を追加**しないと、Slack 側の発言は流れてこない。

### 8. 疎通確認

```bash
curl -fsS -o /dev/null -w "%{http_code}\n" https://<host>/health                 # 200
curl -fsS -o /dev/null -w "%{http_code}\n" -X POST https://<host>/slack/events   # 401（署名なしなので正しい）
```

`401` が返れば署名検証が効いている。`404` ならバックエンドに Slack 実装が入っていない。

**鍵が合っているかまで確かめる**なら、正しい署名を作って投げる（署名の計算は DGX 上で行い、
Signing Secret を手元に降ろさないこと）。`url_verification` は Slack が Request URL 保存時に
投げるものそのものなので、これが通れば登録も通る。

```
signed url_verification -> 200（challenge がそのまま返る）
bad signature           -> 401
10分前のタイムスタンプ  -> 401（リプレイ窓）
```

## 撤去手順（**必ず実施**）

> ### 実施期限: **2026-08-29**
> 担当: PL（@shiyow5）／実施したら本文書の末尾にチェックを入れること。

### 1. トンネルを止める

```bash
ssh ootsuka
# ★ 必ず --url まで含める。--no-autoupdate が間に入るので `.*` が要る
pkill -f 'cloudflared tunnel .*--url http://127.0.0.1:18000'
pgrep -af 'cloudflared tunnel' || echo "停止済み"
```

**`pkill -f 'cloudflared tunnel'` のように対象を絞らないコマンドを使わないこと。**
このホストは共有機で、`-f` は全プロセスのコマンドライン全体に一致するため、
**他チームの cloudflared まで巻き込む**。必ず `--url <TARGET>` まで含めて特定する。

> `cloudflared tunnel --url ...` を**連続した文字列として**書くと、実際の argv は
> `tunnel --no-autoupdate --url ...` なので**一致せず、何も止まらない**。
> `pgrep`/`pkill` の `-f` は正規表現として扱われるので、間に `.*` を入れること。
> （`pgrep` をコマンドラインに直書きして確認すると、**自分自身のシェルのargvに
> パターン文字列が載って偽の一致になる**。スクリプト経由で確かめること。）

### 2. 外から到達できないことを確認する

```bash
curl -fsS --max-time 10 https://<host>/health && echo "❌ まだ生きている" || echo "✅ 到達しない"
```

### 3. Slack App を無効化する

- Slack App の管理画面で **Event Subscriptions / Interactivity を OFF**
- ワークスペースから **アプリをアンインストール**（Bot トークンが失効する）
- 不要なら **App 自体を削除**

### 4. `.env` から認証情報を消す

```bash
ssh ootsuka
cd /home/team_a/TEKIJIN
# バックアップは作業ツリーの外へ。`.gitignore` の `.env` は `.env.bak.*` を
# 覆わないので、チェックアウト内に置くと後の `git add .` で全部の秘密が乗る
cp .env ~/env.bak.$(date +%Y%m%d)
chmod 600 ~/env.bak.$(date +%Y%m%d)
sed -i '/^TEKIJIN_SLACK_/d' .env
grep -c '^TEKIJIN_SLACK_' .env || echo "✅ 削除済み"
```

> 消すのは `TEKIJIN_SLACK_*` だけでよい。差し替えたパスワードや `TEKIJIN_AUTH_SECRET` は
> **戻さないこと**（既定値に戻す理由がない）。

消したら**バックエンドを再起動**して、失効した値がメモリに残らないようにする。

### 5. バイナリを片付ける

```bash
rm -f ~/bin/cloudflared
```

### 6. ゾーンに何も残っていないことを確認する

Quick Tunnel は DNS レコードを作らないので**本来何も残らない**が、念のため
`shiyow.dev` に `tekijin` 系のレコードが無いことを確認する。あれば削除する。

### 7. トークンを失効させる

`.env` から消しても、**Slack 側で発行済みのトークンは生きている**。
手順3のアンインストールで Bot トークンは失効するが、
**Client Secret と Signing Secret は App を削除するまで有効**なので、
App を残す場合は管理画面で **rotate** すること。

---

## 撤去チェックリスト

実施日: ____________  実施者: ____________

- [ ] トンネルのプロセスを停止した（`pgrep -af cloudflared` が空）
- [ ] 外部から `https://<host>/health` に到達しないことを確認した
- [ ] Slack App の Event Subscriptions / Interactivity を OFF にした
- [ ] Slack ワークスペースからアプリをアンインストールした
- [ ] `.env` から `TEKIJIN_SLACK_*` を削除し、バックエンドを再起動した
- [ ] `~/bin/cloudflared` を削除した
- [ ] `shiyow.dev` に関連レコードが無いことを確認した
- [ ] Client Secret / Signing Secret を rotate または App ごと削除した

---

## 関連

- `deploy/start_tunnel.sh` — 起動スクリプト
- `deploy/deploy.sh` — `.env` を rsync 除外している箇所
- #388 / #390 / #398 / PR #414 — Slack 連携の実装
- `docs/gpu-server-setup.md` — DGX の全体構成

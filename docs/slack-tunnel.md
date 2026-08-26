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

あわせて **`TEKIJIN_STRICT_AUTH=true`** を `.env` に入れる。DGX は別の理由で
`app_env=development` のままなので（#108/#173）、この安全弁を明示しない限り
**既定パスワードのまま起動できてしまう**。`config.py:384-389` がまさにこの設定を想定している。

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

### 1. `cloudflared` を DGX に置く

DGX の `team_a` は **sudo を持たない**ので、パッケージではなく静的バイナリを
`~/bin` に置く。

```bash
ssh ootsuka
mkdir -p ~/bin
curl -fsSL -o ~/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
chmod +x ~/bin/cloudflared
~/bin/cloudflared --version
```

> DGX Spark は **arm64**。`cloudflared-linux-amd64` を落とすと `Exec format error` になる。

### 2. トンネルを起動する

`deploy/start_tunnel.sh` を使う。**`setsid` でセッションから切り離す**ので、
ssh を抜けてもデプロイが走っても生き残る。

```bash
cd /home/team_a/TEKIJIN
bash deploy/start_tunnel.sh
```

起動後、割り当てられた URL をログから拾う。

```bash
grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' ~/tunnel.log | tail -1
```

### 3. Slack App に URL を登録する

上で得た `https://<host>` を使い、Slack App の3箇所に設定する。

| Slack App の設定 | 値 |
|---|---|
| OAuth & Permissions → Redirect URLs | `https://<host>/slack/oauth/callback` |
| Event Subscriptions → Request URL | `https://<host>/slack/events` |
| Interactivity & Shortcuts → Request URL | `https://<host>/slack/interactivity` |

### 4. `.env` に認証情報を入れる

**`/home/team_a/TEKIJIN/.env`** に書く。このファイルは `deploy/deploy.sh` の
`rsync --exclude` 対象なので、**デプロイしても消えない**。

```bash
TEKIJIN_SLACK_CLIENT_ID=...
TEKIJIN_SLACK_CLIENT_SECRET=...
TEKIJIN_SLACK_SIGNING_SECRET=...
TEKIJIN_SLACK_BOT_TOKEN=xoxb-...
TEKIJIN_SLACK_REDIRECT_URI=https://<host>/slack/oauth/callback
TEKIJIN_SLACK_FRONTEND_URL=http://100.118.131.67:13000
```

> `docker-compose.yml` の `env_file` 設定は**このホストには効かない**。
> DGX のバックエンドは compose ではなく `deploy/start_backend.sh` の
> uvicorn で動いており、設定は `config.py` の
> `env_file=<リポジトリルート>/.env` 経由で読まれる。

### 5. バックエンドを再起動して反映する

`.env` はプロセス起動時に読まれるので、**再起動しないと反映されない**。

```bash
bash deploy/deploy.sh   # あるいは develop に何かマージする
```

### 6. 疎通確認

```bash
curl -fsS -o /dev/null -w "%{http_code}\n" https://<host>/health          # 200
curl -fsS -o /dev/null -w "%{http_code}\n" -X POST https://<host>/slack/events   # 401（署名なしなので正しい）
```

`401` が返れば署名検証が効いている。`200` や `500` が返るなら設定を疑うこと。

---

## 撤去手順（**必ず実施**）

> ### 実施期限: **2026-08-29**
> 担当: PL（@shiyow5）／実施したら本文書の末尾にチェックを入れること。

### 1. トンネルを止める

```bash
ssh ootsuka
pkill -f 'cloudflared tunnel'
pgrep -af cloudflared || echo "停止済み"
```

**`pkill cloudflared` のように対象を絞らないコマンドを使わないこと。**
このホストは共有機で、他チームのプロセスを巻き込む恐れがある。

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
cp .env .env.bak.$(date +%Y%m%d)     # 念のため
sed -i '/^TEKIJIN_SLACK_/d' .env
grep -c '^TEKIJIN_SLACK_' .env || echo "✅ 削除済み"
```

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

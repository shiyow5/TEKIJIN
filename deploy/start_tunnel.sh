#!/usr/bin/env bash
# Start the TEMPORARY Cloudflare Quick Tunnel that lets Slack reach the backend.
#
# ⚠️ REMOVE THIS BY 2026-08-29 — see docs/slack-tunnel.md ("撤去手順").
#    This publishes the whole backend port to the internet. It exists only for
#    the hackathon's Slack integration (#388 / #390 / #398).
#
# A Quick Tunnel is used on purpose: it needs no Cloudflare account and creates
# no DNS record, so it cannot repeat the 2026-07-13 incident where adding a host
# to the `shiyow.dev` zone made the zone's other sites return 403 (the apex cert
# covers only `shiyow.dev`, and browsers coalesce HTTP/2 connections across the
# zone's shared IP). Tearing it down is `pkill` — nothing is left behind.
# The cost is that the URL changes whenever this restarts.
#
# Detaching matters as much as starting: this is meant to survive both the ssh
# session that launched it AND a deploy. `setsid` gives it its own session, and
# `env -u RUNNER_TRACKING_ID` scrubs the tag the GitHub Actions runner puts on
# everything it spawns — without that, the runner's post-job cleanup kills it
# (the same trap `deploy/deploy.sh` documents for the backend, #210).
#
# Overridable via env:
#   TEKIJIN_TUNNEL_TARGET  what to expose      (http://127.0.0.1:18000)
#   TEKIJIN_TUNNEL_BIN     cloudflared path    ($HOME/bin/cloudflared)
#   TEKIJIN_TUNNEL_LOG     log destination     ($HOME/tunnel.log)
set -euo pipefail

TARGET="${TEKIJIN_TUNNEL_TARGET:-http://127.0.0.1:18000}"
BIN="${TEKIJIN_TUNNEL_BIN:-$HOME/bin/cloudflared}"
LOG="${TEKIJIN_TUNNEL_LOG:-$HOME/tunnel.log}"

log() { echo "[tunnel] $*"; }

if [ ! -x "$BIN" ]; then
  echo "[tunnel] cloudflared not found at ${BIN}" >&2
  echo "[tunnel] install it first — see docs/slack-tunnel.md step 1" >&2
  exit 1
fi

# Refuse to expose a port that nothing is serving: an empty tunnel looks alive
# in Slack's URL check and then 502s on every real event.
if ! curl -fsS -o /dev/null --max-time 5 "${TARGET}/health"; then
  echo "[tunnel] ${TARGET}/health did not answer — start the backend first" >&2
  exit 1
fi

# Only ever kill OUR tunnel. This is a SHARED host: a bare `pkill cloudflared`
# could take out another team's process.
if pgrep -f "cloudflared tunnel --url ${TARGET}" >/dev/null 2>&1; then
  log "an existing tunnel for ${TARGET} is running — stopping it first"
  pkill -f "cloudflared tunnel --url ${TARGET}" || true
  sleep 2
fi

log "exposing ${TARGET} (log: ${LOG})"
: >"$LOG"
setsid env -u RUNNER_TRACKING_ID \
  "$BIN" tunnel --no-autoupdate --url "$TARGET" >"$LOG" 2>&1 </dev/null &

# The assigned hostname only appears once the tunnel has registered, so poll for
# it rather than sleeping a fixed amount and hoping.
for _ in $(seq 1 30); do
  url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1 || true)
  if [ -n "${url:-}" ]; then
    log "URL: ${url}"
    log ""
    log "Set these three in the Slack App, then restart the backend:"
    log "  Redirect URL     ${url}/slack/oauth/callback"
    log "  Events           ${url}/slack/events"
    log "  Interactivity    ${url}/slack/interactivity"
    log ""
    log "⚠️ Temporary — tear this down by 2026-08-29 (docs/slack-tunnel.md)."
    exit 0
  fi
  sleep 1
done

echo "[tunnel] no URL appeared within 30s — check ${LOG}" >&2
exit 1

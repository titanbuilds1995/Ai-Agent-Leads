#!/usr/bin/env bash
# One-shot (and re-runnable) VPS setup for the Titan lead engine. Run as the user that will own the cron jobs:
#   TITAN_DIR=$HOME/Ai-Agent-Leads BRANCH=main bash deploy/bootstrap_vps.sh
# It never touches .env or secrets/ — copy those over with scp first (see deploy/DEPLOY.md).
set -euo pipefail

TITAN_DIR="${TITAN_DIR:-$HOME/Ai-Agent-Leads}"
REPO="${REPO:-https://github.com/titanbuilds1995/Ai-Agent-Leads.git}"
BRANCH="${BRANCH:-main}"
TITAN_USER="${TITAN_USER:-$(id -un)}"

step() { printf '\n==> %s\n' "$*"; }

step "python 3.11"
if ! command -v python3.11 >/dev/null; then
  if command -v apt-get >/dev/null; then
    sudo apt-get update -y && sudo apt-get install -y software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update -y
    sudo apt-get install -y python3.11 python3.11-venv
  elif command -v dnf >/dev/null; then
    sudo dnf install -y python3.11
  else
    echo "install python3.11 manually, then re-run" >&2; exit 1
  fi
fi
python3.11 --version
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh || true
export PATH="$HOME/.local/bin:$PATH"

step "repo at $TITAN_DIR ($BRANCH)"
if [ -d "$TITAN_DIR/.git" ]; then
  git -C "$TITAN_DIR" fetch origin "$BRANCH" && git -C "$TITAN_DIR" checkout "$BRANCH" && git -C "$TITAN_DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO" "$TITAN_DIR"
fi
cd "$TITAN_DIR"

step "venv + deps + db"
make setup
mkdir -p logs raw reports secrets && chmod 700 secrets

step "tests"
make test

if [ ! -s .env ] || ! grep -q '^APIFY_TOKEN=.\+' .env; then
  echo
  echo "!! .env is the template copy. scp your filled .env and secrets/ from the Mac before enabling cron:"
  echo "   scp .env $TITAN_USER@<vps>:$TITAN_DIR/.env && scp -r secrets $TITAN_USER@<vps>:$TITAN_DIR/"
fi

step "systemd unit (webhook)"
sed -e "s#__TITAN_DIR__#$TITAN_DIR#g" -e "s#__TITAN_USER__#$TITAN_USER#g" deploy/titan-webhook.service | sudo tee /etc/systemd/system/titan-webhook.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now titan-webhook
sleep 2
curl -fsS http://127.0.0.1:8080/health && echo

step "cron"
if [ "${INSTALL_CRON:-no}" = "yes" ]; then make install-cron; else echo "skipped (INSTALL_CRON=yes to write the crontab block)"; fi
crontab -l 2>/dev/null | grep -A7 'titan lead engine' || true

echo
echo "done. next: nginx + TLS (deploy/DEPLOY.md step 3), then set the Twilio inbound URL."

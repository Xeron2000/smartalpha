#!/usr/bin/env bash
# Install smartalpha on a server (Ubuntu/Debian-ish). Run as the deploy user.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Xeron2000/smartalpha.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/smartalpha}"
BRANCH="${BRANCH:-main}"

echo "==> install dir: $INSTALL_DIR"
if [ -d "$INSTALL_DIR/.git" ]; then
  git -C "$INSTALL_DIR" fetch --all
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

export PATH="$HOME/.local/bin:$PATH"
uv sync

mkdir -p data/logs
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> created .env from example — fill HELIUS_API_KEY (and optional GMGN cookie)"
fi

echo "==> self-check"
uv run smartalpha self-check

echo "==> done. Next:"
echo "  1) edit $INSTALL_DIR/.env"
echo "  2) optional: uv run smartalpha gmgn-cookie import '...'"
echo "  3) uv run smartalpha auto-discover --min-gain 200 --limit 30"
echo "  4) systemd: see deploy/smartalpha-watch.service"
echo "     or: PYTHONUNBUFFERED=1 uv run smartalpha watch-launches >> data/logs/watch_launches.log 2>&1"

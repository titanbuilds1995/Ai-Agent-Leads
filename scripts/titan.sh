#!/bin/bash
# Rodrigo's one-word launcher (2026-11-14). In Terminal:
#   titan           open Claude Code in the project
#   titan fb        start / restart the Facebook investor job
#   titan tortuga   start / continue the Tortuga cash buyer job
#   titan status    how far both jobs got
cd ~/Ai-Agent-Leads || exit 1
if ! git pull --no-edit -q 2>/dev/null; then
  stuck=$(git diff --name-only --diff-filter=U)
  if [ "$stuck" = "CLAUDE.md" ]; then
    git checkout --theirs CLAUDE.md && git add CLAUDE.md && git commit --no-edit -q
  else
    git merge --abort 2>/dev/null
    echo "(couldn't update to the newest code, carrying on with what's here)"
  fi
fi
CLAUDE=$(command -v claude)
for p in ~/.local/bin/claude ~/.claude/local/claude /opt/homebrew/bin/claude /usr/local/bin/claude; do
  [ -z "$CLAUDE" ] && [ -x "$p" ] && CLAUDE="$p"
done
[ -z "$CLAUDE" ] && { echo "Claude Code isn't installed: run  curl -fsSL https://claude.ai/install.sh | bash"; exit 1; }
case "$1" in
  "")      exec "$CLAUDE" ;;
  status)  make buyers-status ;;
  *)       f="docs/prompts/start/$1.txt"
           [ -f "$f" ] || { echo "No job called '$1'. Jobs: $(ls docs/prompts/start | sed 's/\.txt//' | tr '\n' ' ')"; exit 1; }
           start=$(date +%s)
           "$CLAUDE" --chrome "$(cat "$f")" && exit 0
           # an older Claude Code without --chrome fails at once: start it plain (then type /chrome inside)
           [ $(( $(date +%s) - start )) -lt 5 ] && exec "$CLAUDE" "$(cat "$f")" ;;
esac

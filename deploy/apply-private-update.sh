#!/usr/bin/env bash
# Small update for an existing installation. Keeps settings and research data.
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run this script as root.'; exit 1; }
app=/opt/deepresearch
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
[[ -f "$app/server.py" && -x "$app/.venv/bin/python" ]] || { echo 'Existing installation not found.'; exit 1; }
command -v tailscale >/dev/null || { echo 'Install Tailscale first.'; exit 1; }
tailscale status --json | "$app/.venv/bin/python" -c '
import json,sys
s=json.load(sys.stdin)
if s.get("BackendState") != "Running" or not s.get("Self",{}).get("DNSName"):
    sys.exit("Finish tailscale up login first.")
'
curl --fail --silent --show-error http://127.0.0.1:8000/api/health | "$app/.venv/bin/python" -c '
import json,sys
s=json.load(sys.stdin)
if s.get("app") != "open-deep-research-local" or s.get("running") is not False:
    sys.exit("Wait for research tasks to finish before updating.")
'
"$app/.venv/bin/python" -c 'import ast,pathlib,sys; ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))' "$source_dir/server.py"
install -d -m 700 "$app/.data/deploy-backups"
backup=$(mktemp -d "$app/.data/deploy-backups/private-XXXXXXXX")
files=(server.py local_config.py research_runtime.py research_store.py research_budget.py user_accounts.py client/index.html client/app.js client/workspace.js client/style.css client/accounts.css client/accounts.js client/login.html client/login.js client/register.html client/register.js client/mark.svg client/productivity.js client/productivity.css)
for name in "${files[@]}"; do
  mkdir -p -- "$backup/$(dirname -- "$name")"
  if [[ -f "$app/$name" ]]; then cp -p "$app/$name" "$backup/$name"; fi
done
"$app/.venv/bin/python" - "$app" "$backup" <<'PY'
import sqlite3,sys
from pathlib import Path
for name in ('research.sqlite3','research-checkpoints.sqlite3'):
    path=Path(sys.argv[1])/'.data'/name
    if path.exists():
        with sqlite3.connect(path) as source, sqlite3.connect(Path(sys.argv[2])/name) as target:
            source.backup(target)
PY
override=/etc/systemd/system/deepresearch.service.d/tailscale.conf
[[ ! -f "$override" ]] || cp -p "$override" "$backup/tailscale.conf"
[[ ! -f "$app/deploy/configure-tailscale.sh" ]] || cp -p "$app/deploy/configure-tailscale.sh" "$backup/configure-tailscale.sh"
rollback() {
  trap - ERR
  echo "Update failed. Restoring previous application from $backup"
  for name in "${files[@]}"; do
    if [[ -f "$backup/$name" ]]; then cp -p "$backup/$name" "$app/$name"; else rm -f -- "$app/$name"; fi
  done
  if [[ -f "$backup/tailscale.conf" ]]; then
    cp -p "$backup/tailscale.conf" "$override"
  else
    rm -f -- "$override"
  fi
  if [[ -f "$backup/configure-tailscale.sh" ]]; then
    cp -p "$backup/configure-tailscale.sh" "$app/deploy/configure-tailscale.sh"
  else
    rm -f -- "$app/deploy/configure-tailscale.sh"
  fi
  systemctl daemon-reload
  systemctl restart deepresearch
  echo 'Previous configuration restored. Send the error output for troubleshooting.'
  exit 1
}
trap rollback ERR
systemctl stop deepresearch
for name in "${files[@]}"; do
  install -o deepresearch -g deepresearch -m 644 "$source_dir/$name" "$app/$name.update.tmp"
  mv -f -- "$app/$name.update.tmp" "$app/$name"
done
install -d -o deepresearch -g deepresearch -m 750 "$app/deploy"
install -o deepresearch -g deepresearch -m 644 "$source_dir/deploy/configure-tailscale.sh" "$app/deploy/configure-tailscale.sh"
systemctl start deepresearch
curl --fail --silent --show-error --retry 15 --retry-connrefused --retry-delay 1 http://127.0.0.1:8000/api/health
bash "$app/deploy/configure-tailscale.sh"
trap - ERR
printf 'Update complete. Backup: %s\n' "$backup"

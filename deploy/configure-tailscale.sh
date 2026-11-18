#!/usr/bin/env bash
# Run only after Tailscale login. The application remains on server loopback.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run this script as root.'; exit 1; }
app=/opt/deepresearch
command -v tailscale >/dev/null || { echo 'Install Tailscale and run tailscale up first.'; exit 1; }
host=$(tailscale status --json | "$app/.venv/bin/python" -c '
import json, sys
s=json.load(sys.stdin)
if s.get("BackendState") != "Running":
    sys.exit("Run tailscale up and finish the login first.")
print(s["Self"]["DNSName"].rstrip("."))
')
cd "$app"
"$app/.venv/bin/python" -c 'import server,sys; print("Private hostname:",server.tailscale_hostname(sys.argv[1]))' "$host"
curl --fail --silent --show-error http://127.0.0.1:8000/api/health | "$app/.venv/bin/python" -c '
import json,sys
if json.load(sys.stdin).get("running"):
    sys.exit("A research task is running. Wait for it to finish before changing access.")
'
install -d -m 755 /etc/systemd/system/deepresearch.service.d
cat > /etc/systemd/system/deepresearch.service.d/tailscale.conf <<EOF
[Service]
ExecStart=
ExecStart=$app/.venv/bin/python $app/server.py --port 8000 --tailscale-host $host
EOF
systemctl daemon-reload
systemctl restart deepresearch
curl --fail --silent --show-error --retry 15 --retry-connrefused --retry-delay 1 http://127.0.0.1:8000/api/health
curl --fail --silent --show-error -H "Host: $host" http://127.0.0.1:8000/login --output /dev/null
code=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
  -X PATCH -H "Host: $host" -H "Origin: https://$host" -H 'Content-Type: application/json' \
  --data '{"pinned":true}' http://127.0.0.1:8000/api/runs/tailscale-connection-check/pin)
[[ "$code" == 401 ]] || { echo "Private-origin/login check failed: HTTP $code"; exit 1; }
printf '\nBackend configured. Next run: tailscale serve --bg http://127.0.0.1:8000\n'
printf 'If asked to enable HTTPS, open the displayed link in your browser and approve it.\n'
printf 'Phone address after Serve is ready: https://%s/\n' "$host"
printf 'Use Serve only. Do not enable Funnel (public access).\n'

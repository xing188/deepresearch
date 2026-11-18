#!/usr/bin/env bash
# Initial installation on a new Ubuntu 24.04 server, from a release archive.
set -euo pipefail
if [[ $(id -u) -ne 0 ]]; then
  echo 'Run with sudo: sudo bash deploy/install-ubuntu.sh /path/to/deepresearch-release.tar.gz'
  exit 1
fi
archive=${1:?Pass the absolute path to the project release archive.}
target=/opt/deepresearch
if [[ -e "$target/server.py" ]]; then
  echo 'An installation already exists. Update and backup must be handled separately.'
  exit 1
fi
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv ca-certificates curl fonts-noto-cjk
id deepresearch >/dev/null 2>&1 || useradd --system --home-dir "$target" --shell /usr/sbin/nologin deepresearch
install -d -o deepresearch -g deepresearch -m 750 "$target"
tar -xzf "$archive" --directory "$target" --no-same-owner
install -d -o deepresearch -g deepresearch -m 700 "$target/.data"
if [[ ! -f "$target/.data/settings.env" ]]; then
  cp "$target/.env.example" "$target/.data/settings.env"
fi
chmod 600 "$target/.data/settings.env"
chown -R deepresearch:deepresearch "$target"
sudo -u deepresearch python3 -m venv "$target/.venv"
sudo -u deepresearch "$target/.venv/bin/python" -m pip install --disable-pip-version-check -r "$target/requirements.txt"
sudo -u deepresearch "$target/.venv/bin/python" -m pip check
install -m 644 "$target/deploy/deepresearch.service" /etc/systemd/system/deepresearch.service
systemctl daemon-reload
systemctl enable --now deepresearch
sleep 3
curl --fail --silent --show-error http://127.0.0.1:8000/api/health
printf '\nInstallation ready on server loopback. HTTPS/domain access is configured separately.\n'

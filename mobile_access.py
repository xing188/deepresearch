"""Password-protected access from a trusted local network."""
from __future__ import annotations

import base64
import ipaddress
import json
from pathlib import Path
import secrets
import subprocess

ACCESS_FILE = Path(__file__).resolve().parent / '.data' / 'mobile-access.json'


def credentials() -> dict:
    if ACCESS_FILE.exists():
        return json.loads(ACCESS_FILE.read_text(encoding='utf-8'))
    ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {'username': 'research', 'password': secrets.token_urlsafe(12)}
    ACCESS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


def authorized(header: str, access: dict) -> bool:
    try:
        scheme, encoded = header.split(' ', 1)
        if scheme.lower() != 'basic':
            return False
        raw = base64.b64decode(encoded, validate=True)
        expected = (access['username'] + ':' + access['password']).encode('utf-8')
        return secrets.compare_digest(raw, expected)
    except (ValueError, UnicodeError):
        return False


def local_peer(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_ip(value: str) -> str:
    ip = ipaddress.IPv4Address(value)
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        raise ValueError('Please use the private IPv4 address of your Wi-Fi or Ethernet adapter.')
    return str(ip)


def discover_ip() -> str:
    command = "Get-NetIPConfiguration | Where-Object { $_.NetAdapter.HardwareInterface -and $_.IPv4DefaultGateway } | ForEach-Object { $_.IPv4Address.IPAddress } | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=20)
    if result.returncode != 0 or not result.stdout.strip():
        raise ValueError('Cannot detect your Wi-Fi address. Connect Wi-Fi and try again.')
    values = json.loads(result.stdout)
    if isinstance(values, str):
        values = [values]
    valid = []
    for value in values:
        try:
            valid.append(validate_ip(value))
        except ValueError:
            pass
    if not valid:
        raise ValueError('No usable local Wi-Fi/Ethernet IPv4 address was found.')
    return valid[0]


def show_instructions(ip: str, port: int, access: dict):
    text = (f"手机访问深研\n\n手机和电脑连接同一个 Wi-Fi。\n网址：http://{ip}:{port}/\n"
            f"用户名：{access['username']}\n密码：{access['password']}\n\n"
            "电脑保持开机、项目保持运行。以后双击 start-mobile.bat 启动。\n"
            "若无法连接，右键 allow-mobile.bat 选择以管理员身份运行，放行局域网访问。\n"
            "Wi-Fi 地址变化后，以启动窗口显示的新网址为准。只在可信 Wi-Fi 使用。\n")
    ACCESS_FILE.with_name('mobile-access.txt').write_text(text, encoding='utf-8')
    print(f'\nPhone URL: http://{ip}:{port}/\nUsername: {access["username"]}\nPassword: {access["password"]}\n', flush=True)
    print('Connect the phone to the same Wi-Fi. Keep this computer and server running.\nThe password is saved in .data/mobile-access.json.\n', flush=True)

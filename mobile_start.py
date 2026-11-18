"""Double-click launcher that reuses an existing mobile service."""
import json
import sys
from urllib.request import urlopen
from urllib.error import URLError
import webbrowser

import mobile_access


def main():
    try:
        with urlopen('http://127.0.0.1:8000/api/health', timeout=2) as response:
            health = json.load(response)
    except (URLError, TimeoutError, ValueError):
        health = {}
    if health.get('app') == 'open-deep-research-local':
        if health.get('lan_enabled'):
            mobile_access.show_instructions(health['lan_ip'], 8000, mobile_access.credentials())
            webbrowser.open('http://127.0.0.1:8000/')
            input('The server is already running. Press Enter to close this helper window. ')
            return
        raise ValueError('A local-only server is already running. Close its server window first, then start-mobile.bat again.')
    ip = mobile_access.discover_ip()
    sys.argv = [sys.argv[0], '--lan-ip', ip, '--open']
    import server
    server.main()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print('\n' + str(exc), flush=True)
        raise SystemExit(1)

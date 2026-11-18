"""Build only the files needed to add Tailscale access to an existing install."""
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / '.data/deploy/deepresearch-private-access.tar.gz'
FILES = ['server.py', 'local_config.py', 'research_runtime.py', 'research_store.py', 'research_budget.py', 'user_accounts.py', 'deploy/configure-tailscale.sh', 'deploy/apply-private-update.sh'] + [f'client/{name}' for name in ['index.html','app.js','workspace.js','style.css','accounts.css','accounts.js','login.html','login.js','register.html','register.js','mark.svg','productivity.js','productivity.css']]


def build():
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(TARGET, 'w:gz') as archive:
        for name in FILES:
            source = ROOT / name
            if name.endswith('.sh'):
                assert b'\r\n' not in source.read_bytes(), f'{name} must use LF line endings'
            archive.add(source, arcname=name, recursive=False)
    with tarfile.open(TARGET) as archive:
        assert archive.getnames() == FILES
    print(f'Prepared {TARGET.name}: {len(FILES)} files, {TARGET.stat().st_size} bytes; no credentials or research data.')


if __name__ == '__main__':
    build()

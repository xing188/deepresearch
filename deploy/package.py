"""Build a source-only deployment archive, excluding credentials and local data."""
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / '.data' / 'deploy' / 'deepresearch-release.tar.gz'
FILES = ['user_accounts.py','server.py','mobile_access.py','local_config.py','connection_checks.py',
         'research_budget.py','research_documents.py','research_export.py',
         'research_plan.py','research_runtime.py','research_store.py',
         'requirements.txt','.env.example','README.md']
DIRECTORIES = ['client','deploy','open-deep-research/src/open_deep_research']


def build():
    TARGET.parent.mkdir(parents=True,exist_ok=True)
    paths = [ROOT/name for name in FILES]
    for folder in DIRECTORIES:
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and not set(p.parts) & {'__pycache__','.git','.venv','.data','.setup'} and p.suffix != '.pyc')
    paths.extend(p for p in (ROOT/'open-deep-research').glob('LICENSE*') if p.is_file())
    with tarfile.open(TARGET,'w:gz') as archive:
        for path in sorted(set(paths)):
            archive.add(path,arcname=path.relative_to(ROOT).as_posix(),recursive=False)
    with tarfile.open(TARGET) as archive:
        names = archive.getnames()
        assert not any('/.env' in n or '.data/' in n or '.venv/' in n or 'cloud_ed25519' in n for n in names)
        assert all(not n.startswith('/') and '..' not in Path(n).parts for n in names)
    print(f'Prepared {TARGET.name}: {len(names)} files, {TARGET.stat().st_size} bytes; no credentials or research data.')


if __name__ == '__main__':
    build()

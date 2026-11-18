"""Compatibility entry point for users running python app.py."""

import os
import sys
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    interpreter = root / ".venv" / "Scripts" / "python.exe"
    if interpreter.exists() and Path(sys.prefix).resolve() != (root / ".venv").resolve():
        os.execv(str(interpreter), [str(interpreter), str(root / "server.py"), *sys.argv[1:]])
    else:
        from server import main
        main()

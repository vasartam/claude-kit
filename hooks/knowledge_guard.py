#!/usr/bin/env python3
"""Заглушка на один выпуск: хук слит с context-guard — hooks/context-guard.py рядом (CHANGELOG.md).
Держит живые сессии, у которых в снимке хуков ещё старый путь; нет нового файла — молча выход 0."""
import os
import subprocess
import sys
from pathlib import Path

new = Path(__file__).resolve().parent / "context-guard.py"
if new.is_file():
    args = [sys.executable, str(new), *sys.argv[1:]]
    if os.name == "nt":
        # На Windows os.execv запускает новый процесс, а старый сразу выходит с кодом 0.
        sys.exit(subprocess.call(args))
    try:
        os.execv(sys.executable, args)
    except OSError:
        pass

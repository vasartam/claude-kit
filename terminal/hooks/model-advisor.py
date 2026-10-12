#!/usr/bin/env python3
"""Заглушка на один выпуск: хук переехал в hooks/model-advisor.py общего слоя (CHANGELOG.md).
Держит живые сессии, у которых в снимке хуков ещё старый путь; нет нового файла — молча выход 0."""
import os
import subprocess
import sys
from pathlib import Path

new = Path(__file__).resolve().parents[2] / "hooks" / "model-advisor.py"
if new.is_file():
    args = [sys.executable, str(new), *sys.argv[1:]]
    if os.name == "nt":
        # На Windows os.execv запускает новый процесс, а старый сразу выходит с кодом 0.
        sys.exit(subprocess.call(args))
    try:
        os.execv(sys.executable, args)
    except OSError:
        pass

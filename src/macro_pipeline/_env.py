"""Minimal .env loader shared by every entrypoint (avoids a python-dotenv dependency).

Real environment variables win over .env (so CI / GitHub Actions Secrets take
precedence — ADR-001). Lines are `KEY=value`; blanks and `#` comments are skipped.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

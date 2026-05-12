"""`python -m code2e` 진입점."""

import os
from pathlib import Path


def _load_dotenv() -> None:
    for base in (Path.cwd(), Path(__file__).resolve().parents[2]):
        env_path = base / ".env"
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
        return


_load_dotenv()

from code2e.cli.app import main  # noqa: E402

if __name__ == "__main__":
    main()

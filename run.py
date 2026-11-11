"""Prepare the local environment and start the Streamlit dashboard."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"
ENV_FILE = PROJECT_ROOT / ".env"


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def existing_venv() -> Path:
    candidates = (PROJECT_ROOT / ".venv", PROJECT_ROOT / "venv")
    return next(
        (path for path in candidates if venv_python(path).is_file()),
        candidates[0],
    )


def prepare_environment() -> Path:
    """Create the project venv and install dependencies when requirements change."""
    venv_dir = existing_venv()
    python = venv_python(venv_dir)
    if not python.is_file():
        print("Creating the project virtual environment...")
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=PROJECT_ROOT,
            check=True,
        )

    requirements_hash = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    marker = venv_dir / ".requirements.sha256"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != requirements_hash:
        print("Installing project dependencies (only needed on first run or after updates)...")
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            cwd=PROJECT_ROOT,
            check=True,
        )
        marker.write_text(requirements_hash, encoding="utf-8")

    if not ENV_FILE.exists():
        shutil.copy2(ENV_EXAMPLE, ENV_FILE)
        print("Created .env from .env.example. Add optional API keys there to fetch those sources.")
    return python


def main() -> int:
    try:
        python = prepare_environment()
        return subprocess.run(
            [
                str(python),
                "-m",
                "streamlit",
                "run",
                "src/dashboard/app.py",
            ],
            cwd=PROJECT_ROOT,
            check=False,
        ).returncode
    except subprocess.CalledProcessError as error:
        print(
            f"Setup command failed with exit code {error.returncode}. "
            "Review the output above and try again.",
            file=sys.stderr,
        )
        return error.returncode
    except OSError as error:
        print(f"Could not start the dashboard: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

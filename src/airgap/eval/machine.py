"""What a run ran on: the machine, the Python, the packages and the git commit."""

from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

# The checkout this package was installed from (uv installs it editable).
REPO_ROOT = Path(__file__).parents[3]


def _cpu() -> str:
    try:
        if platform.system() == "Darwin":
            return subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        if platform.system() == "Linux":
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    return platform.processor() or platform.machine()


def _git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def git_state(root: Path) -> dict[str, Any] | None:
    commit = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain")
    if commit is None or status is None:
        return None
    return {"commit": commit.strip(), "dirty": bool(status.strip())}


def machine(root: Path = REPO_ROOT) -> dict[str, Any]:
    lock = root / "uv.lock"
    system = platform.system()
    return {
        "platform": platform.platform(),
        "os_version": platform.mac_ver()[0] if system == "Darwin" else platform.version(),
        "cpu": _cpu(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "airgap_version": metadata.version("dualprivilege-airgap"),
        "uv_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None,
        "packages": dict(
            sorted((d.metadata["Name"].lower(), d.version) for d in metadata.distributions())
        ),
        "git": git_state(root),
    }


def run_meta() -> dict[str, Any]:
    """Saved beside paid results, so a later reader knows what produced them."""
    return machine() | {"saved_utc": datetime.now(UTC).isoformat(timespec="seconds")}

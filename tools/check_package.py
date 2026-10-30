"""Build and smoke-test the installed distribution outside the source checkout."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

COLLAB_EXTRA_MODULES = frozenset(
    {
        "llm_bus.collab.daemon",
        "llm_bus.collab.transport",
        "llm_bus.collab.pairing",
        "llm_bus.collab.delivery",
    }
)


def application_modules(source: Path) -> list[str]:
    """Inventory modules and package initializers without executing entrypoints."""
    modules: set[str] = set()
    for path in source.rglob("*.py"):
        relative = path.relative_to(source).with_suffix("")
        if relative.name == "__main__":
            continue
        if relative.name == "__init__":
            relative = relative.parent
        modules.add(".".join(relative.parts))
    if not modules:
        raise ValueError("No application modules found for package check")
    return sorted(modules)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    if uv is None:
        raise FileNotFoundError("uv is required for the package check")
    environment = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="llm-bus-package-") as temporary:
        sandbox = Path(temporary)
        wheels = sandbox / "dist"
        subprocess.run(  # noqa: S603 - resolved executable and fixed argv, no shell
            [uv, "build", "--out-dir", str(wheels)],
            cwd=root,
            env=environment,
            check=True,
        )
        virtualenv = sandbox / "venv"
        subprocess.run(  # noqa: S603 - resolved executable and fixed argv, no shell
            [uv, "venv", "--python", sys.executable, str(virtualenv)],
            cwd=sandbox,
            env=environment,
            check=True,
        )
        binary_directory = virtualenv / ("Scripts" if os.name == "nt" else "bin")
        python = binary_directory / ("python.exe" if os.name == "nt" else "python")
        wheel = next(wheels.glob("*.whl"))
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)],
            cwd=sandbox,
            env=environment,
            check=True,
        )
        modules = application_modules(root / "src")
        core = [name for name in modules if name not in COLLAB_EXTRA_MODULES]
        importer = "import importlib, sys; [importlib.import_module(n) for n in sys.argv[1:]]"
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(python), "-I", "-c", importer, *core], cwd=sandbox, env=environment, check=True
        )
        subprocess.run(  # noqa: S603 - isolated module entry point, no user state
            [str(python), "-I", "-m", "llm_bus", "--help"],
            cwd=sandbox,
            env=environment,
            check=True,
        )
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [uv, "pip", "install", "--python", str(python), f"{wheel}[collab]"],
            cwd=sandbox,
            env=environment,
            check=True,
        )
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [str(python), "-I", "-c", importer, *sorted(COLLAB_EXTRA_MODULES)],
            cwd=sandbox,
            env=environment,
            check=True,
        )
        cli = binary_directory / ("llm-bus.exe" if os.name == "nt" else "llm-bus")
        subprocess.run(  # noqa: S603 - installed entry point; help does not access user state
            [str(cli), "--help"], cwd=sandbox, env=environment, check=True
        )


if __name__ == "__main__":
    main()

"""Build this checkout, reinstall the uv tool, verify it, then prune older packages."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from llm_bus.errors import BusError

PACKAGE_ID = re.compile(r"^[0-9a-f]{8}$")
VERIFY_PATH = "/usr/bin:/bin"
BOOTSTRAP = "install a wheel once as described in README 'Upgrade', then rerun"
RMTREE_UNSAFE = "Refusing to prune: platform rmtree follows symlinks"
NEXT_STEP = (
    "Close Claude Code and Codex, run `llm-bus install`, then restart them; "
    "restart any running `llm-bus bridge`"
)
STDERR_TAIL_LINES = 20
HEAD_REF = "HEAD"
PATH_VARIABLE = "PATH"


@dataclass(frozen=True, slots=True)
class InstalledRequirement:
    """Previous uv tool install, restorable from its wheel."""

    path: Path
    extras: tuple[str, ...]
    python: str | None


def prune_entries(root: Path, *, keep: str | None = None) -> list[str]:
    """Delete every entry of root except keep; never follow symlinks."""
    try:
        mode = os.lstat(root).st_mode
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(mode):
        raise BusError(f"Refusing to prune {root}: not a real directory")
    if not shutil.rmtree.avoids_symlink_attacks:
        raise BusError(RMTREE_UNSAFE)
    removed: list[str] = []
    with os.scandir(root) as scanned:
        entries = sorted(scanned, key=lambda entry: entry.name)
    for entry in entries:
        if entry.name == keep:
            continue
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                os.unlink(entry.path)  # noqa: PTH108 - never follow links
            else:
                shutil.rmtree(entry.path)
        except OSError as error:
            raise BusError(f"Pruning {root} stopped at {entry.name}; removed {removed}") from error
        removed.append(entry.name)
    return removed


def _run(argv: list[str], *, env: dict[str, str] | None = None, label: str | None = None) -> str:
    result = subprocess.run(  # noqa: S603 - fixed argv, resolved executables
        argv, capture_output=True, text=True, check=False, env=env
    )
    if result.returncode != 0:
        tail = "\n".join(result.stderr.splitlines()[-STDERR_TAIL_LINES:])
        name = label or f"{Path(argv[0]).name} {' '.join(argv[1:3])}"
        raise BusError(f"{name} failed: {tail}")
    return result.stdout.strip()


def _git(checkout: Path, *args: str) -> str:
    git = shutil.which("git")
    if git is None:
        raise BusError("git is required to upgrade llm-bus")
    return _run([git, "-C", str(checkout), *args], label=f"git {args[0]}")


def _checkout_root(start: Path) -> Path:
    root = Path(_git(start, "rev-parse", "--show-toplevel"))
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text("utf-8")).get("project")
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise BusError(f"{root} is not an llm-bus checkout") from error
    if not isinstance(project, dict) or cast("dict[str, object]", project).get("name") != "llm-bus":
        raise BusError(f"{root} is not an llm-bus checkout")
    return root


def _require_clean(checkout: Path) -> None:
    if _git(checkout, "status", "--porcelain", "--untracked-files=no"):
        raise BusError("Tracked files have uncommitted changes; commit or stash them first")


def _receipt_path(tools_dir: Path) -> Path:
    return tools_dir / "llm-bus" / "uv-receipt.toml"


def _receipt_requirement(receipt: Path) -> tuple[dict[str, object], dict[str, object]]:
    """Return the receipt's [tool] table and its llm-bus requirement."""
    try:
        data = tomllib.loads(receipt.read_text("utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise BusError(f"receipt {receipt} unreadable: {error}") from error
    tool = data.get("tool")
    requirements = (
        cast("dict[str, object]", tool).get("requirements") if isinstance(tool, dict) else None
    )
    if isinstance(requirements, list):
        for item in cast("list[object]", requirements):
            if isinstance(item, dict) and cast("dict[str, object]", item).get("name") == "llm-bus":
                return cast("dict[str, object]", tool), cast("dict[str, object]", item)
    raise BusError(f"receipt {receipt} has no llm-bus requirement")


def _requirement_extras(requirement: dict[str, object]) -> tuple[str, ...]:
    extras = requirement.get("extras", [])
    if not isinstance(extras, list):
        raise BusError("receipt extras are malformed")
    return tuple(str(extra) for extra in cast("list[object]", extras))


def _read_previous(receipt: Path) -> InstalledRequirement:
    def cannot_restore(reason: str) -> BusError:
        return BusError(
            f"Previous llm-bus install cannot be restored if the upgrade fails ({reason}); "
            f"{BOOTSTRAP}"
        )

    try:
        tool, requirement = _receipt_requirement(receipt)
        extras = _requirement_extras(requirement)
    except BusError as error:
        raise cannot_restore(str(error)) from error
    raw_path = requirement.get("path")
    if not isinstance(raw_path, str):
        raise cannot_restore("not a wheel install")
    path = Path(raw_path)
    if path.suffix != ".whl" or not path.is_file():
        raise cannot_restore(f"{path} is not an existing wheel")
    python = tool.get("python")
    return InstalledRequirement(path, extras, python if isinstance(python, str) else None)


def _is_active(previous: InstalledRequirement, target: Path) -> bool:
    return target.is_dir() and previous.path.parent.samefile(target)


def _build_wheel(uv: str, checkout: Path, packages: Path, target: Path) -> Path:
    for folder in (packages, target):
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            raise BusError(f"Refusing to build into {folder}: not a real directory")
        folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=packages, prefix=".upgrade-") as scratch:
        work = Path(scratch)
        archive = work / "src.tar"
        _git(checkout, "archive", "--format=tar", "-o", str(archive), HEAD_REF)
        source = work / "src"
        with tarfile.open(archive) as tar:
            tar.extractall(source, filter="data")
        dist = work / "dist"
        _run([uv, "build", "--wheel", "--out-dir", str(dist), str(source)])
        wheels = list(dist.glob("*.whl"))
        if len(wheels) != 1:
            raise BusError(f"Expected exactly one wheel in {dist}, found {len(wheels)}")
        wheel = target / wheels[0].name
        wheels[0].replace(wheel)
    return wheel


def _requirement_arg(wheel: Path, extras: tuple[str, ...]) -> str:
    return f"{wheel}[{','.join(extras)}]" if extras else str(wheel)


def _install(uv: str, wheel: Path, extras: tuple[str, ...], python: str | None) -> None:
    command = [uv, "tool", "install", "--force"]
    if python:
        command += ["--python", python]
    _run([*command, _requirement_arg(wheel, extras)])


def _rollback(uv: str, previous: InstalledRequirement) -> str:
    try:
        _install(uv, previous.path, previous.extras, previous.python)
    except (BusError, OSError, subprocess.CalledProcessError) as error:
        return f"restore failed: {error}"
    return "restored"


def _verify(receipt: Path, tools_dir: Path, wheel: Path, extras: tuple[str, ...]) -> Path:
    tool, requirement = _receipt_requirement(receipt)
    if Path(str(requirement.get("path"))) != wheel or _requirement_extras(requirement) != extras:
        raise BusError(f"receipt does not point at {_requirement_arg(wheel, extras)}")
    binary = _entrypoint(tool)
    output = _run(
        [str(binary), "upgrade", "--help"], env={**os.environ, PATH_VARIABLE: VERIFY_PATH}
    )
    if "usage:" not in output:
        raise BusError("installed llm-bus upgrade --help printed no usage")
    if "collab" in extras:
        _run(
            [
                str(tools_dir / "llm-bus" / "bin" / "python"),
                "-I",
                "-c",
                "import llm_bus.collab.transport",
            ]
        )
    return binary


def _entrypoint(tool: dict[str, object]) -> Path:
    entrypoints = tool.get("entrypoints")
    if isinstance(entrypoints, list):
        for item in cast("list[object]", entrypoints):
            if isinstance(item, dict) and cast("dict[str, object]", item).get("name") == "llm-bus":
                binary = Path(str(cast("dict[str, object]", item).get("install-path")))
                if binary.exists():
                    return binary
    raise BusError("receipt has no existing llm-bus entrypoint")


def _require_prunable_placement(checkout: Path, roots: tuple[Path, ...]) -> None:
    for root in roots:
        if checkout.resolve().is_relative_to(root.resolve()):
            raise BusError(
                f"Refusing to upgrade from {checkout}: it is inside {root}, "
                "which the upgrade prunes"
            )
    if not shutil.rmtree.avoids_symlink_attacks:
        raise BusError(RMTREE_UNSAFE)


def _verify_current(tools_dir: Path, wheel: Path, extras: tuple[str, ...]) -> Path:
    try:
        return _verify(_receipt_path(tools_dir), tools_dir, wheel, extras)
    except BusError as error:
        raise BusError(
            f"Verification failed: {error}; nothing deleted; current install unchanged"
        ) from error


def _install_verified(
    uv: str,
    tools_dir: Path,
    wheel: Path,
    python: str,
    previous: InstalledRequirement,
) -> Path:
    """Install wheel and verify; on failure restore the previous wheel and delete nothing."""
    try:
        _install(uv, wheel, previous.extras, python)
        return _verify(_receipt_path(tools_dir), tools_dir, wheel, previous.extras)
    except (BusError, OSError, subprocess.CalledProcessError) as error:
        outcome = _rollback(uv, previous)
        raise BusError(
            f"Upgrade failed: {error}; nothing deleted; previous install {outcome}"
        ) from error


def _prune_old(
    checkout: Path, packages: Path, releases: Path, package_id: str
) -> dict[str, object]:
    """Run only after verification passed; the sole deletion step of the upgrade."""
    removed_packages = prune_entries(packages, keep=package_id)
    try:
        removed_releases = prune_entries(releases)
    except BusError as error:
        raise BusError(f"{error}; removed packages {removed_packages}") from error
    if removed_releases:
        try:
            _git(checkout, "worktree", "prune")
        except BusError as error:
            raise BusError(
                f"{error}; removed packages {removed_packages}, releases {removed_releases}"
            ) from error
    return {"removed_packages": removed_packages, "removed_releases": removed_releases}


def upgrade(start: Path, home: Path) -> dict[str, object]:
    """Install the checkout's HEAD, verify it, then prune older packages and releases."""
    uv = shutil.which("uv")
    if uv is None:
        raise BusError("uv is required to upgrade llm-bus")
    checkout = _checkout_root(start)
    packages = home / ".local/share/llm-bus/packages"
    releases = home / ".local/state/llm-bus/releases"
    _require_prunable_placement(checkout, (packages, releases))
    _require_clean(checkout)
    package_id = _git(checkout, "rev-parse", HEAD_REF)[:8]
    if not PACKAGE_ID.fullmatch(package_id):
        raise BusError(f"Unexpected HEAD id {package_id!r}")
    tools_dir = Path(_run([uv, "tool", "dir"]))
    previous = _read_previous(_receipt_path(tools_dir))
    python = (checkout / ".python-version").read_text("utf-8").strip()
    target = packages / package_id
    if _is_active(previous, target):
        wheel, reinstalled = previous.path, False
        binary = _verify_current(tools_dir, wheel, previous.extras)
    else:
        wheel, reinstalled = _build_wheel(uv, checkout, packages, target), True
        binary = _install_verified(uv, tools_dir, wheel, python, previous)
    removed = _prune_old(checkout, packages, releases, package_id)
    return {
        **removed,
        "package_id": package_id,
        "reinstalled": reinstalled,
        "wheel": str(wheel),
        "extras": list(previous.extras),
        "python": python,
        "binary": str(binary),
        "next_step": NEXT_STEP,
    }

#!/usr/bin/env python3
"""Copy a module and its dependencies to a MicroPython board using mpremote."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class CopySpec:
    local: Path
    remote: str


# User-requested shared dependencies plus the runtime core needed by all modules.
SHARED_SPECS: tuple[CopySpec, ...] = (
    CopySpec(ROOT / "libs/beatbox-protocol/beatbox_protocol/__init__.py", ":beatbox_protocol/__init__.py"),
    CopySpec(ROOT / "libs/beatbox-protocol/beatbox_protocol/crc.py", ":beatbox_protocol/crc.py"),
    CopySpec(ROOT / "libs/beatbox-protocol/beatbox_protocol/message.py", ":beatbox_protocol/message.py"),
    CopySpec(ROOT / "libs/microPython_MCP2515/MCP2515.py", ":MCP2515.py"),
    CopySpec(ROOT / "libs/microPython_MCP2515/canio.py", ":canio.py"),
    CopySpec(ROOT / "libs/microPython_MCP2515/myTimer.py", ":myTimer.py"),
    CopySpec(ROOT / "libs/bb_mpy_module.py", ":bb_mpy_module.py"),
    CopySpec(ROOT / "libs/can_manager.py", ":can_manager.py"),
    CopySpec(ROOT / "libs/can_protocol.py", ":can_protocol.py"),
    CopySpec(ROOT / "libs/beatbox_module.py", ":beatbox_module.py"),
    CopySpec(ROOT / "libs/bb_pin_map.py", ":bb_pin_map.py"),
)

MODULE_EXTRA_SPECS: dict[str, tuple[CopySpec, ...]] = {
    "blinky": (),
    "can_dummy_rx": (),
    "can_dummy_tx": (),
    "communicator": (),
    "feeder": (),
    "ir_barrier": (),
    "nosepoke": (),
    "main_module": (
        CopySpec(ROOT / "libs/serial_manager.py", ":serial_manager.py"),
        CopySpec(ROOT / "libs/cli_protocol.py", ":cli_protocol.py"),
    ),
    "screenmodule": (
        CopySpec(
            ROOT / "libs/ILI9341-parallel-TFT-driver-for-micropython/ILI9341_RP2/ILI9341.py",
            ":ILI9341.py",
        ),
    ),
    "lighting": (
        CopySpec(ROOT / "libs/CircuitPython_PCA9955B/pca9955b/__init__.py", ":pca9955b/__init__.py"),
        CopySpec(ROOT / "libs/CircuitPython_PCA9955B/pca9955b/pca9955b.py", ":pca9955b/pca9955b.py"),
        CopySpec(
            ROOT / "libs/CircuitPython_PCA9955B/pca9955b/pca9955b_groups.py",
            ":pca9955b/pca9955b_groups.py",
        ),
    ),
}


def _build_module_spec(module_name: str, as_main: bool) -> CopySpec:
    module_file = ROOT / "modules" / f"{module_name}.py"
    remote_name = ":main.py" if as_main else f":{module_name}.py"
    return CopySpec(module_file, remote_name)


def build_copy_plan(module_name: str, as_main: bool) -> list[CopySpec]:
    specs = list(SHARED_SPECS)
    specs.extend(MODULE_EXTRA_SPECS[module_name])
    specs.append(_build_module_spec(module_name, as_main))

    # Preserve order while preventing duplicate remote paths.
    deduped: list[CopySpec] = []
    seen_remote = set()
    for spec in specs:
        if spec.remote in seen_remote:
            continue
        seen_remote.add(spec.remote)
        deduped.append(spec)
    return deduped


def _remote_parent_dirs(remote_path: str) -> list[str]:
    if not remote_path.startswith(":"):
        raise ValueError(f"Invalid mpremote destination '{remote_path}', expected ':' prefix")

    parts = remote_path[1:].split("/")
    if len(parts) <= 1:
        return []

    parents = []
    current = []
    for part in parts[:-1]:
        current.append(part)
        parents.append(":" + "/".join(current))
    return parents


def _run(cmd: list[str], dry_run: bool) -> None:
    print("+", " ".join(cmd))
    if dry_run:
        return

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout.strip())
        if completed.stderr:
            print(completed.stderr.strip(), file=sys.stderr)
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(cmd)}")


def _mkdir_remote_if_needed(port: str, remote_dir: str, dry_run: bool) -> None:
    cmd = ["mpremote", "connect", port, "fs", "mkdir", remote_dir]
    if dry_run:
        _run(cmd, dry_run=True)
        return

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode == 0:
        return

    stderr = (completed.stderr or "").lower()
    stdout = (completed.stdout or "").lower()
    if "file exists" in stderr or "eexist" in stderr or "file exists" in stdout or "eexist" in stdout:
        return

    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)
    raise RuntimeError(f"Could not create remote directory {remote_dir}")


def deploy_module(module_name: str, port: str, as_main: bool, dry_run: bool) -> None:
    mpremote_path = shutil.which("mpremote")
    if mpremote_path is None and not dry_run:
        raise RuntimeError("mpremote executable not found in PATH")

    if module_name not in MODULE_EXTRA_SPECS:
        raise ValueError(f"Unknown module '{module_name}'")

    copy_plan = build_copy_plan(module_name, as_main=as_main)

    missing = [str(spec.local) for spec in copy_plan if not spec.local.exists()]
    if missing:
        raise FileNotFoundError("Some dependency files are missing:\n- " + "\n- ".join(missing))

    # Create any needed parent directories once before copying files.
    required_dirs = []
    seen_dirs = set()
    for spec in copy_plan:
        for parent in _remote_parent_dirs(spec.remote):
            if parent not in seen_dirs:
                required_dirs.append(parent)
                seen_dirs.add(parent)

    for remote_dir in required_dirs:
        _mkdir_remote_if_needed(port=port, remote_dir=remote_dir, dry_run=dry_run)

    for spec in copy_plan:
        _run(
            ["mpremote", "connect", port, "fs", "cp", str(spec.local), spec.remote],
            dry_run=dry_run,
        )



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy a module + dependencies to a MicroPython board using mpremote.",
    )
    parser.add_argument(
        "module",
        nargs="?",
        choices=sorted(MODULE_EXTRA_SPECS.keys()),
        help="Module name to deploy.",
    )
    parser.add_argument(
        "--port",
        default="auto",
        help="mpremote connect target (default: auto).",
    )
    parser.add_argument(
        "--as-main",
        action="store_true",
        help="Copy the selected module as :main.py instead of :<module>.py.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands only; do not call mpremote.",
    )
    parser.add_argument(
        "--list-modules",
        action="store_true",
        help="List known modules and exit.",
    )
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.list_modules:
        for module_name in sorted(MODULE_EXTRA_SPECS):
            print(module_name)
        return 0

    if not args.module:
        parser.error("module is required unless --list-modules is used")

    try:
        deploy_module(
            module_name=args.module,
            port=args.port,
            as_main=args.as_main,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Done: deployed '{args.module}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


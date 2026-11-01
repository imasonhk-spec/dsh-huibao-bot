"""Apply the R6 code update to an existing service without reinstalling dependencies.

Run this file from the R6 package using the existing R5 virtual environment:
    <R5-service>/.venv/Scripts/python.exe <R6-service>/tools/apply_r6_update.py <R5-service>

The release manifest belongs at <R6-service>/release_update_manifest.json:
    {"schema_version": 1, "release": "R6", "files": [
        {"path": "src/deskbot_server/example.py", "sha256": "<64 lowercase hex digits>"}
    ]}

Only manifest-listed source files and explicitly allowed tools can be replaced.
Configuration, dependencies, models, and runtime data are never copied from R6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath


SOURCE_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "release_update_manifest.json"
ALLOWED_TOOLS = {
    "tools/apply_r6_update.py",
    "tools/check_speech.py",
    "tools/run_local_windows.ps1",
}
PROTECTED_PARTS = {".env", "config.yaml", "data", "models", ".venv", "runtime_logs"}
WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.I)


class UpdateError(Exception):
    """A safe-to-display update failure, containing no configuration values."""


@dataclass(frozen=True)
class PlannedFile:
    relative: str
    source: Path
    destination: Path
    sha256: str
    previous_sha256: str | None


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise UpdateError("Manifest contains an invalid relative file path.")
    parts = value.split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((".", " "))
        or any(ord(char) < 32 for char in part)
        or any(char in '<>"|?*' for char in part)
        or WINDOWS_RESERVED.match(part)
        for part in parts
    ):
        raise UpdateError("Manifest contains an unsafe file path.")
    lower_parts = [part.casefold() for part in parts]
    if any(part in PROTECTED_PARTS or part.startswith(".env.") for part in lower_parts):
        raise UpdateError("Manifest attempts to replace a protected configuration or runtime path.")
    if "__pycache__" in lower_parts or lower_parts[-1].endswith((".pyc", ".pyo")):
        raise UpdateError("Manifest must not include generated Python bytecode.")
    if not (
        (len(parts) > 2 and parts[:2] == ["src", "deskbot_server"])
        or value in ALLOWED_TOOLS
    ):
        raise UpdateError("Manifest contains a file outside the permitted source/tool paths.")
    return value


def checked_path(root: Path, relative: str) -> Path:
    """Reject links/junctions so a listed path cannot redirect outside the tree."""
    result = root
    for part in PurePosixPath(relative).parts:
        result = result / part
        try:
            info = result.lstat()
        except FileNotFoundError:
            continue
        # st_file_attributes also catches Windows junctions on Python 3.11.
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise UpdateError(f"Refusing a symbolic link or Windows junction: {relative}")
    if not result.resolve().is_relative_to(root):
        raise UpdateError("File path escapes its service directory.")
    return result


def build_plan(source_root: Path, target_root: Path) -> tuple[list[PlannedFile], int]:
    if source_root == target_root:
        raise UpdateError("Source and target are the same. Pass the existing R5 service directory.")
    for relative in ("src/deskbot_server", "tools"):
        if not checked_path(target_root, relative).is_dir():
            raise UpdateError("Target is not a service directory; pass the R5 folder containing src and tools.")
    manifest_path = checked_path(source_root, MANIFEST_NAME)
    if not manifest_path.is_file():
        raise UpdateError("The R6 release manifest is missing. Re-extract the complete R6 package.")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpdateError("The R6 release manifest is not valid UTF-8 JSON.") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("release") != "R6":
        raise UpdateError("Unsupported release manifest; expected schema_version 1 and release R6.")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise UpdateError("The R6 manifest must contain a non-empty files list.")
    plan: list[PlannedFile] = []
    seen: set[str] = set()
    unchanged = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise UpdateError("Each manifest file must contain path and sha256.")
        relative = validate_manifest_path(entry.get("path"))
        if relative.casefold() in seen:
            raise UpdateError("The manifest contains duplicate paths (including Windows case aliases).")
        seen.add(relative.casefold())
        expected = entry.get("sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdateError(f"Missing or invalid SHA-256 for: {relative}")
        source = checked_path(source_root, relative)
        destination = checked_path(target_root, relative)
        if not source.is_file():
            raise UpdateError(f"Missing R6 source file: {relative}")
        if file_sha256(source) != expected:
            raise UpdateError(f"R6 checksum mismatch: {relative}. Re-extract the complete package.")
        if destination.exists() and not destination.is_file():
            raise UpdateError(f"Target file path is occupied by a directory: {relative}")
        previous = file_sha256(destination) if destination.exists() else None
        if previous == expected:
            unchanged += 1
            continue
        plan.append(PlannedFile(relative, source, destination, expected, previous))
    return plan, unchanged


def require_services_stopped() -> None:
    if os.name != "nt":
        return
    for port in (5050, 9000):
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            try:
                with socket.socket(family, socket.SOCK_STREAM) as connection:
                    connection.settimeout(0.3)
                    listening = connection.connect_ex((host, port)) == 0
            except OSError:
                # IPv6 may be disabled; IPv4 is always checked independently.
                continue
            if listening:
                raise UpdateError(
                    f"Port {port} is in use. Press Ctrl+C in the running Core/Web service window, "
                    "wait for it to stop, then run this update again. No process was terminated."
                )


def atomic_copy(source: Path, destination: Path, *, root: Path, sha256: str) -> None:
    relative = destination.relative_to(root).as_posix()
    checked_path(root, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    checked_path(root, relative)
    temp_path: Path | None = None
    try:
        descriptor, temp_name = tempfile.mkstemp(prefix=".r6-update-", dir=destination.parent)
        os.close(descriptor)
        temp_path = Path(temp_name)
        shutil.copyfile(source, temp_path)
        if file_sha256(temp_path) != sha256:
            raise UpdateError("A file changed while copying; the update was stopped.")
        with temp_path.open("r+b") as copied:
            os.fsync(copied.fileno())
        shutil.copystat(source, temp_path)
        checked_path(root, relative)
        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            if temp_path.exists():
                # A Windows read-only source may have propagated its attribute.
                temp_path.chmod(stat.S_IREAD | stat.S_IWRITE)
                temp_path.unlink()


def write_json(path: Path, content: dict) -> None:
    # Metadata contains relative filenames and checksums, never configuration values.
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def apply_update(source_root: Path, target_root: Path, *, dry_run: bool = False) -> Path | None:
    source_root = source_root.resolve()
    target_root = target_root.resolve()
    plan, unchanged = build_plan(source_root, target_root)
    print(f"R6 update: {len(plan)} file(s) to update; {unchanged} already current.")
    if dry_run:
        for item in plan:
            print(f"  {'REPLACE' if item.previous_sha256 else 'ADD'} {item.relative}")
        print("Dry run complete. No files changed.")
        return None
    if not plan:
        print("R6 code is already current. No files changed.")
        return None
    require_services_stopped()
    backup_parent = checked_path(target_root, "data/local/update-backups")
    backup_parent.mkdir(parents=True, exist_ok=True)
    checked_path(target_root, "data/local/update-backups")
    backup = Path(tempfile.mkdtemp(prefix=f"r6-{datetime.now():%Y%m%d-%H%M%S}-", dir=backup_parent))
    metadata = {
        "release": "R6",
        "state": "backing_up",
        "files": [
            {"path": item.relative, "previous_sha256": item.previous_sha256, "sha256": item.sha256}
            for item in plan
        ],
    }
    write_json(backup / "applied_manifest.json", metadata)
    committed: list[PlannedFile] = []
    try:
        # Complete every backup before replacing any installed source file.
        for item in plan:
            checked_path(target_root, item.relative)
            if item.previous_sha256 is not None:
                atomic_copy(item.destination, backup / item.relative, root=backup, sha256=item.previous_sha256)
        metadata["state"] = "applying"
        write_json(backup / "applied_manifest.json", metadata)
        for item in plan:
            checked_path(target_root, item.relative)
            current_hash = file_sha256(item.destination) if item.destination.exists() else None
            if current_hash != item.previous_sha256:
                raise UpdateError(f"Target changed during the update: {item.relative}")
            checked_path(source_root, item.relative)
            # Include the in-flight path so Ctrl+C immediately after os.replace
            # cannot leave a replacement outside the rollback list.
            committed.append(item)
            atomic_copy(item.source, item.destination, root=target_root, sha256=item.sha256)
        metadata["state"] = "complete"
        write_json(backup / "applied_manifest.json", metadata)
    except (Exception, KeyboardInterrupt) as exc:
        rollback_failures = []
        for item in reversed(committed):
            try:
                checked_path(target_root, item.relative)
                if item.previous_sha256 is None:
                    item.destination.unlink(missing_ok=True)
                else:
                    atomic_copy(backup / item.relative, item.destination, root=target_root, sha256=item.previous_sha256)
            except (Exception, KeyboardInterrupt):
                rollback_failures.append(item.relative)
        metadata["state"] = "rollback_incomplete" if rollback_failures else "rolled_back"
        metadata["rollback_failures"] = rollback_failures
        try:
            write_json(backup / "applied_manifest.json", metadata)
        except OSError:
            pass
        if rollback_failures:
            raise UpdateError(
                "Update failed and some files could not be restored. Do not start services. "
                f"Backup: {backup}. Restore these files from the backup: {', '.join(rollback_failures)}"
            ) from exc
        reason = str(exc) if not isinstance(exc, KeyboardInterrupt) else "Interrupted by Ctrl+C"
        raise UpdateError(f"Update failed; all replaced files were restored. Reason: {reason}. Backup: {backup}") from exc
    print(f"R6 code update complete. Backup: {backup}")
    print("Existing .env, config.yaml, .venv, models, runtime logs and user data are preserved.")
    print("Start tools\\run_local_windows.ps1 from the SAME existing service directory, then press Ctrl+F5 in the browser.")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target_service", type=Path, help="Existing R5 service directory containing .venv, src and tools")
    parser.add_argument("--dry-run", action="store_true", help="Validate and list changes without writing any files")
    args = parser.parse_args()
    try:
        apply_update(SOURCE_ROOT, args.target_service, dry_run=args.dry_run)
    except (UpdateError, OSError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

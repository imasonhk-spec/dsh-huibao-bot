"""Exercise the R6 updater using disposable service trees, never real settings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import apply_r6_update as updater


def write_file(root: Path, relative: str, content: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def write_manifest(source: Path, files: dict[str, bytes]) -> None:
    entries = []
    for relative, content in files.items():
        write_file(source, relative, content)
        entries.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest()})
    write_file(source, updater.MANIFEST_NAME, json.dumps({
        "schema_version": 1, "release": "R6", "files": entries,
    }).encode())


@pytest.fixture
def release_trees(tmp_path: Path):
    source = tmp_path / "r6" / "service"
    target = tmp_path / "r5" / "service"
    files = {
        "src/deskbot_server/a.py": b"new a\n",
        "src/deskbot_server/new.py": b"new file\n",
        "tools/check_speech.py": b"new check tool\n",
    }
    original = {
        "src/deskbot_server/a.py": b"old a\n",
        "tools/check_speech.py": b"old check tool\n",
    }
    protected = {
        ".env": b"LOCAL_KEY=opaque-private-value\n",
        "config.yaml": b"existing-user-settings: preserve\n",
        ".venv/pyvenv.cfg": b"home = installed-python\n",
        "models/model.bin": b"existing model weights",
        "runtime_logs/previous.log": b"existing logs",
        "data/local/settings.json": b'{"existing_user_data": true}',
    }
    write_manifest(source, files)
    for relative, content in (original | protected).items():
        write_file(target, relative, content)
    # These differ deliberately: release config/data must never be imported.
    for relative in protected:
        write_file(source, relative, b"release default must not replace user value")
    return SimpleNamespace(source=source, target=target, files=files,
                           original=original, protected=protected)


def assert_files(root: Path, expected: dict[str, bytes]) -> None:
    for relative, content in expected.items():
        assert (root / relative).read_bytes() == content, relative


def test_update_backs_up_code_and_preserves_all_local_settings(release_trees, capsys):
    tree = release_trees
    backup = updater.apply_update(tree.source, tree.target)
    assert backup is not None
    assert backup.parent == tree.target / "data/local/update-backups"
    assert_files(tree.target, tree.files | tree.protected)
    assert_files(backup, tree.original)
    assert not (backup / "src/deskbot_server/new.py").exists()
    assert not (backup / ".env").exists()
    record = json.loads((backup / "applied_manifest.json").read_text())
    assert record["state"] == "complete"
    assert "opaque-private-value" not in capsys.readouterr().out


def test_repeated_update_is_a_noop_without_an_extra_backup(release_trees):
    tree = release_trees
    backup = updater.apply_update(tree.source, tree.target)
    assert updater.apply_update(tree.source, tree.target) is None
    assert list(backup.parent.iterdir()) == [backup]
    assert_files(tree.target, tree.files | tree.protected)


def test_dry_run_writes_nothing(release_trees):
    tree = release_trees
    assert updater.apply_update(tree.source, tree.target, dry_run=True) is None
    assert_files(tree.target, tree.original | tree.protected)
    assert not (tree.target / "data/local/update-backups").exists()
    assert not (tree.target / "src/deskbot_server/new.py").exists()


@pytest.mark.parametrize("relative", [
    ".env", "config.yaml", ".venv/pyvenv.cfg", "data/local/settings.json",
    "models/model.bin", "runtime_logs/previous.log",
    "src/deskbot_server/../../.env", "src/deskbot_server/.env",
    "src/deskbot_server/.env.local", "src/deskbot_server/x:stream",
    "src/deskbot_server/NUL.py", "src/deskbot_server/x. ",
    "src/deskbot_server/__pycache__/x.pyc", "tools/setup_full_windows.ps1",
])
def test_manifest_rejects_protected_or_unsafe_paths_before_mutation(release_trees, relative):
    tree = release_trees
    # Do not create unsafe source files: validation must fail before opening them.
    record = {"schema_version": 1, "release": "R6", "files": [{
        "path": relative, "sha256": "0" * 64,
    }]}
    (tree.source / updater.MANIFEST_NAME).write_text(json.dumps(record))
    with pytest.raises(updater.UpdateError):
        updater.apply_update(tree.source, tree.target)
    assert_files(tree.target, tree.original | tree.protected)
    assert not (tree.target / "data/local/update-backups").exists()


def test_missing_manifest_and_identical_source_target_are_rejected(release_trees):
    tree = release_trees
    with pytest.raises(updater.UpdateError, match="same"):
        updater.apply_update(tree.source, tree.source)
    (tree.source / updater.MANIFEST_NAME).unlink()
    with pytest.raises(updater.UpdateError, match="manifest is missing"):
        updater.apply_update(tree.source, tree.target)


def test_checksum_mismatch_is_detected_before_backup(release_trees):
    tree = release_trees
    (tree.source / "tools/check_speech.py").write_bytes(b"corrupted release")
    with pytest.raises(updater.UpdateError, match="checksum mismatch"):
        updater.apply_update(tree.source, tree.target)
    assert_files(tree.target, tree.original | tree.protected)
    assert not (tree.target / "data/local/update-backups").exists()


def test_failure_restores_old_files_and_removes_added_files(release_trees, monkeypatch):
    tree = release_trees
    copy = updater.atomic_copy
    failed = False

    def fail_once(source, destination, **kwargs):
        nonlocal failed
        if destination == tree.target / "tools/check_speech.py" and not failed:
            failed = True
            raise OSError("simulated write failure")
        return copy(source, destination, **kwargs)

    monkeypatch.setattr(updater, "atomic_copy", fail_once)
    with pytest.raises(updater.UpdateError, match="all replaced files were restored"):
        updater.apply_update(tree.source, tree.target)
    assert_files(tree.target, tree.original | tree.protected)
    assert not (tree.target / "src/deskbot_server/new.py").exists()
    record = next((tree.target / "data/local/update-backups").glob("*/applied_manifest.json"))
    assert json.loads(record.read_text())["state"] == "rolled_back"
    assert not list(tree.target.rglob(".r6-update-*"))


def test_interrupt_after_atomic_replace_is_rolled_back(release_trees, monkeypatch):
    tree = release_trees
    copy = updater.atomic_copy
    interrupted = False

    def interrupt_once(source, destination, **kwargs):
        nonlocal interrupted
        result = copy(source, destination, **kwargs)
        if destination == tree.target / "src/deskbot_server/new.py" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return result

    monkeypatch.setattr(updater, "atomic_copy", interrupt_once)
    with pytest.raises(updater.UpdateError, match="Interrupted by Ctrl.C"):
        updater.apply_update(tree.source, tree.target)
    assert_files(tree.target, tree.original | tree.protected)
    assert not (tree.target / "src/deskbot_server/new.py").exists()


def test_incomplete_rollback_reports_backup_and_files(release_trees, monkeypatch):
    tree = release_trees
    copy = updater.atomic_copy

    def fail_apply_and_restore(source, destination, **kwargs):
        if destination == tree.target / "tools/check_speech.py":
            raise OSError("simulated locked file")
        return copy(source, destination, **kwargs)

    monkeypatch.setattr(updater, "atomic_copy", fail_apply_and_restore)
    with pytest.raises(updater.UpdateError, match="Do not start services") as failure:
        updater.apply_update(tree.source, tree.target)
    assert "tools/check_speech.py" in str(failure.value)
    assert "update-backups" in str(failure.value)
    assert_files(tree.target, tree.protected)


def test_service_guard_blocks_mutation(release_trees, monkeypatch):
    tree = release_trees

    def refuse_running_service():
        raise updater.UpdateError("Port 5050 is in use. Press Ctrl+C first.")

    monkeypatch.setattr(updater, "require_services_stopped", refuse_running_service)
    with pytest.raises(updater.UpdateError, match="Ctrl.C"):
        updater.apply_update(tree.source, tree.target)
    assert_files(tree.target, tree.original | tree.protected)
    assert not (tree.target / "data/local/update-backups").exists()


def test_windows_listener_detection_without_terminating_processes(monkeypatch):
    attempts = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def settimeout(self, timeout):
            assert timeout == 0.3

        def connect_ex(self, address):
            attempts.append(address)
            return 0

    # Replace the updater's os reference, rather than global os.name, so pytest
    # and pathlib continue using the actual host's path implementation.
    monkeypatch.setattr(updater, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(updater.socket, "socket", lambda *_: FakeSocket())
    with pytest.raises(updater.UpdateError, match="Port 5050"):
        updater.require_services_stopped()
    assert attempts == [("127.0.0.1", 5050)]


def test_target_symlink_cannot_redirect_update_outside_tree(release_trees):
    tree = release_trees
    outside = tree.target.parent / "outside"
    outside.mkdir()
    link = tree.target / "src/deskbot_server/linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links")
    write_manifest(tree.source, {"src/deskbot_server/linked/new.py": b"new"})
    with pytest.raises(updater.UpdateError, match="symbolic link"):
        updater.apply_update(tree.source, tree.target)
    assert not list(outside.iterdir())
    assert_files(tree.target, tree.original | tree.protected)

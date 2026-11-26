"""Keep the SQLite database safe on free hosting.

A free Hugging Face Space has no permanent disk: every restart starts again from the files in the image. And it
can't reach a Postgres server (only web ports are open), so we can't simply use Supabase. Instead:

  on start:  download the latest copy of local.db from a PRIVATE Hugging Face dataset (if one exists)
  running:   every DB_BACKUP_EVERY_S seconds, if anything changed, upload a consistent snapshot
  on stop:   one last upload

    python -m app.backup restore      # run before the server starts (deploy/hf-space/Dockerfile)
    python -m app.backup now          # manual snapshot

At scale this is replaced by a managed Postgres on a host that allows it (DATABASE_URL) — see the write-up.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

from .config import BACKEND_DIR, get_settings

log = logging.getLogger("backup")
DB_PATH = BACKEND_DIR / "data" / "local.db"
REMOTE_NAME = "local.db"
_last_hash: str | None = None
_lock = threading.Lock()


def enabled() -> bool:
    s = get_settings()
    return bool(s.db_backup_repo and s.hf_token and not s.database_url)


def _api():
    from huggingface_hub import HfApi
    return HfApi(token=get_settings().hf_token)


def restore() -> str:
    """Replace the image's demo DB with the latest backup. First run: nothing to restore, keep the demo DB."""
    if not enabled():
        return "backup disabled"
    s = get_settings()
    api = _api()
    api.create_repo(s.db_backup_repo, repo_type="dataset", private=True, exist_ok=True)
    if not api.file_exists(s.db_backup_repo, REMOTE_NAME, repo_type="dataset"):
        return "no backup yet — starting from the demo database"
    from huggingface_hub import hf_hub_download
    with tempfile.TemporaryDirectory() as d:
        p = hf_hub_download(s.db_backup_repo, REMOTE_NAME, repo_type="dataset", token=s.hf_token, local_dir=d)
        sqlite3.connect(p).execute("PRAGMA integrity_check").fetchone()   # refuse a corrupt download
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, DB_PATH)
    return f"restored {DB_PATH.name} from {s.db_backup_repo}"


def _snapshot(dest: Path) -> str:
    """Consistent copy of the live database (safe while the app is writing); returns its hash."""
    src, dst = sqlite3.connect(DB_PATH), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def backup(force: bool = False) -> str:
    global _last_hash
    if not enabled():
        return "backup disabled"
    with _lock, tempfile.TemporaryDirectory() as d:
        snap = Path(d) / REMOTE_NAME
        digest = _snapshot(snap)
        if not force and digest == _last_hash:
            return "unchanged"
        _api().upload_file(path_or_fileobj=str(snap), path_in_repo=REMOTE_NAME,
                           repo_id=get_settings().db_backup_repo, repo_type="dataset",
                           commit_message="database snapshot")
        _last_hash = digest
    return "uploaded"


def _loop(stop: threading.Event) -> None:
    every = max(60, get_settings().db_backup_every_s)
    while not stop.wait(every):
        try:
            backup()
        except Exception as e:  # noqa: BLE001
            log.warning("backup failed: %s", e)
            from . import notify
            notify.system_error("backup", str(e))


_stop = threading.Event()


def start() -> None:
    if enabled():
        try:
            global _last_hash
            with tempfile.TemporaryDirectory() as d:   # just restored: no upload until something changes
                _last_hash = _snapshot(Path(d) / REMOTE_NAME)
        except Exception:  # noqa: BLE001
            pass
        threading.Thread(target=_loop, args=(_stop,), name="db-backup", daemon=True).start()
        log.info("database backup every %ss to %s", get_settings().db_backup_every_s, get_settings().db_backup_repo)


def stop() -> None:
    _stop.set()
    if enabled():
        try:
            log.info("final backup: %s", backup())
        except Exception as e:  # noqa: BLE001
            log.warning("final backup failed: %s", e)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "now"
    try:
        print(restore() if cmd == "restore" else backup(force=True))
    except Exception as e:  # noqa: BLE001 — never block the server from starting
        print(f"backup {cmd} failed: {e}")

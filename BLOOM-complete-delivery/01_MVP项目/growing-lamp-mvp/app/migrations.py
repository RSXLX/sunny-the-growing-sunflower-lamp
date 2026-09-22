"""Ordered, transactional SQLite migrations. Legacy review is never approval."""
from contextlib import closing
import sqlite3
import tempfile
import os
import time
from pathlib import Path


def manufacturing_v1(db):
    db.execute('''CREATE TABLE manufacturing_versions(
        id TEXT PRIMARY KEY, design_id TEXT NOT NULL REFERENCES designs(id),
        revision INTEGER NOT NULL, source_version_id TEXT REFERENCES manufacturing_versions(id),
        directory TEXT NOT NULL UNIQUE, manifest TEXT NOT NULL, mesh_report TEXT NOT NULL,
        interface TEXT NOT NULL, origin TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft'
            CHECK(status IN ('draft','submitted','approved','rejected','revoked')),
        review_note TEXT, review_checks TEXT, reviewed_by TEXT REFERENCES users(id),
        reviewed_at INTEGER, created INTEGER NOT NULL,
        UNIQUE(design_id, revision))''')
    db.execute('''CREATE TABLE manufacturing_reviews(
        id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES manufacturing_versions(id),
        actor TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
        note TEXT NOT NULL, checks TEXT NOT NULL, created INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE fabrication_records(
        id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES instances(id),
        actor TEXT NOT NULL REFERENCES users(id), stage TEXT NOT NULL,
        note TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL)''')
    db.execute('ALTER TABLE instances ADD COLUMN manufacturing_version_id TEXT REFERENCES manufacturing_versions(id)')
    db.execute('ALTER TABLE instances ADD COLUMN legacy_unverified INTEGER NOT NULL DEFAULT 0')
    db.execute('UPDATE instances SET legacy_unverified=1')
    db.execute('ALTER TABLE designs ADD COLUMN published_version_id TEXT REFERENCES manufacturing_versions(id)')
    db.execute('CREATE INDEX versions_design ON manufacturing_versions(design_id, revision DESC)')
    db.execute('CREATE INDEX fabrication_instance ON fabrication_records(instance_id, created)')


def reference_assets_v2(db):
    db.execute('''CREATE TABLE reference_assets(
        id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES users(id),
        name TEXT NOT NULL, media_type TEXT NOT NULL, width INTEGER NOT NULL,
        height INTEGER NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
        created INTEGER NOT NULL)''')
    db.execute('CREATE INDEX reference_owner ON reference_assets(owner,created)')
    db.execute('ALTER TABLE designs ADD COLUMN reference_asset_id TEXT REFERENCES reference_assets(id)')
    db.execute('ALTER TABLE designs ADD COLUMN request_snapshot TEXT')
    db.execute('ALTER TABLE designs ADD COLUMN upload_token TEXT')
    db.execute('ALTER TABLE designs ADD COLUMN raw_sha256 TEXT')
    db.execute('ALTER TABLE designs ADD COLUMN paid_reserved INTEGER NOT NULL DEFAULT 0')
    db.execute("UPDATE designs SET paid_reserved=1 WHERE provider='tripo'")


def fabrication_v3(db):
    db.execute('ALTER TABLE instances ADD COLUMN source_instance_id TEXT REFERENCES instances(id)')
    db.execute('ALTER TABLE instances ADD COLUMN creation_key TEXT')
    db.execute('ALTER TABLE instances ADD COLUMN creation_hash TEXT')
    db.execute('CREATE UNIQUE INDEX instance_creation_request ON instances(owner,creation_key) WHERE creation_key IS NOT NULL')
    db.execute('ALTER TABLE fabrication_records ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1')
    db.execute('ALTER TABLE fabrication_records ADD COLUMN request_key TEXT')
    db.execute('ALTER TABLE fabrication_records ADD COLUMN request_hash TEXT')
    db.execute('CREATE UNIQUE INDEX fabrication_request ON fabrication_records(instance_id,request_key) WHERE request_key IS NOT NULL')
    db.execute('''CREATE TABLE fabrication_photos(
        id TEXT PRIMARY KEY, record_id TEXT NOT NULL REFERENCES fabrication_records(id),
        name TEXT NOT NULL, bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
        width INTEGER NOT NULL, height INTEGER NOT NULL)''')
    db.execute('CREATE INDEX fabrication_photo_record ON fabrication_photos(record_id)')
    # Recover attempt numbers from the append-only history; keep IDs and timestamps.
    for (instance,) in db.execute('SELECT DISTINCT instance_id FROM fabrication_records').fetchall():
        attempt=1;previous=None
        for record,stage in db.execute('SELECT id,stage FROM fabrication_records WHERE instance_id=? ORDER BY created,rowid',(instance,)).fetchall():
            if previous=='failed' and stage=='printing':attempt+=1
            db.execute('UPDATE fabrication_records SET attempt=? WHERE id=?',(attempt,record));previous=stage


MIGRATIONS = [(1, manufacturing_v1), (2, reference_assets_v2), (3, fabrication_v3)]


def migrate(path: Path):
    with closing(sqlite3.connect(path, timeout=15)) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        # All schema/data changes are atomic, including migration bookkeeping.
        db.execute('BEGIN IMMEDIATE')
        has_history=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        applied = {row[0] for row in db.execute('SELECT version FROM schema_migrations')} if has_history else set()
        if applied - {version for version, _ in MIGRATIONS}:
            raise RuntimeError('Database uses a newer schema; start the matching application version')
        if any(version not in applied for version, _ in MIGRATIONS):
            # The reserved writer lock prevents changes while a second connection
            # takes a consistent SQLite backup, including committed WAL pages.
            folder=Path(path).parent/'backups'
            folder.mkdir(mode=0o700,exist_ok=True)
            fd, name=tempfile.mkstemp(prefix=f'pre-migration-{time.time_ns()}-',suffix='.sqlite3',dir=folder)
            os.close(fd)
            try:
                with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(name)) as target:
                    source.backup(target)
                    if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                        raise RuntimeError('Pre-migration backup failed integrity check')
            except Exception:
                Path(name).unlink(missing_ok=True)
                raise
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at INTEGER NOT NULL)')
        for version, apply in MIGRATIONS:
            if version not in applied:
                apply(db)
                db.execute('INSERT INTO schema_migrations VALUES(?,?)', (version, int(time.time())))

"""اسکیمای **واقعیِ** نسخه‌های قبلی پایگاه recorder — فقط برای تست.

چرا اینجا و نه ساختنِ یک پایگاه با «عدد نسخه‌ی دستکاری‌شده»: آن کار
فقط متادیتا را عوض می‌کند و ساختار واقعی را نمی‌سازد، پس تستی که با
آن نوشته شود دقیقاً همان باگی را از دست می‌دهد که باید بگیرد — پایگاهی
که عددش سازگار به نظر می‌رسد ولی **ستون‌هایش** فرق دارند.

این متن‌ها کپیِ عینیِ `_SCHEMA` در همان کامیت‌اند و **هرگز به‌روز
نمی‌شوند**: اگر با اسکیمای جاری همگام شوند، دیگر نسخه‌ی قدیمی نیستند.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TEHRAN = timezone(timedelta(hours=3, minutes=30))

#: اسکیمای نسخه ۱ (پیش از افزودن `conflicts` و `invalid_fields`).
SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    requested_at    TEXT NOT NULL,
    received_at     TEXT,
    source_time     TEXT,
    source          TEXT NOT NULL,
    is_live         INTEGER NOT NULL,
    endpoint        TEXT,
    status          TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    currency        TEXT NOT NULL,
    row_count       INTEGER,
    contract_count  INTEGER,
    rejected_count  INTEGER,
    raw_payload     BLOB,
    raw_bytes       INTEGER,
    error           TEXT
);
CREATE TABLE IF NOT EXISTS contract_specs (
    spec_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ins_code             TEXT NOT NULL,
    symbol               TEXT NOT NULL,
    option_type          TEXT NOT NULL,
    underlying           TEXT,
    underlying_ins_code  TEXT,
    strike               REAL,
    expiry               TEXT,
    contract_size        INTEGER,
    begin_date           TEXT,
    full_name            TEXT,
    first_seen_at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quotes (
    snapshot_id  TEXT NOT NULL,
    spec_id      INTEGER NOT NULL,
    ins_code     TEXT NOT NULL,
    bid          REAL,
    PRIMARY KEY (snapshot_id, ins_code)
);
CREATE TABLE IF NOT EXISTS underlying_quotes (
    snapshot_id  TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, symbol)
);
CREATE TABLE IF NOT EXISTS rejected_rows (
    snapshot_id  TEXT NOT NULL,
    row_index    INTEGER NOT NULL,
    side         TEXT NOT NULL,
    reason       TEXT NOT NULL,
    ins_code     TEXT
);
"""

#: اسکیمای نسخه ۲، **عیناً** از کامیت 4b39635.
#:
#: تفاوت حیاتی‌اش با نسخه ۳: `invalid_fields` ستون `ins_code` دارد و
#: `kind`/`key` ندارد، و `snapshots` ستون `invalid_field_count` ندارد.
#: همین دو تفاوت باعث `no such column: key` می‌شدند.
SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    requested_at    TEXT NOT NULL,
    received_at     TEXT,
    source_time     TEXT,
    source          TEXT NOT NULL,
    is_live         INTEGER NOT NULL,
    endpoint        TEXT,
    status          TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    currency        TEXT NOT NULL,
    row_count       INTEGER,
    contract_count  INTEGER,
    rejected_count  INTEGER,
    conflict_count  INTEGER,
    duplicate_count INTEGER,
    raw_payload     BLOB,
    raw_bytes       INTEGER,
    fingerprint     TEXT,
    error           TEXT
);
CREATE TABLE IF NOT EXISTS contract_specs (
    spec_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ins_code             TEXT NOT NULL,
    symbol               TEXT NOT NULL,
    option_type          TEXT NOT NULL,
    underlying           TEXT,
    underlying_ins_code  TEXT,
    strike               REAL,
    expiry               TEXT,
    contract_size        INTEGER,
    begin_date           TEXT,
    full_name            TEXT,
    first_seen_at        TEXT NOT NULL,
    UNIQUE (
        ins_code, symbol, option_type, underlying, underlying_ins_code,
        strike, expiry, contract_size, begin_date, full_name
    )
);
CREATE TABLE IF NOT EXISTS quotes (
    snapshot_id  TEXT NOT NULL,
    spec_id      INTEGER NOT NULL,
    ins_code     TEXT NOT NULL,
    bid          REAL,
    PRIMARY KEY (snapshot_id, ins_code)
);
CREATE TABLE IF NOT EXISTS underlying_quotes (
    snapshot_id  TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, symbol)
);
CREATE TABLE IF NOT EXISTS rejected_rows (
    snapshot_id  TEXT NOT NULL,
    row_index    INTEGER NOT NULL,
    side         TEXT NOT NULL,
    reason       TEXT NOT NULL,
    ins_code     TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
    snapshot_id  TEXT NOT NULL,
    kind         TEXT NOT NULL,
    key          TEXT NOT NULL,
    row_index    INTEGER NOT NULL,
    side         TEXT NOT NULL,
    reason       TEXT NOT NULL
);
-- ⚠️ ستون‌ها `ins_code` است، نه `kind`/`key` — همان تفاوتی که
-- بی‌صدا از بررسی نسخه رد می‌شد.
CREATE TABLE IF NOT EXISTS invalid_fields (
    snapshot_id  TEXT NOT NULL,
    ins_code     TEXT NOT NULL,
    reason       TEXT NOT NULL
);
"""

LEGACY_SCHEMAS = {1: SCHEMA_V1, 2: SCHEMA_V2}


def build_legacy_db(path: str | Path, version: int, kind: str = "test") -> Path:
    """یک پایگاه با ساختار **واقعیِ** نسخه‌ی قدیمی می‌سازد.

    یک ردیف نمونه هم می‌گذارد تا تست بتواند ثابت کند داده‌ی قبلی
    دست‌نخورده می‌ماند.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(destination))
    try:
        connection.executescript(LEGACY_SCHEMAS[version])
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)",
            [
                ("kind", kind),
                ("db_schema_version", str(version)),
                ("created_at", datetime(2026, 9, 10, 10, tzinfo=_TEHRAN).isoformat()),
            ],
        )
        connection.execute(
            """
            INSERT INTO snapshots (
                snapshot_id, requested_at, source, is_live, status,
                schema_version, currency, contract_count
            ) VALUES ('legacy-snap', '2026-09-10T10:00:00+03:30', 'fixture',
                      0, 'complete', 1, 'IRR', 7)
            """
        )
        connection.commit()
    finally:
        connection.close()
    return destination

"""ثبت ماندگار نوبت‌های دریافت داده‌ی بازار آپشن — یک SQLite، بدون وابستگی جدید.

**چرا این لایه لازم شد:** تا امروز پاسخ دیده‌بان بازار فقط ~۲۰ ثانیه در
**حافظه** کش می‌شد و دور ریخته می‌شد. هیچ‌جا زنجیره‌ی بازار به‌عنوان
تاریخچه نگه داشته نمی‌شد. هر روزی که ثبت نشود، برای همیشه رفته است.

**payload خام به‌صورت BLOB در همان پایگاه** ذخیره می‌شود، نه فایل کنار
آن: با فایل جداگانه، نوشتنِ فایل و تراکنش دیتابیس دو عمل جدا می‌شدند و
خرابی بین آن دو یعنی ردیفی که به فایلِ ناموجود اشاره می‌کند. داخل
تراکنش، یا هر دو هست یا هیچ‌کدام.

**اتمی بودن:** هر نوبت دریافت یک `snapshot_id` ثابت می‌گیرد. ردیف
snapshot اول با `status='partial'` نوشته می‌شود؛ payload، مشخصات،
قیمت‌ها و گذارِ نهایی به `complete` همه در **یک تراکنش** انجام می‌شوند.
برنامه اگر وسط کار بمیرد، ردیف `partial` می‌ماند و مصرف‌کننده ردش
می‌کند.

⚠️ `complete` یعنی «پاسخِ دریافت‌شده کامل ثبت و پردازش شد». **به معنی
تضمین پوشش کل بازار از سوی منبع نیست** — اگر منبع نصف بازار را داده
باشد، ما همان نصف را کامل ثبت کرده‌ایم.

**تفکیک زنده و آزمایشی:** نوع پایگاه هنگام ساخت در جدول `meta` مهر
می‌شود و در هر بازکردن بعدی کنترل می‌گردد. پایگاه `live` هرگز داده‌ی
fixture نمی‌پذیرد و پایگاه `test` هرگز داده‌ی زنده. این قید در **لایه‌ی
ذخیره‌سازی** است، نه در CLI، تا هیچ فلگی نتواند دورش بزند.
"""

from __future__ import annotations

import gzip
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data.raw_snapshot import CURRENCY, SCHEMA_VERSION, ExtractionResult

logger = logging.getLogger(__name__)

#: نسخه‌ی اسکیمای همین جدول‌ها (جدا از `SCHEMA_VERSION` نگاشت فیلدها).
DB_SCHEMA_VERSION = 1

#: انواع مجاز پایگاه. مخلوط‌شدنشان ممنوع است.
KIND_LIVE = "live"
KIND_TEST = "test"

STATUS_PARTIAL = "partial"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- یک ردیف به‌ازای هر نوبت دریافت.
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    requested_at    TEXT NOT NULL,   -- زمان شروع درخواست (timezone-aware)
    received_at     TEXT,            -- زمان دریافت پاسخ (timezone-aware)
    source_time     TEXT,            -- فقط اگر منبع داده باشد؛ وگرنه NULL
    source          TEXT NOT NULL,   -- tsetmc | fixture | ...
    is_live         INTEGER NOT NULL,
    endpoint        TEXT,
    status          TEXT NOT NULL,   -- partial | complete | failed
    schema_version  INTEGER NOT NULL,
    currency        TEXT NOT NULL,
    row_count       INTEGER,
    contract_count  INTEGER,
    rejected_count  INTEGER,
    raw_payload     BLOB,            -- JSON فشرده‌شده با gzip
    raw_bytes       INTEGER,
    error           TEXT
);
CREATE INDEX IF NOT EXISTS idx_snapshots_requested_at ON snapshots(requested_at);
CREATE INDEX IF NOT EXISTS idx_snapshots_status ON snapshots(status);

-- نسخه‌های مشخصات قرارداد. هر تغییر در مشخصات = ردیف تازه، نه ویرایش.
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
CREATE INDEX IF NOT EXISTS idx_specs_ins_code ON contract_specs(ins_code);

-- مشاهده‌ی قیمتی. کلید مرکب یعنی retry همان نوبت ردیف تکراری نمی‌سازد،
-- ولی دو نوبت مستقل با قیمت یکسان دو مشاهده‌ی جدا می‌مانند.
CREATE TABLE IF NOT EXISTS quotes (
    snapshot_id             TEXT NOT NULL,
    spec_id                 INTEGER NOT NULL,
    ins_code                TEXT NOT NULL,
    bid                     REAL,
    bid_qty                 INTEGER,
    ask                     REAL,
    ask_qty                 INTEGER,
    last_price              REAL,
    close_price             REAL,
    previous_close          REAL,
    volume                  INTEGER,
    value                   REAL,
    trade_count             INTEGER,
    open_interest           INTEGER,
    previous_open_interest  INTEGER,
    notional_value          REAL,
    remained_day            INTEGER,
    PRIMARY KEY (snapshot_id, ins_code),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id),
    FOREIGN KEY (spec_id) REFERENCES contract_specs(spec_id)
);
CREATE INDEX IF NOT EXISTS idx_quotes_ins_code ON quotes(ins_code);

-- آنچه از نماد پایه در همان payload آمده (نام، کد و سه قیمت — همین).
CREATE TABLE IF NOT EXISTS underlying_quotes (
    snapshot_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    ins_code        TEXT,
    last_price      REAL,
    close_price     REAL,
    previous_close  REAL,
    PRIMARY KEY (snapshot_id, symbol),
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
);

-- ردیف‌هایی که استخراج نشدند. بی‌صدا حذف نمی‌شوند.
CREATE TABLE IF NOT EXISTS rejected_rows (
    snapshot_id  TEXT NOT NULL,
    row_index    INTEGER NOT NULL,
    side         TEXT NOT NULL,
    reason       TEXT NOT NULL,
    ins_code     TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES snapshots(snapshot_id)
);
"""


class DatabaseKindMismatch(RuntimeError):
    """تلاش برای مخلوط‌کردن داده‌ی زنده و آزمایشی در یک پایگاه."""


@dataclass
class SnapshotStatus:
    """خلاصه‌ی وضعیت محلی — بدون هیچ درخواست شبکه‌ای."""

    kind: str
    db_path: str
    db_bytes: int
    total_snapshots: int
    last_attempt_at: str | None
    last_attempt_status: str | None
    last_complete_at: str | None
    last_complete_contracts: int | None
    last_error_at: str | None
    last_error: str | None
    distinct_contracts: int


class MarketRecorder:
    """ثبت نوبت‌های دریافت در یک فایل SQLite.

    Args:
        db_path: مسیر فایل پایگاه (پوشه‌اش در صورت نبود ساخته می‌شود)
        kind: `live` یا `test`. هنگام ساخت مهر می‌شود و در بازکردن‌های
            بعدی کنترل می‌گردد؛ ناسازگاری، خطا می‌دهد.
    """

    def __init__(self, db_path: str | Path, kind: str = KIND_LIVE) -> None:
        if kind not in (KIND_LIVE, KIND_TEST):
            raise ValueError(f"نوع پایگاه نامعتبر است: {kind!r}")
        self.db_path = Path(db_path)
        self.kind = kind
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()
        self._check_kind()

    # ------------------------------------------------------------------
    def _check_kind(self) -> None:
        """نوع پایگاه را مهر یا کنترل می‌کند.

        اولین بار مهر می‌شود؛ دفعات بعد اگر نوع نخواند، خطا می‌دهد.
        این قید عمداً اینجاست و نه در CLI: هیچ فلگی نباید بتواند
        داده‌ی fixture را وارد پایگاه زنده کند.
        """
        row = self._connection.execute(
            "SELECT value FROM meta WHERE key = 'kind'"
        ).fetchone()
        if row is None:
            self._connection.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [
                    ("kind", self.kind),
                    ("db_schema_version", str(DB_SCHEMA_VERSION)),
                    ("created_at", datetime.now().astimezone().isoformat()),
                ],
            )
            self._connection.commit()
            return
        if row["value"] != self.kind:
            raise DatabaseKindMismatch(
                f"این پایگاه از نوع «{row['value']}» است ولی با نوع "
                f"«{self.kind}» باز شد. داده‌ی زنده و آزمایشی نباید در یک "
                f"پایگاه مخلوط شوند؛ مسیر جداگانه بدهید."
            )

    def close(self) -> None:
        self._connection.close()

    @staticmethod
    def new_snapshot_id() -> str:
        """شناسه‌ی ثابت یک نوبت دریافت.

        بیرون از `record` ساخته می‌شود تا فراخوان بتواند **همان** نوبت
        را دوباره ذخیره کند بدون اینکه ردیف تکراری بسازد.
        """
        return uuid.uuid4().hex

    # ------------------------------------------------------------------
    def record(
        self,
        snapshot_id: str,
        payload: dict[str, Any],
        extraction: ExtractionResult,
        *,
        source: str,
        is_live: bool,
        requested_at: datetime,
        received_at: datetime,
        endpoint: str | None = None,
        source_time: datetime | None = None,
    ) -> int:
        """ثبت **اتمیک** یک نوبت کامل. تعداد قرارداد ثبت‌شده را برمی‌گرداند.

        همه‌چیز در یک تراکنش است: ردیف snapshot با `partial`، payload
        فشرده، نسخه‌های مشخصات، مشاهده‌ها، نمادهای پایه، ردیف‌های ردشده،
        و در آخر گذار به `complete`. خرابی در هر نقطه یعنی rollback کامل.

        فراخوانِ دوباره با همان `snapshot_id` (مثلاً retry ذخیره‌سازی)
        داده را بازنویسی می‌کند، **نه** اینکه ردیف تکراری بسازد.

        Raises:
            DatabaseKindMismatch: اگر منشأ داده با نوع پایگاه نخواند.
        """
        self._guard_kind(is_live)

        blob = gzip.compress(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        try:
            with self._connection:  # تراکنش: commit یا rollback کامل
                self._purge(snapshot_id)
                self._connection.execute(
                    """
                    INSERT INTO snapshots (
                        snapshot_id, requested_at, received_at, source_time,
                        source, is_live, endpoint, status, schema_version,
                        currency, row_count, contract_count, rejected_count,
                        raw_payload, raw_bytes, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        snapshot_id,
                        _stamp(requested_at),
                        _stamp(received_at),
                        _stamp(source_time) if source_time else None,
                        source,
                        int(is_live),
                        endpoint,
                        STATUS_PARTIAL,
                        SCHEMA_VERSION,
                        CURRENCY,
                        extraction.row_count,
                        extraction.contract_count,
                        len(extraction.rejected),
                        blob,
                        len(blob),
                    ),
                )
                written = self._write_rows(snapshot_id, extraction)
                self._connection.execute(
                    "UPDATE snapshots SET status = ? WHERE snapshot_id = ?",
                    (STATUS_COMPLETE, snapshot_id),
                )
        except sqlite3.Error:
            logger.exception("ثبت snapshot %s ناموفق بود؛ تراکنش برگشت خورد.", snapshot_id)
            raise

        logger.info(
            "snapshot %s ثبت شد: %s قرارداد، %s ردیف ردشده، %s KiB فشرده.",
            snapshot_id[:8], written, len(extraction.rejected), len(blob) // 1024,
        )
        return written

    def record_failure(
        self,
        snapshot_id: str,
        *,
        source: str,
        is_live: bool,
        requested_at: datetime,
        error: str,
        endpoint: str | None = None,
        received_at: datetime | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """ثبت یک نوبت **ناموفق** تا خطا در `--status` دیده شود.

        نبودِ داده هم یک واقعیت است: بدون این، یک قطعی چندروزه از
        تاریخچه نامرئی می‌ماند و بعداً «بازار آرام بود» تفسیر می‌شود.
        """
        self._guard_kind(is_live)
        blob = (
            gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            if payload is not None
            else None
        )
        with self._connection:
            self._purge(snapshot_id)
            self._connection.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, requested_at, received_at, source_time, source,
                    is_live, endpoint, status, schema_version, currency,
                    row_count, contract_count, rejected_count, raw_payload,
                    raw_bytes, error
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    _stamp(requested_at),
                    _stamp(received_at) if received_at else None,
                    source,
                    int(is_live),
                    endpoint,
                    STATUS_FAILED,
                    SCHEMA_VERSION,
                    CURRENCY,
                    blob,
                    len(blob) if blob else None,
                    error,
                ),
            )

    # ------------------------------------------------------------------
    def _guard_kind(self, is_live: bool) -> None:
        expected = KIND_LIVE if is_live else KIND_TEST
        if expected != self.kind:
            raise DatabaseKindMismatch(
                f"داده‌ی {'زنده' if is_live else 'آزمایشی'} در پایگاه از نوع "
                f"«{self.kind}» ثبت نمی‌شود. برای داده‌ی آزمایشی پایگاه "
                f"جداگانه بسازید."
            )

    def _purge(self, snapshot_id: str) -> None:
        """پاک‌کردن آثار قبلی همان `snapshot_id` (برای retry ذخیره‌سازی).

        ترتیب مهم است: فرزندها قبل از والد، چون `foreign_keys` روشن است.
        """
        for table in ("quotes", "underlying_quotes", "rejected_rows"):
            self._connection.execute(
                f"DELETE FROM {table} WHERE snapshot_id = ?", (snapshot_id,)
            )
        self._connection.execute(
            "DELETE FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
        )

    def _write_rows(self, snapshot_id: str, extraction: ExtractionResult) -> int:
        written = 0
        for quote in extraction.quotes:
            spec_id = self._spec_id(quote.spec)
            self._connection.execute(
                """
                INSERT OR REPLACE INTO quotes (
                    snapshot_id, spec_id, ins_code, bid, bid_qty, ask, ask_qty,
                    last_price, close_price, previous_close, volume, value,
                    trade_count, open_interest, previous_open_interest,
                    notional_value, remained_day
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, spec_id, quote.spec.ins_code,
                    quote.bid, quote.bid_qty, quote.ask, quote.ask_qty,
                    quote.last_price, quote.close_price, quote.previous_close,
                    quote.volume, quote.value, quote.trade_count,
                    quote.open_interest, quote.previous_open_interest,
                    quote.notional_value, quote.remained_day,
                ),
            )
            written += 1

        for underlying in extraction.underlyings:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO underlying_quotes (
                    snapshot_id, symbol, ins_code, last_price, close_price,
                    previous_close
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, underlying.symbol, underlying.ins_code,
                    underlying.last_price, underlying.close_price,
                    underlying.previous_close,
                ),
            )

        for rejected in extraction.rejected:
            self._connection.execute(
                """
                INSERT INTO rejected_rows (snapshot_id, row_index, side, reason, ins_code)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, rejected.row_index, rejected.side,
                    rejected.reason, rejected.ins_code,
                ),
            )
        return written

    def _spec_id(self, spec) -> int:
        """شناسه‌ی نسخه‌ی مشخصات؛ در صورت نبود ساخته می‌شود.

        یکتایی روی **کل مشخصات** است نه فقط `ins_code`: اگر منبع فردا
        استرایک یا اندازه‌ی قرارداد دیگری بدهد، نسخه‌ی تازه‌ای ساخته
        می‌شود و مشاهده‌های قبلی همچنان به نسخه‌ی خودشان وصل می‌مانند.
        """
        values = (
            spec.ins_code, spec.symbol, spec.option_type, spec.underlying,
            spec.underlying_ins_code, spec.strike, spec.expiry,
            spec.contract_size, spec.begin_date, spec.full_name,
        )
        row = self._connection.execute(
            """
            SELECT spec_id FROM contract_specs
            WHERE ins_code IS ? AND symbol IS ? AND option_type IS ?
              AND underlying IS ? AND underlying_ins_code IS ? AND strike IS ?
              AND expiry IS ? AND contract_size IS ? AND begin_date IS ?
              AND full_name IS ?
            """,
            values,
        ).fetchone()
        if row is not None:
            return int(row["spec_id"])
        cursor = self._connection.execute(
            """
            INSERT INTO contract_specs (
                ins_code, symbol, option_type, underlying, underlying_ins_code,
                strike, expiry, contract_size, begin_date, full_name, first_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values, datetime.now().astimezone().isoformat()),
        )
        return int(cursor.lastrowid)

    # ------------------------------------------------------------------
    def status(self) -> SnapshotStatus:
        """خلاصه‌ی وضعیت — **فقط از روی پایگاه محلی**، بدون هیچ درخواستی."""
        conn = self._connection
        total = conn.execute("SELECT COUNT(*) AS n FROM snapshots").fetchone()["n"]
        attempt = conn.execute(
            "SELECT requested_at, status FROM snapshots"
            " ORDER BY requested_at DESC LIMIT 1"
        ).fetchone()
        complete = conn.execute(
            "SELECT received_at, contract_count FROM snapshots"
            " WHERE status = ? ORDER BY requested_at DESC LIMIT 1",
            (STATUS_COMPLETE,),
        ).fetchone()
        failure = conn.execute(
            "SELECT requested_at, error FROM snapshots"
            " WHERE status = ? ORDER BY requested_at DESC LIMIT 1",
            (STATUS_FAILED,),
        ).fetchone()
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT ins_code) AS n FROM quotes"
        ).fetchone()["n"]
        return SnapshotStatus(
            kind=self.kind,
            db_path=str(self.db_path),
            db_bytes=self.db_path.stat().st_size if self.db_path.exists() else 0,
            total_snapshots=int(total),
            last_attempt_at=attempt["requested_at"] if attempt else None,
            last_attempt_status=attempt["status"] if attempt else None,
            last_complete_at=complete["received_at"] if complete else None,
            last_complete_contracts=(
                int(complete["contract_count"])
                if complete and complete["contract_count"] is not None
                else None
            ),
            last_error_at=failure["requested_at"] if failure else None,
            last_error=failure["error"] if failure else None,
            distinct_contracts=int(distinct),
        )

    def backup_to(self, target: str | Path) -> Path:
        """پشتیبان **سازگار** با استفاده از API خودِ SQLite.

        کپی ساده‌ی فایل هنگام نوشتن می‌تواند پایگاه نیمه‌نوشته بدهد.
        `sqlite3.Connection.backup` قفل‌ها را رعایت می‌کند و روی پایگاهِ
        در حال استفاده هم نسخه‌ی سالم می‌سازد.
        """
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(destination)) as backup:
            self._connection.backup(backup)
        return destination


def _stamp(moment: datetime) -> str:
    """ISO با منطقه‌ی زمانی. زمان بدون منطقه پذیرفته **نمی‌شود**.

    این قید عمدی است: تاریخچه‌ای که ندانیم ساعتش برای کجاست، بعداً
    قابل هم‌ترازی با هیچ منبع دیگری نیست.
    """
    if moment.tzinfo is None:
        raise ValueError("زمان باید timezone-aware باشد؛ زمان بدون منطقه ثبت نمی‌شود.")
    return moment.isoformat()

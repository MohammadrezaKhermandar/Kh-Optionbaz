"""خواندنِ تداومِ معامله از پایگاه **خام** recorder — فقط‌خواندنی.

`scripts/record_market.py` زنجیره‌ی کامل بازار را **پیش از** هر فیلتری
ثبت می‌کند. این ماژول از همان تاریخچه فقط می‌**خواند** تا غربالگر
بفهمد یک قرارداد در جلسه‌های گذشته واقعاً معامله می‌شده یا نه.

**قرارداد سختِ این ماژول: هیچ نوشتنی.**

* اتصال با `mode=ro` باز می‌شود، پس حتی یک `UPDATE` اتفاقی هم خطا
  می‌دهد.
* فایلِ نبوده **ساخته نمی‌شود** (`uri=True` + `mode=ro` این را تضمین
  می‌کند؛ `sqlite3.connect` معمولی فایل خالی می‌ساخت).
* اسکیما بررسی می‌شود ولی هرگز مهاجرت داده نمی‌شود.

چرا این‌قدر تأکید: غربالگر نباید به ثبت خام نشت کند. حذف یک قرارداد از
**پیشنهادها** ربطی به تاریخچه‌اش ندارد؛ داده‌ی همه‌ی قراردادها باید
دست‌نخورده بماند تا فردا بشود همین تصمیم را دوباره سنجید.

**«جلسه» یعنی روز.** چند snapshot در یک روز، یک جلسه شمرده می‌شود:
وگرنه قراردادی که در یک روزِ پرنوسان ده بار ثبت شده، «ده جلسه تداوم»
نشان می‌داد.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from market.tradability import HistoryStats

logger = logging.getLogger(__name__)

#: نسخه‌ی اسکیمای recorder که این خواننده می‌فهمد.
SUPPORTED_SCHEMA = 3

_UNKNOWN = HistoryStats(known=False)


class MarketHistoryReader:
    """آمار تداومِ معامله‌ی هر قرارداد، از snapshotهای ثبت‌شده.

    Args:
        db_path: مسیر پایگاه recorder (`var/recorder/market.db`)
        lookback_sessions: چند جلسه‌ی اخیر سنجیده شود

    هیچ استثنایی به بیرون نمی‌دهد: نبودِ پایگاه، اسکیمای ناشناخته یا
    خرابی فایل همه به «نمی‌دانیم» ترجمه می‌شوند — چون غربالگر باید
    نبودِ داده را از نقدشوندگیِ بد جدا کند، و خطا انداختن این تمایز را
    به یک ۵۰۰ تبدیل می‌کرد.
    """

    def __init__(self, db_path: str | Path, lookback_sessions: int = 20) -> None:
        self.db_path = Path(db_path)
        self.lookback_sessions = max(int(lookback_sessions), 1)
        self._connection: sqlite3.Connection | None = None
        self._cache: dict[str, HistoryStats] | None = None
        self._unavailable_reason: str | None = None

    # -- چرخه‌ی عمر ---------------------------------------------------------
    def _connect(self) -> sqlite3.Connection | None:
        if self._connection is not None:
            return self._connection
        if self._unavailable_reason is not None:
            return None
        if not self.db_path.exists():
            self._unavailable_reason = f"پایگاه تاریخچه نیست: {self.db_path}"
            logger.info("%s؛ تداوم معامله «نامعلوم» می‌ماند.", self._unavailable_reason)
            return None
        try:
            # `mode=ro` تضمینِ فقط‌خواندنی بودن است، نه یک قرارداد شفاهی.
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            version = connection.execute(
                "SELECT value FROM meta WHERE key = 'db_schema_version'"
            ).fetchone()
            if version is None or int(version[0]) != SUPPORTED_SCHEMA:
                found = "نامعلوم" if version is None else version[0]
                self._unavailable_reason = (
                    f"نسخه‌ی اسکیمای تاریخچه {found} است، نه {SUPPORTED_SCHEMA}"
                )
                logger.warning("%s؛ تاریخچه نادیده گرفته شد.", self._unavailable_reason)
                connection.close()
                return None
        except sqlite3.Error as exc:
            self._unavailable_reason = f"پایگاه تاریخچه باز نشد: {exc}"
            logger.warning("%s؛ تداوم معامله «نامعلوم» می‌ماند.", self._unavailable_reason)
            return None
        self._connection = connection
        return connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> MarketHistoryReader:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def available(self) -> bool:
        """آیا تاریخچه‌ای برای خواندن هست؟"""
        return self._connect() is not None

    @property
    def unavailable_reason(self) -> str | None:
        self._connect()
        return self._unavailable_reason

    # -- آمار ---------------------------------------------------------------
    def stats_for(self, ins_code: str) -> HistoryStats:
        """آمار تداوم یک قرارداد. نبودِ داده = `known=False`."""
        table = self._load()
        if table is None:
            return _UNKNOWN
        return table.get(ins_code, self._empty_for_known_history())

    def _empty_for_known_history(self) -> HistoryStats:
        """قراردادی که در هیچ‌کدام از جلسه‌های ثبت‌شده دیده نشده.

        این هم «نمی‌دانیم» است، نه «صفر معامله»: ممکن است قرارداد تازه
        باز شده باشد و اصلاً در آن جلسه‌ها وجود نداشته.
        """
        return _UNKNOWN

    def _load(self) -> dict[str, HistoryStats] | None:
        if self._cache is not None:
            return self._cache
        connection = self._connect()
        if connection is None:
            return None
        try:
            sessions = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT date(requested_at) AS session
                      FROM snapshots
                     WHERE status != 'failed'
                     ORDER BY session DESC
                     LIMIT ?
                    """,
                    (self.lookback_sessions,),
                )
            ]
            if not sessions:
                self._cache = {}
                return self._cache

            placeholders = ",".join("?" for _ in sessions)
            rows = connection.execute(
                f"""
                SELECT q.ins_code                                   AS ins_code,
                       COUNT(DISTINCT date(s.requested_at))          AS sessions,
                       COUNT(DISTINCT CASE WHEN q.trade_count > 0
                                           THEN date(s.requested_at) END) AS traded,
                       COUNT(DISTINCT CASE WHEN q.bid IS NOT NULL
                                            AND q.ask IS NOT NULL
                                           THEN date(s.requested_at) END) AS quoted,
                       MIN(date(s.requested_at))                     AS first_session,
                       MAX(date(s.requested_at))                     AS last_session
                  FROM quotes q
                  JOIN snapshots s ON s.snapshot_id = q.snapshot_id
                 WHERE s.status != 'failed'
                   AND date(s.requested_at) IN ({placeholders})
                 GROUP BY q.ins_code
                """,
                sessions,
            ).fetchall()
        except sqlite3.Error as exc:
            self._unavailable_reason = f"خواندن تاریخچه شکست خورد: {exc}"
            logger.warning("%s؛ تداوم معامله «نامعلوم» ماند.", self._unavailable_reason)
            self._cache = None
            self.close()
            return None

        self._cache = {
            str(row["ins_code"]): HistoryStats(
                sessions=int(row["sessions"]),
                sessions_with_trades=int(row["traded"]),
                sessions_with_both_quotes=int(row["quoted"]),
                first_session=row["first_session"],
                last_session=row["last_session"],
                known=True,
            )
            for row in rows
        }
        logger.info(
            "تاریخچه‌ی نقدشوندگی خوانده شد: %s قرارداد در %s جلسه‌ی اخیر.",
            len(self._cache), len(sessions),
        )
        return self._cache

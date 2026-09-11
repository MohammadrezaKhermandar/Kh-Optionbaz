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

**«جلسه» یعنی یک روزِ معاملاتیِ تأییدشده.** دو قید، و هر دو لازم‌اند:

۱. چند snapshot در یک روز، **یک** جلسه شمرده می‌شود؛ وگرنه قراردادی که
   در یک روزِ پرنوسان ده بار ثبت شده «ده جلسه تداوم» نشان می‌داد.
۲. روزی که تقویم **روز معاملاتی** نداند، اصلاً شمرده نمی‌شود. recorder
   در روز تعطیل هم snapshot می‌گیرد و مقادیرِ آن snapshot ماندهٔ جلسه‌ی
   قبل است — منبع مهر زمانی نمی‌دهد که بشود خلافش را نشان داد. شمردنِ
   چنین روزی به‌عنوان «روزِ دارای معامله» ادعای نادرستی است.

اگر تقویمی داده نشود، آمار `sessions_verified=False` برمی‌گردد و
غربالگر آن را «نامعلوم» می‌خواند، نه «خوب».

**تازگی:** پایگاه در طول اجرای برنامه پر می‌شود. این خواننده با
`mtime`/اندازه‌ی فایل می‌فهمد چیزی عوض شده و دوباره می‌خواند، و اگر
پایگاه اولش نبوده و بعداً ساخته شود همان لحظه وصل می‌شود — بدون
راه‌اندازی مجدد.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from datetime import date
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

    def __init__(
        self,
        db_path: str | Path,
        lookback_sessions: int = 20,
        is_trading_day: Callable[[date], bool] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.lookback_sessions = max(int(lookback_sessions), 1)
        #: تابعِ «این روز، روز معاملاتی بود؟». نبودنش یعنی نمی‌شود
        #: تأیید کرد، و آمار «تأییدنشده» برمی‌گردد.
        self.is_trading_day = is_trading_day
        self._connection: sqlite3.Connection | None = None
        self._cache: dict[str, HistoryStats] | None = None
        self._unavailable_reason: str | None = None
        #: امضای فایل در لحظه‌ی آخرین خواندن؛ تغییرش یعنی داده‌ی تازه.
        self._signature: tuple[float, int] | None = None
        self._verified = False
        self._skipped_non_trading = 0

    # -- چرخه‌ی عمر ---------------------------------------------------------
    def _file_signature(self) -> tuple[float, int] | None:
        """امضای فایل (زمان تغییر، اندازه). `None` یعنی فایل نیست."""
        try:
            stat = self.db_path.stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size)

    def _connect(self) -> sqlite3.Connection | None:
        if self._connection is not None:
            return self._connection
        # ⚠️ دلیلِ در دسترس نبودن **کش نمی‌شود**: پایگاه ممکن است چند
        # دقیقه بعد به‌دست recorder ساخته شود و کاربر نباید برای دیدنش
        # برنامه را دوباره راه بیندازد.
        self._unavailable_reason = None
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

    def refresh(self) -> None:
        """کشِ درون‌حافظه‌ای را دور می‌ریزد تا خواندن بعدی تازه باشد."""
        self._cache = None
        self._signature = None
        self.close()

    def _load(self) -> dict[str, HistoryStats] | None:
        # فایل که عوض شده باشد، خواندهٔ قبلی کهنه است. بدون این، داده‌ای
        # که recorder همین حالا نوشته تا راه‌اندازی بعدی دیده نمی‌شد.
        signature = self._file_signature()
        if self._cache is not None and signature == self._signature:
            return self._cache
        if self._cache is not None:
            logger.info("پایگاه تاریخچه تغییر کرد؛ دوباره خوانده می‌شود.")
            self._cache = None
            self.close()

        connection = self._connect()
        if connection is None:
            return None
        self._signature = signature
        try:
            recorded = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT date(requested_at) AS session
                      FROM snapshots
                     WHERE status != 'failed'
                     ORDER BY session DESC
                     """
                )
            ]
            sessions, skipped = self._trading_sessions(recorded)
            if not sessions:
                self._cache = {}
                self._skipped_non_trading = skipped
                self._verified = self.is_trading_day is not None
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

        self._skipped_non_trading = skipped
        self._verified = self.is_trading_day is not None
        self._cache = {
            str(row["ins_code"]): HistoryStats(
                sessions=int(row["sessions"]),
                sessions_with_trades=int(row["traded"]),
                sessions_with_both_quotes=int(row["quoted"]),
                first_session=row["first_session"],
                last_session=row["last_session"],
                known=True,
                sessions_verified=self._verified,
                skipped_non_trading_days=skipped,
            )
            for row in rows
        }
        logger.info(
            "تاریخچه‌ی نقدشوندگی خوانده شد: %s قرارداد در %s جلسه‌ی معاملاتی "
            "(%s روز غیرمعاملاتی کنار گذاشته شد، تأیید تقویم: %s).",
            len(self._cache), len(sessions), skipped,
            "بله" if self._verified else "خیر",
        )
        return self._cache

    def _trading_sessions(self, recorded: list[str]) -> tuple[list[str], int]:
        """روزهای ثبت‌شده را به روزهای **معاملاتی** فیلتر می‌کند.

        بدون تقویم هیچ روزی کنار گذاشته نمی‌شود، ولی آمار «تأییدنشده»
        علامت می‌خورد — حذف نکردن با تأیید کردن یکی نیست.
        """
        if self.is_trading_day is None:
            return recorded[: self.lookback_sessions], 0

        kept: list[str] = []
        skipped = 0
        for day in recorded:
            try:
                is_trading = self.is_trading_day(date.fromisoformat(day))
            except Exception as exc:
                logger.warning("تقویم برای %s جواب نداد: %s", day, exc)
                # تقویمی که جواب ندهد، تأییدی نداده: روز شمرده نمی‌شود.
                skipped += 1
                continue
            if is_trading:
                kept.append(day)
                if len(kept) >= self.lookback_sessions:
                    break
            else:
                skipped += 1
        return kept, skipped

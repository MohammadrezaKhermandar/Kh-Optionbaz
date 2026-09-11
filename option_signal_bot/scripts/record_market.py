"""ثبت یک نوبت داده‌ی واقعی بازار آپشن — پیش از هر فیلتر کیفیت.

این ابزار **کاملاً مستقل** از حساب کارگزاری، تلگرام و لایه‌ی اجراست.
فقط از API عمومی TSETMC می‌خواند (بدون لاگین و توکن) و در یک SQLite
می‌نویسد.

    # یک نوبت ثبت زنده
    python scripts/record_market.py --once

    # همان، روی پاسخ ضبط‌شده و در پایگاه آزمایشیِ جدا
    python scripts/record_market.py --once --kind test \\
        --fixture tests/fixtures/tsetmc_option_market_watch.json \\
        --db var/recorder/test.db

    # وضعیت محلی (بدون هیچ درخواست شبکه‌ای)
    python scripts/record_market.py --status

    # ثبت منظم تا وقتی با Ctrl+C متوقفش کنید
    python scripts/record_market.py --loop

**`--loop` چه تضمین‌هایی دارد** (جزئیات در `market/schedule.py`):

* نوبت‌ها روی **ساعت دیوار** هم‌تراز می‌شوند، نه از پایان نوبت قبلی —
  پس هم‌پوشانی ممکن نیست و نوبت‌های ازدست‌رفته (خوابِ ویندوز، جهش
  ساعت) **پرش** می‌شوند، نه اینکه پشت سر هم اجرا شوند.
* بیرون بازه‌ی فعالیت هیچ دریافتی انجام نمی‌شود و **هیچ ردیفی هم ثبت
  نمی‌شود** — انتظار با خرابی اشتباه نمی‌شود.
* خطای موقت حلقه را نمی‌خواباند؛ خطای تنظیمات، نسخه‌ی پایگاه و قفل
  **پیش از** ورود به حلقه متوقف می‌کنند.
* قفل بین‌پردازه‌ای مانع اجرای دو نمونه روی یک پایگاه می‌شود.

تاریخچه‌ی ازدست‌رفته بازسازی **نمی‌شود**: اگر ماشین یک روز خاموش
بماند، آن روز رفته و snapshot امروز جایش را نمی‌گیرد.

**درباره‌ی کش:** این ابزار عمداً مستقیم `HttpPayloadSource.fetch()` را
صدا می‌زند و از `TsetmcOptionChainClient` رد می‌شود. آن کلاینت هم
فیلتر کیفیت دارد و هم کش ~۲۰ ثانیه‌ای در حافظه؛ ثبت از روی کش یعنی
زمان دریافتِ ثبت‌شده با زمان واقعیِ داده نمی‌خواند. پس هر اجرا یک
**دریافت تازه** است.
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time as time_module
from datetime import datetime, time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import force_utf8_stdio
from config.loader import SettingsError, load_settings, resolve_path, section
from data.raw_snapshot import ExtractionResult, SnapshotPayloadError, extract
from data.tsetmc_option_chain_client import (
    OPTION_WATCH_URL,
    FilePayloadSource,
    HttpPayloadSource,
)
from market.schedule import (
    RecordingWindow,
    decide,
    next_tick,
    resolve_timezone,
    skipped_ticks,
)
from storage.market_recorder import (
    KIND_LIVE,
    KIND_TEST,
    DatabaseKindMismatch,
    MarketRecorder,
    SchemaVersionMismatch,
    SnapshotConflict,
)
from storage.process_lock import LockUnavailable, ProcessLock

logger = logging.getLogger("record_market")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_MISUSE = 2


def _now() -> datetime:
    """اکنون، **با منطقه‌ی زمانی محلی صریح**. زمان بدون منطقه ثبت نمی‌شود."""
    return datetime.now().astimezone()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ثبت یک نوبت داده‌ی خام بازار آپشن (بدون فیلتر کیفیت)"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="یک نوبت ثبت و خروج")
    mode.add_argument(
        "--status", action="store_true", help="وضعیت محلی؛ هیچ درخواست شبکه‌ای نمی‌زند"
    )
    mode.add_argument(
        "--loop",
        action="store_true",
        help="ثبت منظم تا وقتی با Ctrl+C متوقفش کنید",
    )
    parser.add_argument("--config", type=Path, default=None, help="مسیر settings.yaml")
    # پیش‌فرضِ `None` عمدی است: تشخیص «کاربر صریحاً داد» از «نداد» تنها
    # راهی است که تقدم CLI بر تنظیمات معنا پیدا کند.
    parser.add_argument(
        "--db", default=None, help="مسیر پایگاه؛ بر مقدار تنظیمات مقدم است"
    )
    parser.add_argument(
        "--kind",
        choices=(KIND_LIVE, KIND_TEST),
        default=KIND_LIVE,
        help="نوع پایگاه. داده‌ی fixture فقط در پایگاه «test» ثبت می‌شود.",
    )
    parser.add_argument(
        "--fixture",
        default=None,
        help="پخش یک پاسخ ضبط‌شده به‌جای دریافت زنده. نیازمند --kind test.",
    )
    parser.add_argument("--market", type=int, default=None, help="0=همه، 1=بورس، 2=فرابورس")
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument(
        "--interval", type=int, default=None, help="فاصله‌ی نوبت‌ها (ثانیه)"
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def resolve_options(args: argparse.Namespace) -> dict:
    """ادغام تنظیمات پروژه با آرگومان‌های CLI.

    **تنها منبع پیش‌فرض‌ها `config/loader.py` است**؛ اینجا هیچ عدد یا
    مسیر پیش‌فرضی تکرار نمی‌شود. آرگومان صریح CLI بر تنظیمات مقدم است.

    مسیر پایگاه از `kind` می‌آید: نوع `test` به `test_sqlite_path`
    می‌رود، پس `--kind test` بدون `--db` هم پایگاه زنده را لمس نمی‌کند.
    """
    config = section(load_settings(args.config), "recorder")
    key = "sqlite_path" if args.kind == KIND_LIVE else "test_sqlite_path"
    return {
        "db": Path(args.db) if args.db else resolve_path(config[key]),
        "market": config["market"] if args.market is None else args.market,
        "timeout": config["timeout"] if args.timeout is None else args.timeout,
        "retries": config["retries"] if args.retries is None else args.retries,
        "interval": (
            config["interval_seconds"] if args.interval is None else args.interval
        ),
        "timezone": config["timezone"],
        "window": RecordingWindow(
            start=_clock(config["session_start"], "session_start"),
            end=_clock(config["session_end"], "session_end"),
            closing_grace_seconds=int(config["closing_grace_seconds"]),
        ),
    }


def _clock(value: str, field: str) -> time:
    """`"09:00"` را به `time` تبدیل می‌کند، با خطای خوانا."""
    try:
        hour, minute = (int(part) for part in str(value).split(":", 1))
        return time(hour, minute)
    except (TypeError, ValueError) as exc:
        raise SettingsError(
            f"مقدار recorder.{field} باید به شکل «HH:MM» باشد، نه {value!r}."
        ) from exc


def _guard_kind_combination(args: argparse.Namespace, is_live: bool) -> int | None:
    """قیدهای نوع پایگاه، **پیش از** هر شبکه و پیش از ساخت پایگاه.

    `None` یعنی ترکیب مجاز است.
    """
    if not is_live and args.kind != KIND_TEST:
        print(
            "خطا: داده‌ی fixture فقط در پایگاه آزمایشی ثبت می‌شود.\n"
            "      --kind test را هم بدهید.",
            file=sys.stderr,
        )
        return EXIT_MISUSE
    if is_live and args.kind == KIND_TEST:
        print(
            "خطا: --kind test بدون --fixture یعنی داده‌ی زنده در پایگاه\n"
            "      آزمایشی، که همان مخلوط‌شدن است. یا --fixture بدهید یا\n"
            "      --kind live.",
            file=sys.stderr,
        )
        return EXIT_MISUSE
    return None


def _build_source(args: argparse.Namespace, options: dict) -> Any:
    """منبع پاسخ. یک بار ساخته و در حلقه بازاستفاده می‌شود.

    `HttpPayloadSource` حالت درون‌حافظه‌ای ندارد و هر `fetch` یک
    درخواست تازه است، پس بازاستفاده‌اش داده‌ی کهنه نمی‌دهد.
    """
    if args.fixture:
        return FilePayloadSource(args.fixture)
    return HttpPayloadSource(
        market=options["market"],
        timeout=options["timeout"],
        retries=options["retries"],
    )


def run_once(args: argparse.Namespace, options: dict) -> int:
    """یک نوبت دریافت و ثبت. کد خروجی: ۰ موفق، ۱ ناموفق، ۲ استفاده‌ی نادرست."""
    is_live = args.fixture is None
    guard = _guard_kind_combination(args, is_live)
    if guard is not None:
        return guard

    source = _build_source(args, options)
    endpoint = args.fixture or OPTION_WATCH_URL.format(market=options["market"])

    try:
        recorder = MarketRecorder(options["db"], kind=args.kind)
    except (DatabaseKindMismatch, SchemaVersionMismatch) as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE
    except sqlite3.Error as exc:
        print(f"خطا: پایگاه باز نشد: {exc}", file=sys.stderr)
        return EXIT_FAILED

    try:
        return _record_once(args, options, recorder, source, endpoint, is_live)
    finally:
        recorder.close()


def _record_once(
    args: argparse.Namespace,
    options: dict,
    recorder: MarketRecorder,
    source: Any,
    endpoint: str,
    is_live: bool,
) -> int:
    snapshot_id = recorder.new_snapshot_id()
    requested_at = _now()

    try:
        payload = source.fetch()
        received_at = _now()
    except Exception as exc:  # نبودِ داده هم باید در تاریخچه دیده شود
        return _note_failure(
            recorder, snapshot_id, source, endpoint, is_live, requested_at,
            f"دریافت ناموفق: {exc}", None, None,
        )

    try:
        extraction = extract(payload)
    except SnapshotPayloadError as exc:
        # ساختار غیرمنتظره «موفق با صفر قرارداد» نیست. payload هرچه که
        # باشد نگه داشته می‌شود — حتی وقتی dict نیست — تا بعداً بشود
        # فهمید منبع واقعاً چه فرستاده بود.
        return _note_failure(
            recorder, snapshot_id, source, endpoint, is_live, requested_at,
            f"ساختار پاسخ نامعتبر: {exc}", received_at, payload,
        )

    if extraction.contract_count == 0:
        return _note_failure(
            recorder, snapshot_id, source, endpoint, is_live, requested_at,
            f"پاسخ هیچ قرارداد قابل ثبتی نداشت ({extraction.row_count} ردیف، "
            f"{len(extraction.rejected)} ردشده، {len(extraction.conflicts)} تعارض)",
            received_at, payload,
        )

    try:
        written = recorder.record(
            snapshot_id, payload, extraction,
            source=source.source_name,
            is_live=is_live,
            requested_at=requested_at,
            received_at=received_at,
            endpoint=endpoint,
            # ⚠️ دیده‌بان آپشن مهر زمانی منبع ندارد؛ جعلش نمی‌کنیم.
            source_time=None,
        )
    except (DatabaseKindMismatch, SnapshotConflict) as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE
    except sqlite3.Error as exc:
        # تراکنش برگشته. حالا تلاش می‌کنیم خودِ شکست ثبت شود؛ اگر پایگاه
        # اصلاً قابل نوشتن نباشد همین هم شکست می‌خورد و صریح گفته می‌شود.
        return _note_failure(
            recorder, snapshot_id, source, endpoint, is_live, requested_at,
            f"ثبت ناموفق (تراکنش برگشت خورد): {exc}", received_at, payload,
        )

    _print_summary(args, options, recorder, extraction, snapshot_id, written, is_live)
    return EXIT_OK


def _note_failure(
    recorder: MarketRecorder,
    snapshot_id: str,
    source: Any,
    endpoint: str,
    is_live: bool,
    requested_at: datetime,
    message: str,
    received_at: datetime | None,
    payload: Any,
) -> int:
    """شکست را در پایگاه ثبت می‌کند و کد خروجی مناسب برمی‌گرداند.

    اگر خودِ پایگاه قابل نوشتن نباشد، تضمینی برای ثبت وجود ندارد:
    آن‌وقت خطای روشن روی stderr می‌رود و خروج غیرصفر می‌ماند. وعده‌ی
    «هر خطایی ثبت می‌شود» روی پایگاه خراب، وعده‌ی بی‌پایه است.
    """
    print(message, file=sys.stderr)
    try:
        recorder.record_failure(
            snapshot_id,
            source=source.source_name,
            is_live=is_live,
            requested_at=requested_at,
            received_at=received_at,
            error=message,
            endpoint=endpoint,
            payload=payload,
        )
    except (sqlite3.Error, SnapshotConflict, DatabaseKindMismatch) as exc:
        print(
            f"⚠️ ثبت خودِ خطا هم در پایگاه ممکن نشد: {exc}\n"
            f"   یعنی این نوبت در تاریخچه دیده نمی‌شود.",
            file=sys.stderr,
        )
    return EXIT_FAILED


def _print_summary(
    args: argparse.Namespace,
    options: dict,
    recorder: MarketRecorder,
    extraction: ExtractionResult,
    snapshot_id: str,
    written: int,
    is_live: bool,
) -> None:
    status = recorder.status()
    print(f"snapshot ثبت شد: {snapshot_id}")
    print(f"  پایگاه         : {options['db']}")
    print(f"  نوع            : {args.kind}   ({'زنده' if is_live else 'آزمایشی'})")
    print(f"  ردیف استرایک   : {extraction.row_count}")
    print(f"  قرارداد ثبت‌شده : {written}")
    print(f"  تکرار یکسان    : {extraction.duplicate_count}")
    print(f"  تعارض          : {len(extraction.conflicts)}")
    print(f"  ردیف ردشده     : {len(extraction.rejected)}")
    print(f"  فیلد نامعتبر   : {extraction.invalid_field_count}")
    print(f"  نماد پایه      : {len(extraction.underlyings)}")
    print(f"  وضعیت          : {status.last_attempt_status}")
    print(f"  حجم پایگاه     : {status.db_bytes / 1024:.0f} KiB")
    for label, reasons in (
        ("علت‌های رد شدن", [r.reason for r in extraction.rejected]),
        ("تعارض‌ها", [c.reason for c in extraction.conflicts]),
        (
            "فیلدهای نامعتبر",
            [r for q in extraction.quotes for r in q.invalid_fields]
            + [r for u in extraction.underlyings for r in u.invalid_fields],
        ),
    ):
        if not reasons:
            continue
        print(f"  {label}:")
        counts: dict[str, int] = {}
        for reason in reasons:
            counts[reason] = counts.get(reason, 0) + 1
        for reason, count in sorted(counts.items(), key=lambda pair: -pair[1])[:5]:
            print(f"    {count:5}  {reason}")


def show_status(args: argparse.Namespace, options: dict) -> int:
    """وضعیت محلی. هیچ درخواست شبکه‌ای زده نمی‌شود."""
    if not Path(options["db"]).exists():
        print(f"پایگاهی در {options['db']} نیست. هنوز چیزی ثبت نشده.")
        return EXIT_OK
    try:
        recorder = MarketRecorder(options["db"], kind=args.kind)
    except (DatabaseKindMismatch, SchemaVersionMismatch) as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE
    except sqlite3.Error as exc:
        print(f"خطا: پایگاه باز نشد: {exc}", file=sys.stderr)
        return EXIT_FAILED

    try:
        status = recorder.status()
    finally:
        recorder.close()

    contracts = (
        "—"
        if status.last_complete_contracts is None
        else str(status.last_complete_contracts)
    )
    print(f"پایگاه            : {status.db_path}")
    print(f"نوع               : {status.kind}")
    print(f"حجم               : {status.db_bytes / 1024:.0f} KiB")
    print(f"تعداد نوبت ثبت    : {status.total_snapshots}")
    print(f"قرارداد یکتا      : {status.distinct_contracts}")
    print(f"آخرین تلاش        : {status.last_attempt_at or '—'}"
          f"  ({status.last_attempt_status or '—'})")
    print(f"آخرین ثبت کامل    : {status.last_complete_at or '—'}")
    print(f"  قرارداد آن نوبت : {contracts}")
    print(f"نوبت دارای مشکل   : {status.snapshots_with_issues}")
    print(f"  تعارض           : {status.total_conflicts}")
    print(f"  فیلد نامعتبر    : {status.total_invalid_fields}")
    print(f"  ردیف ردشده      : {status.total_rejected_rows}")
    print(f"آخرین خطا         : {status.last_error_at or '—'}")
    if status.last_error:
        print(f"  متن خطا         : {status.last_error}")
    return EXIT_OK


def run_loop(args: argparse.Namespace, options: dict) -> int:
    """ثبت منظم تا وقتی کاربر متوقف کند.

    سه قید که رفتار را تعیین می‌کنند:

    * **تیکِ مطلق** — نوبت بعدی از ساعت دیوار حساب می‌شود، نه از پایان
      نوبت قبلی. پس هم‌پوشانی ممکن نیست و اسلات‌های ازدست‌رفته پرش
      می‌شوند، نه اینکه پشت سر هم اجرا شوند.
    * **خطای موقت متوقف نمی‌کند** — شکست دریافت در پایگاه ثبت می‌شود و
      حلقه به نوبت بعدی می‌رود. `fetch_json` از قبل تلاش مجدد دارد و
      اینجا لایه‌ی دومی روی آن گذاشته **نمی‌شود**.
    * **خطای غیرقابل ادامه متوقف می‌کند** — تنظیمات، نسخه‌ی پایگاه و
      قفل، همه **پیش از** ورود به حلقه بررسی می‌شوند.
    """
    is_live = args.fixture is None
    guard = _guard_kind_combination(args, is_live)
    if guard is not None:
        return guard

    try:
        zone = resolve_timezone(options["timezone"])
    except ValueError as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE

    source = _build_source(args, options)
    endpoint = args.fixture or OPTION_WATCH_URL.format(market=options["market"])
    interval = options["interval"]
    if interval <= 0:
        print("خطا: فاصله‌ی ثبت باید مثبت باشد.", file=sys.stderr)
        return EXIT_MISUSE

    # نسخه و نوع پایگاه پیش از حلقه بررسی می‌شوند: خطای اسکیما در
    # نوبت پنجاهم، نیمه‌شب، بدتر از خطای فوری است.
    try:
        recorder = MarketRecorder(options["db"], kind=args.kind)
    except (DatabaseKindMismatch, SchemaVersionMismatch) as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE
    except sqlite3.Error as exc:
        print(f"خطا: پایگاه باز نشد: {exc}", file=sys.stderr)
        return EXIT_FAILED

    calendar = _trading_calendar(args)
    lock_path = Path(f"{options['db']}.lock")
    try:
        with ProcessLock(lock_path):
            return _loop_forever(
                args, options, recorder, source, endpoint, is_live,
                zone, interval, calendar,
            )
    except LockUnavailable as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE
    finally:
        recorder.close()


def _loop_forever(
    args: argparse.Namespace,
    options: dict,
    recorder: MarketRecorder,
    source: Any,
    endpoint: str,
    is_live: bool,
    zone: Any,
    interval: int,
    calendar: Any,
) -> int:
    window = options["window"]
    logger.info(
        "ثبت منظم شروع شد: هر %s ثانیه، %s–%s (%s)، پایگاه %s. Ctrl+C برای توقف.",
        interval, window.start, window.end, options["timezone"], options["db"],
    )
    recorded = failed = 0
    try:
        while True:
            target = next_tick(datetime.now(zone), interval)
            _sleep_until(target, zone)

            moment = datetime.now(zone)
            missed = skipped_ticks(target, moment, interval)
            if missed:
                # خوابِ ویندوز، نوبت طولانی، یا جهش ساعت. جبران
                # نمی‌شود — فقط دیده می‌شود.
                logger.warning(
                    "%s نوبت جا ماند (بیداری با %.0f ثانیه تأخیر)؛ جبران نمی‌شود.",
                    missed, (moment - target).total_seconds(),
                )

            verdict = decide(moment, window, _is_trading_day(calendar, moment))
            if verdict.is_waiting:
                # ⚠️ انتظار در پایگاه ثبت نمی‌شود؛ وگرنه تاریخچه پر
                # می‌شد از شکست‌های ساختگی.
                logger.info("نوبت %s رد شد: %s", moment.strftime("%H:%M"), verdict.reason)
                continue

            code = _record_once(
                args, options, recorder, source, endpoint, is_live
            )
            if code == EXIT_MISUSE:
                # ناسازگاری نوع/شناسه ادامه‌دادنی نیست.
                return code
            if code == EXIT_OK:
                recorded += 1
            else:
                failed += 1
                logger.warning("نوبت ناموفق بود؛ حلقه ادامه می‌دهد.")
    except KeyboardInterrupt:
        print(
            f"\nمتوقف شد. {recorded} نوبت موفق، {failed} ناموفق.",
            file=sys.stderr,
        )
        return EXIT_OK


def _sleep_until(target: datetime, zone: Any) -> None:
    """انتظار تا `target`، در قطعه‌های کوتاه.

    قطعه‌قطعه بودن دو کار می‌کند: Ctrl+C فوری جواب می‌گیرد، و جهشِ
    ساعت سیستم یا بازگشت از خواب در همان ثانیه دیده می‌شود نه پس از
    یک انتظار پنج‌دقیقه‌ای.
    """
    while True:
        remaining = (target - datetime.now(zone)).total_seconds()
        if remaining <= 0:
            return
        time_module.sleep(min(remaining, 1.0))


def _is_trading_day(calendar: Any, moment: datetime) -> bool:
    """روز معاملاتی؟ شکستِ تقویم نباید ثبت را بخواباند.

    `TradingCalendar` یادگیری را یک بار تلاش می‌کند و در صورت شکست به
    «فقط آخرهفته» برمی‌گردد — همان رفتار محافظه‌کارانه‌ای که برای ثبت
    هم درست است: بهتر است یک روز تعطیل چند snapshot اضافه ثبت شود تا
    اینکه یک روز باز از دست برود.
    """
    if calendar is None:
        return True
    try:
        return calendar.is_trading_day(moment.date())
    except Exception as exc:
        logger.warning("تقویم معاملاتی جواب نداد (%s)؛ نوبت انجام می‌شود.", exc)
        return True


def _trading_calendar(args: argparse.Namespace) -> Any:
    """تقویم از تنظیمات پروژه؛ در حالت fixture اصلاً ساخته نمی‌شود.

    ساختِ تقویم می‌تواند یک بار تاریخچه از شبکه بکشد، و اجرای
    آزمایشی باید کاملاً آفلاین بماند.
    """
    if args.fixture is not None:
        return None
    from bootstrap import build_market_data, build_trading_calendar

    settings = load_settings(args.config)
    try:
        market_data = build_market_data(settings)
        return build_trading_calendar(settings, market_data)
    except Exception as exc:
        logger.warning("تقویم معاملاتی ساخته نشد (%s)؛ فقط آخرهفته لحاظ می‌شود.", exc)
        return None


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        options = resolve_options(args)
    except SettingsError as exc:
        print(f"خطا در تنظیمات: {exc}", file=sys.stderr)
        return EXIT_MISUSE
    if args.status:
        return show_status(args, options)
    if args.loop:
        return run_loop(args, options)
    return run_once(args, options)


if __name__ == "__main__":
    sys.exit(main())

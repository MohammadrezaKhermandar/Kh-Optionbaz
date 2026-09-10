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

⚠️ **حلقه‌ی زمان‌بندی در این نسخه نیست.** هر اجرا یک نوبت ثبت می‌کند.
اجرای مداوم با تناوب ۵ دقیقه در مرحله‌ی بعد اضافه می‌شود.

**درباره‌ی کش:** این ابزار عمداً مستقیم `HttpPayloadSource.fetch()` را
صدا می‌زند و از `TsetmcOptionChainClient` رد می‌شود. آن کلاینت هم
فیلتر کیفیت دارد و هم کش ~۲۰ ثانیه‌ای در حافظه؛ ثبت از روی کش یعنی
زمان دریافتِ ثبت‌شده با زمان واقعیِ داده نمی‌خواند. پس هر اجرا یک
**دریافت تازه** است.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import force_utf8_stdio
from data.raw_snapshot import SnapshotPayloadError, extract
from data.tsetmc_option_chain_client import (
    OPTION_WATCH_URL,
    FilePayloadSource,
    HttpPayloadSource,
)
from storage.market_recorder import (
    KIND_LIVE,
    KIND_TEST,
    DatabaseKindMismatch,
    MarketRecorder,
)

logger = logging.getLogger("record_market")

DEFAULT_DB = "var/recorder/market.db"

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
    parser.add_argument("--db", default=DEFAULT_DB, help=f"مسیر پایگاه (پیش‌فرض {DEFAULT_DB})")
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
    parser.add_argument("--market", type=int, default=0, help="0=همه، 1=بورس، 2=فرابورس")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--log-level", default="INFO")
    return parser


def run_once(args: argparse.Namespace) -> int:
    """یک نوبت دریافت و ثبت. کد خروجی: ۰ موفق، ۱ ناموفق."""
    is_live = args.fixture is None
    if not is_live and args.kind != KIND_TEST:
        # قید دوم؛ قید اول در خودِ لایه‌ی ذخیره‌سازی است و دور زدنی نیست.
        print(
            "خطا: داده‌ی fixture فقط در پایگاه آزمایشی ثبت می‌شود.\n"
            "      --kind test را هم بدهید و یک مسیر --db جداگانه.",
            file=sys.stderr,
        )
        return EXIT_MISUSE

    source = (
        FilePayloadSource(args.fixture)
        if args.fixture
        else HttpPayloadSource(
            market=args.market, timeout=args.timeout, retries=args.retries
        )
    )
    endpoint = args.fixture or OPTION_WATCH_URL.format(market=args.market)

    try:
        recorder = MarketRecorder(args.db, kind=args.kind)
    except DatabaseKindMismatch as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE

    snapshot_id = recorder.new_snapshot_id()
    requested_at = _now()
    try:
        payload = source.fetch()
        received_at = _now()
    except Exception as exc:  # نبودِ داده هم باید در تاریخچه دیده شود
        recorder.record_failure(
            snapshot_id,
            source=source.source_name,
            is_live=is_live,
            requested_at=requested_at,
            error=f"دریافت ناموفق: {exc}",
            endpoint=endpoint,
        )
        print(f"دریافت ناموفق بود: {exc}", file=sys.stderr)
        recorder.close()
        return EXIT_FAILED

    try:
        extraction = extract(payload)
    except SnapshotPayloadError as exc:
        # ساختار غیرمنتظره «موفق با صفر قرارداد» نیست. payload نگه داشته
        # می‌شود تا بعداً بشود فهمید منبع چه فرستاده بود.
        recorder.record_failure(
            snapshot_id,
            source=source.source_name,
            is_live=is_live,
            requested_at=requested_at,
            received_at=received_at,
            error=f"ساختار پاسخ نامعتبر: {exc}",
            endpoint=endpoint,
            payload=payload if isinstance(payload, dict) else None,
        )
        print(f"ساختار پاسخ نامعتبر بود: {exc}", file=sys.stderr)
        recorder.close()
        return EXIT_FAILED

    if extraction.contract_count == 0:
        recorder.record_failure(
            snapshot_id,
            source=source.source_name,
            is_live=is_live,
            requested_at=requested_at,
            received_at=received_at,
            error=(
                f"پاسخ هیچ قراردادی نداشت ({extraction.row_count} ردیف، "
                f"{len(extraction.rejected)} ردشده)"
            ),
            endpoint=endpoint,
            payload=payload,
        )
        print("پاسخ هیچ قراردادی نداشت؛ به‌عنوان ناموفق ثبت شد.", file=sys.stderr)
        recorder.close()
        return EXIT_FAILED

    try:
        written = recorder.record(
            snapshot_id,
            payload,
            extraction,
            source=source.source_name,
            is_live=is_live,
            requested_at=requested_at,
            received_at=received_at,
            endpoint=endpoint,
            # ⚠️ دیده‌بان آپشن مهر زمانی منبع ندارد؛ جعلش نمی‌کنیم.
            source_time=None,
        )
    except DatabaseKindMismatch as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        recorder.close()
        return EXIT_MISUSE

    status = recorder.status()
    recorder.close()

    print(f"snapshot ثبت شد: {snapshot_id}")
    print(f"  نوع پایگاه   : {args.kind}   ({'زنده' if is_live else 'آزمایشی'})")
    print(f"  ردیف استرایک : {extraction.row_count}")
    print(f"  قرارداد ثبت‌شده: {written}")
    print(f"  ردیف ردشده   : {len(extraction.rejected)}")
    print(f"  نماد پایه    : {len(extraction.underlyings)}")
    print(f"  حجم پایگاه   : {status.db_bytes / 1024:.0f} KiB")
    if extraction.rejected:
        print("  علت‌های رد شدن:")
        reasons: dict[str, int] = {}
        for item in extraction.rejected:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
        for reason, count in sorted(reasons.items(), key=lambda p: -p[1]):
            print(f"    {count:5}  {reason}")
    return EXIT_OK


def show_status(args: argparse.Namespace) -> int:
    """وضعیت محلی. هیچ درخواست شبکه‌ای زده نمی‌شود."""
    if not Path(args.db).exists():
        print(f"پایگاهی در {args.db} نیست. هنوز چیزی ثبت نشده.")
        return EXIT_OK
    try:
        recorder = MarketRecorder(args.db, kind=args.kind)
    except DatabaseKindMismatch as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return EXIT_MISUSE

    status = recorder.status()
    recorder.close()

    print(f"پایگاه            : {status.db_path}")
    print(f"نوع               : {status.kind}")
    print(f"حجم               : {status.db_bytes / 1024:.0f} KiB")
    print(f"تعداد نوبت ثبت    : {status.total_snapshots}")
    print(f"قرارداد یکتا      : {status.distinct_contracts}")
    print(f"آخرین تلاش        : {status.last_attempt_at or '—'}"
          f"  ({status.last_attempt_status or '—'})")
    contracts = (
        "—"
        if status.last_complete_contracts is None
        else str(status.last_complete_contracts)
    )
    print(f"آخرین ثبت کامل    : {status.last_complete_at or '—'}")
    print(f"  قرارداد آن نوبت : {contracts}")
    print(f"آخرین خطا         : {status.last_error_at or '—'}")
    if status.last_error:
        print(f"  متن خطا         : {status.last_error}")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return show_status(args) if args.status else run_once(args)


if __name__ == "__main__":
    sys.exit(main())

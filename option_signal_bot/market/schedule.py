"""زمان‌بندی نوبت‌های ثبت — توابع خالص، با ساعت تزریقی.

هیچ‌کدام از این‌ها ساعت سیستم را خودشان نمی‌خوانند: `now` همیشه ورودی
است. به این ترتیب تست می‌تواند یک روز کامل بازار، خوابِ ویندوز و
عقب‌رفتن ساعت را در چند میلی‌ثانیه بسازد، بدون انتظار واقعی.

**تیکِ مطلق، نه `sleep(interval)`**

نوبت بعدی از روی **ساعت دیوار** حساب می‌شود، نه از لحظه‌ی پایان نوبت
قبلی. سه خاصیت رایگان به دست می‌آید:

* **بدون هم‌پوشانی** — نوبتی که طول کشیده، اسلات بعدی را می‌بلعد.
* **بدون جبران انبوه** — پس از خوابِ ویندوز، اسلات‌های ازدست‌رفته
  **پرش** می‌شوند و فقط یک نوبت اجرا می‌شود. صف عقب‌افتاده وجود ندارد.
* **قابل پیش‌بینی** — با فاصله‌ی ۳۰۰ ثانیه، نوبت‌ها روی ۰۹:۰۰، ۰۹:۰۵،
  … می‌نشینند، نه روی زمان‌های سرگردان.

تاریخچه‌ی ازدست‌رفته هم بازسازی **نمی‌شود**: snapshot امروز چیزی
درباره‌ی دیروز نمی‌گوید.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone, tzinfo

logger = logging.getLogger(__name__)

#: منطقه‌ی زمانی پیش‌فرضِ تصمیم‌گیری.
DEFAULT_TIMEZONE = "Asia/Tehran"

#: بازه‌ی رسمی جلسه‌ی معاملاتی بورس تهران.
DEFAULT_SESSION_START = time(9, 0)
DEFAULT_SESSION_END = time(12, 30)

#: چند ثانیه پس از پایان جلسه هم ثبت ادامه یابد.
#:
#: ⚠️ این snapshotها **«قیمت پایانی» نیستند**. دیده‌بان آپشن هیچ مهر
#: زمانی منبع نمی‌دهد (`source_time` همیشه `NULL`)، پس تنها چیزی که
#: می‌دانیم `received_at` است. برچسب «قیمت قطعی پایان بازار» روی
#: داده‌ای که زمانش را نمی‌دانیم، ادعای بی‌پشتوانه است. منبع درستِ
#: قیمت پایانی، `pClosing` در تاریخچه‌ی روزانه است که `dEven`/`hEven`
#: دارد — و آن مسیر اینجا نیست.
DEFAULT_CLOSING_GRACE_SECONDS = 300

#: آفست ثابت ایران. ایران از ۲۰۲۲ ساعت تابستانی ندارد، پس این مقدار
#: امروز **دقیق** است، نه تقریب.
_IRAN_FIXED_OFFSET = timezone(timedelta(hours=3, minutes=30), "Asia/Tehran")


def resolve_timezone(name: str = DEFAULT_TIMEZONE) -> tzinfo:
    """منطقه‌ی زمانی، با بازگشت امن به آفست ثابت.

    `zoneinfo` روی ویندوز به پایگاه tz سیستم یا بسته‌ی `tzdata` نیاز
    دارد و هیچ‌کدام تضمینی نیستند. هسته‌ی این پروژه عمداً وابستگی
    اجباری ندارد، پس به‌جای افزودن `tzdata`، در نبودش به آفست ثابت
    برمی‌گردیم و **هشدار می‌دهیم**.

    این بازگشت برای `Asia/Tehran` بی‌خطر است (بدون DST)، ولی برای هر
    منطقه‌ی دیگری می‌تواند غلط باشد — پس هشدار صریح است.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception as exc:  # پایگاه tz نبود یا نام ناشناخته بود
        if name != DEFAULT_TIMEZONE:
            raise ValueError(
                f"منطقه‌ی زمانی «{name}» در دسترس نیست: {exc}\n"
                f"روی ویندوز بسته‌ی `tzdata` را نصب کنید، یا "
                f"«{DEFAULT_TIMEZONE}» را به کار ببرید که بازگشت امن دارد."
            ) from exc
        logger.warning(
            "پایگاه منطقه‌ی زمانی در دسترس نیست (%s)؛ آفست ثابت +۰۳:۳۰ "
            "به کار می‌رود. برای ایران درست است (بدون ساعت تابستانی).",
            exc,
        )
        return _IRAN_FIXED_OFFSET


@dataclass(frozen=True)
class RecordingWindow:
    """بازه‌ای که در آن دریافت انجام می‌شود.

    شامل جلسه‌ی معاملاتی به‌علاوه‌ی مهلت پایانی. تصمیمِ «امروز روز
    معاملاتی هست؟» اینجا نیست — `is_trading_day` از بیرون تزریق
    می‌شود تا این ماژول به تقویم و شبکه وابسته نباشد.
    """

    start: time = DEFAULT_SESSION_START
    end: time = DEFAULT_SESSION_END
    closing_grace_seconds: int = DEFAULT_CLOSING_GRACE_SECONDS

    def __post_init__(self) -> None:
        if self.start >= self.end:
            raise ValueError(
                f"شروع جلسه ({self.start}) باید پیش از پایان ({self.end}) باشد."
            )
        if self.closing_grace_seconds < 0:
            raise ValueError("مهلت پایانی نمی‌تواند منفی باشد.")

    def contains(self, moment: datetime) -> bool:
        """آیا این لحظه داخل بازه‌ی دریافت است؟ (بدون در نظر گرفتن تعطیلی)"""
        end_with_grace = (
            datetime.combine(moment.date(), self.end, tzinfo=moment.tzinfo)
            + timedelta(seconds=self.closing_grace_seconds)
        )
        return (
            datetime.combine(moment.date(), self.start, tzinfo=moment.tzinfo)
            <= moment
            <= end_with_grace
        )

    def is_after_close(self, moment: datetime) -> bool:
        """آیا این لحظه در مهلتِ **پس از** پایان جلسه است؟"""
        close = datetime.combine(moment.date(), self.end, tzinfo=moment.tzinfo)
        return moment > close and self.contains(moment)


def next_tick(now: datetime, interval_seconds: int) -> datetime:
    """اولین لحظه‌ی هم‌تراز که **اکیداً بعد از** `now` است.

    هم‌ترازی نسبت به نیمه‌شبِ همان روزِ محلی است، پس با فاصله‌ی ۳۰۰
    ثانیه نوبت‌ها روی دقیقه‌های گرد می‌افتند.

    چون همیشه از `now` حساب می‌شود، یک وقفه‌ی طولانی فقط یعنی اسلات
    بعدی — نه صفی از نوبت‌های عقب‌افتاده.
    """
    if interval_seconds <= 0:
        raise ValueError("فاصله‌ی ثبت باید مثبت باشد.")
    midnight = datetime.combine(now.date(), time(0, 0), tzinfo=now.tzinfo)
    elapsed = (now - midnight).total_seconds()
    return midnight + timedelta(
        seconds=(int(elapsed // interval_seconds) + 1) * interval_seconds
    )


def skipped_ticks(expected: datetime, actual: datetime, interval_seconds: int) -> int:
    """چند اسلات بین زمان انتظار و زمان واقعیِ بیداری جا ماند.

    صفر یعنی به‌موقع بیدار شدیم. عدد بزرگ یعنی سیستم خواب بوده یا
    نوبت قبلی طول کشیده — هر دو در لاگ دیده می‌شوند، ولی **جبران
    نمی‌شوند**.
    """
    if interval_seconds <= 0:
        raise ValueError("فاصله‌ی ثبت باید مثبت باشد.")
    late = (actual - expected).total_seconds()
    return max(int(late // interval_seconds), 0)


@dataclass(frozen=True)
class TickDecision:
    """تصمیم یک نوبت: دریافت شود یا فقط منتظر بمانیم."""

    should_record: bool
    reason: str

    #: ⚠️ «انتظار» خرابی نیست. بیرون ساعت بازار هیچ‌چیزی در پایگاه ثبت
    #: نمی‌شود — نه snapshot، نه failure — وگرنه تاریخچه پر می‌شد از
    #: شکست‌های ساختگی که با قطعی واقعی داده اشتباه گرفته می‌شدند.
    @property
    def is_waiting(self) -> bool:
        return not self.should_record


def decide(
    moment: datetime,
    window: RecordingWindow,
    is_trading_day: bool,
) -> TickDecision:
    """آیا در این لحظه باید دریافت کرد؟"""
    if not is_trading_day:
        return TickDecision(False, "روز معاملاتی نیست")
    if not window.contains(moment):
        return TickDecision(
            False,
            f"بیرون بازه‌ی دریافت ({window.start}–{window.end}"
            f" + {window.closing_grace_seconds}s)",
        )
    if window.is_after_close(moment):
        # ثبت می‌شود، ولی هیچ برچسب ویژه‌ای نمی‌گیرد.
        return TickDecision(True, "مهلت پس از پایان جلسه")
    return TickDecision(True, "جلسه باز است")

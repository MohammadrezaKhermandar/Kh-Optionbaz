"""قابلیت معامله‌ی یک قرارداد اختیار — منطق خالص، بدون شبکه و بدون پایگاه.

**مسئله‌ای که این ماژول حل می‌کند**

بازار اختیار تهران پر از قرارداد مرده است: بدون مظنه، بدون موقعیت باز،
با اسپرد ده‌ها درصد. سیگنالی که روی چنین قراردادی صادر شود، روی کاغذ
جذاب است و در عمل **اجرا نمی‌شود** — و بدتر از آن، خروجش هم ممکن نیست.
پس «قابلیت معامله» یک امتیاز نیست که با امتیاز بالای استراتژی جبران
شود؛ یک **دروازه** است. یا رد می‌شوی، یا نمی‌شوی.

**سه وضعیت، و تفاوتشان تصمیم‌ساز است**

| وضعیت | یعنی |
|---|---|
| `tradable` | همه‌ی سنجه‌ها دانسته‌اند و از آستانه گذشته‌اند |
| `needs_review` | داده‌ی لازم نیست؛ **نه تأیید، نه رد** |
| `rejected` | دست‌کم یک سنجه‌ی دانسته، آستانه را رد کرده |

`needs_review` عمداً از `tradable` جداست. نبودِ تاریخچه شاهدِ نقدشوندگی
**نیست**؛ اگر یکی بود، هر قرارداد تازه‌پذیرفته‌شده خودبه‌خود «سالم»
حساب می‌شد.

**سمت خروج، نه سمت ورود**

برای موقعیت خرید، خروج یعنی **فروش** — پس عمقِ سمت خریدِ بازار
(`bid`) اهمیت دارد. برای بستنِ یک موقعیت فروش، خروج یعنی **خرید** —
پس سمت فروش (`ask`). اشتباه گرفتن این دو، رایج‌ترین راهِ خوش‌بینیِ
کاذب درباره‌ی نقدشوندگی است.

**اندازه‌ی سفارشِ کاربر بخشی از سؤال است**

قراردادی که برای ۱ قرارداد نقدشونده است، لزوماً برای ۵۰ قرارداد نیست.
عمق همیشه نسبت به **اندازه‌ی همین سفارش** سنجیده می‌شود، نه به‌طور
مطلق.

⚠️ **ظرفیت خروج امروز، تضمین خروج فردا نیست.** همه‌ی سنجه‌های لحظه‌ای
عکسِ یک لحظه‌اند؛ `observed_at` کنارشان می‌آید تا کهنگی دیده شود.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Verdict(str, Enum):
    """نتیجه‌ی غربال. `str, Enum` چون مستقیم در JSON پاسخ می‌نشیند."""

    TRADABLE = "tradable"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.TRADABLE: "قابل معامله",
    Verdict.NEEDS_REVIEW: "نیازمند بررسی",
    Verdict.REJECTED: "رد شده",
}


@dataclass(frozen=True)
class Check:
    """یک سنجه‌ی منفرد، با عددِ اندازه‌گیری‌شده و آستانه‌اش.

    هر سه بخش لازم است تا کاربر بفهمد **چرا**: چه چیزی سنجیده شد، چقدر
    درآمد، و مرزش کجا بود. پیام بدون عدد، توضیح نیست.
    """

    key: str
    label: str
    #: عددِ اندازه‌گیری‌شده؛ `None` یعنی داده‌اش نبود
    value: float | None
    threshold: float | None
    unit: str
    #: `True` گذشت، `False` رد، `None` دانسته نیست
    passed: bool | None
    detail: str = ""

    @property
    def is_unknown(self) -> bool:
        return self.passed is None

    def describe(self) -> str:
        """یک جمله‌ی خوانا برای رابط و لاگ."""
        if self.is_unknown:
            return f"{self.label}: داده‌ای نیست{(' — ' + self.detail) if self.detail else ''}"
        shown = "—" if self.value is None else f"{self.value:,.2f}".rstrip("0").rstrip(".")
        limit = "" if self.threshold is None else f" (آستانه {self.threshold:,.2f}".rstrip(
            "0"
        ).rstrip(".") + ")"
        state = "گذشت" if self.passed else "رد"
        return f"{self.label}: {shown} {self.unit}{limit} — {state}"


@dataclass(frozen=True)
class Thresholds:
    """آستانه‌های غربال. **واحد هر کدام در نامش است.**

    ⚠️ این اعداد **اثبات‌شده نیستند.** هیچ پژوهشی پشتشان نیست؛ نقطه‌ی
    شروعی محافظه‌کارانه‌اند تا قراردادهای آشکارا مرده رد شوند. با
    تاریخچه‌ی خودتان تنظیمشان کنید (`docs/strategy-research.md`).
    """

    #: موقعیت باز، به تعداد قرارداد
    min_open_interest_contracts: int = 50
    #: تعداد معاملات امروزِ همین قرارداد
    min_trades_today_count: int = 1
    #: اسپرد نسبت به میانه‌ی مظنه، درصد
    max_relative_spread_pct: float = 25.0
    #: عمقِ سمت خروج تقسیم بر اندازه‌ی سفارش. ۱ یعنی دقیقاً به اندازه‌ی
    #: سفارش عمق هست؛ بالاتر یعنی حاشیه‌ی اطمینان.
    min_exit_depth_ratio: float = 1.0
    #: چند درصد از جلسه‌های ثبت‌شده، این قرارداد معامله داشته است
    min_sessions_with_trades_pct: float = 60.0
    #: کمتر از این تعداد جلسه‌ی ثبت‌شده یعنی «نمی‌دانیم»، نه «بد»
    min_history_sessions: int = 5
    #: روز تا سررسید؛ خیلی نزدیک به سررسید یعنی ریسک تسویه و افت نقدشوندگی
    min_days_to_expiry: int = 3
    #: عمقِ سمت خروج از این بدتر قیمت بدهد، «ظرفیت» نیست (درصد).
    #: عمق در قیمت‌های دور، سفارش را پر می‌کند ولی با زیانی که خودش
    #: معامله را بی‌معنا می‌کند — پس عمق بدون قیدِ قیمت، عدد گمراه‌کننده
    #: است.
    max_exit_slippage_pct: float = 10.0
    #: دادهٔ دریافتیِ ما از این کهنه‌تر باشد، قابل اتکا نیست (ثانیه).
    #: ⚠️ این عمرِ دریافت است، نه زمانِ بازار — بخش `source_time` را ببینید.
    max_quote_age_seconds: float = 120.0


@dataclass(frozen=True)
class LiquidityObservation:
    """آنچه **همین حالا** درباره‌ی یک قرارداد می‌دانیم.

    همه‌ی فیلدهای عددی `None`-پذیرند: نبودِ داده با صفر یکی نیست.
    """

    symbol: str
    #: سمتِ موقعیت: `buy` یعنی long (خروجش فروش است)، `sell` یعنی
    #: بستنِ فروش (خروجش خرید است).
    position_side: str
    #: اندازه‌ی سفارشِ کاربر، به تعداد قرارداد
    quantity: int
    observed_at: datetime
    bid: float | None = None
    ask: float | None = None
    #: عمقِ **کلِ** سمت خروج، به تعداد قرارداد (از دفتر چندسطحی؛ اگر فقط
    #: سطح اول را داریم، همان). عمداً به اندازه‌ی سفارش **بریده
    #: نمی‌شود**: با بریدن، نسبت هرگز از ۱ بالاتر نمی‌رفت و «دو برابرِ
    #: سفارش عمق دارد» از «دقیقاً به اندازه‌ی سفارش» قابل تشخیص نبود.
    exit_depth_contracts: int | None = None
    #: میانگین وزنیِ قیمتی که سفارشِ خروج با آن پر می‌شود
    exit_fill_price: float | None = None
    #: بهترین قیمتِ سمت خروج (سطح اول)
    best_exit_price: float | None = None
    #: میانگین وزنیِ قیمتی که سفارشِ **ورود** با آن پر می‌شود — همان
    #: تعداد قرارداد. بدون این، هزینه‌ی رفت‌وبرگشت قابل محاسبه نیست و
    #: نصفِ اسپرد جایش گذاشته می‌شد که هزینه‌ی رفت‌وبرگشت **نیست**.
    entry_fill_price: float | None = None
    #: عمقِ سمت خروج که در محدوده‌ی قیمتیِ قابل قبول است. حجمی که فقط
    #: در قیمت‌های دور هست حاشیه‌ی امنِ خروج نیست، پس اینجا شمرده
    #: نمی‌شود. (دروازه‌ی غربال همچنان با عمقِ کل کار می‌کند.)
    exit_depth_within_band_contracts: int | None = None
    open_interest: int | None = None
    trades_today: int | None = None
    days_to_expiry: int | None = None
    #: عمرِ **دریافتِ خودِ ما** در لحظه‌ی ارزیابی (ثانیه)؛ `None` یعنی
    #: نمی‌دانیم. این با «چقدر از زمان بازار گذشته» فرق دارد.
    quote_age_seconds: float | None = None
    #: مهر زمانیِ **بازار** روی این داده، اگر منبع بدهد.
    #:
    #: ⚠️ دیده‌بان اختیار TSETMC و دفتر سفارشش هیچ مهر زمانی نمی‌دهند،
    #: پس این تقریباً همیشه `None` است. زمانِ دریافت جایش گذاشته
    #: **نمی‌شود**: داده‌ای که ما همین حالا گرفتیم می‌تواند ساعت‌ها پیش
    #: در بازار ساخته شده باشد (مثلاً روز غیرمعاملاتی).
    source_time: datetime | None = None

    @property
    def exit_side(self) -> str:
        """سمتِ بازار که برای **خروج** لازم است."""
        return "bid" if self.position_side.lower() == "buy" else "ask"

    @property
    def relative_spread_pct(self) -> float | None:
        """اسپرد نسبت به میانه‌ی مظنه، درصد. `None` اگر یک طرف نباشد."""
        if not self.bid or not self.ask:
            return None
        mid = (self.bid + self.ask) / 2.0
        if mid <= 0:
            return None
        return (self.ask - self.bid) / mid * 100.0

    @property
    def exit_depth_ratio(self) -> float | None:
        """عمقِ کلِ سمت خروج تقسیم بر اندازه‌ی سفارش.

        می‌تواند از ۱ بزرگ‌تر باشد و باید بتواند — «سه برابرِ سفارش عمق
        دارد» اطلاعاتِ متفاوتی از «دقیقاً به اندازه‌ی سفارش» است.
        """
        if self.exit_depth_contracts is None or self.quantity <= 0:
            return None
        return self.exit_depth_contracts / self.quantity

    @property
    def exit_slippage_pct(self) -> float | None:
        """فاصله‌ی قیمتِ پرشدنِ خروج از بهترین مظنه، درصد.

        `None` وقتی سفارش اصلاً کامل پر نمی‌شود (آن‌وقت خودِ عمق رد
        می‌کند) یا داده‌ای نیست.
        """
        if not self.exit_fill_price or not self.best_exit_price:
            return None
        if self.best_exit_price <= 0:
            return None
        # خروجِ long روی مظنه‌ی خرید پر می‌شود، پس قیمتِ بدتر یعنی
        # **پایین‌تر**؛ خروجِ short روی مظنه‌ی فروش، پس بدتر یعنی بالاتر.
        if self.exit_side == "bid":
            drop = self.best_exit_price - self.exit_fill_price
        else:
            drop = self.exit_fill_price - self.best_exit_price
        return max(drop / self.best_exit_price * 100.0, 0.0)

    @property
    def round_trip_cost_pct(self) -> float | None:
        """هزینه‌ی قیمتیِ ورود و خروجِ **همین تعداد قرارداد**، درصد.

        مخرج: قیمتِ اجراپذیرِ **ورود** (پرمیومی که واقعاً پرداخت
        می‌شود). یعنی «اگر همین حالا وارد و بلافاصله خارج شوی، چند درصد
        از پرمیومِ پرداختی از دست می‌رود».

        این عدد **کلِ** اسپرد را در بر می‌گیرد به‌علاوه‌ی لغزشِ هر دو
        سمت برای این حجم — نه نصفِ اسپرد، که هزینه‌ی رفت‌وبرگشت نیست.

        `None` وقتی یکی از دو سمت برای این حجم اجراپذیر نیست؛ آن‌وقت
        عددی ساخته نمی‌شود.
        """
        if not self.entry_fill_price or not self.exit_fill_price:
            return None
        if self.entry_fill_price <= 0:
            return None
        return max(
            (self.entry_fill_price - self.exit_fill_price)
            / self.entry_fill_price
            * 100.0,
            0.0,
        )

    @property
    def usable_exit_depth_ratio(self) -> float | None:
        """عمقِ **درون محدوده‌ی قیمتی** تقسیم بر اندازه‌ی سفارش."""
        if self.exit_depth_within_band_contracts is None or self.quantity <= 0:
            return None
        return self.exit_depth_within_band_contracts / self.quantity

    @property
    def source_time_known(self) -> bool:
        """آیا مهر زمانیِ بازار روی این داده هست؟"""
        return self.source_time is not None


@dataclass(frozen=True)
class HistoryStats:
    """تداومِ معامله در جلسه‌های **ثبت‌شده‌ی** recorder.

    `known=False` یعنی تاریخچه‌ای در دست نیست — نه اینکه بد است.
    """

    sessions: int = 0
    sessions_with_trades: int = 0
    sessions_with_both_quotes: int = 0
    first_session: str | None = None
    last_session: str | None = None
    known: bool = False
    #: آیا روزهای شمرده‌شده **روز معاملاتی** بودنشان تأیید شده است؟
    #:
    #: ⚠️ روزِ تقویمیِ ثبت، جلسه‌ی معاملاتی نیست. recorder در روز تعطیل
    #: هم snapshot می‌گیرد و مقادیرش ماندهٔ جلسه‌ی قبل است؛ منبع هم مهر
    #: زمانی نمی‌دهد که بشود خلافش را ثابت کرد. بدون تأیید تقویم، این
    #: آمار «نامعلوم» است نه «خوب».
    sessions_verified: bool = False
    #: روزهایی که ثبت شده‌اند ولی روز معاملاتی نبودند و کنار گذاشته شدند
    skipped_non_trading_days: int = 0

    @property
    def sessions_with_trades_pct(self) -> float | None:
        if not self.known or self.sessions <= 0:
            return None
        return self.sessions_with_trades / self.sessions * 100.0

    @property
    def sessions_without_trades(self) -> int:
        return max(self.sessions - self.sessions_with_trades, 0)


@dataclass(frozen=True)
class TradabilityReport:
    """نتیجه‌ی کاملِ غربال یک قرارداد."""

    symbol: str
    verdict: Verdict
    checks: tuple[Check, ...]
    observation: LiquidityObservation
    history: HistoryStats

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS[self.verdict]

    @property
    def source_time_note(self) -> str:
        """جمله‌ای که نبودِ زمان بازار را صریح می‌گوید.

        بدون این، کاربر «عمر دادهٔ دریافتی: ۰ ثانیه» را «بازار همین حالا
        این را ساخته» می‌خواند — که ادعای نادرستی است.
        """
        if self.observation.source_time is not None:
            return f"زمان بازار: {self.observation.source_time.isoformat(timespec='seconds')}"
        return (
            "زمان بازار نامعلوم است: منبع مهر زمانی نمی‌دهد. عددِ «عمر داده» "
            "فقط می‌گوید چند ثانیه از دریافتِ ما گذشته، نه اینکه بازار کِی "
            "این قیمت را ساخته."
        )

    @property
    def failed(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.passed is False)

    @property
    def unknown(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.is_unknown)

    @property
    def reason(self) -> str:
        """چرا این نتیجه؟ یک جمله، با عدد."""
        if self.verdict is Verdict.REJECTED:
            return "؛ ".join(c.describe() for c in self.failed)
        if self.verdict is Verdict.NEEDS_REVIEW:
            return "؛ ".join(c.describe() for c in self.unknown)
        return "همه‌ی سنجه‌ها از آستانه گذشتند"


def _check(
    key: str,
    label: str,
    value: float | None,
    threshold: float | None,
    unit: str,
    *,
    higher_is_better: bool,
    detail: str = "",
) -> Check:
    """یک سنجه بساز؛ `value is None` یعنی دانسته نیست، نه اینکه رد شده."""
    if value is None:
        passed: bool | None = None
    elif threshold is None:
        passed = True
    elif higher_is_better:
        passed = value >= threshold
    else:
        passed = value <= threshold
    return Check(key, label, value, threshold, unit, passed, detail)


def evaluate(
    observation: LiquidityObservation,
    history: HistoryStats,
    thresholds: Thresholds,
) -> TradabilityReport:
    """غربالِ یک قرارداد برای **همین اندازه‌ی سفارش**.

    ترتیب تصمیم عمدی است: هر سنجه‌ی دانسته‌ای که رد شود، حکم را `rejected`
    می‌کند — حتی اگر سنجه‌های دیگر نامعلوم باشند. بعد اگر چیزی نامعلوم
    مانده باشد، `needs_review`. «همه دانسته و همه گذشته» تنها راهِ رسیدن
    به `tradable` است.
    """
    exit_label = "عمق سمت خرید (برای فروشِ خروج)" if observation.exit_side == "bid" else (
        "عمق سمت فروش (برای خریدِ بازگشت)"
    )
    checks = [
        _check(
            "days_to_expiry", "روز تا سررسید",
            None if observation.days_to_expiry is None else float(observation.days_to_expiry),
            float(thresholds.min_days_to_expiry), "روز", higher_is_better=True,
            detail="سررسید نامعلوم",
        ),
        _check(
            "open_interest", "موقعیت باز",
            None if observation.open_interest is None else float(observation.open_interest),
            float(thresholds.min_open_interest_contracts), "قرارداد",
            higher_is_better=True, detail="موقعیت باز گزارش نشده",
        ),
        _check(
            "trades_today", "معاملات امروز",
            None if observation.trades_today is None else float(observation.trades_today),
            float(thresholds.min_trades_today_count), "معامله",
            higher_is_better=True, detail="تعداد معامله گزارش نشده",
        ),
        _check(
            "relative_spread", "اسپرد نسبی",
            observation.relative_spread_pct, thresholds.max_relative_spread_pct, "٪",
            higher_is_better=False, detail="یک طرف مظنه خالی است",
        ),
        _check(
            "exit_depth", exit_label,
            observation.exit_depth_ratio, thresholds.min_exit_depth_ratio,
            f"برابرِ سفارش ({observation.quantity} قرارداد)",
            higher_is_better=True, detail="عمق سمت خروج در دسترس نیست",
        ),
        _check(
            "exit_slippage", "لغزش خروج تا پرشدن کامل",
            observation.exit_slippage_pct, thresholds.max_exit_slippage_pct,
            "٪ از بهترین مظنه", higher_is_better=False,
            detail="سفارش کامل پر نمی‌شود یا مظنه‌ای نیست",
        ),
        _check(
            "quote_age", "عمر دادهٔ دریافتی (نه زمان بازار)",
            observation.quote_age_seconds, thresholds.max_quote_age_seconds, "ثانیه",
            higher_is_better=False, detail="عمر داده نامعلوم است",
        ),
    ]

    # تاریخچه: کمتر از حداقلِ جلسه یعنی «نمی‌دانیم»، نه «بد». روزی که
    # معاملاتی بودنش تأیید نشده هم شمرده نمی‌شود.
    if (
        not history.known
        or not history.sessions_verified
        or history.sessions < thresholds.min_history_sessions
    ):
        checks.append(Check(
            key="trading_continuity",
            label="تداوم معامله",
            value=None,
            threshold=thresholds.min_sessions_with_trades_pct,
            unit="٪ از جلسه‌ها",
            passed=None,
            detail=(
                "تاریخچه‌ی ثبت‌شده‌ای در دسترس نیست"
                if not history.known
                else "روز معاملاتی بودنِ جلسه‌های ثبت‌شده تأیید نشد "
                     "(تقویم در دسترس نبود)"
                if not history.sessions_verified
                else f"فقط {history.sessions} جلسه‌ی معاملاتی ثبت شده "
                     f"(حداقل {thresholds.min_history_sessions} لازم است)"
                     + (
                         f"؛ {history.skipped_non_trading_days} روز غیرمعاملاتی "
                         "کنار گذاشته شد"
                         if history.skipped_non_trading_days
                         else ""
                     )
            ),
        ))
    else:
        checks.append(_check(
            "trading_continuity", "تداوم معامله",
            history.sessions_with_trades_pct,
            thresholds.min_sessions_with_trades_pct,
            f"٪ از {history.sessions} جلسه", higher_is_better=True,
            detail=f"{history.sessions_without_trades} جلسه بدون معامله",
        ))

    frozen = tuple(checks)
    if any(c.passed is False for c in frozen):
        verdict = Verdict.REJECTED
    elif any(c.is_unknown for c in frozen):
        verdict = Verdict.NEEDS_REVIEW
    else:
        verdict = Verdict.TRADABLE

    return TradabilityReport(
        symbol=observation.symbol,
        verdict=verdict,
        checks=frozen,
        observation=observation,
        history=history,
    )


@dataclass
class ScreeningRecord:
    """نتیجه‌ی غربال یک سیگنال، برای نمایش در رابط."""

    symbol: str
    strategy: str
    side: str
    quantity: int
    report: TradabilityReport
    #: شناسه‌ی ساختار چندپایه، اگر پایه‌ی یک ساختار باشد
    leg_group_id: str | None = None
    #: شناسه‌ی **همان** سیگنالی که غربال شد.
    #:
    #: نماد برای وصل‌کردنِ نتیجه به سیگنال کافی نیست: روی یک نماد
    #: می‌تواند چند سیگنال از چند استراتژی، با سمت و تعدادِ متفاوت،
    #: در یک پاس صادر شود. وصل‌کردن با نماد یعنی نتیجه‌ی غربالِ یکی به
    #: دیگری بچسبد و عددهای بی‌ربط قاطی شوند.
    signal_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "side": self.side,
            "quantity": self.quantity,
            "leg_group_id": self.leg_group_id,
            "signal_id": self.signal_id,
            "verdict": self.report.verdict.value,
            "verdict_label": self.report.verdict_label,
            "reason": self.report.reason,
            "observed_at": self.report.observation.observed_at.isoformat(
                timespec="seconds"
            ),
            "source_time_known": self.report.observation.source_time_known,
            "source_time_note": self.report.source_time_note,
            "exit_side": self.report.observation.exit_side,
            "exit_depth_contracts": self.report.observation.exit_depth_contracts,
            "exit_fill_price": self.report.observation.exit_fill_price,
            "best_exit_price": self.report.observation.best_exit_price,
            "checks": [
                {
                    "key": c.key,
                    "label": c.label,
                    "value": c.value,
                    "threshold": c.threshold,
                    "unit": c.unit,
                    "passed": c.passed,
                    "detail": c.detail,
                    "text": c.describe(),
                }
                for c in self.report.checks
            ],
            "history": {
                "known": self.report.history.known,
                "sessions_verified": self.report.history.sessions_verified,
                "skipped_non_trading_days": self.report.history.skipped_non_trading_days,
                "sessions": self.report.history.sessions,
                "sessions_with_trades": self.report.history.sessions_with_trades,
                "sessions_without_trades": self.report.history.sessions_without_trades,
                "first_session": self.report.history.first_session,
                "last_session": self.report.history.last_session,
            },
        }


@dataclass
class ScreeningSummary:
    """جمع‌بندی یک پاس غربال."""

    records: list[ScreeningRecord] = field(default_factory=list)
    #: ساختارهایی که چون **یک پایه‌شان** رد شد، کل ساختار کنار رفت
    dropped_groups: list[dict[str, object]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {v.value: 0 for v in Verdict}
        for record in self.records:
            out[record.report.verdict.value] += 1
        return out

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": self.counts(),
            "records": [r.to_dict() for r in self.records],
            "dropped_groups": self.dropped_groups,
        }

"""وضعیتِ **فعلیِ** بازار و نماد پایه — صعودی، نزولی، رنج، یا نامشخص.

**این چه چیزی هست و چه چیزی نیست**

توصیفِ چیزی است که **تا امروز** اتفاق افتاده، نه پیش‌بینیِ آنچه تا
سررسید می‌شود. هیچ درصدِ اطمینان، احتمالِ برد یا مدلِ پیش‌بینی پشتش
نیست و ساخته هم نمی‌شود: چیزی که اعتبارسنجی نشده، عدد نمی‌گیرد.

به همین دلیل **وارد امتیازِ رتبه‌بندی نمی‌شود**. کنارِ فرصت نشان داده
می‌شود تا کاربر خودش ببیند جهتِ فرصت با وضعیتِ سهم و بازار می‌خواند یا
نه. یک مؤلفه‌ی ارزیابی‌نشده نباید بی‌صدا در رتبه بنشیند.

**روش — عمداً ساده و قابل بازسازی**

سه سنجه، هر سه از قیمتِ پایانیِ جلسه‌های **کاملِ** گذشته:

1. **تغییر در افق** — درصد تغییر قیمت از ابتدای افق تا آخرین جلسه.
2. **کاراییِ حرکت** (Efficiency Ratio): `|تغییر خالص| ÷ مجموع حرکت روزانه`.
   عددی بین ۰ و ۱: نزدیک ۱ یعنی مسیر تقریباً مستقیم بوده، نزدیک ۰ یعنی
   همان مسیر با رفت‌وبرگشت طی شده. این همان چیزی است که «روندِ ضعیف» را
   از «روندِ قوی» جدا می‌کند، بدون هیچ مدل پیچیده‌ای.
3. **نوسان** — انحراف معیارِ بازده روزانه. از آن «حرکتِ معمولِ این بازه»
   ساخته می‌شود (`نوسان × √افق`) تا تغییرِ قیمت **نسبت به نوسانِ خودِ
   همین نماد** سنجیده شود، نه با یک آستانه‌ی ثابت که برای یک نماد بزرگ
   است و برای دیگری کوچک.

حکم: اگر کاراییِ حرکت کم باشد **یا** تغییرِ خالص از نصفِ حرکتِ معمولِ
همان بازه کمتر باشد ⇒ **رنج**. وگرنه با علامتِ تغییر ⇒ **صعودی** یا
**نزولی**. دادهٔ کم یا کهنه ⇒ **نامشخص** (نه «رنج»).

⚠️ آستانه‌ها **فرضِ اولیه‌اند**، نه نتیجه‌ی پژوهش. از تنظیمات قابل
تغییرند و هر کدام واحدش در نامش هست.

**دو قیدِ داده که رعایت می‌شوند**

* **بدون دادهٔ آینده:** فقط جلسه‌هایی که تاریخشان **پیش از** لحظه‌ی
  ارزیابی است. جلسه‌ی جاری هم حساب نمی‌شود چون هنوز تمام نشده و
  پایانی‌اش نهایی نیست.
* **تعدیلِ قیمت — و مرزهای دقیقش:** برای **سهم**، قیمتِ تاریخی باید با
  افزایش سرمایه تعدیل شده باشد وگرنه یک ریزشِ ساختگی دیده می‌شود
  (`market/corporate_actions.py`). این ماژول خودش تعدیل نمی‌کند؛
  فراخواننده وضعیت را اعلام می‌کند و همان در گزارش می‌آید.

  **پوشش:** فقط **افزایش سرمایه**، آن هم اگر پرسیدنش ممکن بوده باشد.
  **سود نقدی در هیچ حالتی تعدیل نمی‌شود** — منبع عمومیِ قابل اتکایی
  ندارد و این پروژه حدس نمی‌زند. پس حتی «تعدیل شد» هم یعنی «افزایش
  سرمایه اعمال شد»، نه «سری کاملاً هم‌مبنا است».

  **و وقتی وضعیتِ تعدیل نامعلوم است، حکمِ جهت‌دار صادر نمی‌شود:** همان
  افزایش سرمایه‌ی تعدیل‌نشده، یک «روندِ قویِ نزولی» تمام‌عیار می‌سازد.
  در آن حالت خروجی «نامشخص» است با علتِ صریح، و سنجه‌ها هم برای دیدن
  می‌مانند. شاخص تعدیل نمی‌خواهد و این قید شاملش نیست.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum


class RegimeState(str, Enum):
    """حکمِ وضعیت. `str, Enum` چون مستقیم در JSON پاسخ می‌نشیند."""

    UP = "up"
    DOWN = "down"
    RANGE = "range"
    UNKNOWN = "unknown"


REGIME_LABELS: dict[RegimeState, str] = {
    RegimeState.UP: "صعودی",
    RegimeState.DOWN: "نزولی",
    RegimeState.RANGE: "رنج",
    RegimeState.UNKNOWN: "نامشخص",
}


class Adjustment(str, Enum):
    """وضعیتِ تعدیلِ قیمتِ تاریخی — چهار حالت که هیچ دوتایشان یکی نیستند.

    ⚠️ **پوششِ واقعیِ تعدیل، حتی در بهترین حالت، ناقص است.** آنچه این
    پروژه می‌تواند بپرسد فقط **افزایش سرمایه** است
    (`GetInstrumentShareChange`). برای **سود نقدی** هیچ endpoint عمومیِ
    قابل اتکایی نیست، پس افتِ قیمتِ روزِ مجمع در هیچ حالتی تعدیل
    نمی‌شود. این محدودیت در همه‌ی حالت‌ها هست و در برچسبِ همه‌شان هم
    می‌آید.
    """

    #: افزایش سرمایه پرسیده شد و **اعمال** شد.
    APPLIED = "applied"
    #: پرسیده شد و رویدادی در این بازه نبود.
    NO_CAPITAL_EVENTS = "no_capital_events"
    #: موضوعیت ندارد — شاخص خودش سریِ سطح است.
    NOT_APPLICABLE = "not_applicable"
    #: اصلاً نشد پرسید. با «رویدادی نبود» یکی نیست.
    UNKNOWN = "unknown"


#: یادآوریِ ثابتی که کنارِ هر حالتِ سهم می‌آید.
DIVIDEND_GAP_NOTE = (
    "سود نقدی در هیچ حالتی تعدیل نمی‌شود (منبع عمومیِ قابل اتکا ندارد)، "
    "پس افتِ قیمتِ روزِ مجمع می‌تواند در سری بماند"
)

ADJUSTMENT_LABELS: dict[Adjustment, str] = {
    Adjustment.APPLIED: (
        "قیمت‌ها با **افزایش سرمایه** تعدیل شده‌اند؛ " + DIVIDEND_GAP_NOTE
    ),
    Adjustment.NO_CAPITAL_EVENTS: (
        "افزایش سرمایه‌ای در این بازه ثبت نشده بود؛ " + DIVIDEND_GAP_NOTE
    ),
    Adjustment.NOT_APPLICABLE: "تعدیل موضوعیت ندارد (شاخص، سریِ سطح است)",
    Adjustment.UNKNOWN: (
        "وضعیت تعدیل **نامعلوم** است: رویدادهای شرکتی پرسیده نشدند یا در "
        "دسترس نبودند. اگر افزایش سرمایه‌ای در این بازه بوده باشد، تغییرِ "
        "قیمت می‌تواند کاملاً ساختگی باشد"
    ),
}


@dataclass(frozen=True)
class PricePoint:
    """یک جلسه: تاریخ و پایانی. همین دو تا برای این روش کافی است."""

    date: date
    close: float


@dataclass(frozen=True)
class RegimeThresholds:
    """آستانه‌های تشخیص وضعیت.

    ⚠️ این اعداد **اثبات‌شده نیستند**؛ نقطه‌ی شروعی‌اند تا چیزی برای
    تنظیم‌کردن وجود داشته باشد. با تاریخچه‌ی خودتان عوضشان کنید.
    """

    #: افق تحلیل، به تعداد جلسه‌ی معاملاتی. ۲۰ جلسه ≈ یک ماه معاملاتی.
    horizon_sessions: int = 20
    #: کمتر از این تعداد جلسه‌ی کامل ⇒ «نامشخص»، نه «رنج».
    min_sessions: int = 40
    #: آخرین جلسه از این کهنه‌تر باشد (روز تقویمی) ⇒ «نامشخص».
    #: تعطیلات و آخر هفته هم در همین عدد می‌نشینند.
    max_staleness_days: int = 7
    #: کاراییِ حرکت کمتر از این ⇒ رنج (رفت‌وبرگشت غالب بوده).
    min_efficiency: float = 0.30
    #: تغییرِ خالص کمتر از این نسبت از «حرکتِ معمولِ بازه» ⇒ رنج.
    min_normalized_move: float = 0.50


@dataclass(frozen=True)
class RegimeMeasure:
    """یک سنجه‌ی قابل نمایش، با واحد و جمله‌ی خوانا."""

    key: str
    label: str
    value: float | None
    unit: str
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "value": None if self.value is None else round(self.value, 3),
            "unit": self.unit,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RegimeReport:
    """وضعیتِ یک موضوع (بازار یا یک نماد)، با دلیل و کیفیتِ داده."""

    subject: str
    #: `market` یا `underlying` — دو چیزِ جدا که جداگانه هم گزارش می‌شوند.
    subject_kind: str
    state: RegimeState
    measures: tuple[RegimeMeasure, ...]
    reasons: tuple[str, ...]
    as_of: date
    last_session: date | None
    sessions_used: int
    horizon_sessions: int
    adjustment: Adjustment
    #: وقتی حکم «نامشخص» است، اینجا می‌گوید چرا. وگرنه `None`.
    unknown_reason: str | None = None

    @property
    def label(self) -> str:
        return REGIME_LABELS[self.state]

    @property
    def staleness_days(self) -> int | None:
        if self.last_session is None:
            return None
        return (self.as_of - self.last_session).days

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "subject_kind": self.subject_kind,
            "state": self.state.value,
            "state_label": self.label,
            "measures": [m.to_dict() for m in self.measures],
            "reasons": list(self.reasons),
            "as_of": self.as_of.isoformat(),
            "last_session": (
                None if self.last_session is None else self.last_session.isoformat()
            ),
            "staleness_days": self.staleness_days,
            "sessions_used": self.sessions_used,
            "horizon_sessions": self.horizon_sessions,
            "adjustment": self.adjustment.value,
            "adjustment_note": ADJUSTMENT_LABELS[self.adjustment],
            "unknown_reason": self.unknown_reason,
            "note": (
                "این توصیفِ وضعیتِ **فعلی** است، نه پیش‌بینی تا سررسید. "
                "هیچ درصد اطمینان یا احتمال بردی پشتش نیست و در امتیازِ "
                "رتبه‌بندی هم وارد نمی‌شود."
            ),
        }


# ----------------------------------------------------------------------
def assess_regime(
    points: list[PricePoint],
    *,
    subject: str,
    subject_kind: str,
    as_of: date,
    thresholds: RegimeThresholds | None = None,
    adjustment: Adjustment = Adjustment.UNKNOWN,
) -> RegimeReport:
    """حکمِ وضعیت برای یک سریِ قیمت، با دلیل و کیفیتِ داده.

    `points` می‌تواند نامرتب یا شاملِ جلسه‌های آینده باشد؛ همین‌جا مرتب
    و بریده می‌شود تا **هیچ دادهٔ آینده‌ای** وارد محاسبه نشود.
    """
    limits = thresholds or RegimeThresholds()
    usable = sorted(
        (p for p in points if p.date < as_of and p.close > 0), key=lambda p: p.date
    )

    def unknown(reason: str, measures: tuple[RegimeMeasure, ...] = ()) -> RegimeReport:
        return RegimeReport(
            subject=subject,
            subject_kind=subject_kind,
            state=RegimeState.UNKNOWN,
            measures=measures,
            reasons=(reason,),
            as_of=as_of,
            last_session=usable[-1].date if usable else None,
            sessions_used=len(usable),
            horizon_sessions=limits.horizon_sessions,
            adjustment=adjustment,
            unknown_reason=reason,
        )

    if len(usable) < limits.min_sessions:
        return unknown(
            f"فقط {len(usable)} جلسه‌ی کامل در دست است و حداقل "
            f"{limits.min_sessions} جلسه لازم است. کمبودِ داده «رنج» "
            "ترجمه نمی‌شود."
        )

    last_session = usable[-1].date
    staleness = (as_of - last_session).days
    if staleness > limits.max_staleness_days:
        return unknown(
            f"آخرین جلسه‌ی موجود {last_session.isoformat()} است، یعنی "
            f"{staleness} روز پیش (آستانه {limits.max_staleness_days} روز). "
            "وضعیتِ امروز از دادهٔ کهنه ساخته نمی‌شود."
        )

    horizon = min(limits.horizon_sessions, len(usable) - 1)
    window = usable[-(horizon + 1):]
    closes = [p.close for p in window]
    start, end = closes[0], closes[-1]
    change_pct = (end / start - 1.0) * 100.0

    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    efficiency = (abs(end - start) / path) if path > 0 else 0.0

    returns = [
        (usable[i].close / usable[i - 1].close - 1.0) * 100.0
        for i in range(1, len(usable))
    ]
    daily_vol = _stdev(returns)
    expected_move = daily_vol * math.sqrt(horizon) if daily_vol else 0.0
    normalized = (change_pct / expected_move) if expected_move > 0 else 0.0

    measures = (
        RegimeMeasure(
            key="change",
            label="تغییر قیمت در افق",
            value=change_pct,
            unit="٪",
            detail=(
                f"از {start:,.0f} به {end:,.0f} در {horizon} جلسه = "
                f"{change_pct:+,.2f}٪"
            ),
        ),
        RegimeMeasure(
            key="efficiency",
            label="کارایی حرکت (قدرت روند)",
            value=efficiency,
            unit="۰ تا ۱",
            detail=(
                f"تغییرِ خالص {abs(end - start):,.0f} در برابر مجموعِ حرکتِ "
                f"روزانه {path:,.0f} = {efficiency:,.2f} "
                f"(آستانه‌ی روندِ معنادار {limits.min_efficiency:,.2f})"
            ),
        ),
        RegimeMeasure(
            key="volatility",
            label="نوسان روزانه",
            value=daily_vol,
            unit="٪ (انحراف معیار بازده)",
            detail=(
                f"{daily_vol:,.2f}٪ روزانه روی {len(returns)} جلسه ≈ "
                f"{daily_vol * math.sqrt(252):,.1f}٪ سالانه"
            ),
        ),
        RegimeMeasure(
            key="normalized_move",
            label="تغییر نسبت به نوسانِ معمولِ همین بازه",
            value=normalized,
            unit="برابر",
            detail=(
                f"حرکتِ معمولِ {horizon} جلسه ≈ {expected_move:,.2f}٪ و تغییرِ "
                f"واقعی {change_pct:+,.2f}٪ ⇒ {normalized:+,.2f} برابر "
                f"(آستانه {limits.min_normalized_move:,.2f})"
            ),
        ),
    )

    if expected_move <= 0:
        return unknown(
            "نوسانِ قابل اندازه‌گیری در این بازه نیست (قیمت‌ها تکان "
            "نخورده‌اند یا داده تکراری است).",
            measures,
        )

    reasons: list[str] = []
    # ⚠️ **حکمِ جهت‌دار روی سریِ تعدیل‌نامعلوم صادر نمی‌شود.** یک افزایش
    # سرمایه‌ی تعدیل‌نشده دقیقاً همان چیزی را می‌سازد که این ماژول
    # دنبالش می‌گردد: تغییرِ بزرگِ جهت‌دار با کاراییِ بالا. تشخیصِ
    # «صعودی/نزولی» از چنین سری‌ای، حدس را به‌جای مشاهده می‌نشاند.
    unknown_adjustment = (
        subject_kind == "underlying" and adjustment is Adjustment.UNKNOWN
    )

    if efficiency < limits.min_efficiency:
        state = RegimeState.RANGE
        reasons.append(
            f"کاراییِ حرکت {efficiency:,.2f} است (کمتر از "
            f"{limits.min_efficiency:,.2f}): مسیر با رفت‌وبرگشت طی شده، نه "
            "با روند."
        )
    elif abs(normalized) < limits.min_normalized_move:
        state = RegimeState.RANGE
        reasons.append(
            f"تغییرِ خالص {change_pct:+,.2f}٪ است، یعنی فقط {abs(normalized):,.2f} "
            f"برابرِ حرکتِ معمولِ همین بازه ({expected_move:,.2f}٪) — برای "
            "روند خواندن کافی نیست."
        )
    elif unknown_adjustment:
        # حرکتِ جهت‌دار هست، ولی نمی‌دانیم قیمت‌ها هم‌مبنا هستند یا نه.
        return unknown(
            f"حرکتِ {change_pct:+,.2f}٪ در {horizon} جلسه دیده می‌شود، ولی "
            "وضعیتِ تعدیلِ قیمت نامعلوم است و همین حرکت می‌تواند از تغییرِ "
            "مبنای قیمت (افزایش سرمایه) آمده باشد، نه از بازار. تا روشن "
            "شدنِ تعدیل، حکمِ صعودی/نزولی صادر نمی‌شود.",
            measures,
        )
    else:
        state = RegimeState.UP if change_pct > 0 else RegimeState.DOWN
        reasons.append(
            f"تغییرِ {change_pct:+,.2f}٪ در {horizon} جلسه، "
            f"{abs(normalized):,.2f} برابرِ حرکتِ معمولِ این بازه است و "
            f"کاراییِ حرکت {efficiency:,.2f} — یعنی جهت‌دار بوده."
        )

    reasons.append(ADJUSTMENT_LABELS[adjustment] + ".")

    return RegimeReport(
        subject=subject,
        subject_kind=subject_kind,
        state=state,
        measures=measures,
        reasons=tuple(reasons),
        as_of=as_of,
        last_session=last_session,
        sessions_used=len(usable),
        horizon_sessions=horizon,
        adjustment=adjustment,
    )


def _stdev(values: list[float]) -> float:
    """انحراف معیار نمونه‌ای. کمتر از دو مقدار ⇒ صفر (یعنی «نمی‌دانیم»)."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)

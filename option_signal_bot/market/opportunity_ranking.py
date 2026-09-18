"""رتبه‌بندیِ توضیح‌پذیرِ فرصت‌ها — منطق خالص، بدون شبکه و بدون پایگاه.

**این امتیاز چه چیزی هست و چه چیزی نیست**

«امتیاز اولویت بررسی» است: با داده‌ای که همین حالا داریم، کدام فرصت
ارزشِ وقت گذاشتن و نگاه دقیق‌تر دارد. این عدد **نه** احتمال برد است،
**نه** بازده مورد انتظار، و **نه** توصیه‌ی خرید. هیچ مدل پیش‌بینی،
خبری یا احتمالی پشتش نیست؛ فقط چند سنجه‌ی قابل اندازه‌گیری که وزن‌دار
جمع شده‌اند.

**غربال دروازه است، رتبه‌بندی صف.**

فقط چیزی وارد رتبه‌بندی اصلی می‌شود که از غربال (`market/tradability.py`)
با حکم `tradable` رد شده باشد. امتیازِ بالا **هیچ‌وقت** جای نقدشوندگی
یا داده‌ی لازم را نمی‌گیرد: گزینه‌ی ردشده هر چقدر هم سودِ ظاهری داشته
باشد، اینجا رتبه نمی‌گیرد — در فهرست «کنارگذاشته‌ها» با علتش می‌آید.

**هزینه از قیمتِ اجراپذیر می‌آید، نه از نصفِ اسپرد**

هزینه‌ی رفت‌وبرگشت یعنی روی `ask` بخری و روی `bid` بفروشی — کلِ اسپرد،
به‌علاوه‌ی لغزشِ هر دو سمت برای **همین تعداد قرارداد**. پس از میانگینِ
وزنیِ پرشدنِ واقعیِ هر دو سمت حساب می‌شود و مخرجش قیمتِ ورود است:
«چند درصد از پرمیومِ پرداختی». اگر یکی از دو سمت برای این حجم اجراپذیر
نباشد، عددی **ساخته نمی‌شود**.

اسپرد و لغزش جدا وزن نمی‌گیرند: هر دو در همین یک عدد هستند، وگرنه یک
شاهد دو بار پاداش می‌گرفت. عمق جداست چون چیز دیگری می‌گوید — ظرفیت،
نه قیمت — و فقط عمقِ **درون محدوده‌ی قیمتی** شمرده می‌شود: حجمی که در
قیمت‌های دور نشسته حاشیه‌ی امنِ خروج نیست.

**نامعلوم پنهان نمی‌شود و به نفع گزینه تمام نمی‌شود**

مؤلفه‌ای که داده‌اش نیست از مخرج حذف **نمی‌شود**. دو عدد گزارش می‌شود:

* `score` — محافظه‌کارانه: نامعلوم = صفر. **مبنای چیدنِ رتبه همین است**،
  پس نبودِ داده هرگز کسی را بالا نمی‌برد.
* `score_best_case` — خوش‌بینانه: نامعلوم = صد. سقفِ ممکن.

فاصله‌ی این دو، «نواری» است که می‌گوید چقدر نمی‌دانیم. `coverage_pct`
همان را به درصدِ وزنِ دانسته بیان می‌کند.

**دامنه‌ی نسخه‌ی اول — صریح: فقط خریدِ اختیارِ تک‌پایه**

همه‌ی فرمول‌های اینجا از منطقِ **خرید** می‌آیند: سرمایه‌ی درگیر یعنی
پرمیومِ پرداختی، حداکثر زیانِ نظری یعنی از دست دادنِ همان پرمیوم، و
سر‌به‌سر یعنی استرایک به‌علاوه‌ی هزینه‌ی ورود. هیچ‌کدامِ این‌ها برای
**فروش** یا ساختارهای پوششی معتبر نیست: آن‌ها وجه تضمین می‌خواهند و
پروفایل زیانشان فرق دارد. پس فروش و ساختار چندپایه رتبه نمی‌گیرند و
با علتِ صریح کنار گذاشته می‌شوند — مدل‌سازیِ درستشان کارِ این نسخه
نیست، و امتیازدادن به آن‌ها با فرمولِ خرید یعنی ریسکشان را غلط نشان
بدهیم.

**عملکردِ استراتژی در این نسخه امتیاز نمی‌دهد**

تنها دادهٔ در دسترس نرخ برد است، و نرخ برد به‌تنهایی شاهدِ عملکرد
نیست. `EVIDENCE_DISABLED_NOTE` را ببینید.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from market.tradability import Thresholds, TradabilityReport, Verdict

#: گروه‌های نمایشی مؤلفه‌ها. فقط برای دسته‌بندی در رابط‌اند؛ محاسبه مسطح است.
GROUP_EXECUTION = "اجرا و نقدشوندگی"
GROUP_RISK = "ریسک و سرمایه"


@dataclass(frozen=True)
class RankingWeights:
    """وزن‌ها و آستانه‌های رتبه‌بندی.

    ⚠️ این اعداد **فرضِ اولیه‌اند**، نه نتیجه‌ی پژوهش یا بهینه‌سازی. هیچ
    داده‌ای پشتشان نیست که بگوید این ترکیب بهتر از ترکیب دیگری جواب
    می‌دهد؛ نقطه‌ی شروعی‌اند تا چیزی برای تنظیم‌کردن وجود داشته باشد.
    همه از داشبورد قابل تغییرند و اثرشان در ارزیابیِ بعدی دیده می‌شود.
    """

    #: هزینه‌ی قیمتیِ رفت‌وبرگشت، از قیمتِ **اجراپذیرِ** ورود و خروج
    weight_round_trip_cost: float = 30.0
    #: ظرفیتِ سمت خروج، فقط عمقِ درونِ محدوده‌ی قیمتی
    weight_exit_capacity: float = 20.0
    #: فاصله تا سررسید
    weight_time_to_expiry: float = 15.0
    #: حرکتی که پایه باید بکند تا این موقعیت به سر‌به‌سر برسد
    weight_required_move: float = 25.0
    #: کارمزدِ رفت‌وبرگشت نسبت به سرمایه‌ی درگیر
    weight_fee_cost: float = 10.0

    #: عمقی که «راحت» حساب می‌شود: این ضریبِ حداقلِ غربال. عمقِ بالاتر
    #: از این، امتیاز بیشتری نمی‌گیرد — حاشیه‌ی اطمینان اشباع می‌شود.
    depth_comfort_multiple: float = 3.0
    #: فاصله‌ی راحت تا سررسید (روز). از این بیشتر امتیاز اضافه نمی‌کند.
    days_to_expiry_comfort: int = 30
    #: حرکتِ لازم تا سر‌به‌سر از این بیشتر باشد، امتیازِ این مؤلفه صفر است.
    max_required_move_pct: float = 25.0
    #: هزینه‌ی رفت‌وبرگشتِ قیمتی از این بیشتر باشد، امتیازش صفر است
    #: (درصد از پرمیومِ پرداختی).
    max_round_trip_cost_pct: float = 30.0
    #: کارمزدِ رفت‌وبرگشت از این بیشتر باشد، امتیازِ این مؤلفه صفر است.
    max_fee_cost_pct: float = 5.0

    def as_dict(self) -> dict[str, float]:
        return {
            "weight_round_trip_cost": self.weight_round_trip_cost,
            "weight_exit_capacity": self.weight_exit_capacity,
            "weight_time_to_expiry": self.weight_time_to_expiry,
            "weight_required_move": self.weight_required_move,
            "weight_fee_cost": self.weight_fee_cost,
            "depth_comfort_multiple": self.depth_comfort_multiple,
            "days_to_expiry_comfort": self.days_to_expiry_comfort,
            "max_required_move_pct": self.max_required_move_pct,
            "max_round_trip_cost_pct": self.max_round_trip_cost_pct,
            "max_fee_cost_pct": self.max_fee_cost_pct,
        }


#: چرا مؤلفه‌ی «عملکرد استراتژی» در این نسخه **اصلاً ساخته نمی‌شود**.
#:
#: تنها چیزی که در دست داریم نرخ برد است، و نرخ برد به‌تنهایی شاهدِ
#: عملکرد نیست: ۹ برد کوچک و ۱ باخت بزرگ نرخ بردِ ۹۰٪ می‌دهد و پول از
#: دست می‌دهد. برای امتیازدادن به عملکرد، سود و زیانِ **پس از هزینه**
#: با مبنای قابل مقایسه لازم است که این پروژه هنوز ثبت نمی‌کند. تا آن
#: موقع، نداشتنِ مؤلفه صادقانه‌تر از داشتنِ مؤلفه‌ی گمراه‌کننده است.
EVIDENCE_DISABLED_NOTE = (
    "مؤلفه‌ی عملکرد استراتژی در این نسخه غیرفعال است: تنها دادهٔ موجود "
    "نرخ برد است و نرخ برد به‌تنهایی شاهدِ عملکرد نیست (چند برد کوچک و "
    "یک باخت بزرگ هم نرخ بردِ بالا می‌سازد). تا وقتی سود و زیانِ پس از "
    "هزینه با مبنای قابل مقایسه ثبت نشود، این مؤلفه امتیاز نمی‌دهد."
)


@dataclass(frozen=True)
class Component:
    """یک مؤلفه‌ی امتیاز، با همه‌ی چیزی که برای توضیحش لازم است."""

    key: str
    label: str
    group: str
    weight: float
    #: ۰ تا ۱۰۰؛ `None` یعنی داده‌اش نبود — نه صفر.
    score: float | None
    #: عددِ خامی که امتیاز از آن آمد (برای نمایش)
    measured: float | None
    unit: str
    #: جمله‌ی خوانا: چه چیزی سنجیده شد و چرا این امتیاز
    detail: str

    @property
    def is_known(self) -> bool:
        return self.score is not None

    @property
    def contribution(self) -> float:
        """سهمِ واقعی این مؤلفه در امتیازِ محافظه‌کارانه (نامعلوم = صفر)."""
        return 0.0 if self.score is None else self.score * self.weight / 100.0

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "group": self.group,
            "weight": self.weight,
            "score": self.score,
            "measured": self.measured,
            "unit": self.unit,
            "detail": self.detail,
            "known": self.is_known,
            "contribution": round(self.contribution, 2),
        }


@dataclass(frozen=True)
class RankedOpportunity:
    """یک فرصتِ رتبه‌گرفته، با همه‌ی دلایلش."""

    symbol: str
    strategy: str
    side: str
    quantity: int
    components: tuple[Component, ...]
    #: سرمایه‌ی درگیر به ریال: قیمتِ **اجراپذیرِ** ورود × تعداد ×
    #: اندازه‌ی قرارداد، به‌علاوه‌ی کارمزدِ **اعلام‌شده** اگر باشد.
    #: **گزارش می‌شود، امتیاز نمی‌گیرد** — ارزان بودن مزیت نیست.
    capital_required: float | None
    #: زیانِ نظری در بدترین حالت: خریدِ اختیار می‌تواند **کلِ** پرمیوم
    #: را از دست بدهد (به‌علاوه‌ی کارمزد). این با «زیان تا حد ضرر» یکی
    #: نیست و قاطی‌کردنشان ریسک را کم‌تر از واقع نشان می‌دهد.
    max_theoretical_loss: float | None
    #: زیان اگر حد ضررِ پیشنهادیِ ماژول ریسک بخورد — مشروط به اینکه
    #: واقعاً بشود در آن قیمت خارج شد.
    stop_loss_loss: float | None
    breakeven: float | None
    #: آیا سر‌به‌سر کارمزد را هم در بر دارد؟ اگر نه، «خالص» نیست.
    breakeven_includes_fees: bool
    observed_at: str
    verdict: str

    @property
    def total_weight(self) -> float:
        return sum(c.weight for c in self.components)

    @property
    def known_weight(self) -> float:
        return sum(c.weight for c in self.components if c.is_known)

    @property
    def score(self) -> float:
        """امتیازِ محافظه‌کارانه: نامعلوم = صفر. **مبنای رتبه.**"""
        total = self.total_weight
        if total <= 0:
            return 0.0
        return sum(c.contribution for c in self.components) / total * 100.0

    @property
    def score_best_case(self) -> float:
        """سقفِ ممکن اگر هر مؤلفه‌ی نامعلوم بهترین حالتش باشد."""
        total = self.total_weight
        if total <= 0:
            return 0.0
        unknown = total - self.known_weight
        return (
            sum(c.contribution for c in self.components) + unknown
        ) / total * 100.0

    @property
    def coverage_pct(self) -> float:
        total = self.total_weight
        return 100.0 if total <= 0 else self.known_weight / total * 100.0

    @property
    def strengths(self) -> tuple[Component, ...]:
        """مؤلفه‌هایی که بیشترین سهم را در این امتیاز داشته‌اند."""
        known = [c for c in self.components if c.is_known]
        return tuple(sorted(known, key=lambda c: c.contribution, reverse=True)[:2])

    @property
    def weakness(self) -> Component | None:
        """مهم‌ترین ضعف: بزرگ‌ترین وزنی که کمترین امتیاز را گرفته.

        نامعلوم‌ها اینجا نمی‌آیند؛ آن‌ها ضعف نیستند، **ندانستن**‌اند و
        جای خودشان (`unknown_components`) گزارش می‌شوند.
        """
        known = [c for c in self.components if c.is_known]
        if not known:
            return None
        # فاصله تا سقفِ همان مؤلفه: چقدر امتیاز اینجا از دست رفته است
        return max(known, key=lambda c: (100.0 - (c.score or 0.0)) * c.weight)

    @property
    def unknown_components(self) -> tuple[Component, ...]:
        return tuple(c for c in self.components if not c.is_known)

    def to_dict(self) -> dict[str, object]:
        weakness = self.weakness
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "side": self.side,
            "quantity": self.quantity,
            "score": round(self.score, 1),
            "score_best_case": round(self.score_best_case, 1),
            "coverage_pct": round(self.coverage_pct, 1),
            "capital_required": self.capital_required,
            "max_theoretical_loss": self.max_theoretical_loss,
            "stop_loss_loss": self.stop_loss_loss,
            "breakeven": self.breakeven,
            "breakeven_includes_fees": self.breakeven_includes_fees,
            "observed_at": self.observed_at,
            "verdict": self.verdict,
            "components": [c.to_dict() for c in self.components],
            "strengths": [c.label for c in self.strengths],
            "weakness": None if weakness is None else {
                "label": weakness.label,
                "detail": weakness.detail,
                "score": weakness.score,
            },
            "unknown": [
                {"label": c.label, "detail": c.detail}
                for c in self.unknown_components
            ],
        }


@dataclass(frozen=True)
class ExcludedOpportunity:
    """گزینه‌ای که وارد رتبه‌بندی اصلی نشد، با علتِ صریح."""

    symbol: str
    strategy: str
    reason: str
    verdict: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "reason": self.reason,
            "verdict": self.verdict,
        }


@dataclass(frozen=True)
class RankingResult:
    """خروجی یک پاس رتبه‌بندی."""

    ranked: tuple[RankedOpportunity, ...] = ()
    excluded: tuple[ExcludedOpportunity, ...] = ()
    weights: RankingWeights = field(default_factory=RankingWeights)
    evaluated_at: datetime | None = None
    #: داده‌ی نمایشی است یا واقعی؟ نمایش آزمایشی باید برچسب داشته باشد.
    demo: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "ranked": [r.to_dict() for r in self.ranked],
            "excluded": [e.to_dict() for e in self.excluded],
            "weights": self.weights.as_dict(),
            "evaluated_at": (
                self.evaluated_at.isoformat(timespec="seconds")
                if self.evaluated_at
                else None
            ),
            "demo": self.demo,
            "scope": (
                "نسخه‌ی اول فقط **خریدِ اختیارِ تک‌پایه** را رتبه می‌دهد. "
                "فروش و ساختارهای چندپایه با فرمولِ خرید امتیاز نمی‌گیرند."
            ),
            "evidence_note": EVIDENCE_DISABLED_NOTE,
            "note": (
                "«امتیاز اولویت بررسی» است، نه احتمال برد و نه بازده مورد "
                "انتظار. وزن‌ها فرضِ اولیه‌اند و اثباتی پشتشان نیست. رتبه با "
                "امتیازِ محافظه‌کارانه چیده می‌شود: مؤلفه‌ی نامعلوم صفر حساب "
                "می‌شود تا نبودِ داده کسی را بالا نبرد."
            ),
        }


# ----------------------------------------------------------------------
def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _lower_is_better(value: float | None, limit: float) -> float | None:
    """۱۰۰ وقتی صفر است، صفر وقتی به سقف رسیده. اکیداً نزولی."""
    if value is None or limit <= 0:
        return None
    return _clamp01(1.0 - value / limit) * 100.0


def _higher_is_better(
    value: float | None, floor: float, comfort: float
) -> float | None:
    """صفر روی کفِ غربال، ۱۰۰ از «راحت» به بالا. اکیداً صعودی بینشان."""
    if value is None:
        return None
    span = comfort - floor
    if span <= 0:
        return 100.0 if value >= floor else 0.0
    return _clamp01((value - floor) / span) * 100.0


def _round_trip_cost_component(
    report: TradabilityReport, weights: RankingWeights
) -> Component:
    """هزینه‌ی قیمتیِ رفت‌وبرگشت، از قیمتِ **اجراپذیرِ** هر دو سمت.

    نصفِ اسپرد هزینه‌ی رفت‌وبرگشت **نیست**: رفت‌وبرگشت یعنی روی
    `ask` بخری و روی `bid` بفروشی، یعنی کلِ اسپرد — به‌علاوه‌ی لغزشِ
    هر دو سمت برای همین تعداد قرارداد.

    مخرج قیمتِ ورود است، پس عدد یعنی «چند درصد از پرمیومِ پرداختی».
    اگر یکی از دو سمت برای این حجم اجراپذیر نباشد، عددی ساخته نمی‌شود.
    """
    observation = report.observation
    cost = observation.round_trip_cost_pct
    label = "هزینه‌ی قیمتی رفت‌وبرگشت"
    unit = "٪ از پرمیومِ پرداختی"

    if cost is None:
        missing = (
            "سمتِ ورود" if not observation.entry_fill_price else "سمتِ خروج"
        )
        return Component(
            key="round_trip_cost",
            label=label,
            group=GROUP_EXECUTION,
            weight=weights.weight_round_trip_cost,
            score=None,
            measured=None,
            unit=unit,
            detail=(
                f"{missing} برای {observation.quantity} قرارداد اجراپذیر نیست؛ "
                "هزینه‌ی رفت‌وبرگشت محاسبه نشد و عددی جایش ساخته نمی‌شود."
            ),
        )

    return Component(
        key="round_trip_cost",
        label=label,
        group=GROUP_EXECUTION,
        weight=weights.weight_round_trip_cost,
        score=_lower_is_better(cost, weights.max_round_trip_cost_pct),
        measured=round(cost, 2),
        unit=unit,
        detail=(
            f"ورود {observation.entry_fill_price:,.0f} ← خروج "
            f"{observation.exit_fill_price:,.0f} برای {observation.quantity} "
            f"قرارداد = {cost:,.2f}٪ از پرمیومِ پرداختی "
            f"(سقفِ تنظیم‌شده {weights.max_round_trip_cost_pct:,.1f}٪)"
        ),
    )


def _exit_capacity_component(
    report: TradabilityReport, thresholds: Thresholds, weights: RankingWeights
) -> Component:
    """ظرفیتِ خروج — فقط عمقی که **در محدوده‌ی قیمتی** است.

    حجمی که در قیمت‌های دور نشسته سفارش را پر می‌کند ولی حاشیه‌ی امنِ
    خروج نیست؛ امتیاز دادن به آن یعنی وانمود کنیم راهِ خروجی هست که
    عملاً با زیانِ معنادار همراه است.
    """
    observation = report.observation
    ratio = observation.usable_exit_depth_ratio
    floor = thresholds.min_exit_depth_ratio
    # اگر کاربر کفِ غربال را صفر بگذارد، ضرب‌کردن هم صفر می‌شود و این
    # سنجه بی‌صدا بی‌اثر می‌ماند. آن‌وقت خودِ ضریب، مقیاسِ مطلق می‌شود.
    comfort = max(floor * weights.depth_comfort_multiple, weights.depth_comfort_multiple)
    label = "ظرفیت خروج در محدوده‌ی قیمتی"
    unit = "برابرِ سفارش"
    if ratio is None:
        return Component(
            key="exit_capacity",
            label=label,
            group=GROUP_EXECUTION,
            weight=weights.weight_exit_capacity,
            score=None,
            measured=None,
            unit=unit,
            detail="عمقِ سمت خروج در دسترس نیست.",
        )
    total = observation.exit_depth_ratio
    extra = (
        ""
        if total is None or total <= ratio + 1e-9
        else f"؛ عمقِ کل {total:,.2f} برابر است ولی مازادش در قیمت‌های دورتر از "
             f"{thresholds.max_exit_slippage_pct:,.1f}٪ نشسته و شمرده نشد"
    )
    return Component(
        key="exit_capacity",
        label=label,
        group=GROUP_EXECUTION,
        weight=weights.weight_exit_capacity,
        score=_higher_is_better(ratio, floor, comfort),
        measured=round(ratio, 2),
        unit=unit,
        detail=(
            f"عمقِ درونِ {thresholds.max_exit_slippage_pct:,.1f}٪ از بهترین مظنه، "
            f"{ratio:,.2f} برابرِ سفارشِ {observation.quantity} قراردادی است "
            f"(کفِ غربال {floor:,.2f}، «راحت» {comfort:,.2f}){extra}"
        ),
    )


def _time_component(
    report: TradabilityReport, thresholds: Thresholds, weights: RankingWeights
) -> Component:
    """فاصله تا سررسید: فرصتِ محقق‌شدنِ فرضِ استراتژی."""
    days = report.observation.days_to_expiry
    floor = float(thresholds.min_days_to_expiry)
    comfort = float(weights.days_to_expiry_comfort)
    if days is None:
        return Component(
            key="time_to_expiry",
            label="فاصله تا سررسید",
            group=GROUP_RISK,
            weight=weights.weight_time_to_expiry,
            score=None,
            measured=None,
            unit="روز",
            detail="سررسید دانسته نیست.",
        )
    return Component(
        key="time_to_expiry",
        label="فاصله تا سررسید",
        group=GROUP_RISK,
        weight=weights.weight_time_to_expiry,
        score=_higher_is_better(float(days), floor, comfort),
        measured=float(days),
        unit="روز",
        detail=(
            f"{days} روز تا سررسید (کفِ غربال {floor:,.0f} روز، "
            f"«راحت» {comfort:,.0f} روز)"
        ),
    )


def _required_move_component(
    breakeven: float | None,
    spot: float | None,
    option_type: str,
    weights: RankingWeights,
) -> Component:
    """چند درصد باید پایه حرکت کند تا این موقعیت به سر‌به‌سر برسد.

    ⚠️ این **احتمال** نیست. فقط می‌گوید فرضِ استراتژی چقدر سخت‌گیرانه
    است: هرچه حرکتِ لازم بزرگ‌تر، ادعا بزرگ‌تر. هیچ توزیعی روی این
    حرکت فرض نمی‌شود چون داده‌اش را نداریم.
    """
    label = "حرکت لازم تا سر‌به‌سر"
    if not breakeven or not spot or spot <= 0:
        return Component(
            key="required_move",
            label=label,
            group=GROUP_RISK,
            weight=weights.weight_required_move,
            score=None,
            measured=None,
            unit="٪",
            detail="قیمتِ پایه یا سر‌به‌سر دانسته نیست.",
        )

    if option_type == "call":
        move_pct = (breakeven - spot) / spot * 100.0
    else:
        move_pct = (spot - breakeven) / spot * 100.0
    needed = max(move_pct, 0.0)

    return Component(
        key="required_move",
        label=label,
        group=GROUP_RISK,
        weight=weights.weight_required_move,
        score=_lower_is_better(needed, weights.max_required_move_pct),
        measured=round(needed, 2),
        unit="٪ حرکت پایه",
        detail=(
            f"سر‌به‌سر {breakeven:,.0f} در برابر پایه {spot:,.0f} → "
            + (
                "همین حالا از سر‌به‌سر گذشته است"
                if needed <= 0
                else f"{needed:,.2f}٪ حرکت لازم است "
                f"(سقفِ تنظیم‌شده {weights.max_required_move_pct:,.1f}٪)"
            )
        ),
    )


def _fee_cost_component(
    round_trip_fees: float | None,
    fees_known: bool,
    capital: float | None,
    weights: RankingWeights,
) -> Component:
    """کارمزدِ رفت‌وبرگشت نسبت به سرمایه‌ی درگیر.

    ⚠️ سه حالتِ متفاوت، که قاطی‌کردنشان گمراه‌کننده است:

    * نرخ **اعلام‌شده‌ی** ناصفر → هزینه‌ی دانسته؛
    * نرخ **اعلام‌شده‌ی** صفر → هزینه‌ی دانسته و برابر صفر (امتیاز کامل)؛
    * نرخی تنظیم **نشده** → **نامعلوم**، نه رایگان.
    """
    label = "سهم کارمزد از سرمایه"
    unit = "٪ از سرمایه‌ی درگیر"
    if not fees_known or not capital or capital <= 0:
        return Component(
            key="fee_cost",
            label=label,
            group=GROUP_RISK,
            weight=weights.weight_fee_cost,
            score=None,
            measured=None,
            unit=unit,
            detail=(
                "نرخ کارمزد تنظیم/اعلام نشده است؛ هزینه‌ی واقعی نامعلوم است "
                "و صفر فرض نمی‌شود."
            ),
        )
    fees = round_trip_fees or 0.0
    drag = fees / capital * 100.0
    return Component(
        key="fee_cost",
        label=label,
        group=GROUP_RISK,
        weight=weights.weight_fee_cost,
        score=_lower_is_better(drag, weights.max_fee_cost_pct),
        measured=round(drag, 3),
        unit=unit,
        detail=(
            f"کارمزد رفت‌وبرگشتِ اعلام‌شده {fees:,.0f} ریال = {drag:,.3f}٪ "
            f"از سرمایه‌ی {capital:,.0f} ریالی"
        ),
    )


def breakeven_price(
    strike: float,
    entry_price: float,
    option_type: str,
    fee_per_unit: float = 0.0,
) -> float | None:
    """سر‌به‌سرِ یک موقعیتِ **خریدِ** تک‌پایه، به ازای هر واحدِ پایه.

    `entry_price` باید قیمتِ **اجراپذیرِ** ورود باشد، نه پرمیومِ
    پیشنهادیِ استراتژی: سر‌به‌سر روی پولی حساب می‌شود که واقعاً پرداخت
    می‌شود.

    `fee_per_unit` وقتی صفر است که یا نرخ صفرِ اعلام‌شده باشد یا اصلاً
    کارمزدی در کار نباشد؛ اگر نرخ **نامعلوم** است، فراخواننده باید
    صفر بدهد و نتیجه را «بدون کارمزد» معرفی کند — نه «خالص».
    """
    if strike <= 0 or entry_price < 0:
        return None
    total = entry_price + max(fee_per_unit, 0.0)
    return strike + total if option_type == "call" else strike - total


def rank_opportunity(
    *,
    symbol: str,
    strategy: str,
    side: str,
    quantity: int,
    report: TradabilityReport,
    thresholds: Thresholds,
    weights: RankingWeights,
    option_type: str,
    strike: float,
    contract_size: int,
    underlying_price: float | None,
    stop_loss_loss: float | None,
    round_trip_fees: float | None,
    fees_known: bool,
) -> RankedOpportunity:
    """امتیازِ یک **خریدِ اختیارِ تک‌پایه**، با همه‌ی مؤلفه‌ها و دلایلشان.

    همه‌ی عددهای ریالی از قیمتِ **اجراپذیرِ ورود** می‌آیند، نه از پرمیومِ
    پیشنهادیِ استراتژی — تا سرمایه، سر‌به‌سر و زیان با یک تعریف حساب
    شوند و با هم بخوانند.
    """
    entry = report.observation.entry_fill_price
    units = quantity * max(contract_size, 1)

    # کارمزد فقط وقتی وارد عددها می‌شود که **اعلام‌شده** باشد. نرخِ
    # نامعلوم صفر فرض نمی‌شود؛ به‌جایش سر‌به‌سر «بدون کارمزد» می‌ماند.
    fees = (round_trip_fees or 0.0) if fees_known else 0.0
    fee_per_unit = (fees / units) if (fees_known and units) else 0.0

    premium_cost = entry * units if entry else None
    capital_required = None if premium_cost is None else premium_cost + fees
    # خریدِ اختیار: بدترین حالت یعنی بی‌ارزش منقضی شدن — کلِ پرمیوم
    # به‌علاوه‌ی کارمزد. این «حداکثر زیان نظری» است.
    max_theoretical_loss = capital_required

    breakeven = (
        None if entry is None
        else breakeven_price(strike, entry, option_type, fee_per_unit)
    )

    components = (
        _round_trip_cost_component(report, weights),
        _exit_capacity_component(report, thresholds, weights),
        _time_component(report, thresholds, weights),
        _required_move_component(breakeven, underlying_price, option_type, weights),
        _fee_cost_component(round_trip_fees, fees_known, capital_required, weights),
    )
    return RankedOpportunity(
        symbol=symbol,
        strategy=strategy,
        side=side,
        quantity=quantity,
        components=components,
        capital_required=capital_required,
        max_theoretical_loss=max_theoretical_loss,
        stop_loss_loss=stop_loss_loss,
        breakeven=breakeven,
        breakeven_includes_fees=fees_known,
        observed_at=report.observation.observed_at.isoformat(timespec="seconds"),
        verdict=report.verdict.value,
    )


def rank_opportunities(
    candidates: list[dict[str, object]],
    thresholds: Thresholds,
    weights: RankingWeights,
    evaluated_at: datetime | None = None,
    demo: bool = False,
) -> RankingResult:
    """رتبه‌بندیِ یک پاس.

    هر عضو `candidates` یک دیکشنری با کلیدهای لازمِ `rank_opportunity`
    به‌علاوه‌ی `report` است. سه دروازه پیش از رتبه‌گرفتن هست و ردشدن از
    هر کدام در `excluded` **با علت** می‌آید، نه بی‌صدا.
    """
    ranked: list[RankedOpportunity] = []
    excluded: list[ExcludedOpportunity] = []

    for candidate in candidates:
        report: TradabilityReport = candidate["report"]  # type: ignore[assignment]
        symbol = str(candidate["symbol"])
        strategy = str(candidate["strategy"])
        side = str(candidate["side"]).lower()

        # دروازه‌ی اول: غربال. امتیاز جای نقدشوندگی را نمی‌گیرد.
        if report.verdict is not Verdict.TRADABLE:
            excluded.append(ExcludedOpportunity(
                symbol=symbol,
                strategy=strategy,
                verdict=report.verdict.value,
                reason=f"{report.verdict_label} در غربال — {report.reason}",
            ))
            continue

        # دروازه‌ی دوم: دامنه‌ی نسخه‌ی اول — ساختار چندپایه.
        if candidate.get("leg_group_id"):
            excluded.append(ExcludedOpportunity(
                symbol=symbol,
                strategy=strategy,
                verdict=report.verdict.value,
                reason=(
                    "پایه‌ی یک ساختار چندپایه است؛ نسخه‌ی اولِ رتبه‌بندی فقط "
                    "خریدِ اختیارِ تک‌پایه را می‌سنجد (مقایسه‌ی ساختارها مخرجِ "
                    "یکسان ندارد و وجه تضمین مدل نشده است)."
                ),
            ))
            continue

        # دروازه‌ی سوم: فقط **خرید**. فرمولِ این نسخه — سرمایه‌ی درگیر،
        # سر‌به‌سر، حداکثر زیانِ نظری — همه از منطقِ خریدِ اختیار می‌آیند.
        # فروش ریسک و وجه تضمینِ کاملاً متفاوتی دارد؛ امتیاز دادن به آن
        # با همین فرمول یعنی ریسکش را غلط نشان بدهیم.
        if side != "buy":
            excluded.append(ExcludedOpportunity(
                symbol=symbol,
                strategy=strategy,
                verdict=report.verdict.value,
                reason=(
                    "موقعیتِ فروش است؛ نسخه‌ی اولِ رتبه‌بندی فقط خریدِ اختیار "
                    "را می‌سنجد. فرمولِ سرمایه و زیانِ خرید برای فروش معتبر "
                    "نیست و وجه تضمینش در این پروژه مدل نشده است."
                ),
            ))
            continue

        ranked.append(rank_opportunity(
            symbol=symbol,
            strategy=strategy,
            side=side,
            quantity=int(candidate["quantity"]),  # type: ignore[arg-type]
            report=report,
            thresholds=thresholds,
            weights=weights,
            option_type=str(candidate["option_type"]),
            strike=float(candidate["strike"]),  # type: ignore[arg-type]
            contract_size=int(candidate.get("contract_size") or 1),  # type: ignore[arg-type]
            underlying_price=candidate.get("underlying_price"),  # type: ignore[arg-type]
            stop_loss_loss=candidate.get("stop_loss_loss"),  # type: ignore[arg-type]
            round_trip_fees=candidate.get("round_trip_fees"),  # type: ignore[arg-type]
            fees_known=bool(candidate.get("fees_known")),
        ))

    # چیدنِ رتبه با امتیازِ **محافظه‌کارانه**: نامعلوم هیچ‌وقت بالا نمی‌برد.
    # گره‌شکن‌ها هم قطعی‌اند تا خروجی قابلِ بازتولید بماند.
    ranked.sort(key=lambda r: (-r.score, -r.coverage_pct, r.symbol))
    return RankingResult(
        ranked=tuple(ranked),
        excluded=tuple(excluded),
        weights=weights,
        evaluated_at=evaluated_at,
        demo=demo,
    )

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

**سنجه‌های هم‌بسته یک بار شمرده می‌شوند**

اسپرد و لغزش هر دو «هزینه‌ی قیمتیِ ورود و خروج» را می‌سنجند؛ جدا
وزن‌دادن به هر دو یعنی یک شاهد را دو بار پاداش دادن. پس در یک عدد
جمع می‌شوند: `نصفِ اسپرد + لغزش` — یعنی هزینه‌ی عبور از مظنه به‌علاوه‌ی
بدترشدنِ قیمت برای **همین اندازه‌ی سفارش**. عمق جداست چون چیز دیگری
می‌گوید: ظرفیت، نه قیمت.

**نامعلوم پنهان نمی‌شود و به نفع گزینه تمام نمی‌شود**

مؤلفه‌ای که داده‌اش نیست از مخرج حذف **نمی‌شود**. دو عدد گزارش می‌شود:

* `score` — محافظه‌کارانه: نامعلوم = صفر. **مبنای چیدنِ رتبه همین است**،
  پس نبودِ داده هرگز کسی را بالا نمی‌برد.
* `score_best_case` — خوش‌بینانه: نامعلوم = صد. سقفِ ممکن.

فاصله‌ی این دو، «نواری» است که می‌گوید چقدر نمی‌دانیم. `coverage_pct`
همان را به درصدِ وزنِ دانسته بیان می‌کند.

**دامنه‌ی نسخه‌ی اول — صریح**

فقط موقعیتِ **تک‌پایه** رتبه می‌گیرد. ساختار چندپایه (استردل، کالر، …)
کنار گذاشته می‌شود چون مقایسه‌ی سود/بازدهِ ساختارها با مخرج‌های متفاوت
عددِ گمراه‌کننده می‌سازد و وجه تضمینِ پایه‌ی فروش هم در این پروژه مدل
نشده است. این حذف **اعلام** می‌شود، بی‌صدا نیست.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from market.tradability import Thresholds, TradabilityReport, Verdict

#: گروه‌های نمایشی مؤلفه‌ها. فقط برای دسته‌بندی در رابط‌اند؛ محاسبه مسطح است.
GROUP_EXECUTION = "اجرا و نقدشوندگی"
GROUP_RISK = "ریسک و سرمایه"
GROUP_EVIDENCE = "شواهد استراتژی"


@dataclass(frozen=True)
class RankingWeights:
    """وزن‌ها و آستانه‌های رتبه‌بندی.

    ⚠️ این اعداد **فرضِ اولیه‌اند**، نه نتیجه‌ی پژوهش یا بهینه‌سازی. هیچ
    داده‌ای پشتشان نیست که بگوید این ترکیب بهتر از ترکیب دیگری جواب
    می‌دهد؛ نقطه‌ی شروعی‌اند تا چیزی برای تنظیم‌کردن وجود داشته باشد.
    همه از داشبورد قابل تغییرند و اثرشان در ارزیابیِ بعدی دیده می‌شود.
    """

    #: هزینه‌ی قیمتیِ رفت‌وبرگشت (اسپرد + لغزش) برای همین اندازه‌ی سفارش
    weight_price_cost: float = 30.0
    #: ظرفیتِ سمت خروج نسبت به اندازه‌ی سفارش
    weight_exit_capacity: float = 20.0
    #: فاصله تا سررسید
    weight_time_to_expiry: float = 15.0
    #: حرکتی که پایه باید بکند تا این موقعیت به سر‌به‌سر برسد
    weight_required_move: float = 20.0
    #: کارمزدِ رفت‌وبرگشت نسبت به ارزش موقعیت
    weight_cost_drag: float = 5.0
    #: کارنامه‌ی **محقق‌شده‌ی** همین استراتژی در تاریخچه‌ی خودِ پروژه
    weight_strategy_evidence: float = 10.0

    #: عمقی که «راحت» حساب می‌شود: این ضریبِ حداقلِ غربال. عمقِ بالاتر
    #: از این، امتیاز بیشتری نمی‌گیرد — حاشیه‌ی اطمینان اشباع می‌شود.
    depth_comfort_multiple: float = 3.0
    #: فاصله‌ی راحت تا سررسید (روز). از این بیشتر امتیاز اضافه نمی‌کند.
    days_to_expiry_comfort: int = 30
    #: حرکتِ لازم تا سر‌به‌سر از این بیشتر باشد، امتیازِ این مؤلفه صفر است.
    max_required_move_pct: float = 25.0
    #: کارمزدِ رفت‌وبرگشت از این بیشتر باشد، امتیازِ این مؤلفه صفر است.
    max_cost_drag_pct: float = 5.0
    #: کمتر از این تعداد سیگنالِ **نتیجه‌دار**، کارنامه «نامعلوم» است —
    #: نه «بد». نرخ بردِ ۲ سیگنال، شاهد نیست.
    min_resolved_signals: int = 10

    def as_dict(self) -> dict[str, float]:
        return {
            "weight_price_cost": self.weight_price_cost,
            "weight_exit_capacity": self.weight_exit_capacity,
            "weight_time_to_expiry": self.weight_time_to_expiry,
            "weight_required_move": self.weight_required_move,
            "weight_cost_drag": self.weight_cost_drag,
            "weight_strategy_evidence": self.weight_strategy_evidence,
            "depth_comfort_multiple": self.depth_comfort_multiple,
            "days_to_expiry_comfort": self.days_to_expiry_comfort,
            "max_required_move_pct": self.max_required_move_pct,
            "max_cost_drag_pct": self.max_cost_drag_pct,
            "min_resolved_signals": self.min_resolved_signals,
        }


@dataclass(frozen=True)
class StrategyEvidence:
    """کارنامه‌ی محقق‌شده‌ی یک استراتژی، از تاریخچه‌ی خودِ پروژه.

    ⚠️ گذشته است، نه پیش‌بینی. و تا وقتی نمونه کم است، **نامعلوم** می‌ماند:
    نرخ بردِ سه سیگنال، عدد است ولی شاهد نیست.
    """

    strategy: str
    resolved: int = 0
    wins: int = 0
    win_rate_pct: float | None = None
    avg_pnl_pct: float | None = None


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
    #: ارزشِ کلِ موقعیت به ریال — **گزارش می‌شود، امتیاز نمی‌گیرد**.
    #: ارزان بودن به‌خودی‌خود مزیت نیست.
    notional: float | None
    max_loss: float | None
    breakeven: float | None
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
            "notional": self.notional,
            "max_loss": self.max_loss,
            "breakeven": self.breakeven,
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


def _price_cost_component(
    report: TradabilityReport, thresholds: Thresholds, weights: RankingWeights
) -> Component:
    """هزینه‌ی قیمتیِ رفت‌وبرگشت: نصفِ اسپرد + لغزشِ خروج.

    چرا با هم: هر دو یک چیز را می‌سنجند — پولی که صرفِ **قیمت** می‌شود،
    نه ظرفیت. جدا وزن‌دادنشان یعنی یک شاهد دو بار پاداش بگیرد.
    نصفِ اسپرد چون عبور از وسطِ مظنه تا یک سمت، نیمِ اسپرد هزینه دارد.
    """
    observation = report.observation
    spread = observation.relative_spread_pct
    slippage = observation.exit_slippage_pct
    limit = thresholds.max_relative_spread_pct / 2.0 + thresholds.max_exit_slippage_pct

    if spread is None or slippage is None:
        missing = "اسپرد" if spread is None else "لغزش خروج"
        return Component(
            key="price_cost",
            label="هزینه‌ی قیمتی رفت‌وبرگشت",
            group=GROUP_EXECUTION,
            weight=weights.weight_price_cost,
            score=None,
            measured=None,
            unit="٪",
            detail=f"{missing} دانسته نیست؛ هزینه‌ی قیمتی محاسبه نشد.",
        )

    cost = spread / 2.0 + slippage
    return Component(
        key="price_cost",
        label="هزینه‌ی قیمتی رفت‌وبرگشت",
        group=GROUP_EXECUTION,
        weight=weights.weight_price_cost,
        score=_lower_is_better(cost, limit),
        measured=round(cost, 2),
        unit="٪ (نصف اسپرد + لغزش خروج)",
        detail=(
            f"نصفِ اسپرد {spread / 2:,.2f}٪ + لغزشِ خروجِ {observation.quantity} "
            f"قرارداد {slippage:,.2f}٪ = {cost:,.2f}٪ از سقفِ {limit:,.2f}٪"
        ),
    )


def _exit_capacity_component(
    report: TradabilityReport, thresholds: Thresholds, weights: RankingWeights
) -> Component:
    """ظرفیتِ سمت خروج نسبت به **همین** اندازه‌ی سفارش."""
    ratio = report.observation.exit_depth_ratio
    floor = thresholds.min_exit_depth_ratio
    comfort = floor * weights.depth_comfort_multiple
    if ratio is None:
        return Component(
            key="exit_capacity",
            label="ظرفیت خروج",
            group=GROUP_EXECUTION,
            weight=weights.weight_exit_capacity,
            score=None,
            measured=None,
            unit="برابرِ سفارش",
            detail="عمقِ سمت خروج در دسترس نیست.",
        )
    return Component(
        key="exit_capacity",
        label="ظرفیت خروج",
        group=GROUP_EXECUTION,
        weight=weights.weight_exit_capacity,
        score=_higher_is_better(ratio, floor, comfort),
        measured=round(ratio, 2),
        unit="برابرِ سفارش",
        detail=(
            f"عمقِ سمتِ خروج {ratio:,.2f} برابرِ سفارشِ "
            f"{report.observation.quantity} قراردادی است "
            f"(کفِ غربال {floor:,.2f}، «راحت» {comfort:,.2f})"
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


def _cost_drag_component(
    round_trip_fees: float | None, notional: float | None, weights: RankingWeights
) -> Component:
    """کارمزدِ رفت‌وبرگشت نسبت به ارزش موقعیت.

    ⚠️ نبودِ نرخ یعنی **نامعلوم**، نه رایگان. همان قاعده‌ای که کلِ پروژه
    درباره‌ی کارمزد دارد: صفرِ پیش‌فرض ادعای بی‌هزینه بودن نیست.
    """
    label = "سهم کارمزد از موقعیت"
    if round_trip_fees is None or not notional or notional <= 0:
        return Component(
            key="cost_drag",
            label=label,
            group=GROUP_RISK,
            weight=weights.weight_cost_drag,
            score=None,
            measured=None,
            unit="٪",
            detail=(
                "نرخ کارمزد تنظیم نشده است؛ هزینه‌ی واقعی نامعلوم است "
                "(صفر فرض نمی‌شود)."
            ),
        )
    drag = round_trip_fees / notional * 100.0
    return Component(
        key="cost_drag",
        label=label,
        group=GROUP_RISK,
        weight=weights.weight_cost_drag,
        score=_lower_is_better(drag, weights.max_cost_drag_pct),
        measured=round(drag, 3),
        unit="٪ از ارزش موقعیت",
        detail=(
            f"کارمزد رفت‌وبرگشت {round_trip_fees:,.0f} ریال = {drag:,.3f}٪ "
            f"از {notional:,.0f} ریال"
        ),
    )


def _evidence_component(
    evidence: StrategyEvidence | None, strategy: str, weights: RankingWeights
) -> Component:
    """کارنامه‌ی **محقق‌شده‌ی** همین استراتژی در تاریخچه‌ی خودِ پروژه.

    ⚠️ گذشته است، نه پیش‌بینی. و زیر حداقلِ نمونه، نامعلوم می‌ماند —
    نه صفر و نه ۵۰٪. «شاهدی نداریم» با «شاهدِ بد داریم» یکی نیست.
    """
    label = "کارنامه‌ی محقق‌شده‌ی استراتژی"
    minimum = weights.min_resolved_signals
    if evidence is None or evidence.resolved < minimum or evidence.win_rate_pct is None:
        seen = 0 if evidence is None else evidence.resolved
        return Component(
            key="strategy_evidence",
            label=label,
            group=GROUP_EVIDENCE,
            weight=weights.weight_strategy_evidence,
            score=None,
            measured=None,
            unit="٪ نرخ برد",
            detail=(
                f"فقط {seen} سیگنالِ نتیجه‌دار از «{strategy}» ثبت شده "
                f"(حداقل {minimum} لازم است)؛ کارنامه نامعلوم است، نه بد."
            ),
        )
    return Component(
        key="strategy_evidence",
        label=label,
        group=GROUP_EVIDENCE,
        weight=weights.weight_strategy_evidence,
        score=_clamp01(evidence.win_rate_pct / 100.0) * 100.0,
        measured=round(evidence.win_rate_pct, 1),
        unit="٪ نرخ برد",
        detail=(
            f"{evidence.wins} برد از {evidence.resolved} سیگنالِ نتیجه‌دار "
            f"({evidence.win_rate_pct:,.1f}٪). این گذشته است، نه پیش‌بینی."
        ),
    )


def breakeven_price(
    strike: float, premium: float, option_type: str
) -> float | None:
    """سر‌به‌سرِ یک موقعیتِ **خریدِ** تک‌پایه، به ازای هر واحدِ پایه."""
    if strike <= 0 or premium < 0:
        return None
    return strike + premium if option_type == "call" else strike - premium


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
    premium: float,
    underlying_price: float | None,
    notional: float | None,
    max_loss: float | None,
    round_trip_fees: float | None,
    evidence: StrategyEvidence | None,
) -> RankedOpportunity:
    """امتیازِ یک فرصت، با همه‌ی مؤلفه‌ها و دلایلشان."""
    breakeven = breakeven_price(strike, premium, option_type)
    components = (
        _price_cost_component(report, thresholds, weights),
        _exit_capacity_component(report, thresholds, weights),
        _time_component(report, thresholds, weights),
        _required_move_component(breakeven, underlying_price, option_type, weights),
        _cost_drag_component(round_trip_fees, notional, weights),
        _evidence_component(evidence, strategy, weights),
    )
    return RankedOpportunity(
        symbol=symbol,
        strategy=strategy,
        side=side,
        quantity=quantity,
        components=components,
        notional=notional,
        max_loss=max_loss,
        breakeven=breakeven,
        observed_at=report.observation.observed_at.isoformat(timespec="seconds"),
        verdict=report.verdict.value,
    )


def rank_opportunities(
    candidates: list[dict[str, object]],
    thresholds: Thresholds,
    weights: RankingWeights,
    evidence_by_strategy: dict[str, StrategyEvidence] | None = None,
    evaluated_at: datetime | None = None,
    demo: bool = False,
) -> RankingResult:
    """رتبه‌بندیِ یک پاس.

    هر عضو `candidates` یک دیکشنری با کلیدهای لازمِ `rank_opportunity`
    به‌علاوه‌ی `report` است. گزینه‌ای که حکمش `tradable` نیست یا پایه‌ی
    یک ساختار چندپایه است، وارد رتبه‌بندی **نمی‌شود** و در `excluded`
    با علت می‌آید.
    """
    evidence_by_strategy = evidence_by_strategy or {}
    ranked: list[RankedOpportunity] = []
    excluded: list[ExcludedOpportunity] = []

    for candidate in candidates:
        report: TradabilityReport = candidate["report"]  # type: ignore[assignment]
        symbol = str(candidate["symbol"])
        strategy = str(candidate["strategy"])

        # دروازه‌ی اول: غربال. امتیاز جای نقدشوندگی را نمی‌گیرد.
        if report.verdict is not Verdict.TRADABLE:
            excluded.append(ExcludedOpportunity(
                symbol=symbol,
                strategy=strategy,
                verdict=report.verdict.value,
                reason=f"{report.verdict_label} در غربال — {report.reason}",
            ))
            continue

        # دروازه‌ی دوم: دامنه‌ی نسخه‌ی اول.
        if candidate.get("leg_group_id"):
            excluded.append(ExcludedOpportunity(
                symbol=symbol,
                strategy=strategy,
                verdict=report.verdict.value,
                reason=(
                    "پایه‌ی یک ساختار چندپایه است؛ نسخه‌ی اولِ رتبه‌بندی فقط "
                    "موقعیتِ تک‌پایه را می‌سنجد (مقایسه‌ی ساختارها مخرجِ "
                    "یکسان ندارد و وجه تضمین مدل نشده است)."
                ),
            ))
            continue

        ranked.append(rank_opportunity(
            symbol=symbol,
            strategy=strategy,
            side=str(candidate["side"]),
            quantity=int(candidate["quantity"]),  # type: ignore[arg-type]
            report=report,
            thresholds=thresholds,
            weights=weights,
            option_type=str(candidate["option_type"]),
            strike=float(candidate["strike"]),  # type: ignore[arg-type]
            premium=float(candidate["premium"]),  # type: ignore[arg-type]
            underlying_price=candidate.get("underlying_price"),  # type: ignore[arg-type]
            notional=candidate.get("notional"),  # type: ignore[arg-type]
            max_loss=candidate.get("max_loss"),  # type: ignore[arg-type]
            round_trip_fees=candidate.get("round_trip_fees"),  # type: ignore[arg-type]
            evidence=evidence_by_strategy.get(strategy),
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

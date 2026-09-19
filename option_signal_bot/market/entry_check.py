"""بررسیِ **پیش از ورود** — همان چیزی که یک بار دیگر، با دادهٔ تازه.

**چرا لازم است**

رتبه‌بندی عکسِ یک لحظه است. کاربر ممکن است ده دقیقه بعد تصمیم بگیرد،
تعداد را عوض کند، یا اصلاً دفترِ سفارش تا آن موقع خالی شده باشد. رتبه‌ی
دیروز — و حتی رتبه‌ی ده دقیقه پیش — **تضمینِ اجرای امروز نیست**.

پس پیش از ثبتِ معامله‌ی کاغذی، همه‌چیز با دادهٔ تازه و **برای همان
تعدادی که کاربر انتخاب کرده** دوباره حساب می‌شود: غربال، اجراپذیریِ
ورود و خروج، قیمتِ اجرایی، وجهِ لازم، و دوباره خودِ امتیاز.

**مانع در برابر هشدار**

* `blockers` یعنی «نمی‌شود»: غربال رد کرده، ورود اجراپذیر نیست، سررسید
  گذشته، یا وجه کافی نیست. با وجودِ هر کدام، بلیتِ تأییدی صادر نمی‌شود.
* `warnings` یعنی «بشود، ولی بدان»: نرخِ کارمزد اعلام‌نشده، مهر زمانیِ
  بازار نامعلوم، یا امتیازی که از زمانِ دیدنِ کارت پایین آمده. این‌ها
  جلوی کار را نمی‌گیرند، ولی پنهان هم نمی‌شوند.

**اثر انگشت (`fingerprint`)**

خلاصه‌ی چیزهایی که اگر عوض شوند، تأییدِ قبلی دیگر معنا ندارد: نماد،
سمت، تعداد و قیمتِ اجراییِ ورود. بلیتِ تأیید روی همین بسته می‌شود.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from market.opportunity_ranking import (
    RankedOpportunity,
    RankingWeights,
    rank_opportunity,
)
from market.tradability import (
    EntryStatus,
    ScreeningRecord,
    Thresholds,
    TradabilityReport,
    Verdict,
)
from risk.fees import FeeSchedule


@dataclass(frozen=True)
class Blocker:
    """یک دلیلِ مشخص که چرا همین حالا نمی‌شود وارد شد."""

    code: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class EntryCheck:
    """نتیجه‌ی بررسیِ پیش از ورود، برای همین تعداد و همین لحظه."""

    symbol: str
    side: str
    quantity: int
    checked_at: datetime
    record: ScreeningRecord
    #: ارزیابیِ **تازه**ی همین فرصت؛ `None` وقتی اصلاً قابل امتیازدادن نیست.
    opportunity: RankedOpportunity | None
    blockers: tuple[Blocker, ...]
    warnings: tuple[str, ...]
    #: وجهِ نقدِ در دسترسِ حساب در لحظه‌ی بررسی؛ `None` یعنی پرسیده نشد.
    available_cash: float | None
    fee_basis: str

    @property
    def ok(self) -> bool:
        return not self.blockers

    @property
    def entry_price(self) -> float | None:
        return self.record.report.observation.entry_fill_price

    @property
    def fingerprint(self) -> str:
        """چیزی که تأیید روی آن بسته می‌شود. تغییرش یعنی تأییدِ باطل."""
        price = self.entry_price
        return "|".join((
            self.symbol,
            self.side,
            str(self.quantity),
            "-" if price is None else f"{price:.4f}",
        ))

    def to_dict(self) -> dict[str, object]:
        opportunity = self.opportunity
        return {
            "ok": self.ok,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "checked_at": self.checked_at.isoformat(timespec="seconds"),
            "blockers": [b.to_dict() for b in self.blockers],
            "warnings": list(self.warnings),
            "screening": self.record.to_dict(),
            "opportunity": None if opportunity is None else opportunity.to_dict(),
            "entry_price": self.entry_price,
            "available_cash": self.available_cash,
            "fee_basis": self.fee_basis,
            "fingerprint": self.fingerprint,
            "note": (
                "این بررسی با دادهٔ همین لحظه و برای همین تعداد انجام شد. "
                "رتبه‌ای که پیش‌تر دیده‌اید تضمینِ اجرای حالا نیست."
            ),
        }


def check_entry(
    *,
    symbol: str,
    side: str,
    quantity: int,
    report: TradabilityReport,
    strategy: str,
    option_type: str,
    strike: float,
    contract_size: int,
    expiry: date,
    underlying_price: float | None,
    stop_loss_price: float | None,
    fees: FeeSchedule | None,
    thresholds: Thresholds,
    weights: RankingWeights,
    available_cash: float | None = None,
    previous_score: float | None = None,
    signal_id: str | None = None,
    today: date | None = None,
) -> EntryCheck:
    """همه‌ی شرط‌های ورود را یک‌جا، با دادهٔ تازه، برای همین تعداد.

    ترتیبِ بررسی‌ها عمدی است: اول چیزهایی که اصلاً اجازه‌ی ورود
    نمی‌دهند، بعد عددهای مالی — تا وقتی مبنا نداریم عددی ساخته نشود.
    """
    now = report.observation.observed_at
    observation = report.observation
    blockers: list[Blocker] = []
    warnings: list[str] = []

    record = ScreeningRecord(
        symbol=symbol,
        strategy=strategy,
        side=side,
        quantity=quantity,
        report=report,
        signal_id=signal_id,
    )

    if side.lower() != "buy":
        blockers.append(Blocker(
            "scope_sell",
            "این نسخه فقط **خریدِ** اختیارِ تک‌پایه را ارزیابی و ثبت می‌کند؛ "
            "وجه تضمینِ فروش در این پروژه مدل نشده است.",
        ))

    if expiry < (today or date.today()):
        blockers.append(Blocker(
            "expired",
            f"{symbol} در {expiry.isoformat()} سررسید شده است؛ ورودِ تازه "
            "روی قراردادِ سررسیدشده معنا ندارد.",
        ))

    if report.verdict is not Verdict.TRADABLE:
        blockers.append(Blocker(
            "screening",
            f"{report.verdict_label} در غربال — {report.reason}",
        ))

    status = observation.entry_status
    if status is EntryStatus.SHORT_OF_DEPTH:
        depth = observation.entry_depth_contracts or 0
        blockers.append(Blocker(
            "entry_short_of_depth",
            f"سمتِ ورود برای {quantity} قرارداد اجراپذیر نیست: عمقِ موجود "
            f"{depth} قرارداد است. تعداد را کمتر کنید و دوباره بررسی کنید.",
        ))
    elif status is EntryStatus.UNKNOWN:
        blockers.append(Blocker(
            "entry_unknown",
            f"اجراپذیریِ ورود برای {quantity} قرارداد **نامعلوم** است: "
            "دفترِ سمت ورود در دسترس نیست. این کمبودِ عمق نیست، نبودِ داده "
            "است؛ با تعدادِ کمتر هم روشن نمی‌شود.",
        ))

    opportunity: RankedOpportunity | None = None
    if status is EntryStatus.EXECUTABLE and side.lower() == "buy":
        opportunity = rank_opportunity(
            symbol=symbol,
            strategy=strategy,
            side=side.lower(),
            quantity=quantity,
            report=report,
            thresholds=thresholds,
            weights=weights,
            option_type=option_type,
            strike=strike,
            contract_size=contract_size,
            underlying_price=underlying_price,
            stop_loss_price=stop_loss_price,
            fees=fees,
        )
        needed = opportunity.capital_required
        if available_cash is not None:
            if needed is not None and needed > available_cash:
                blockers.append(Blocker(
                    "insufficient_cash",
                    f"وجهِ لازم برای ورود {needed:,.0f} ریال است ولی موجودیِ "
                    f"نقدِ حساب {available_cash:,.0f} ریال. تعداد را کمتر "
                    "کنید یا حساب را شارژ کنید.",
                ))
            elif needed is None and opportunity.premium_cost > available_cash:
                blockers.append(Blocker(
                    "insufficient_cash",
                    f"فقط پرمیومِ پرداختی {opportunity.premium_cost:,.0f} ریال "
                    f"است و از موجودیِ نقدِ {available_cash:,.0f} ریالی بیشتر "
                    "می‌شود — کارمزدش هم هنوز روی آن نیامده.",
                ))
        if needed is None:
            warnings.append(
                "نرخ کارمزد اعلام نشده است: وجهِ لازم برای ورود و حداکثر "
                "زیان «نامعلوم» می‌مانند و صفر فرض نمی‌شوند."
            )
        if previous_score is not None and opportunity.score < previous_score - 0.05:
            warnings.append(
                f"امتیاز از زمانی که کارت را دیدید پایین آمده: "
                f"{previous_score:,.1f} ← {opportunity.score:,.1f}."
            )

    if not observation.source_time_known:
        warnings.append(report.source_time_note)

    return EntryCheck(
        symbol=symbol,
        side=side.lower(),
        quantity=quantity,
        checked_at=now,
        record=record,
        opportunity=opportunity,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        available_cash=available_cash,
        fee_basis=_fee_basis(fees),
    )


def _fee_basis(fees: FeeSchedule | None) -> str:
    """با کدام نرخ‌ها این عددها ساخته شدند — تا قابل بازسازی بماند."""
    if fees is None or not fees.rates_known:
        return "نرخ کارمزد اعلام نشده است؛ هزینه‌ها نامعلوم‌اند و صفر فرض نمی‌شوند."
    described = fees.describe()
    return described or "نرخ کارمزدِ اعلام‌شده: صفر (هزینه‌ی دانسته)."


def decision_snapshot(check: EntryCheck, ticket_id: str) -> dict[str, object]:
    """عکسِ تصمیم، برای نگه‌داشتن کنارِ خودِ معامله.

    چرا این‌ها: بدونشان بعداً نمی‌شود پرسید «چرا وارد شدم و آن موقع چه
    می‌دانستم؟». عمداً فقط چیزهایی که برای ارزیابیِ بعدی لازم‌اند —
    نه کلِ پاسخِ بررسی، که با هر تغییرِ رابط بی‌معنا می‌شود.
    """
    opportunity = check.opportunity
    snapshot: dict[str, object] = {
        "ticket_id": ticket_id,
        "checked_at": check.checked_at.isoformat(timespec="seconds"),
        "signal_id": check.record.signal_id,
        "strategy": check.record.strategy,
        "quantity": check.quantity,
        "entry_price_at_decision": check.entry_price,
        "screening_verdict": check.record.report.verdict.value,
        "screening_reason": check.record.report.reason,
        "warnings": list(check.warnings),
        "fee_basis": check.fee_basis,
        "fingerprint": check.fingerprint,
    }
    if opportunity is not None:
        snapshot.update({
            "score": round(opportunity.score, 1),
            "score_best_case": round(opportunity.score_best_case, 1),
            "coverage_pct": round(opportunity.coverage_pct, 1),
            "premium_cost": opportunity.premium_cost,
            "entry_fee": opportunity.entry_fee,
            "capital_required": opportunity.capital_required,
            "round_trip_fees_estimate": opportunity.round_trip_fees_estimate,
            "max_theoretical_loss": opportunity.max_theoretical_loss,
            "stop_loss_loss": opportunity.stop_loss_loss,
            "breakeven": opportunity.breakeven,
            "breakeven_includes_entry_fees": opportunity.breakeven_includes_entry_fees,
            "strengths": [c.label for c in opportunity.strengths],
            "weakness": (
                None if opportunity.weakness is None else opportunity.weakness.label
            ),
            "unknown": [c.label for c in opportunity.unknown_components],
        })
    return snapshot

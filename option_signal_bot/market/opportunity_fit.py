"""تناسبِ جهتِ فرصت با وضعیتِ سهم و بازار — بدون هیچ عددِ اطمینان.

**سؤالی که جواب می‌دهد**

«این فرصت در جهتی است که سهم و بازار **تا امروز** رفته‌اند، یا در جهتِ
مخالف؟» همین. نه احتمالِ برد می‌سازد، نه امتیاز، و نه پیش‌بینی تا
سررسید. خروجی یک حکمِ کیفی است به‌علاوه‌ی دلیل‌هایش.

**چرا وارد امتیاز نمی‌شود**

چون هیچ اعتبارسنجی‌ای پشتِ این تشخیص نیست. یک مؤلفه‌ی ارزیابی‌نشده که
بی‌صدا در رتبه بنشیند، رتبه را خراب می‌کند بدون اینکه کسی بفهمد. پس
کنارِ فرصت نشان داده می‌شود و کاربر خودش قضاوت می‌کند.

**زمان هم بخشی از تناسب است**

خریدِ اختیار با گذرِ زمان ارزش از دست می‌دهد. اگر عمرِ باقی‌مانده‌ی
قرارداد از افقِ تحلیل کوتاه‌تر باشد، وضعیتی که روی ۲۰ جلسه دیده شده
لزوماً فرصتِ تحقق ندارد. این را می‌گوییم، ولی از آن عددِ احتمال
نمی‌سازیم.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from market.regime import REGIME_LABELS, RegimeReport, RegimeState

#: هر جلسه‌ی معاملاتی تقریباً ۱٫۴ روزِ تقویمی است (۵ جلسه در هفته).
#: تقریبی است و همین‌جا صریح گفته می‌شود، نه اینکه جای عددِ دقیق را بگیرد.
CALENDAR_DAYS_PER_SESSION = 7 / 5


class FitState(str, Enum):
    """حکمِ تناسب. چهار حالت، هر کدام با معنای روشن."""

    #: جهتِ فرصت با وضعیتِ سهم می‌خواند و بازار هم مخالفش نیست.
    ALIGNED = "aligned"
    #: یکی موافق، دیگری مخالف یا بی‌جهت — یا خودِ سهم در رنج است.
    MIXED = "mixed"
    #: وضعیتِ سهم در جهتِ **مخالفِ** فرصت است.
    CONFLICT = "conflict"
    #: وضعیتِ سهم نامشخص است؛ چیزی برای مقایسه نداریم.
    UNKNOWN = "unknown"


FIT_LABELS: dict[FitState, str] = {
    FitState.ALIGNED: "سازگار با وضعیت",
    FitState.MIXED: "مختلط",
    FitState.CONFLICT: "در تعارض با وضعیت",
    FitState.UNKNOWN: "نامشخص",
}


@dataclass(frozen=True)
class FitAssessment:
    """نتیجه‌ی سنجشِ تناسب، با دلیل‌های قابل خواندن."""

    state: FitState
    #: جهتی که این فرصت برای سود لازم دارد: `up` یا `down`.
    needed_direction: str
    underlying_state: RegimeState
    market_state: RegimeState
    reasons: tuple[str, ...]
    time_note: str

    @property
    def label(self) -> str:
        return FIT_LABELS[self.state]

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "state_label": self.label,
            "needed_direction": self.needed_direction,
            "underlying_state": self.underlying_state.value,
            "underlying_state_label": REGIME_LABELS[self.underlying_state],
            "market_state": self.market_state.value,
            "market_state_label": REGIME_LABELS[self.market_state],
            "reasons": list(self.reasons),
            "time_note": self.time_note,
            "note": (
                "تناسب با وضعیتِ **فعلی** سنجیده شده، نه با پیش‌بینیِ تا "
                "سررسید — و در امتیازِ رتبه‌بندی وارد نمی‌شود."
            ),
        }


def assess_fit(
    *,
    option_type: str,
    side: str,
    underlying: RegimeReport | None,
    market: RegimeReport | None,
    days_to_expiry: int | None,
) -> FitAssessment:
    """آیا جهتِ این فرصت با وضعیتِ سهم و بازار می‌خواند؟

    دامنه همان دامنه‌ی رتبه‌بندی است: **خریدِ اختیارِ تک‌پایه**. برای
    فروش، جهتِ سودآور برعکس است و وجه تضمینش هم مدل نشده، پس اینجا
    حکمی صادر نمی‌شود.
    """
    needed = "up" if option_type.lower() == "call" else "down"
    underlying_state = underlying.state if underlying else RegimeState.UNKNOWN
    market_state = market.state if market else RegimeState.UNKNOWN
    reasons: list[str] = []

    if side.lower() != "buy":
        return FitAssessment(
            state=FitState.UNKNOWN,
            needed_direction=needed,
            underlying_state=underlying_state,
            market_state=market_state,
            reasons=(
                "این نسخه فقط برای خریدِ اختیارِ تک‌پایه تناسب می‌سنجد؛ "
                "جهتِ سودآورِ فروش برعکس است و جداگانه مدل نشده.",
            ),
            time_note="",
        )

    want = RegimeState.UP if needed == "up" else RegimeState.DOWN
    against = RegimeState.DOWN if needed == "up" else RegimeState.UP
    direction_word = "صعود" if needed == "up" else "نزول"

    if underlying_state is RegimeState.UNKNOWN:
        state = FitState.UNKNOWN
        subject = underlying.subject if underlying else "نماد پایه"
        why = (
            f" — {underlying.unknown_reason}"
            if underlying is not None and underlying.unknown_reason
            else ""
        )
        reasons.append(f"وضعیتِ {subject} نامشخص است{why}")
    elif underlying_state is against:
        state = FitState.CONFLICT
        reasons.append(
            f"این فرصت از {direction_word} سود می‌برد، ولی وضعیتِ "
            f"{underlying.subject} «{REGIME_LABELS[against]}» است."
        )
    elif underlying_state is RegimeState.RANGE:
        state = FitState.MIXED
        reasons.append(
            f"وضعیتِ {underlying.subject} «رنج» است: نه جهتِ لازم را تأیید "
            "می‌کند نه رد. برای خریدِ اختیار، رنج یعنی زمان علیه شماست."
        )
    else:  # موافق
        state = FitState.ALIGNED
        reasons.append(
            f"وضعیتِ {underlying.subject} «{REGIME_LABELS[want]}» است و همان "
            f"{direction_word}ی است که این فرصت از آن سود می‌برد."
        )

    # بازار زمینه است، نه حکم: تعارضش هشدار می‌دهد ولی جای وضعیتِ سهم
    # را نمی‌گیرد.
    if market is not None and market_state is not RegimeState.UNKNOWN:
        if market_state is against:
            reasons.append(
                f"بازار ({market.subject}) «{REGIME_LABELS[against]}» است — "
                "در جهتِ مخالفِ این فرصت."
            )
            if state is FitState.ALIGNED:
                state = FitState.MIXED
        elif market_state is want:
            reasons.append(
                f"بازار ({market.subject}) هم «{REGIME_LABELS[want]}» است."
            )
        else:
            reasons.append(f"بازار ({market.subject}) «رنج» است.")
    else:
        reasons.append("وضعیتِ بازار نامشخص است؛ فقط سهم مبنا قرار گرفت.")

    horizon = underlying.horizon_sessions if underlying else (
        market.horizon_sessions if market else 0
    )
    time_note = _time_note(days_to_expiry, horizon)

    return FitAssessment(
        state=state,
        needed_direction=needed,
        underlying_state=underlying_state,
        market_state=market_state,
        reasons=tuple(reasons),
        time_note=time_note,
    )


def _time_note(days_to_expiry: int | None, horizon_sessions: int) -> str:
    """عمرِ قرارداد در برابر افقِ تحلیل — با تقریبِ صریح."""
    if not horizon_sessions:
        return "افقِ تحلیل معلوم نیست."
    horizon_days = round(horizon_sessions * CALENDAR_DAYS_PER_SESSION)
    if days_to_expiry is None:
        return (
            f"افقِ تحلیل {horizon_sessions} جلسه (≈{horizon_days} روز) است؛ "
            "روزِ باقی‌مانده تا سررسید دانسته نیست."
        )
    if days_to_expiry < horizon_days:
        return (
            f"{days_to_expiry} روز تا سررسید مانده، کمتر از افقِ تحلیل "
            f"(≈{horizon_days} روز): وضعیتی که روی {horizon_sessions} جلسه "
            "دیده شده، لزوماً در این فرصتِ کوتاه تکرار نمی‌شود."
        )
    return (
        f"{days_to_expiry} روز تا سررسید مانده و افقِ تحلیل ≈{horizon_days} "
        "روز است. باز هم این تشخیصِ وضعیتِ فعلی است، نه پیش‌بینی تا سررسید."
    )

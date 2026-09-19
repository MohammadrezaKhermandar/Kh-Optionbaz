"""تست‌های تشخیص وضعیت و تناسبِ فرصت با آن.

فقط روی **خطرهای رفتاری** تمرکز دارد — آن چیزهایی که اگر خراب شوند،
یک تحلیلِ غلط با ظاهرِ درست ساخته می‌شود:

* دادهٔ **آینده** نباید در حکمِ امروز اثر بگذارد؛
* دادهٔ کم یا کهنه باید «نامشخص» بدهد، نه «رنج»؛
* رفت‌وبرگشت نباید «روند» خوانده شود؛
* قیمتِ **تعدیل‌نشده** نباید یک نزولِ ساختگی بسازد؛
* تناسب باید تعارضِ جهت را صریح بگوید، بدون ساختنِ عددِ احتمال.

سریِ قیمتِ این تست‌ها جایی که موضوع **قاعده** است ساخته می‌شود (نه
بازار)، و جایی که موضوع رفتار روی دادهٔ واقعی است، از همان پاسخِ
ضبط‌شده‌ی TSETMC می‌آید.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from data.market_data_client import Candle
from data.tsetmc_index_client import (
    TSE_ALL_SHARE_INS_CODE,
    TsetmcIndexClient,
)
from market.corporate_actions import CorporateAction, CorporateActionLog
from market.opportunity_fit import FitState, assess_fit
from market.regime import (
    Adjustment,
    PricePoint,
    RegimeState,
    RegimeThresholds,
    assess_regime,
)

INDEX_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "index"
TODAY = date(2026, 9, 19)
THRESHOLDS = RegimeThresholds()


def _series(values: list[float], end: date = TODAY - timedelta(days=1)) -> list[PricePoint]:
    """سریِ روزانه‌ی ساختگی که به `end` ختم می‌شود.

    ساختگی است چون موضوعِ این تست‌ها **قاعده** است، نه بازار: باید
    بتوانیم دقیقاً یک رفتار (روند، رفت‌وبرگشت، کهنگی) را بسازیم و
    ببینیم حکم چه می‌شود.
    """
    return [
        PricePoint(end - timedelta(days=len(values) - 1 - i), value)
        for i, value in enumerate(values)
    ]


def _regime(points, **kwargs):
    defaults = dict(
        subject="نمونه",
        subject_kind="underlying",
        as_of=TODAY,
        thresholds=THRESHOLDS,
        # پیش‌فرضِ این فایل: «پرسیدیم و رویدادی نبود». حالتِ «نپرسیدیم»
        # قیدِ جداگانه‌ای دارد که خودش تست دارد.
        adjustment=Adjustment.NO_CAPITAL_EVENTS,
    )
    defaults.update(kwargs)
    return assess_regime(points, **defaults)


# ----------------------------------------------------------------------
# ۱. دادهٔ آینده و دادهٔ ناکافی
# ----------------------------------------------------------------------
def test_tomorrow_never_changes_todays_verdict():
    """جلسه‌ای که هنوز نیامده نباید در حکمِ امروز دیده شود.

    بدون این قید، هر ارزیابیِ گذشته‌نگر با دانشِ آینده انجام می‌شد و
    نتیجه‌اش بی‌معنا بود.
    """
    rising = [100.0 + i for i in range(60)]
    base = _regime(_series(rising))

    # یک جهشِ بزرگ **بعد از** لحظه‌ی ارزیابی
    with_future = [
        *_series(rising),
        PricePoint(TODAY, 1_000.0),
        PricePoint(TODAY + timedelta(days=1), 2_000.0),
    ]
    after = _regime(with_future)

    assert after.state is base.state
    assert after.sessions_used == base.sessions_used
    assert after.last_session == base.last_session


def test_too_few_sessions_is_unknown_not_range():
    """کمبودِ داده «رنج» ترجمه نمی‌شود — رنج خودش یک حکم است."""
    report = _regime(_series([100.0 + i for i in range(10)]))

    assert report.state is RegimeState.UNKNOWN
    assert "جلسه" in (report.unknown_reason or "")


def test_stale_history_is_unknown_with_the_date_said_out_loud():
    """وضعیتِ امروز از دادهٔ سه‌هفته‌پیش ساخته نمی‌شود."""
    old_end = TODAY - timedelta(days=21)
    report = _regime(_series([100.0 + i for i in range(60)], end=old_end))

    assert report.state is RegimeState.UNKNOWN
    assert old_end.isoformat() in (report.unknown_reason or "")
    assert report.staleness_days == 21


# ----------------------------------------------------------------------
# ۲. حکم‌ها
# ----------------------------------------------------------------------
def test_a_steady_climb_reads_up_and_a_steady_fall_reads_down():
    up = _regime(_series([100.0 * (1.01**i) for i in range(60)]))
    down = _regime(_series([100.0 * (0.99**i) for i in range(60)]))

    assert up.state is RegimeState.UP
    assert down.state is RegimeState.DOWN
    assert any("جهت‌دار" in r for r in up.reasons)


def test_a_round_trip_that_ends_where_it_started_is_range_not_trend():
    """همان مسیر، ولی با رفت‌وبرگشت: کاراییِ حرکت پایین است."""
    zigzag = [100.0 + (5.0 if i % 2 else -5.0) for i in range(60)]

    report = _regime(_series(zigzag))

    assert report.state is RegimeState.RANGE
    efficiency = next(m for m in report.measures if m.key == "efficiency")
    assert efficiency.value < THRESHOLDS.min_efficiency


def test_a_move_smaller_than_the_usual_noise_is_not_called_a_trend():
    """حرکتِ کوچک‌تر از نوسانِ معمولِ همان نماد، روند نیست.

    آستانه‌ی ثابت (مثلاً «۳٪») برای یک نماد پرنوسان بی‌معناست و برای
    یک نمادِ آرام سخت‌گیر؛ مقایسه با نوسانِ خودِ نماد این را حل می‌کند.
    """
    noisy = [100.0 + (3.0 if i % 3 else -3.0) + i * 0.02 for i in range(60)]

    report = _regime(_series(noisy))

    assert report.state is RegimeState.RANGE
    normalized = next(m for m in report.measures if m.key == "normalized_move")
    assert abs(normalized.value) < THRESHOLDS.min_normalized_move


# ----------------------------------------------------------------------
# ۳. تعدیلِ قیمت
# ----------------------------------------------------------------------
def _capital_increase_series() -> tuple[list, date]:
    """رشدِ آرام، ولی از روزِ افزایش سرمایه قیمت **نصف** می‌شود.

    همان افتِ مصنوعیِ ناشی از تغییرِ مبنای قیمت: هیچ ریزشی در بازار
    نیفتاده، فقط تعداد سهم دو برابر شده.
    """
    split_day = TODAY - timedelta(days=20)
    raw: list[Candle] = []
    for i in range(60):
        day = TODAY - timedelta(days=60 - i)
        price = 1_000.0 * (1.005**i)
        if day >= split_day:
            price /= 2
        raw.append(Candle(date=day, open=price, high=price, low=price,
                          close=price, volume=0.0))
    return raw, split_day


def test_an_unknown_adjustment_never_produces_a_directional_verdict():
    """افتِ مصنوعیِ مبنای قیمت نباید «نزولی» خوانده شود.

    این خطرناک‌ترین حالت است: سری **دقیقاً** همان شکلی را دارد که
    ماژول دنبالش می‌گردد — تغییرِ بزرگ با کاراییِ بالا. اگر وضعیتِ
    تعدیل نامعلوم باشد، حکم باید «نامشخص» بماند و علتش دیده شود.
    """
    raw, _split_day = _capital_increase_series()

    report = _regime(
        [PricePoint(c.date, c.close) for c in raw], adjustment=Adjustment.UNKNOWN
    )

    assert report.state is RegimeState.UNKNOWN
    assert "تعدیل" in (report.unknown_reason or "")
    assert "افزایش سرمایه" in (report.unknown_reason or "")
    # سنجه‌ها می‌مانند تا کاربر خودش ببیند چه اتفاقی افتاده.
    change = next(m for m in report.measures if m.key == "change")
    assert change.value < -40


def test_an_unknown_adjustment_never_produces_a_positive_fit():
    """و تناسبِ مثبت هم از آن در نمی‌آید."""
    raw, _ = _capital_increase_series()
    unknown_state = _regime(
        [PricePoint(c.date, c.close) for c in raw], adjustment=Adjustment.UNKNOWN
    )

    fit = assess_fit(
        option_type="put",
        side="buy",
        underlying=unknown_state,
        market=_report(RegimeState.DOWN, subject="شاخص", kind="market"),
        days_to_expiry=45,
    )

    assert fit.state is FitState.UNKNOWN
    assert any("نامشخص" in r for r in fit.reasons)


def test_adjusting_the_same_series_recovers_the_real_direction():
    """با تعدیلِ افزایش سرمایه، همان سری «صعودی» خوانده می‌شود."""
    raw, split_day = _capital_increase_series()
    log = CorporateActionLog([
        CorporateAction(
            underlying="نمونه",
            effective_date=split_day,
            kind="capital_increase",
            ratio=0.5,
        )
    ])

    adjusted = _regime(
        [PricePoint(c.date, c.close) for c in log.adjust_history(raw)],
        adjustment=Adjustment.APPLIED,
    )

    assert adjusted.state is RegimeState.UP
    assert any("افزایش سرمایه" in r for r in adjusted.reasons)


def test_the_dividend_gap_is_admitted_in_every_state():
    """«تعدیل شد» یعنی افزایش سرمایه اعمال شد، نه اینکه سری بی‌عیب است.

    سود نقدی منبعِ عمومیِ قابل اتکا ندارد و تعدیل نمی‌شود؛ اگر این را
    نگوییم، کاربر «تعدیل‌شده» را «کاملاً هم‌مبنا» می‌فهمد.
    """
    from market.regime import ADJUSTMENT_LABELS

    for state in (Adjustment.APPLIED, Adjustment.NO_CAPITAL_EVENTS):
        assert "سود نقدی" in ADJUSTMENT_LABELS[state]
    assert "شاخص" in ADJUSTMENT_LABELS[Adjustment.NOT_APPLICABLE]


# ----------------------------------------------------------------------
# ۴. دادهٔ واقعیِ ضبط‌شده
# ----------------------------------------------------------------------
def test_the_recorded_index_gets_a_verdict_with_reasons_and_no_network():
    """روی پاسخِ **واقعیِ** ضبط‌شده‌ی شاخص، بدون شبکه."""
    fixture = INDEX_FIXTURE_DIR / f"{TSE_ALL_SHARE_INS_CODE}.json"
    if not fixture.exists():
        pytest.skip("نمونه‌ی شاخص ضبط نشده؛ scripts/record_fixtures.py")

    rows = json.loads(fixture.read_text(encoding="utf-8"))["indexB2"]
    client = TsetmcIndexClient(history_dir=INDEX_FIXTURE_DIR)
    points = [PricePoint(p.date, p.close) for p in client.get_history(days=200)]
    last_day = points[-1].date

    report = assess_regime(
        points,
        subject="شاخص کل بورس تهران",
        subject_kind="market",
        as_of=last_day + timedelta(days=1),
        adjustment=Adjustment.NOT_APPLICABLE,
    )

    assert len(rows) > 100, "نمونه باید تاریخچه‌ی معناداری داشته باشد"
    assert report.state is not RegimeState.UNKNOWN
    assert report.reasons and report.measures
    assert report.adjustment is Adjustment.NOT_APPLICABLE
    payload = report.to_dict()
    assert "پیش‌بینی" in payload["note"], "باید صریح بگوید پیش‌بینی نیست"


# ----------------------------------------------------------------------
# ۵. تناسبِ فرصت
# ----------------------------------------------------------------------
def _report(state: RegimeState, subject: str = "خودرو", kind: str = "underlying"):
    """گزارشِ وضعیت با حکمِ دلخواه، برای سنجشِ خودِ منطقِ تناسب."""
    series = {
        RegimeState.UP: [100.0 * (1.01**i) for i in range(60)],
        RegimeState.DOWN: [100.0 * (0.99**i) for i in range(60)],
        RegimeState.RANGE: [100.0 + (5.0 if i % 2 else -5.0) for i in range(60)],
        RegimeState.UNKNOWN: [100.0, 101.0],
    }[state]
    return _regime(_series(series), subject=subject, subject_kind=kind)


def test_a_call_in_a_falling_stock_is_reported_as_a_conflict():
    fit = assess_fit(
        option_type="call",
        side="buy",
        underlying=_report(RegimeState.DOWN),
        market=_report(RegimeState.UP, subject="شاخص", kind="market"),
        days_to_expiry=45,
    )

    assert fit.state is FitState.CONFLICT
    assert any("مخالف" in r or "نزولی" in r for r in fit.reasons)
    assert "احتمال" not in fit.to_dict()["note"] or "نه" in fit.to_dict()["note"]


def test_a_call_in_a_rising_stock_and_market_is_aligned():
    fit = assess_fit(
        option_type="call",
        side="buy",
        underlying=_report(RegimeState.UP),
        market=_report(RegimeState.UP, subject="شاخص", kind="market"),
        days_to_expiry=45,
    )

    assert fit.state is FitState.ALIGNED


def test_a_rising_stock_in_a_falling_market_is_not_called_aligned():
    """بازارِ مخالف، حکم را از «سازگار» پایین می‌آورد — ولی رد نمی‌کند."""
    fit = assess_fit(
        option_type="call",
        side="buy",
        underlying=_report(RegimeState.UP),
        market=_report(RegimeState.DOWN, subject="شاخص", kind="market"),
        days_to_expiry=45,
    )

    assert fit.state is FitState.MIXED
    assert any("بازار" in r for r in fit.reasons)


def test_a_range_bound_stock_warns_that_time_works_against_the_buyer():
    fit = assess_fit(
        option_type="call",
        side="buy",
        underlying=_report(RegimeState.RANGE),
        market=_report(RegimeState.RANGE, subject="شاخص", kind="market"),
        days_to_expiry=45,
    )

    assert fit.state is FitState.MIXED
    assert any("زمان علیه" in r for r in fit.reasons)


def test_an_unknown_underlying_state_is_not_dressed_up_as_agreement():
    fit = assess_fit(
        option_type="put",
        side="buy",
        underlying=_report(RegimeState.UNKNOWN),
        market=_report(RegimeState.DOWN, subject="شاخص", kind="market"),
        days_to_expiry=45,
    )

    assert fit.state is FitState.UNKNOWN
    assert fit.needed_direction == "down"


def test_an_expiry_shorter_than_the_horizon_is_said_out_loud():
    """عمرِ کوتاهِ قرارداد باید در توضیحِ تناسب دیده شود."""
    fit = assess_fit(
        option_type="call",
        side="buy",
        underlying=_report(RegimeState.UP),
        market=None,
        days_to_expiry=5,
    )

    assert "کمتر از افقِ تحلیل" in fit.time_note
    assert "۲۰" in fit.time_note or "20" in fit.time_note


def test_a_sell_position_gets_no_fit_verdict_in_this_version():
    fit = assess_fit(
        option_type="call",
        side="sell",
        underlying=_report(RegimeState.UP),
        market=None,
        days_to_expiry=45,
    )

    assert fit.state is FitState.UNKNOWN
    assert any("خرید" in r for r in fit.reasons)

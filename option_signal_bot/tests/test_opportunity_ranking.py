"""تست‌های رتبه‌بندی اولویت بررسی.

فقط روی **خطرهای رفتاری** همین قابلیت تمرکز دارد — آن چیزهایی که اگر
خراب شوند، امتیاز بی‌صدا گمراه‌کننده می‌شود:

* گزینه‌ی ردشده‌ی غربال، هر چقدر هم جذاب، نباید رتبه بگیرد؛
* ورودی که برای اندازه‌ی سفارش اجراپذیر نیست نباید رتبه بگیرد — و
  «عمق کم بود» باید از «داده نداشتیم» جدا بماند؛
* کارمزد باید از قیمتِ اجراییِ **همان سمت** بیاید، نه از مبلغی که
  ماژول ریسک روی پرمیومِ پیشنهادی ساخته؛
* وجهِ لازم برای ورود با هزینه‌ی فرضیِ رفت‌وبرگشت قاطی نشود؛
* هزینه‌ی رفت‌وبرگشت باید از قیمتِ **اجراپذیر** بیاید، نه نصفِ اسپرد؛
* اجرای بدتر نباید امتیاز بهتر بگیرد؛
* اندازه‌ی سفارش باید واقعاً بر رتبه اثر بگذارد؛
* عمقِ قیمت‌های دور نباید حاشیه‌ی امنِ خروج حساب شود؛
* داده‌ی ناقص نباید برتریِ مصنوعی بسازد؛
* «زیان تا حد ضرر» با «حداکثر زیان نظری» قاطی نشود؛
* امتیاز باید از همان ورودی‌ها قابلِ بازتولید و توضیح باشد.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from data.order_book import BookLevel, OrderBook
from market.opportunity_ranking import (
    RankingWeights,
    breakeven_price,
    rank_opportunities,
)
from market.tradability import (
    HistoryStats,
    LiquidityObservation,
    Thresholds,
    evaluate,
)
from risk.fees import FeeSchedule

NOW = datetime(2026, 5, 20, 10, 30)
THRESHOLDS = Thresholds()
WEIGHTS = RankingWeights()
CONTRACT_SIZE = 1_000

#: تاریخچه‌ای که همه‌ی سنجه‌هایش دانسته و سالم است، تا حکمِ غربال به
#: خودِ مظنه و عمق بستگی داشته باشد نه به نبودِ تاریخچه.
HEALTHY = HistoryStats(
    sessions=20,
    sessions_with_trades=18,
    sessions_with_both_quotes=20,
    known=True,
    sessions_verified=True,
)

#: نرخِ **اعلام‌شده**: ۰٫۲۵٪ هر سمت. مبلغش را رتبه‌بندی باید از قیمتِ
#: اجراییِ همان سمت بسازد، نه از پرمیومِ پیشنهادی.
FEES = FeeSchedule(buy_rate=0.0025, sell_rate=0.0025, declared=True)


def _book(bids, asks, symbol="ضخود۱۰۰۱") -> OrderBook:
    return OrderBook(
        symbol,
        bids=tuple(BookLevel(p, q) for p, q in bids),
        asks=tuple(BookLevel(p, q) for p, q in asks),
    )


def _observation(
    symbol: str = "ضخود۱۰۰۱",
    quantity: int = 10,
    bids=((980.0, 100),),
    asks=((1_020.0, 100),),
    **overrides,
) -> LiquidityObservation:
    """مشاهده را از یک دفترِ واقعی می‌سازد تا عددها با هم بخوانند."""
    book = _book(bids, asks, symbol)
    exit_price, exit_filled = book.fill_price("sell", quantity)
    entry_price, entry_filled = book.fill_price("buy", quantity)
    defaults = dict(
        symbol=symbol,
        position_side="buy",
        quantity=quantity,
        observed_at=NOW,
        bid=book.best_bid,
        ask=book.best_ask,
        exit_depth_contracts=book.real_depth("sell"),
        exit_depth_within_band_contracts=book.depth_within(
            "sell", THRESHOLDS.max_exit_slippage_pct
        ),
        exit_fill_price=exit_price if exit_filled >= quantity else None,
        entry_fill_price=entry_price if entry_filled >= quantity else None,
        entry_depth_contracts=book.real_depth("buy"),
        best_exit_price=book.best_bid,
        open_interest=500,
        trades_today=40,
        days_to_expiry=45,
        quote_age_seconds=5.0,
    )
    defaults.update(overrides)
    return LiquidityObservation(**defaults)


def _candidate(observation=None, history=HEALTHY, **overrides) -> dict:
    observation = observation or _observation()
    candidate = {
        "report": evaluate(observation, history, THRESHOLDS),
        "symbol": observation.symbol,
        "strategy": "directional_ma_cross",
        "side": observation.position_side,
        "quantity": observation.quantity,
        "leg_group_id": None,
        "option_type": "call",
        "strike": 10_000.0,
        "contract_size": CONTRACT_SIZE,
        "underlying_price": 10_500.0,
        # حد ضررِ ۳۵٪ روی قیمتِ اجراییِ ورودِ همین دفتر (۱۰۲۰ × ۰٫۶۵)
        "stop_loss_price": 663.0,
        "fees": FEES,
    }
    candidate.update(overrides)
    return candidate


def _rank(candidates, weights=WEIGHTS, thresholds=THRESHOLDS):
    return rank_opportunities(
        candidates=candidates,
        thresholds=thresholds,
        weights=weights,
        evaluated_at=NOW,
    )


def _component(ranked, key):
    return next(c for c in ranked.components if c.key == key)


# ----------------------------------------------------------------------
# ۱. غربال دروازه است، امتیاز جایش را نمی‌گیرد
# ----------------------------------------------------------------------
def test_a_rejected_option_never_enters_the_ranking_however_attractive():
    """گزینه‌ی ردشده حتی با بهترین عددهای دیگر هم رتبه نمی‌گیرد."""
    dead = _candidate(_observation(
        symbol="ضمرده", bids=((500.0, 100),), asks=((1_500.0, 100),)
    ))
    assert dead["report"].verdict.value == "rejected"

    result = _rank([dead])

    assert result.ranked == ()
    assert [e.symbol for e in result.excluded] == ["ضمرده"]
    assert result.excluded[0].verdict == "rejected"
    assert "رد شده" in result.excluded[0].reason


def test_a_needs_review_option_keeps_its_own_status_and_reason():
    """نبودِ داده «قبول» ترجمه نمی‌شود — و علتش با «رد شده» فرق دارد."""
    unknown_history = _candidate(history=HistoryStats(known=False))
    assert unknown_history["report"].verdict.value == "needs_review"

    result = _rank([unknown_history])

    assert result.ranked == ()
    assert result.excluded[0].verdict == "needs_review"
    assert "نیازمند بررسی" in result.excluded[0].reason


# ----------------------------------------------------------------------
# ۲. دامنه‌ی نسخه‌ی اول: فقط خریدِ تک‌پایه
# ----------------------------------------------------------------------
def test_a_multi_leg_leg_is_excluded_with_a_stated_scope_reason():
    result = _rank([_candidate(leg_group_id="grp-1")])

    assert result.ranked == ()
    assert "چندپایه" in result.excluded[0].reason


def test_a_sell_position_is_not_scored_with_the_buy_formula():
    """فروش نباید با فرمولِ خرید امتیاز بگیرد.

    سرمایه‌ی درگیر، سر‌به‌سر و حداکثر زیانِ این نسخه همه از منطقِ خرید
    می‌آیند؛ اعمالشان روی فروش یعنی ریسک را غلط نشان بدهیم.
    """
    result = _rank([_candidate(side="sell")])

    assert result.ranked == ()
    assert result.excluded[0].verdict == "tradable", "غربال را پاس کرده بود"
    assert "فروش" in result.excluded[0].reason


def test_the_payload_states_the_scope_and_the_disabled_evidence():
    payload = _rank([_candidate()]).to_dict()

    assert "تک‌پایه" in payload["scope"]
    assert "نرخ برد" in payload["evidence_note"]
    assert not any(
        c["key"] == "strategy_evidence" for c in payload["ranked"][0]["components"]
    ), "مؤلفه‌ی عملکرد نباید اصلاً ساخته شود"


# ----------------------------------------------------------------------
# ۳. اجراپذیریِ ورود و هزینه‌ی رفت‌وبرگشت از قیمتِ اجراپذیر
# ----------------------------------------------------------------------
def test_round_trip_cost_uses_both_executable_sides_not_half_the_spread():
    """هزینه باید کلِ اسپرد را ببیند، نه نصفش.

    دفتر: خرید روی ۱۰۲۰، فروش روی ۹۸۰. رفت‌وبرگشت یعنی ۴۰ ریال از
    ۱۰۲۰ = ۳٫۹۲٪. نسخه‌ی قبلی «نصف اسپرد» می‌گرفت که تقریباً نصفِ این
    عدد بود و هزینه را کم‌تر از واقع نشان می‌داد.
    """
    ranked = _rank([_candidate()]).ranked[0]

    cost = _component(ranked, "round_trip_cost")
    assert cost.measured == pytest.approx((1_020.0 - 980.0) / 1_020.0 * 100, abs=0.01)
    assert "پرمیومِ پرداختی" in cost.unit
    # و نصفِ اسپرد (۲٪) عددِ دیگری است؛ نباید با آن اشتباه شود.
    assert cost.measured > 3.0


def test_an_entry_that_cannot_be_filled_for_the_order_size_leaves_the_ranking():
    """ورودی که کامل پر نمی‌شود نباید در صفِ اصلی بماند.

    دفتر: ۱۰۰ قرارداد در سمت فروش برای خروج (پس غربال ردش نمی‌کند)،
    ولی فقط ۵ قرارداد در سمت خرید برای سفارشِ ۵۰ تایی. بدون قیمتِ
    اجراییِ ورود، سرمایه و سر‌به‌سر و زیان هیچ‌کدام مبنا ندارند — پس
    کم‌کردنِ امتیاز کافی نیست و گزینه کنار می‌رود.
    """
    thin_entry = _candidate(_observation(
        quantity=50, bids=((980.0, 100),), asks=((1_020.0, 5),)
    ))
    assert thin_entry["report"].verdict.value == "tradable", "غربال عبورش داده"

    result = _rank([thin_entry])

    assert result.ranked == (), "ورودِ اجراناپذیر نباید رتبه بگیرد"
    excluded = result.excluded[0]
    assert excluded.code == "entry_short_of_depth"
    assert excluded.verdict == "tradable"
    assert "۵۰ قرارداد" in excluded.reason or "50 قرارداد" in excluded.reason
    assert "کمبودِ قطعیِ عمق" in excluded.reason


def test_a_missing_entry_book_is_not_reported_as_a_depth_shortfall():
    """«دفتر را ندیدیم» با «عمقش کم بود» یکی نیست.

    اولی با سفارشِ کوچک‌تر حل می‌شود، دومی با داده. یک جمله برای هر
    دو یعنی کاربر نداند کدام کار را بکند.
    """
    no_entry_data = _candidate(_observation(
        entry_fill_price=None, entry_depth_contracts=None
    ))
    assert no_entry_data["report"].verdict.value == "tradable"

    result = _rank([no_entry_data])

    assert result.ranked == ()
    excluded = result.excluded[0]
    assert excluded.code == "entry_unknown"
    assert "نامعلوم" in excluded.reason
    assert "نبودِ داده" in excluded.reason
    assert "کمبودِ قطعیِ عمق" not in excluded.reason.replace("نه کمبودِ قطعیِ عمق", "")


def test_the_two_entry_failures_do_not_share_one_code_or_one_sentence():
    """تفکیک باید در خروجیِ ماشین‌خوان هم باشد، نه فقط در متن."""
    short = _candidate(_observation(
        symbol="ضکم", quantity=50, bids=((980.0, 100),), asks=((1_020.0, 5),)
    ))
    unknown = _candidate(_observation(
        symbol="ضنادانسته", entry_fill_price=None, entry_depth_contracts=None
    ))

    payload = _rank([short, unknown]).to_dict()

    codes = {row["symbol"]: row["code"] for row in payload["excluded"]}
    assert codes == {
        "ضکم": "entry_short_of_depth",
        "ضنادانسته": "entry_unknown",
    }


def test_worse_execution_never_scores_better_in_otherwise_equal_conditions():
    tight = _candidate(_observation(
        symbol="ضتنگ", bids=((995.0, 100),), asks=((1_005.0, 100),)
    ))
    wide = _candidate(_observation(
        symbol="ضپهن", bids=((950.0, 100),), asks=((1_050.0, 100),)
    ))

    result = _rank([tight, wide])

    assert [r.symbol for r in result.ranked] == ["ضتنگ", "ضپهن"]
    assert result.ranked[0].score > result.ranked[1].score


# ----------------------------------------------------------------------
# ۴. ظرفیت خروج: فقط عمقِ درونِ محدوده‌ی قیمتی
# ----------------------------------------------------------------------
def test_far_away_volume_is_not_counted_as_exit_head_room():
    """حجمی که در قیمت‌های دور نشسته، حاشیه‌ی امنِ خروج نیست.

    هر دو دفتر ۱۰ قرارداد روی بهترین مظنه دارند؛ دومی هزار قرارداد
    دیگر هم دارد ولی ۳۰٪ پایین‌تر. اگر آن حجم شمرده شود، این گزینه
    بی‌دلیل «پرظرفیت» به نظر می‌رسد.
    """
    near = _candidate(_observation(
        symbol="ضنزدیک", bids=((1_000.0, 10), (960.0, 20)), asks=((1_010.0, 100),)
    ))
    far = _candidate(_observation(
        symbol="ضدور", bids=((1_000.0, 10), (700.0, 1_000)), asks=((1_010.0, 100),)
    ))

    result = _rank([near, far])

    by_symbol = {r.symbol: r for r in result.ranked}
    near_cap = _component(by_symbol["ضنزدیک"], "exit_capacity")
    far_cap = _component(by_symbol["ضدور"], "exit_capacity")
    assert near_cap.measured > far_cap.measured
    # عمقِ کل برای «ضدور» خیلی بیشتر است، ولی امتیاز ظرفیتش نباید بالاتر برود.
    assert far_cap.score <= near_cap.score
    assert "دورتر" in far_cap.detail


def test_a_zero_depth_floor_does_not_silently_disable_the_capacity_measure():
    """کفِ صفرِ غربال نباید سنجه‌ی ظرفیت را بی‌اثر کند."""
    thresholds = Thresholds(min_exit_depth_ratio=0.0)
    thin = _candidate(_observation(symbol="ضنازک", bids=((980.0, 10),)))
    deep = _candidate(_observation(symbol="ضعمیق", bids=((980.0, 300),)))

    result = _rank([thin, deep], thresholds=thresholds)

    scores = {r.symbol: r.score for r in result.ranked}
    assert scores["ضعمیق"] > scores["ضنازک"]


def test_a_bigger_order_can_lose_its_place_to_depth_and_slippage():
    """همان دفتر، سفارشِ بزرگ‌تر: ظرفیتِ نسبی کم و هزینه زیاد می‌شود."""
    book = (((1_000.0, 10), (900.0, 200)), ((1_010.0, 500),))
    small = _candidate(_observation(
        symbol="ضکوچک", quantity=10, bids=book[0], asks=book[1]
    ))
    big = _candidate(_observation(
        symbol="ضبزرگ", quantity=100, bids=book[0], asks=book[1]
    ))

    result = _rank([small, big])

    assert [r.symbol for r in result.ranked] == ["ضکوچک", "ضبزرگ"]
    assert result.ranked[0].score > result.ranked[1].score


# ----------------------------------------------------------------------
# ۵. پول: یک مبنا، سناریوهای جدا، و نامعلومی که صفر نمی‌شود
# ----------------------------------------------------------------------
def test_each_side_pays_its_own_fee_from_its_own_executable_price():
    """کارمزد از قیمتِ اجراییِ **همان سمت** می‌آید، نه از یک مبلغِ آماده.

    ورودِ اجرایی ۱۰۲۰ و خروجِ اجرایی ۹۸۰ برای ۱۰ قرارداد × ۱۰۰۰ واحد،
    با نرخِ اعلام‌شده‌ی ۰٫۲۵٪ هر سمت:

    * کارمزد ورود = ۱۰٬۲۰۰٬۰۰۰ × ۰٫۲۵٪ = ۲۵٬۵۰۰
    * کارمزد خروجِ فرضی = ۹٬۸۰۰٬۰۰۰ × ۰٫۲۵٪ = ۲۴٬۵۰۰

    مبلغِ ماژول ریسک هر دو سمت را روی **ارزشِ ورود** حساب می‌کرد
    (۵۱٬۰۰۰)؛ آن عدد اینجا مبنا نیست.
    """
    ranked = _rank([_candidate()]).ranked[0]

    assert ranked.premium_cost == pytest.approx(1_020 * 10 * 1_000)
    assert ranked.entry_fee == pytest.approx(25_500)
    assert ranked.exit_fee_estimate == pytest.approx(24_500)
    assert ranked.round_trip_fees_estimate == pytest.approx(50_000)
    # هر دو سمت روی ارزشِ ورود ⇒ ۵۱٬۰۰۰؛ مبنای قدیمی نباید برگردد.
    assert ranked.round_trip_fees_estimate != pytest.approx(51_000)


def test_cash_to_enter_is_not_inflated_with_the_hypothetical_exit_fee():
    """وجهِ ورود پولی است که امروز لازم است؛ کارمزد خروج فرضی است."""
    ranked = _rank([_candidate()]).ranked[0]

    assert ranked.capital_required == pytest.approx(10_200_000 + 25_500)
    assert ranked.capital_required == pytest.approx(
        ranked.premium_cost + ranked.entry_fee
    )
    # و هزینه‌ی فرضیِ رفت‌وبرگشت جدا گزارش می‌شود، نه داخلِ وجهِ ورود.
    assert ranked.round_trip_fees_estimate > ranked.entry_fee
    assert ranked.capital_required < ranked.premium_cost + (
        ranked.round_trip_fees_estimate
    )


def test_stop_loss_loss_is_not_presented_as_the_theoretical_maximum():
    """این دو عدد یکی نیستند، و هر کدام فرضِ خودش را اعلام می‌کند.

    حداکثر زیانِ نظری = کلِ پرمیوم + کارمزد ورود (اختیار بی‌ارزش
    منقضی شود؛ فروشی در کار نیست پس کارمزد خروج ندارد).

    زیان تا حد ضرر = افت از ورودِ اجرایی ۱۰۲۰ تا ۶۶۳ برای ۱۰٬۰۰۰ واحد
    (۳٬۵۷۰٬۰۰۰) + کارمزد ورود ۲۵٬۵۰۰ + کارمزد خروج در همان قیمت
    ۱۶٬۵۷۵.
    """
    ranked = _rank([_candidate()]).ranked[0]

    assert ranked.max_theoretical_loss == pytest.approx(10_225_500)
    assert ranked.stop_loss_loss == pytest.approx(3_570_000 + 25_500 + 16_575)
    assert ranked.max_theoretical_loss > ranked.stop_loss_loss
    # و هر دو باید تعریف و فرضشان را بگویند، نه فقط یک عدد.
    assert "بی‌ارزش منقضی" in ranked.max_theoretical_loss_basis
    assert "اعمال/تسویه" in ranked.max_theoretical_loss_basis
    assert "فرض" in ranked.stop_loss_loss_basis


def test_the_expiry_breakeven_does_not_hide_an_ordinary_exit_fee_inside_it():
    """سر‌به‌سرِ سررسید فقط کارمزدِ ورود را دارد و «خالص» نیست.

    در سررسید فروشی در بازار انجام نمی‌شود؛ اعمال/تسویه است که نرخش
    در این پروژه معلوم نیست. پس جمع‌کردنِ کارمزدِ خروجِ عادی با آن،
    عددی می‌سازد که نه سر‌به‌سرِ سررسید است نه خالص.
    """
    ranked = _rank([_candidate()]).ranked[0]

    entry_fee_per_unit = 25_500 / (10 * 1_000)
    assert ranked.breakeven == pytest.approx(10_000 + 1_020 + entry_fee_per_unit)
    # مبنای قدیمی، کارمزدِ رفت‌وبرگشت را هم داخلش می‌برد.
    assert ranked.breakeven != pytest.approx(10_000 + 1_020 + 50_000 / 10_000)
    assert ranked.breakeven_includes_entry_fees is True
    assert "خالص" in ranked.breakeven_basis
    assert "اعمال/تسویه" in ranked.breakeven_basis


def test_an_unknown_fee_rate_is_never_replaced_with_zero():
    """نرخِ اعلام‌نشده یعنی نامعلوم — نه رایگان.

    آنچه دانسته است (پرمیومِ پرداختی) گزارش می‌شود؛ آنچه نیست عددِ
    ساختگی نمی‌گیرد.
    """
    ranked = _rank([_candidate(fees=None)]).ranked[0]

    assert ranked.premium_cost == pytest.approx(10_200_000), "این دانسته است"
    assert ranked.entry_fee is None
    assert ranked.capital_required is None
    assert ranked.max_theoretical_loss is None
    assert ranked.round_trip_fees_estimate is None
    assert ranked.stop_loss_loss is None, "بدون کارمزد، زیانِ کل دانسته نیست"
    assert "صفر فرض نمی‌شود" in ranked.max_theoretical_loss_basis
    # سر‌به‌سر همچنان گفته می‌شود، ولی با برچسبِ روشن.
    assert ranked.breakeven == pytest.approx(10_000 + 1_020)
    assert ranked.breakeven_includes_entry_fees is False
    assert "خالص" in ranked.breakeven_basis


def test_declared_zero_fees_differ_from_an_unknown_rate():
    """صفرِ اعلام‌شده هزینه‌ی دانسته است؛ نرخِ تنظیم‌نشده نامعلوم."""
    zero = FeeSchedule(declared=True)
    declared_zero = _rank([_candidate(fees=zero)]).ranked[0]
    unknown = _rank([_candidate(fees=None)]).ranked[0]

    assert declared_zero.entry_fee == pytest.approx(0.0)
    assert declared_zero.capital_required == pytest.approx(10_200_000)
    assert declared_zero.max_theoretical_loss == pytest.approx(10_200_000)
    assert _component(declared_zero, "fee_cost").score == pytest.approx(100.0)
    assert _component(unknown, "fee_cost").score is None
    assert declared_zero.coverage_pct > unknown.coverage_pct


def test_the_fee_component_measures_the_round_trip_against_the_cash_to_enter():
    """کسر و مخرجِ این مؤلفه هم باید یک مبنا داشته باشند."""
    fee = _component(_rank([_candidate()]).ranked[0], "fee_cost")

    assert fee.measured == pytest.approx(50_000 / 10_225_500 * 100, abs=0.001)
    assert "وجهِ ورود" in fee.unit
    assert "کارمزد خروجِ فرضی" in fee.detail


# ----------------------------------------------------------------------
# ۶. داده‌ی ناقص برتریِ مصنوعی نمی‌سازد
# ----------------------------------------------------------------------
def test_missing_data_never_outranks_a_fully_known_option():
    known = _candidate(_observation(symbol="ضدانسته"))
    unknown = _candidate(
        _observation(symbol="ضنامعلوم"), fees=None
    )

    result = _rank([known, unknown])

    by_symbol = {r.symbol: r for r in result.ranked}
    assert by_symbol["ضدانسته"].score > by_symbol["ضنامعلوم"].score
    assert [r.symbol for r in result.ranked] == ["ضدانسته", "ضنامعلوم"]


def test_an_unknown_component_is_reported_not_hidden():
    ranked = _rank([_candidate(fees=None)]).ranked[0]

    assert ranked.coverage_pct < 100.0
    assert "سهم کارمزد از سرمایه" in [c.label for c in ranked.unknown_components]
    assert ranked.weakness is None or ranked.weakness.is_known


def test_the_conservative_score_never_exceeds_the_best_case():
    partial = _rank([_candidate(fees=None)]).ranked[0]

    assert partial.score < partial.score_best_case
    assert partial.score_best_case - partial.score == pytest.approx(
        WEIGHTS.weight_fee_cost
    )


def test_required_move_is_unknown_without_an_underlying_price():
    ranked = _rank([_candidate(underlying_price=None)]).ranked[0]

    assert _component(ranked, "required_move").score is None
    assert ranked.coverage_pct < 100.0


# ----------------------------------------------------------------------
# ۷. امتیاز قابل بازتولید و توضیح است
# ----------------------------------------------------------------------
def test_the_score_is_the_weighted_sum_of_its_own_components():
    ranked = _rank([_candidate()]).ranked[0]

    rebuilt = sum(c.contribution for c in ranked.components) / ranked.total_weight * 100
    assert ranked.score == pytest.approx(rebuilt)
    assert sum(c.weight for c in ranked.components) == pytest.approx(100.0)


def test_the_same_inputs_give_the_same_score_every_time():
    first = _rank([_candidate()]).ranked[0]
    second = _rank([_candidate()]).ranked[0]

    assert first.score == second.score
    assert [c.score for c in first.components] == [c.score for c in second.components]


def test_changing_a_weight_changes_the_score_in_the_stated_direction():
    candidate = _candidate(_observation(bids=((980.0, 1_000),)))
    light = _rank([candidate], weights=RankingWeights(weight_exit_capacity=5.0))
    heavy = _rank([candidate], weights=RankingWeights(weight_exit_capacity=60.0))

    assert heavy.ranked[0].score > light.ranked[0].score


def test_every_component_explains_itself_with_a_number():
    ranked = _rank([_candidate()]).ranked[0]

    for component in ranked.components:
        assert component.detail.strip(), component.key
        assert component.label.strip(), component.key
        assert component.weight > 0, component.key


def test_the_output_says_what_the_score_is_not():
    payload = _rank([_candidate()]).to_dict()

    assert "احتمال برد" in payload["note"]
    assert "بازده مورد انتظار" in payload["note"]


# ----------------------------------------------------------------------
# سر‌به‌سر
# ----------------------------------------------------------------------
def test_breakeven_moves_the_right_way_for_calls_and_puts():
    assert breakeven_price(10_000, 500, "call") == 10_500
    assert breakeven_price(10_000, 500, "put") == 9_500
    # کارمزد سر‌به‌سر را دورتر می‌برد، در هر دو جهت.
    assert breakeven_price(10_000, 500, "call", fee_per_unit=10) == 10_510
    assert breakeven_price(10_000, 500, "put", fee_per_unit=10) == 9_490


def test_an_option_needing_a_huge_move_ranks_below_one_that_is_already_there():
    near = _candidate(_observation(symbol="ضنزدیک"), strike=9_000.0)
    far = _candidate(_observation(symbol="ضدور"), strike=14_000.0)

    result = _rank([near, far])

    assert [r.symbol for r in result.ranked] == ["ضنزدیک", "ضدور"]

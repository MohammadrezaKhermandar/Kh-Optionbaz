"""تست‌های رتبه‌بندی اولویت بررسی.

فقط روی **خطرهای رفتاری** همین قابلیت تمرکز دارد — آن چیزهایی که اگر
خراب شوند، امتیاز بی‌صدا گمراه‌کننده می‌شود:

* گزینه‌ی ردشده‌ی غربال، هر چقدر هم جذاب، نباید رتبه بگیرد؛
* اجرای بدتر نباید امتیاز بهتر بگیرد؛
* اندازه‌ی سفارش باید واقعاً بر رتبه اثر بگذارد؛
* داده‌ی ناقص نباید برتریِ مصنوعی بسازد؛
* امتیاز باید از همان ورودی‌ها قابلِ بازتولید و توضیح باشد.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from market.opportunity_ranking import (
    RankingWeights,
    StrategyEvidence,
    breakeven_price,
    rank_opportunities,
)
from market.tradability import (
    HistoryStats,
    LiquidityObservation,
    Thresholds,
    evaluate,
)

NOW = datetime(2026, 5, 20, 10, 30)
THRESHOLDS = Thresholds()
WEIGHTS = RankingWeights()

#: تاریخچه‌ای که همه‌ی سنجه‌هایش دانسته و سالم است، تا حکمِ غربال به
#: خودِ مظنه و عمق بستگی داشته باشد نه به نبودِ تاریخچه.
HEALTHY = HistoryStats(
    sessions=20,
    sessions_with_trades=18,
    sessions_with_both_quotes=20,
    known=True,
    sessions_verified=True,
)


def _observation(**kwargs) -> LiquidityObservation:
    """یک قرارداد سالم و نقدشونده؛ هر تست فقط چیزی را که می‌سنجد عوض می‌کند."""
    defaults = dict(
        symbol="ضخود۱۰۰۱",
        position_side="buy",
        quantity=10,
        observed_at=NOW,
        bid=980.0,
        ask=1_020.0,
        exit_depth_contracts=100,
        exit_fill_price=980.0,
        best_exit_price=980.0,
        open_interest=500,
        trades_today=40,
        days_to_expiry=45,
        quote_age_seconds=5.0,
    )
    defaults.update(kwargs)
    return LiquidityObservation(**defaults)


def _candidate(observation=None, history=HEALTHY, **overrides) -> dict:
    observation = observation or _observation()
    report = evaluate(observation, history, THRESHOLDS)
    candidate = {
        "report": report,
        "symbol": observation.symbol,
        "strategy": "directional_ma_cross",
        "side": observation.position_side,
        "quantity": observation.quantity,
        "leg_group_id": None,
        "option_type": "call",
        "strike": 10_000.0,
        "premium": 1_000.0,
        "underlying_price": 10_500.0,
        "notional": 1_000.0 * observation.quantity * 1_000,
        "max_loss": 1_000.0 * observation.quantity * 1_000,
        "round_trip_fees": 50_000.0,
    }
    candidate.update(overrides)
    return candidate


def _rank(candidates, weights=WEIGHTS, evidence=None):
    return rank_opportunities(
        candidates=candidates,
        thresholds=THRESHOLDS,
        weights=weights,
        evidence_by_strategy=evidence or {},
        evaluated_at=NOW,
    )


# ----------------------------------------------------------------------
# ۱. غربال دروازه است، امتیاز جایش را نمی‌گیرد
# ----------------------------------------------------------------------
def test_a_rejected_option_never_enters_the_ranking_however_attractive():
    """گزینه‌ی ردشده حتی با بهترین عددهای دیگر هم رتبه نمی‌گیرد.

    این همان چیزی است که کل معماری بر آن بنا شده: نقدشوندگی جبران‌شدنی
    نیست. اگر امتیاز بتواند ردشده را بالا بیاورد، غربال بی‌معنا می‌شود.
    """
    # اسپرد ۶۰٪ — خیلی بالاتر از سقفِ ۲۵٪ غربال؛ بقیه‌ی سنجه‌ها عالی‌اند.
    dead = _candidate(_observation(symbol="ضمرده", bid=500.0, ask=1_500.0))
    assert dead["report"].verdict.value == "rejected"

    result = _rank([dead])

    assert result.ranked == ()
    assert [e.symbol for e in result.excluded] == ["ضمرده"]
    assert "رد شده" in result.excluded[0].reason


def test_a_needs_review_option_is_excluded_too_with_its_own_reason():
    """نبودِ داده «قبول» ترجمه نمی‌شود — ولی علتش با «رد شده» فرق دارد."""
    unknown_history = _candidate(history=HistoryStats(known=False))
    assert unknown_history["report"].verdict.value == "needs_review"

    result = _rank([unknown_history])

    assert result.ranked == ()
    assert "نیازمند بررسی" in result.excluded[0].reason


def test_a_multi_leg_leg_is_excluded_with_a_stated_scope_reason():
    """دامنه‌ی نسخه‌ی اول صریح اعلام می‌شود، بی‌صدا حذف نمی‌شود."""
    leg = _candidate(leg_group_id="grp-1")

    result = _rank([leg])

    assert result.ranked == ()
    assert "چندپایه" in result.excluded[0].reason


# ----------------------------------------------------------------------
# ۲. اجرای بدتر، امتیاز بهتر نمی‌گیرد
# ----------------------------------------------------------------------
def test_worse_execution_never_scores_better_in_otherwise_equal_conditions():
    """در شرایط برابر، اسپردِ بدتر باید امتیازِ کمتر بدهد، نه بیشتر."""
    tight = _candidate(_observation(symbol="ضتنگ", bid=995.0, ask=1_005.0))
    wide = _candidate(_observation(symbol="ضپهن", bid=950.0, ask=1_050.0))

    result = _rank([tight, wide])

    ordered = [r.symbol for r in result.ranked]
    assert ordered == ["ضتنگ", "ضپهن"], "اسپرد بازتر نباید بالاتر بنشیند"
    assert result.ranked[0].score > result.ranked[1].score


def test_slippage_lowers_the_score_even_when_the_spread_is_identical():
    """لغزش سنجه‌ی جدایی است: عمق دور از مظنه، ظرفیت نیست.

    این دو قرارداد اسپرد یکسان دارند؛ تنها فرقشان این است که سفارش در
    یکی روی بهترین مظنه پر می‌شود و در دیگری با ۵٪ بدتر.
    """
    clean = _candidate(_observation(symbol="ضصاف", exit_fill_price=980.0))
    slipping = _candidate(_observation(symbol="ضلغزان", exit_fill_price=931.0))

    result = _rank([clean, slipping])

    assert [r.symbol for r in result.ranked] == ["ضصاف", "ضلغزان"]
    assert result.ranked[0].score > result.ranked[1].score


def test_deeper_book_scores_at_least_as_high_as_a_thin_one():
    thin = _candidate(_observation(symbol="ضکم‌عمق", exit_depth_contracts=10))
    deep = _candidate(_observation(symbol="ضپرعمق", exit_depth_contracts=100))

    result = _rank([thin, deep])

    assert [r.symbol for r in result.ranked] == ["ضپرعمق", "ضکم‌عمق"]


# ----------------------------------------------------------------------
# ۳. اندازه‌ی سفارش واقعاً اثر می‌گذارد
# ----------------------------------------------------------------------
def test_a_bigger_order_can_lose_its_place_to_depth_and_slippage():
    """همان قرارداد، با سفارشِ بزرگ‌تر: عمقِ نسبی کم و لغزش زیاد می‌شود.

    دفتر: ۱۰ قرارداد روی ۱۰۰۰ و ۹۰ قرارداد روی ۹۰۰. سفارشِ ۱۰تایی روی
    بهترین مظنه پر می‌شود؛ سفارشِ ۱۰۰تایی میانگینِ بدتری می‌گیرد و
    نسبتِ عمقش هم از ۱۰ برابر به ۱ برابر می‌افتد.
    """
    small = _candidate(_observation(
        symbol="ضکوچک", quantity=10,
        exit_depth_contracts=100, exit_fill_price=1_000.0, best_exit_price=1_000.0,
    ))
    big = _candidate(_observation(
        symbol="ضبزرگ", quantity=100,
        exit_depth_contracts=100, exit_fill_price=910.0, best_exit_price=1_000.0,
    ))

    result = _rank([small, big])

    assert [r.symbol for r in result.ranked] == ["ضکوچک", "ضبزرگ"]
    assert result.ranked[0].score > result.ranked[1].score


def test_an_order_too_big_for_the_book_is_rejected_by_the_gate_not_ranked_low():
    """سفارشی که عمق کفافش را نمی‌دهد اصلاً رتبه نمی‌گیرد — رتبه‌ی پایین نه.

    این تفاوت مهم است: «بد» با «غیرقابل اجرا» یکی نیست.
    """
    oversized = _candidate(_observation(
        symbol="ضعظیم", quantity=500, exit_depth_contracts=100, exit_fill_price=None,
    ))

    result = _rank([oversized])

    assert result.ranked == ()
    assert result.excluded[0].verdict == "rejected"


# ----------------------------------------------------------------------
# ۴. داده‌ی ناقص برتریِ مصنوعی نمی‌سازد
# ----------------------------------------------------------------------
def test_missing_data_never_outranks_a_fully_known_option():
    """مؤلفه‌ی نامعلوم از مخرج حذف نمی‌شود؛ صفر حساب می‌شود.

    اگر حذف می‌شد، گزینه‌ای که کارمزدش را نمی‌دانیم خودبه‌خود از گزینه‌ی
    یکسانی که هزینه‌اش را می‌دانیم بالاتر می‌نشست — یعنی ندانستن پاداش
    می‌گرفت.
    """
    known = _candidate(_observation(symbol="ضدانسته"), round_trip_fees=50_000.0)
    unknown = _candidate(_observation(symbol="ضنامعلوم"), round_trip_fees=None)

    result = _rank([known, unknown])

    by_symbol = {r.symbol: r for r in result.ranked}
    assert by_symbol["ضدانسته"].score > by_symbol["ضنامعلوم"].score
    assert [r.symbol for r in result.ranked] == ["ضدانسته", "ضنامعلوم"]


def test_an_unknown_component_is_reported_not_hidden():
    """نامعلوم باید دیده شود: هم در پوشش داده، هم در فهرست خودش."""
    candidate = _candidate(round_trip_fees=None)

    ranked = _rank([candidate]).ranked[0]

    assert ranked.coverage_pct < 100.0
    labels = [c.label for c in ranked.unknown_components]
    assert "سهم کارمزد از موقعیت" in labels
    # و در «ضعف» شمرده نمی‌شود؛ ندانستن ضعف نیست.
    assert ranked.weakness is None or ranked.weakness.is_known


def test_the_conservative_score_never_exceeds_the_best_case():
    partial = _rank([_candidate(round_trip_fees=None)]).ranked[0]

    assert partial.score < partial.score_best_case
    # فاصله دقیقاً به اندازه‌ی وزنِ نامعلوم است — نواری که می‌گوید چقدر نمی‌دانیم.
    assert partial.score_best_case - partial.score == pytest.approx(
        WEIGHTS.weight_cost_drag + WEIGHTS.weight_strategy_evidence
    )


def test_evidence_stays_unknown_until_the_sample_is_big_enough():
    """نرخ بردِ سه سیگنال عدد است، ولی شاهد نیست."""
    thin = StrategyEvidence(
        strategy="directional_ma_cross", resolved=3, wins=3, win_rate_pct=100.0
    )

    ranked = _rank([_candidate()], evidence={"directional_ma_cross": thin}).ranked[0]

    evidence = next(c for c in ranked.components if c.key == "strategy_evidence")
    assert evidence.score is None
    assert "نامعلوم است، نه بد" in evidence.detail


def test_a_real_track_record_counts_once_the_sample_is_big_enough():
    solid = StrategyEvidence(
        strategy="directional_ma_cross", resolved=20, wins=14, win_rate_pct=70.0
    )

    ranked = _rank([_candidate()], evidence={"directional_ma_cross": solid}).ranked[0]

    evidence = next(c for c in ranked.components if c.key == "strategy_evidence")
    assert evidence.score == pytest.approx(70.0)
    assert "پیش‌بینی" in evidence.detail, "باید صریح بگوید گذشته است، نه پیش‌بینی"


# ----------------------------------------------------------------------
# ۵. امتیاز قابل بازتولید و توضیح است
# ----------------------------------------------------------------------
def test_the_score_is_the_weighted_sum_of_its_own_components():
    """امتیاز باید دقیقاً از همان سهم‌هایی بیاید که نمایش داده می‌شود.

    اگر عدد و توضیح از دو جا بیایند، «چرا این رتبه» بی‌معنا می‌شود.
    """
    ranked = _rank([_candidate()]).ranked[0]

    rebuilt = sum(c.contribution for c in ranked.components) / ranked.total_weight * 100
    assert ranked.score == pytest.approx(rebuilt)
    assert sum(c.weight for c in ranked.components) == pytest.approx(100.0)


def test_the_same_inputs_give_the_same_score_every_time():
    first = _rank([_candidate()]).ranked[0]
    second = _rank([_candidate()]).ranked[0]

    assert first.score == second.score
    assert [c.score for c in first.components] == [c.score for c in second.components]


def test_a_zero_depth_floor_does_not_silently_disable_the_capacity_measure():
    """کفِ صفرِ غربال نباید سنجه‌ی ظرفیت را بی‌اثر کند.

    اگر «راحت» از ضربِ کفِ صفر می‌آمد، صفر می‌شد و هر عمقی امتیاز کامل
    می‌گرفت — یعنی دفترِ نازک و دفترِ عمیق فرقی نمی‌کردند.
    """
    thresholds = Thresholds(min_exit_depth_ratio=0.0)
    thin = _candidate(_observation(symbol="ضنازک", exit_depth_contracts=10))
    deep = _candidate(_observation(symbol="ضعمیق", exit_depth_contracts=300))

    result = rank_opportunities(
        candidates=[thin, deep], thresholds=thresholds, weights=WEIGHTS,
        evidence_by_strategy={}, evaluated_at=NOW,
    )

    scores = {r.symbol: r.score for r in result.ranked}
    assert scores["ضعمیق"] > scores["ضنازک"]


def test_changing_a_weight_changes_the_score_in_the_stated_direction():
    """وزن‌ها واقعاً تنظیم‌پذیرند — نه عددی تزئینی."""
    candidate = _candidate(_observation(exit_depth_contracts=1_000))
    light = _rank([candidate], weights=RankingWeights(weight_exit_capacity=5.0))
    heavy = _rank([candidate], weights=RankingWeights(weight_exit_capacity=60.0))

    # ظرفیتِ خروج اینجا امتیاز کاملی دارد، پس وزنِ بیشتر یعنی امتیاز بالاتر.
    assert heavy.ranked[0].score > light.ranked[0].score


def test_every_component_explains_itself_with_a_number():
    """توضیح بدون عدد، توضیح نیست — همان قاعده‌ای که غربال هم دارد."""
    ranked = _rank([_candidate()]).ranked[0]

    for component in ranked.components:
        assert component.detail.strip(), component.key
        assert component.label.strip(), component.key
        assert component.weight > 0, component.key


def test_the_output_says_what_the_score_is_not():
    """این عدد نباید با احتمال برد یا بازده مورد انتظار اشتباه شود."""
    payload = _rank([_candidate()]).to_dict()

    assert "احتمال برد" in payload["note"]
    assert "بازده مورد انتظار" in payload["note"]


# ----------------------------------------------------------------------
# سر‌به‌سر و حرکت لازم
# ----------------------------------------------------------------------
def test_breakeven_moves_the_right_way_for_calls_and_puts():
    assert breakeven_price(10_000, 500, "call") == 10_500
    assert breakeven_price(10_000, 500, "put") == 9_500


def test_an_option_needing_a_huge_move_ranks_below_one_that_is_already_there():
    """هرچه حرکتِ لازم بزرگ‌تر، ادعای استراتژی بزرگ‌تر — نه احتمالش کمتر.

    اینجا هیچ احتمالی ادعا نمی‌شود؛ فقط «چقدر باید اتفاق بیفتد» سنجیده
    می‌شود.
    """
    near = _candidate(
        _observation(symbol="ضنزدیک"), strike=10_000.0, premium=100.0,
        underlying_price=10_500.0,
    )
    far = _candidate(
        _observation(symbol="ضدور"), strike=14_000.0, premium=100.0,
        underlying_price=10_500.0,
    )

    result = _rank([near, far])

    assert [r.symbol for r in result.ranked] == ["ضنزدیک", "ضدور"]


def test_required_move_is_unknown_without_an_underlying_price():
    ranked = _rank([_candidate(underlying_price=None)]).ranked[0]

    move = next(c for c in ranked.components if c.key == "required_move")
    assert move.score is None
    assert ranked.coverage_pct < 100.0

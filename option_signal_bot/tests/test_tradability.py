"""غربالِ قابلیت معامله — همان خطرهایی که بدون تست بی‌صدا رد می‌شوند.

پوشش عمداً محدود است به پنج چیزی که اگر خراب باشند، پیشنهادها بی‌معنا
می‌شوند:

۱. گزینه‌ی جذاب ولی **غیرقابل خروج** کنار برود.
۲. آنچه برای سفارش کوچک قابل معامله است، برای سفارش بزرگ لزوماً نیست.
۳. نبودِ تاریخچه **تأیید کیفیت نباشد**.
۴. تغییر آستانه‌ها واقعاً بر نتیجه اثر بگذارد.
۵. غربال به ثبت خامِ recorder **نشت نکند**.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from data.option_chain_client import OptionContract
from data.order_book import BookLevel, OrderBook
from market.tradability import (
    HistoryStats,
    LiquidityObservation,
    Thresholds,
    Verdict,
    evaluate,
)
from market.tradability_screener import TradabilityScreener
from storage.market_history import MarketHistoryReader

SYMBOL = "ضخود7001"
INS_CODE = "12345"

#: تاریخچه‌ی سالم و کافی — تا تست‌هایی که موضوعشان تاریخچه نیست، به آن
#: برنخورند.
HEALTHY = HistoryStats(
    sessions=20, sessions_with_trades=18, sessions_with_both_quotes=20,
    first_session="2026-08-01", last_session="2026-09-10", known=True,
    sessions_verified=True,
)


def _observation(**overrides) -> LiquidityObservation:
    base = {
        "symbol": SYMBOL,
        "position_side": "buy",
        "quantity": 1,
        "observed_at": datetime(2026, 9, 11, 10, 0, 0),
        "bid": 1_000.0,
        "ask": 1_050.0,
        "exit_depth_contracts": 100,
        # خروج دقیقاً روی بهترین مظنه پر می‌شود → لغزش صفر
        "exit_fill_price": 1_000.0,
        "best_exit_price": 1_000.0,
        "open_interest": 5_000,
        "trades_today": 40,
        "days_to_expiry": 30,
        "quote_age_seconds": 5.0,
    }
    base.update(overrides)
    return LiquidityObservation(**base)


# ======================================================================
# ۱. جذاب ولی غیرقابل خروج
# ======================================================================
def test_a_contract_with_no_exit_bid_is_rejected(tmp_path):
    """قراردادی با مظنه‌ی فروشِ عالی ولی **بدون خریدار** رد می‌شود.

    این دقیقاً تله‌ی رایج است: ask هست، قیمت جذاب به نظر می‌رسد، ولی
    وقتی بخواهی بفروشی کسی نیست. سمتِ ورود سمتِ خروج نیست.
    """
    del tmp_path
    report = evaluate(
        _observation(bid=None, exit_depth_contracts=0, exit_fill_price=None,
                     best_exit_price=None),
        HEALTHY,
        Thresholds(),
    )

    assert report.verdict is Verdict.REJECTED
    failed = {c.key for c in report.failed}
    assert "exit_depth" in failed
    assert "اسپرد" in report.reason or "عمق" in report.reason


def test_the_exit_side_follows_the_position_side():
    """برای long سمت خرید بازار، برای بستنِ فروش سمت فروش بازار."""
    assert _observation(position_side="buy").exit_side == "bid"
    assert _observation(position_side="sell").exit_side == "ask"


def test_a_high_score_cannot_buy_its_way_past_the_gate():
    """غربال دروازه است، نه امتیاز: هیچ ورودی‌ای آن را جبران نمی‌کند.

    حتی با موقعیت باز عظیم و معاملات فراوان، نبودِ عمقِ خروج رد است.
    """
    report = evaluate(
        _observation(open_interest=1_000_000, trades_today=5_000,
                     exit_depth_contracts=0, exit_fill_price=None),
        HEALTHY,
        Thresholds(),
    )
    assert report.verdict is Verdict.REJECTED


# ======================================================================
# ۲. اندازه‌ی سفارش بخشی از سؤال است
# ======================================================================
def test_the_same_contract_passes_small_and_fails_large():
    """عمقِ ۱۰ قرارداد برای سفارش ۵ کافی است، برای ۵۰ نه."""
    thresholds = Thresholds(min_exit_depth_ratio=1.0)

    small = evaluate(
        _observation(quantity=5, exit_depth_contracts=10), HEALTHY, thresholds
    )
    large = evaluate(
        _observation(quantity=50, exit_depth_contracts=10, exit_fill_price=None),
        HEALTHY, thresholds
    )

    assert small.verdict is Verdict.TRADABLE
    assert large.verdict is Verdict.REJECTED
    depth = next(c for c in large.failed if c.key == "exit_depth")
    assert depth.value == pytest.approx(0.2), "۱۰ ÷ ۵۰"
    assert "۵۰" in depth.unit or "50" in depth.unit, "اندازه‌ی سفارش در واحد بیاید"


def test_depth_ratio_is_measured_against_the_order_not_absolutely():
    assert _observation(quantity=4, exit_depth_contracts=8).exit_depth_ratio == 2.0
    assert _observation(quantity=8, exit_depth_contracts=4).exit_depth_ratio == 0.5


# ======================================================================
# ۳. نبودِ تاریخچه تأیید نیست
# ======================================================================
def test_missing_history_is_needs_review_not_tradable():
    """بدون تاریخچه، «نمی‌دانیم» — نه «سالم»."""
    report = evaluate(_observation(), HistoryStats(known=False), Thresholds())

    assert report.verdict is Verdict.NEEDS_REVIEW
    assert {c.key for c in report.unknown} == {"trading_continuity"}
    assert "تاریخچه" in report.reason


def test_thin_history_is_needs_review_not_tradable():
    """تاریخچه‌ی کوتاه‌تر از حداقل هم قضاوت‌پذیر نیست."""
    thin = HistoryStats(sessions=2, sessions_with_trades=2, known=True,
                        sessions_verified=True)
    report = evaluate(_observation(), thin, Thresholds(min_history_sessions=5))

    assert report.verdict is Verdict.NEEDS_REVIEW
    assert "۲ جلسه" in report.reason or "2 جلسه" in report.reason


def test_history_with_many_silent_sessions_is_rejected():
    """تاریخچه‌ی موجود ولی پر از روزِ بدون معامله = رد، نه نامعلوم."""
    silent = HistoryStats(sessions=20, sessions_with_trades=4, known=True,
                          sessions_verified=True)
    report = evaluate(_observation(), silent, Thresholds())

    assert report.verdict is Verdict.REJECTED
    continuity = next(c for c in report.failed if c.key == "trading_continuity")
    assert continuity.value == pytest.approx(20.0)


def test_unknown_and_failed_together_still_rejects():
    """یک سنجه‌ی ردشده، حتی کنار سنجه‌ی نامعلوم، حکم را رد می‌کند."""
    report = evaluate(
        _observation(exit_depth_contracts=0, exit_fill_price=None),
        HistoryStats(known=False), Thresholds()
    )
    assert report.verdict is Verdict.REJECTED


# ======================================================================
# ۴. آستانه‌ها واقعاً اثر دارند
# ======================================================================
def test_changing_a_threshold_changes_the_verdict():
    observation = _observation(open_interest=100)

    strict = evaluate(observation, HEALTHY, Thresholds(min_open_interest_contracts=500))
    relaxed = evaluate(observation, HEALTHY, Thresholds(min_open_interest_contracts=50))

    assert strict.verdict is Verdict.REJECTED
    assert relaxed.verdict is Verdict.TRADABLE


def test_spread_threshold_is_in_percent_of_mid():
    """اسپرد ۱۰۰۰ تا ۱۲۰۰ یعنی ۱۸٫۱۸٪ از میانه، نه ۲۰٪ از bid."""
    observation = _observation(bid=1_000.0, ask=1_200.0)
    assert observation.relative_spread_pct == pytest.approx(18.1818, abs=1e-3)

    assert evaluate(
        observation, HEALTHY, Thresholds(max_relative_spread_pct=20.0)
    ).verdict is Verdict.TRADABLE
    assert evaluate(
        observation, HEALTHY, Thresholds(max_relative_spread_pct=15.0)
    ).verdict is Verdict.REJECTED


def test_every_check_reports_value_threshold_and_unit():
    """«چرا» باید با عدد گفته شود، نه با برچسب."""
    report = evaluate(_observation(open_interest=1), HEALTHY, Thresholds())
    check = next(c for c in report.failed if c.key == "open_interest")

    assert check.value == 1.0
    assert check.threshold == 50.0
    assert check.unit == "قرارداد"
    assert "۱" in check.describe() or "1" in check.describe()


# ======================================================================
# ۵. غربال به ثبت خام نشت نمی‌کند
# ======================================================================
def _recorder_db(path, sessions: list[tuple[str, int]]) -> None:
    """یک پایگاه recorder کوچک با اسکیمای نسخه ۳."""
    connection = sqlite3.connect(str(path))
    connection.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE snapshots (
            snapshot_id TEXT PRIMARY KEY, requested_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE quotes (
            snapshot_id TEXT NOT NULL, ins_code TEXT NOT NULL,
            bid REAL, ask REAL, trade_count INTEGER,
            PRIMARY KEY (snapshot_id, ins_code)
        );
        """
    )
    connection.execute("INSERT INTO meta VALUES ('db_schema_version', '3')")
    for index, (day, trades) in enumerate(sessions):
        connection.execute(
            "INSERT INTO snapshots VALUES (?, ?, 'complete')",
            (f"s{index}", f"{day}T10:00:00+03:30"),
        )
        connection.execute(
            "INSERT INTO quotes VALUES (?, ?, 100.0, 110.0, ?)",
            (f"s{index}", INS_CODE, trades),
        )
    connection.commit()
    connection.close()


def _append_session(path, day: str, trades: int) -> None:
    """یک جلسه‌ی تازه به پایگاه اضافه می‌کند — شبیه‌سازی کارِ recorder."""
    connection = sqlite3.connect(str(path))
    connection.execute(
        "INSERT INTO snapshots VALUES (?, ?, 'complete')",
        (f"s-{day}", f"{day}T10:00:00+03:30"),
    )
    connection.execute(
        "INSERT INTO quotes VALUES (?, ?, 100.0, 110.0, ?)",
        (f"s-{day}", INS_CODE, trades),
    )
    connection.commit()
    connection.close()


def test_history_reader_never_writes_to_the_recorder_database(tmp_path):
    """پایگاه خام باید پس از خواندن **بایت‌به‌بایت** همان باشد.

    حذف یک قرارداد از پیشنهادها نباید تاریخچه‌اش را لمس کند؛ فردا باید
    بشود همین تصمیم را دوباره سنجید.
    """
    db = tmp_path / "market.db"
    _recorder_db(db, [(f"2026-09-{d:02d}", 5) for d in range(1, 11)])
    before = db.read_bytes()

    with MarketHistoryReader(db) as reader:
        stats = reader.stats_for(INS_CODE)

    assert stats.known and stats.sessions == 10
    assert db.read_bytes() == before, "پایگاه خام نباید تغییر کرده باشد"
    assert not list(tmp_path.glob("*.db-wal")), "حتی فایل جانبی هم ساخته نشود"


def test_history_reader_refuses_to_write_even_if_asked(tmp_path):
    """اتصال `mode=ro` است، پس نوشتنِ اتفاقی هم خطا می‌دهد."""
    db = tmp_path / "market.db"
    _recorder_db(db, [("2026-09-01", 3)])

    with MarketHistoryReader(db) as reader:
        assert reader.available
        with pytest.raises(sqlite3.OperationalError):
            reader._connection.execute("DELETE FROM quotes")


def test_a_missing_history_database_is_not_created(tmp_path):
    """نبودِ پایگاه نباید فایل خالی بسازد و نباید خطا بدهد."""
    db = tmp_path / "absent.db"

    with MarketHistoryReader(db) as reader:
        assert reader.available is False
        assert reader.stats_for(INS_CODE).known is False

    assert not db.exists(), "خواندن نباید پایگاه بسازد"


def test_an_unknown_schema_is_ignored_not_migrated(tmp_path):
    """اسکیمای ناشناخته نادیده گرفته می‌شود، نه اینکه ارتقا داده شود."""
    db = tmp_path / "old.db"
    _recorder_db(db, [("2026-09-01", 3)])
    connection = sqlite3.connect(str(db))
    connection.execute("UPDATE meta SET value = '1' WHERE key = 'db_schema_version'")
    connection.commit()
    connection.close()
    before = db.read_bytes()

    with MarketHistoryReader(db) as reader:
        assert reader.available is False
        assert "نسخه" in (reader.unavailable_reason or "")

    assert db.read_bytes() == before


def test_multiple_snapshots_in_one_day_count_as_one_session(tmp_path):
    """چند snapshot در یک روز، یک جلسه است — نه چند جلسه تداوم."""
    db = tmp_path / "market.db"
    _recorder_db(db, [("2026-09-01", 2), ("2026-09-01", 2), ("2026-09-02", 0)])

    with MarketHistoryReader(db) as reader:
        stats = reader.stats_for(INS_CODE)

    assert stats.sessions == 2, "دو روز، نه سه snapshot"
    assert stats.sessions_with_trades == 1
    assert stats.sessions_without_trades == 1


# ======================================================================
# غربالگر روی داده‌ی واقعیِ قرارداد
# ======================================================================
def _contract(**overrides) -> OptionContract:
    base = {
        "symbol": SYMBOL,
        "underlying": "خودرو",
        "option_type": "call",
        "strike": 2_000.0,
        "expiry": date.today() + timedelta(days=30),
        "bid": 1_000.0,
        "ask": 1_050.0,
        "open_interest": 5_000,
        "contract_size": 1_000,
        "ins_code": INS_CODE,
        "trade_count": 40,
        "bid_quantity": 100,
        "ask_quantity": 100,
    }
    base.update(overrides)
    return OptionContract(**base)


class _Books:
    def __init__(self, book: OrderBook | None) -> None:
        self.book = book

    def try_get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook | None:
        del ins_code, symbol
        return self.book


def test_screener_uses_multi_level_depth_for_the_exit_side():
    """عمقِ چندسطحی معیار است، نه فقط سطح اول."""
    book = OrderBook(
        SYMBOL,
        bids=(BookLevel(1_000.0, 3), BookLevel(990.0, 12)),
        asks=(BookLevel(1_050.0, 2),),
    )
    screener = TradabilityScreener(
        resolve_contract=lambda s: _contract(),
        thresholds=Thresholds(min_history_sessions=1),
        order_book_client=_Books(book),
        history=None,
    )

    report = screener.evaluate_symbol(SYMBOL, position_side="buy", quantity=10)

    depth = next(c for c in report.checks if c.key == "exit_depth")
    assert depth.value == pytest.approx(1.5), "عمق کل ۱۵ است، تقسیم بر ۱۰"


def test_screener_falls_back_to_level_one_without_an_order_book():
    """بدون دفتر چندسطحی، سطح اولِ زنجیره مبنا می‌شود."""
    screener = TradabilityScreener(
        resolve_contract=lambda s: _contract(bid_quantity=7),
        thresholds=Thresholds(),
        order_book_client=None,
        history=None,
    )

    report = screener.evaluate_symbol(SYMBOL, position_side="buy", quantity=7)
    depth = next(c for c in report.checks if c.key == "exit_depth")
    assert depth.value == pytest.approx(1.0)
    slippage = next(c for c in report.checks if c.key == "exit_slippage")
    assert slippage.value == pytest.approx(0.0), "تک‌سطحی، پس بدون لغزش"


def test_screener_treats_a_missing_field_as_unknown_not_zero():
    """فیلدی که منبع نداده، «نامعلوم» است — نه صفر."""
    screener = TradabilityScreener(
        resolve_contract=lambda s: _contract(trade_count=None),
        thresholds=Thresholds(),
        order_book_client=None,
        history=None,
    )

    report = screener.evaluate_symbol(SYMBOL, position_side="buy", quantity=1)
    trades = next(c for c in report.checks if c.key == "trades_today")

    assert trades.is_unknown
    assert report.verdict is Verdict.NEEDS_REVIEW, "نامعلوم، نه رد"


def test_screener_rejects_an_unknown_symbol_without_raising():
    screener = TradabilityScreener(
        resolve_contract=lambda s: None,
        thresholds=Thresholds(),
        order_book_client=None,
        history=None,
    )

    report = screener.evaluate_symbol("ناشناخته", position_side="buy", quantity=1)
    assert report.verdict is Verdict.NEEDS_REVIEW
    assert report.unknown


# ======================================================================
# رگرسیون بازبینی دوم PR #6
# ======================================================================
class _AgingBooks:
    """کلاینتی که هم دفتر می‌دهد هم عمرِ کش را اعلام می‌کند."""

    def __init__(self, book: OrderBook | None, age: float | None) -> None:
        self.book = book
        self.age = age

    def try_get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook | None:
        del ins_code, symbol
        return self.book

    def cache_age_seconds(self, ins_code: str) -> float | None:
        del ins_code
        return self.age


def _deep_book(bid_levels, ask_levels=((1_050.0, 100),)) -> OrderBook:
    return OrderBook(
        SYMBOL,
        bids=tuple(BookLevel(p, q) for p, q in bid_levels),
        asks=tuple(BookLevel(p, q) for p, q in ask_levels),
    )


def _screener(book, age=0.0, **threshold_overrides) -> TradabilityScreener:
    defaults = {"min_history_sessions": 1}
    defaults.update(threshold_overrides)
    return TradabilityScreener(
        resolve_contract=lambda s: _contract(),
        thresholds=Thresholds(**defaults),
        order_book_client=_AgingBooks(book, age=age),
        history=None,
    )


# --- ۱. تازگی واقعی ---------------------------------------------------
def test_quote_age_comes_from_the_real_cache_age_not_a_constant():
    """پیش از این صفرِ ثابت بود، پس آستانه‌ی کهنگی هرگز اثر نمی‌کرد."""
    report = _screener(_deep_book([(1_000.0, 100)]), age=42.0).evaluate_symbol(
        SYMBOL, "buy", 1
    )
    age = next(c for c in report.checks if c.key == "quote_age")

    assert age.value == pytest.approx(42.0), "عمر واقعی، نه صفرِ ثابت"


def test_the_staleness_threshold_actually_changes_the_verdict():
    """همان دادهٔ کهنه، با آستانه‌ی سخت‌گیرانه رد می‌شود."""
    book = _deep_book([(1_000.0, 100)])

    lenient = _screener(book, age=90.0, max_quote_age_seconds=120.0).evaluate_symbol(
        SYMBOL, "buy", 1
    )
    strict = _screener(book, age=90.0, max_quote_age_seconds=30.0).evaluate_symbol(
        SYMBOL, "buy", 1
    )

    assert next(c for c in lenient.checks if c.key == "quote_age").passed is True
    assert strict.verdict is Verdict.REJECTED
    assert "quote_age" in {c.key for c in strict.failed}


def test_an_unknown_cache_age_is_unknown_not_fresh():
    """کلاینتی که عمر نمی‌دهد، «تازه» تفسیر نمی‌شود."""
    report = _screener(_deep_book([(1_000.0, 100)]), age=None).evaluate_symbol(
        SYMBOL, "buy", 1
    )
    age = next(c for c in report.checks if c.key == "quote_age")

    assert age.is_unknown
    assert report.verdict is Verdict.NEEDS_REVIEW


def test_missing_market_timestamp_is_stated_explicitly():
    """زمانِ دریافت جای زمانِ بازار معرفی نمی‌شود."""
    report = evaluate(_observation(), HEALTHY, Thresholds())

    assert report.observation.source_time_known is False
    assert "زمان بازار نامعلوم" in report.source_time_note
    age = next(c for c in report.checks if c.key == "quote_age")
    assert "نه زمان بازار" in age.label


# --- ۲. ظرفیت خروج با قیمت قابل‌قبول ---------------------------------
def test_depth_ratio_is_not_capped_at_one():
    """عمقِ سه‌برابرِ سفارش باید ۳ گزارش شود، نه ۱.

    پیش از این `fill_price` مبنا بود که حداکثر به اندازه‌ی سفارش پر
    می‌کند، پس نسبت هرگز از ۱ بالاتر نمی‌رفت و حاشیه‌ی اطمینان دیده
    نمی‌شد.
    """
    report = _screener(_deep_book([(1_000.0, 30)])).evaluate_symbol(SYMBOL, "buy", 10)
    depth = next(c for c in report.checks if c.key == "exit_depth")

    assert depth.value == pytest.approx(3.0), "۳۰ ÷ ۱۰"


def test_depth_far_from_the_touch_does_not_count_as_capacity():
    """حجم در قیمت‌های دور، سفارش را پر می‌کند ولی ظرفیت خروج نیست.

    بهترین مظنه ۱۰۰۰ ولی فقط ۱ قرارداد؛ بقیه در ۵۰۰. سفارش ۱۰تایی پر
    می‌شود، با میانگین ۵۵۰ — یعنی ۴۵٪ لغزش.
    """
    book = _deep_book([(1_000.0, 1), (500.0, 100)])
    report = _screener(book, max_exit_slippage_pct=10.0).evaluate_symbol(
        SYMBOL, "buy", 10
    )
    slippage = next(c for c in report.checks if c.key == "exit_slippage")

    assert report.verdict is Verdict.REJECTED
    assert slippage.passed is False
    assert slippage.value == pytest.approx(45.0, abs=0.1)
    # عمق «کافی» بود — رد فقط به‌خاطر قیمت است.
    assert next(c for c in report.checks if c.key == "exit_depth").passed is True


def test_a_generous_slippage_threshold_accepts_the_same_book():
    """همان دفتر با آستانه‌ی بازتر پذیرفته می‌شود — آستانه واقعاً اثر دارد."""
    book = _deep_book([(1_000.0, 1), (500.0, 100)])
    report = _screener(book, max_exit_slippage_pct=50.0).evaluate_symbol(
        SYMBOL, "buy", 10
    )

    assert next(c for c in report.checks if c.key == "exit_slippage").passed is True


def test_slippage_direction_follows_the_exit_side():
    """خروجِ long با قیمتِ پایین‌تر بد است؛ بازخریدِ short با بالاتر."""
    long_exit = _observation(
        position_side="buy", best_exit_price=1_000.0, exit_fill_price=900.0
    )
    short_exit = _observation(
        position_side="sell", best_exit_price=1_000.0, exit_fill_price=1_100.0
    )

    assert long_exit.exit_slippage_pct == pytest.approx(10.0)
    assert short_exit.exit_slippage_pct == pytest.approx(10.0)


# --- ۳. تاریخچه‌ی زنده و روز معاملاتی --------------------------------
_TRADING = {date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)}


def test_a_non_trading_day_is_not_counted_as_a_session(tmp_path):
    """۲۰۲۶-۰۹-۱۱ جمعه بود؛ snapshotش ماندهٔ جلسه‌ی قبل است.

    شمردنش به‌عنوان «روزِ دارای معامله» همان ادعای نادرستی است که این
    اصلاح جلویش را می‌گیرد.
    """
    db = tmp_path / "market.db"
    _recorder_db(db, [("2026-09-10", 5), ("2026-09-11", 5)])

    with MarketHistoryReader(db, is_trading_day=lambda d: d in _TRADING) as reader:
        stats = reader.stats_for(INS_CODE)

    assert stats.sessions == 1, "فقط پنج‌شنبه، نه جمعه"
    assert stats.skipped_non_trading_days == 1
    assert stats.sessions_verified is True


def test_without_a_calendar_the_history_is_unverified(tmp_path):
    """بدون تقویم هیچ روزی حذف نمی‌شود، ولی تأیید هم نمی‌شود."""
    db = tmp_path / "market.db"
    _recorder_db(db, [(f"2026-09-{d:02d}", 5) for d in range(1, 11)])

    with MarketHistoryReader(db) as reader:
        stats = reader.stats_for(INS_CODE)

    assert stats.known is True
    assert stats.sessions_verified is False
    report = evaluate(_observation(), stats, Thresholds(min_history_sessions=1))
    assert report.verdict is Verdict.NEEDS_REVIEW, "تأییدنشده = نامعلوم"
    assert "تأیید نشد" in report.reason


def test_new_snapshots_are_seen_without_a_restart(tmp_path):
    """داده‌ای که در طول اجرا اضافه شود، بدون راه‌اندازی مجدد دیده شود."""
    db = tmp_path / "market.db"
    _recorder_db(db, [("2026-09-08", 5)])
    reader = MarketHistoryReader(db, is_trading_day=lambda d: d in _TRADING)
    assert reader.stats_for(INS_CODE).sessions == 1

    _append_session(db, "2026-09-09", 7)
    _append_session(db, "2026-09-10", 3)

    assert reader.stats_for(INS_CODE).sessions == 3, "بدون راه‌اندازی مجدد"
    reader.close()


def test_a_database_created_later_is_picked_up(tmp_path):
    """پایگاهی که اول نبود و بعد ساخته شد، همان لحظه وصل می‌شود."""
    db = tmp_path / "later.db"
    reader = MarketHistoryReader(db, is_trading_day=lambda d: d in _TRADING)

    assert reader.available is False
    assert reader.stats_for(INS_CODE).known is False

    _recorder_db(db, [("2026-09-09", 4)])

    assert reader.available is True, "بدون راه‌اندازی مجدد"
    assert reader.stats_for(INS_CODE).sessions == 1
    reader.close()

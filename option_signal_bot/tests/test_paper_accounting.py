"""حسابداری حساب کاغذی — سناریوهای عددیِ **دستی**.

هر تست یک سناریوی کوچک است که عددهای انتظارش **بیرون از برنامه** حساب
شده و در خودِ تست به‌صورت ثابت نوشته شده‌اند. عمداً از فرمول‌های همان
کدی که سنجیده می‌شود استفاده نمی‌شود: تستی که همان محاسبه را تکرار کند،
فقط خودش را تأیید می‌کند.

قرارداد مشترک همه‌ی سناریوها:

    اندازه‌ی قرارداد = ۱۰۰۰
    کارمزد خرید = ۰٫۱٪   کارمزد فروش = ۰٫۲٪   مالیات فروش = ۰٫۱٪
    پس نرخ خروج = ۰٫۳٪
    سرمایه‌ی اولیه = ۱۰۰٬۰۰۰٬۰۰۰ ریال

محدوده‌ی پوشش، همان خطرهای واقعی است: ورود و خروج کامل، خروج جزئی،
هزینه‌ها، سرمایه‌ی ناکافی، قیمت ناموجود، و اتمیک‌بودن.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from data.option_chain_client import OptionContract
from data.order_book import BookLevel, OrderBook
from execution.paper_account import ValuationStatus
from execution.paper_broker import PaperBroker
from risk.fees import FeeSchedule
from storage.paper_trading_store import PaperTradingStore

SYMBOL = "ضخود7001"
INS_CODE = "12345"
CONTRACT_SIZE = 1_000
INITIAL = 100_000_000.0

#: همان نرخ‌هایی که بالای فایل توضیح داده شد.
FEES = FeeSchedule(buy_rate=0.001, sell_rate=0.002, sell_tax_rate=0.001)


class FakeBooks:
    """دفتر ثابت برای هر ins_code؛ `None` یعنی دفتر در دسترس نیست."""

    def __init__(self, books: dict[str, OrderBook | None]) -> None:
        self.books = books

    def try_get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook | None:
        del symbol
        return self.books.get(ins_code)


def _contract(expiry_days: int = 30) -> OptionContract:
    return OptionContract(
        symbol=SYMBOL,
        underlying="خودرو",
        option_type="call",
        strike=2000.0,
        expiry=date.today() + timedelta(days=expiry_days),
        last_price=1_000.0,
        contract_size=CONTRACT_SIZE,
        ins_code=INS_CODE,
    )


def _book(bid: float | None, ask: float | None, qty: int = 100) -> OrderBook:
    return OrderBook(
        SYMBOL,
        bids=(BookLevel(bid, qty),) if bid is not None else (),
        asks=(BookLevel(ask, qty),) if ask is not None else (),
    )


def _broker(
    tmp_path,
    book: OrderBook | None,
    *,
    balance: float = INITIAL,
    fees: FeeSchedule | None = FEES,
    expiry_days: int = 30,
) -> PaperBroker:
    contract = _contract(expiry_days)
    return PaperBroker(
        store=PaperTradingStore(tmp_path / "paper.db"),
        order_book_client=FakeBooks({INS_CODE: book}),
        resolve_contract=lambda s: contract if s == SYMBOL else None,
        initial_balance=balance,
        fees=fees or FeeSchedule(),
    )


# ======================================================================
# سناریو ۱ — ورود کامل: ارزش کل حساب نباید بپرد
# ======================================================================
def test_scenario_open_position_keeps_account_value_intact(tmp_path):
    """خرید ۵ قرارداد در ۱۰۰۰، مظنه‌ی خرید ۹۵۰.

    دستی:
        پرداختی   = ۱۰۰۰ × ۵ × ۱۰۰۰            = ۵٬۰۰۰٬۰۰۰
        کارمزد ورود = ۵٬۰۰۰٬۰۰۰ × ۰٫۰۰۱          =     ۵٬۰۰۰
        نقد        = ۱۰۰٬۰۰۰٬۰۰۰ − ۵٬۰۰۵٬۰۰۰    = ۹۴٬۹۹۵٬۰۰۰
        ارزش روز   = ۹۵۰ × ۵ × ۱۰۰۰             = ۴٬۷۵۰٬۰۰۰
        ارزش حساب  = ۹۴٬۹۹۵٬۰۰۰ + ۴٬۷۵۰٬۰۰۰     = ۹۹٬۷۴۵٬۰۰۰
        شناور ناخالص = (۹۵۰ − ۱۰۰۰) × ۵ × ۱۰۰۰  =  −۲۵۰٬۰۰۰
        شناور خالص  = −۲۵۰٬۰۰۰ − ۵٬۰۰۰          =  −۲۵۵٬۰۰۰

    رفتار قبلی `نقد + شناور` می‌داد: ۹۴٬۷۴۵٬۰۰۰ — یعنی ۵ میلیون ریال
    زیانِ موهوم در همان لحظه‌ی ورود.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0))
    broker.place_order(SYMBOL, "buy", 5)

    snapshot = broker.account_snapshot()

    assert snapshot.cash == pytest.approx(94_995_000.0)
    assert snapshot.blocked == 0.0
    assert snapshot.available == pytest.approx(94_995_000.0)
    assert snapshot.market_value == pytest.approx(4_750_000.0)
    assert snapshot.equity == pytest.approx(99_745_000.0)
    assert snapshot.unrealized_gross == pytest.approx(-250_000.0)
    assert snapshot.unrealized_net == pytest.approx(-255_000.0)
    assert snapshot.realized.trade_count == 0
    assert snapshot.reconciliation["ok"] is True


# ======================================================================
# سناریو ۲ — خروج کامل: هر دو کارمزد، دقیقاً یک بار
# ======================================================================
def test_scenario_full_exit_counts_both_fees_exactly_once(tmp_path):
    """خرید ۵ در ۱۰۰۰، فروش ۵ در ۱۲۰۰.

    دستی:
        کارمزد ورود  = ۱۰۰۰ × ۵ × ۱۰۰۰ × ۰٫۰۰۱ =     ۵٬۰۰۰
        دریافتی خروج = ۱۲۰۰ × ۵ × ۱۰۰۰         = ۶٬۰۰۰٬۰۰۰
        کارمزد خروج  = ۶٬۰۰۰٬۰۰۰ × ۰٫۰۰۳       =    ۱۸٬۰۰۰
        ناخالص       = (۱۲۰۰ − ۱۰۰۰) × ۵ × ۱۰۰۰ = ۱٬۰۰۰٬۰۰۰
        خالص         = ۱٬۰۰۰٬۰۰۰ − ۵٬۰۰۰ − ۱۸٬۰۰۰ =   ۹۷۷٬۰۰۰
        نقد پایانی   = ۱۰۰٬۰۰۰٬۰۰۰ + ۹۷۷٬۰۰۰     = ۱۰۰٬۹۷۷٬۰۰۰
        سرمایه‌ی درگیر = ۵٬۰۰۰٬۰۰۰ + ۵٬۰۰۰        = ۵٬۰۰۵٬۰۰۰
        بازده        = ۹۷۷٬۰۰۰ ÷ ۵٬۰۰۵٬۰۰۰      ≈ ۱۹٫۵۲۰٪

    رفتار قبلی کارمزد ورود را در نتیجه‌ی معامله نمی‌آورد (۹۸۲٬۰۰۰) و
    درصدش را `(۱۲۰۰−۱۰۰۰)/۱۰۰۰ = ۲۰٪` می‌گفت.
    """
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0))
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)

    trade = broker.store.list_trades()[0]
    assert trade["gross_pnl"] == pytest.approx(1_000_000.0)
    assert trade["entry_fee"] == pytest.approx(5_000.0)
    assert trade["exit_fee"] == pytest.approx(18_000.0)
    assert trade["pnl_absolute"] == pytest.approx(977_000.0)
    assert trade["return_on_cost_pct"] == pytest.approx(19.5205, abs=1e-3)

    snapshot = broker.account_snapshot()
    assert snapshot.cash == pytest.approx(100_977_000.0)
    assert snapshot.equity == pytest.approx(100_977_000.0)
    assert snapshot.realized.gross == pytest.approx(1_000_000.0)
    assert snapshot.realized.costs == pytest.approx(23_000.0)
    assert snapshot.realized.net == pytest.approx(977_000.0)
    # هزینه‌ها یک بار: کل پرداختی = ۵٬۰۰۰ + ۱۸٬۰۰۰
    assert snapshot.total_costs_recorded == pytest.approx(23_000.0)
    assert snapshot.reconciliation["ok"] is True


# ======================================================================
# سناریو ۳ — خروج جزئی: تعداد، هزینه و مانده همه درست جابه‌جا شوند
# ======================================================================
def test_scenario_partial_exit_splits_quantity_and_cost(tmp_path):
    """خرید ۱۰ در ۱۰۰۰، فروش ۴ در ۱۲۰۰، ۶ تا باز می‌ماند.

    دستی:
        کارمزد ورودِ کل = ۱۰۰۰ × ۱۰ × ۱۰۰۰ × ۰٫۰۰۱ =    ۱۰٬۰۰۰
        سهم ۴ تا        = ۱۰٬۰۰۰ × ۴ ÷ ۱۰           =     ۴٬۰۰۰
        سهم ۶ تای باقی  = ۱۰٬۰۰۰ − ۴٬۰۰۰            =     ۶٬۰۰۰
        دریافتی خروج    = ۱۲۰۰ × ۴ × ۱۰۰۰           = ۴٬۸۰۰٬۰۰۰
        کارمزد خروج     = ۴٬۸۰۰٬۰۰۰ × ۰٫۰۰۳         =    ۱۴٬۴۰۰
        ناخالصِ بسته‌شده  = ۲۰۰ × ۴ × ۱۰۰۰            =   ۸۰۰٬۰۰۰
        خالصِ بسته‌شده    = ۸۰۰٬۰۰۰ − ۴٬۰۰۰ − ۱۴٬۴۰۰   =   ۷۸۱٬۶۰۰
        نقد = ۱۰۰٬۰۰۰٬۰۰۰ − ۱۰٬۰۰۰٬۰۰۰ − ۱۰٬۰۰۰
              + ۴٬۸۰۰٬۰۰۰ − ۱۴٬۴۰۰                  = ۹۴٬۷۷۵٬۶۰۰
        ارزش روزِ ۶ تا  = ۱۲۰۰ × ۶ × ۱۰۰۰           = ۷٬۲۰۰٬۰۰۰
        ارزش حساب       = ۹۴٬۷۷۵٬۶۰۰ + ۷٬۲۰۰٬۰۰۰    = ۱۰۱٬۹۷۵٬۶۰۰
        شناور ناخالص    = ۲۰۰ × ۶ × ۱۰۰۰            = ۱٬۲۰۰٬۰۰۰
        شناور خالص      = ۱٬۲۰۰٬۰۰۰ − ۶٬۰۰۰         = ۱٬۱۹۴٬۰۰۰
    """
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=50))
    broker.place_order(SYMBOL, "buy", 10)
    broker.place_order(SYMBOL, "sell", 4)

    position = broker.store.get_position(SYMBOL)
    assert position["quantity"] == 6
    assert position["average_price"] == pytest.approx(1_000.0)
    assert position["entry_fees"] == pytest.approx(6_000.0)

    trade = broker.store.list_trades()[0]
    assert trade["quantity"] == 4
    assert trade["entry_fee"] == pytest.approx(4_000.0)
    assert trade["exit_fee"] == pytest.approx(14_400.0)
    assert trade["pnl_absolute"] == pytest.approx(781_600.0)

    snapshot = broker.account_snapshot()
    assert snapshot.cash == pytest.approx(94_775_600.0)
    assert snapshot.market_value == pytest.approx(7_200_000.0)
    assert snapshot.equity == pytest.approx(101_975_600.0)
    assert snapshot.unrealized_gross == pytest.approx(1_200_000.0)
    assert snapshot.unrealized_net == pytest.approx(1_194_000.0)
    # کارمزد ورود نه گم شد نه دو بار شمرده شد: ۴٬۰۰۰ رفت + ۶٬۰۰۰ ماند
    assert snapshot.realized.entry_costs == pytest.approx(4_000.0)
    assert snapshot.open_entry_costs == pytest.approx(6_000.0)
    assert snapshot.total_costs_recorded == pytest.approx(10_000.0 + 14_400.0)
    assert snapshot.reconciliation["ok"] is True


def test_scenario_partial_then_full_exit_never_double_counts_entry_cost(tmp_path):
    """ادامه‌ی سناریوی ۳: ۶ تای باقی‌مانده هم بسته شود.

    دستی:
        مجموع کارمزد ورودِ ثبت‌شده در دو معامله = ۴٬۰۰۰ + ۶٬۰۰۰ = ۱۰٬۰۰۰
        یعنی دقیقاً همان چیزی که یک بار پرداخت شده بود.
    """
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=50))
    broker.place_order(SYMBOL, "buy", 10)
    broker.place_order(SYMBOL, "sell", 4)
    broker.place_order(SYMBOL, "sell", 6)

    trades = broker.store.list_trades()
    assert len(trades) == 2
    assert sum(t["entry_fee"] for t in trades) == pytest.approx(10_000.0)
    assert broker.get_positions() == []

    snapshot = broker.account_snapshot()
    assert snapshot.open_entry_costs == 0.0
    assert snapshot.realized.entry_costs == pytest.approx(10_000.0)
    assert snapshot.reconciliation["ok"] is True


# ======================================================================
# سناریو ۴ — سرمایه‌ی ناکافی
# ======================================================================
def test_scenario_insufficient_funds_is_refused_and_changes_nothing(tmp_path):
    """موجودی ۳٬۰۰۰٬۰۰۰، خرید ۵ قرارداد که ۵٬۰۰۵٬۰۰۰ لازم دارد.

    باید رد شود و حساب **دست‌نخورده** بماند. رفتار قبلی سفارش را پر
    می‌کرد و نقد را منفی می‌کرد.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0), balance=3_000_000.0)

    order = broker.place_order(SYMBOL, "buy", 5)

    assert order.status.value == "rejected"
    assert "وجه قابل استفاده کافی نیست" in order.metadata["reason"]
    assert broker.get_account_balance()["cash"] == pytest.approx(3_000_000.0)
    assert broker.get_positions() == []


def test_scenario_affordable_edge_is_accepted(tmp_path):
    """درست به اندازه‌ی لازم: ۲ قرارداد = ۲٬۰۰۰٬۰۰۰ + ۲٬۰۰۰ کارمزد."""
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0), balance=2_002_000.0)

    order = broker.place_order(SYMBOL, "buy", 2)

    assert order.status.value == "filled"
    assert broker.get_account_balance()["cash"] == pytest.approx(0.0)


def test_cash_never_goes_negative_across_repeated_buys(tmp_path):
    """خریدهای پیاپی تا جایی که وجه تمام شود؛ نقد نباید زیر صفر برود."""
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0, qty=1_000), balance=10_000_000.0)

    for _ in range(20):
        broker.place_order(SYMBOL, "buy", 3)

    assert broker.get_account_balance()["cash"] >= 0.0


# ======================================================================
# سناریو ۵ — قیمت ناموجود: هرگز صفر
# ======================================================================
def test_scenario_missing_price_is_not_zero_and_makes_equity_unknown(tmp_path):
    """دفتر سفارش در دسترس نیست.

    ارزش روز `None` است، نه صفر؛ و ارزش کل حساب «نامشخص» اعلام می‌شود.
    `equity_priced_part` (= نقد) هم جدا هست تا صفحه خالی نماند، ولی
    «ارزش کل حساب» نیست.
    """
    contract = _contract()
    store = PaperTradingStore(tmp_path / "paper.db")
    books: dict[str, OrderBook | None] = {INS_CODE: _book(bid=950.0, ask=1_000.0)}
    broker = PaperBroker(
        store=store,
        order_book_client=FakeBooks(books),
        resolve_contract=lambda s: contract if s == SYMBOL else None,
        initial_balance=INITIAL,
        fees=FEES,
    )
    broker.place_order(SYMBOL, "buy", 5)
    books[INS_CODE] = None  # دفتر از دسترس خارج شد

    snapshot = broker.account_snapshot()
    valuation = snapshot.positions[0]

    assert valuation.status is ValuationStatus.UNAVAILABLE
    assert valuation.market_value is None
    assert valuation.unrealized_gross is None
    assert snapshot.valuation_complete is False
    assert snapshot.market_value is None
    assert snapshot.equity is None
    assert snapshot.equity_priced_part == pytest.approx(94_995_000.0)
    assert snapshot.total_return_pct is None
    assert snapshot.reconciliation["applicable"] is False


def test_empty_bid_side_is_reported_not_priced_at_zero(tmp_path):
    """دفتر هست ولی سمت خرید خالی است — قیمت خروج وجود ندارد."""
    broker = _broker(tmp_path, _book(bid=None, ask=1_000.0))
    broker.place_order(SYMBOL, "buy", 5)

    valuation = broker.value_positions()[0]
    assert valuation.status is ValuationStatus.NO_DEPTH
    assert valuation.market_value is None


def test_insufficient_exit_depth_is_not_marked_at_a_partial_price(tmp_path):
    """عمق فقط ۲ تا از ۵ تا را می‌خرد.

    قیمتِ میانگینِ یک خروجِ ناقص، ارزشِ کل موقعیت نیست. عدد مرجع داده
    می‌شود ولی در جمعِ حساب نمی‌آید.
    """
    contract = _contract()
    store = PaperTradingStore(tmp_path / "paper.db")
    books: dict[str, OrderBook | None] = {INS_CODE: _book(bid=950.0, ask=1_000.0, qty=100)}
    broker = PaperBroker(
        store=store,
        order_book_client=FakeBooks(books),
        resolve_contract=lambda s: contract if s == SYMBOL else None,
        initial_balance=INITIAL,
        fees=FEES,
    )
    broker.place_order(SYMBOL, "buy", 5)
    books[INS_CODE] = _book(bid=950.0, ask=1_000.0, qty=2)

    valuation = broker.value_positions()[0]
    assert valuation.status is ValuationStatus.PARTIAL_DEPTH
    assert valuation.fillable_quantity == 2
    assert valuation.market_value is None
    assert valuation.reference_price == 950.0


# ======================================================================
# سناریو ۶ — هزینه‌ی مشخص‌نشده
# ======================================================================
def test_unset_rates_make_the_cost_unknown_not_zero(tmp_path):
    """صفرِ **پیش‌فرضِ** نرخ، هزینه‌ی دانسته نیست.

    پیش از این، معامله‌ای که با `FeeSchedule()` انجام می‌شد کارمزدش صفر
    ثبت می‌شد و همان صفر «دانسته» خوانده می‌شد — پس «خالص» عددِ قطعی
    می‌گرفت در حالی که هیچ نرخی وارد نشده بود.
    """
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0), fees=FeeSchedule())
    broker.place_order(SYMBOL, "buy", 5)

    snapshot = broker.account_snapshot()
    valuation = snapshot.positions[0]

    assert snapshot.rates_configured is False
    assert valuation.entry_fees_known is False
    assert valuation.unrealized_gross == pytest.approx(1_000_000.0), "ناخالص معلوم است"
    assert valuation.unrealized_net is None, "خالص نه"
    assert snapshot.costs_known is False
    assert snapshot.unrealized_net is None
    assert snapshot.equity is not None, "ارزش حساب به هزینه وابسته نیست"


def test_unset_rates_keep_both_fees_unknown_through_a_close(tmp_path):
    """خروج هم همان قاعده را دارد: کارمزد خروجِ نامعلوم، `NULL` می‌ماند."""
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0), fees=FeeSchedule())
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)

    trade = broker.store.list_trades()[0]
    assert trade["entry_fee"] is None
    assert trade["exit_fee"] is None
    assert trade["gross_pnl"] == pytest.approx(1_000_000.0), "ناخالص درست است"
    assert trade["return_on_cost_pct"] is None

    snapshot = broker.account_snapshot()
    assert snapshot.costs_known is False
    assert snapshot.realized.net is None
    assert snapshot.realized.gross == pytest.approx(1_000_000.0)
    assert snapshot.realized.trades_missing_entry_cost == 1
    assert snapshot.realized.trades_missing_exit_cost == 1


def test_unset_rates_keep_the_cost_unknown_through_a_partial_exit(tmp_path):
    """خروج جزئی هم نامعلومی را نگه می‌دارد، هم روی معامله و هم روی مانده."""
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=50), fees=FeeSchedule())
    broker.place_order(SYMBOL, "buy", 10)
    broker.place_order(SYMBOL, "sell", 4)

    trade = broker.store.list_trades()[0]
    assert trade["entry_fee"] is None and trade["exit_fee"] is None
    assert broker.store.get_position(SYMBOL)["entry_fees_known"] == 0
    assert broker.account_snapshot().costs_known is False


def test_an_explicitly_declared_zero_rate_is_a_known_cost(tmp_path):
    """صفرِ **اعلام‌شده** با صفرِ پیش‌فرض یکی نیست.

    اگر کاربر بگوید «کارمزد من واقعاً صفر است»، هزینه دانسته است و خالص
    عددِ قطعی می‌گیرد. بدون این تفکیک، حسابِ بی‌کارمزد هرگز نمی‌توانست
    خالص نشان بدهد.
    """
    broker = _broker(
        tmp_path, _book(bid=1_200.0, ask=1_000.0), fees=FeeSchedule(declared=True)
    )
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)

    snapshot = broker.account_snapshot()
    trade = broker.store.list_trades()[0]

    assert snapshot.rates_configured is True
    assert snapshot.costs_known is True
    assert trade["entry_fee"] == 0.0 and trade["exit_fee"] == 0.0
    assert snapshot.realized.net == pytest.approx(1_000_000.0)
    assert snapshot.reconciliation["ok"] is True


def test_setting_a_rate_later_does_not_make_past_costs_known(tmp_path):
    """تنظیم نرخ بعدی نباید سابقه‌ی نامعلوم را معلوم کند.

    پرچمِ «دانسته بودن» هنگام **همان عملیات** روی ردیف می‌نشیند، پس
    تغییر بعدیِ تنظیمات چیزی را بازنویسی نمی‌کند.
    """
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=100), fees=FeeSchedule())
    broker.place_order(SYMBOL, "buy", 5)  # زیر نرخِ نامعلوم

    # کاربر حالا نرخ واقعی را وارد می‌کند.
    broker.fees = FEES

    assert broker.rates_configured() is True
    assert broker.account_snapshot().costs_known is False, "سابقه نباید معلوم شود"

    # خریدِ تازه دانسته است، ولی جمعِ موقعیت همچنان نامعلوم می‌ماند.
    broker.place_order(SYMBOL, "buy", 5)
    assert broker.store.get_position(SYMBOL)["entry_fees_known"] == 0


def test_a_later_close_of_an_unknown_position_stays_unknown(tmp_path):
    """حتی با نرخِ تازه، بستنِ موقعیتِ نامعلوم خالصِ قطعی نمی‌دهد."""
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=100), fees=FeeSchedule())
    broker.place_order(SYMBOL, "buy", 5)
    broker.fees = FEES
    broker.place_order(SYMBOL, "sell", 5)

    trade = broker.store.list_trades()[0]
    assert trade["entry_fee"] is None, "ورودش زیر نرخِ نامعلوم بود"
    assert trade["exit_fee"] == pytest.approx(18_000.0), "خروجش دانسته است"
    assert broker.account_snapshot().costs_known is False


# ======================================================================
# سناریو ۷ — اتمیک بودن
# ======================================================================
def test_a_failure_mid_operation_leaves_no_half_changed_account(tmp_path):
    """اگر ثبتِ معامله بشکند، نقد و موقعیت هم نباید تغییر کرده باشند.

    بدون تراکنش، `update_cash` زودتر commit می‌شد و حساب با پولِ
    کم‌شده ولی موقعیتِ دست‌نخورده می‌ماند — خرابیِ خاموشی که فقط در
    تطبیق دیده می‌شد.
    """
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0))
    broker.place_order(SYMBOL, "buy", 5)

    cash_before = broker.get_account_balance()["cash"]
    position_before = broker.store.get_position(SYMBOL)

    def boom(*args, **kwargs):
        raise RuntimeError("دیسک پر")

    broker.store.record_trade = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="دیسک پر"):
        broker.place_order(SYMBOL, "sell", 5)

    assert broker.get_account_balance()["cash"] == pytest.approx(cash_before)
    assert broker.store.get_position(SYMBOL) == position_before
    assert broker.store.list_trades() == []


def test_a_failed_settlement_leaves_the_position_untouched(tmp_path):
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0), expiry_days=-1)
    # موقعیت را پیش از سررسید باز می‌کنیم تا خریدْ رد نشود.
    live = _contract(expiry_days=30)
    expired = _contract(expiry_days=-1)
    broker.resolve_contract = lambda s: live if s == SYMBOL else None
    broker.place_order(SYMBOL, "buy", 4)
    broker.resolve_contract = lambda s: expired if s == SYMBOL else None

    cash_before = broker.get_account_balance()["cash"]
    broker.store.record_trade = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("خراب"))
    with pytest.raises(RuntimeError):
        broker.settle_position(SYMBOL, settlement_price=500.0)

    assert broker.get_account_balance()["cash"] == pytest.approx(cash_before)
    assert broker.store.get_position(SYMBOL)["quantity"] == 4


# ======================================================================
# سناریو ۸ — افت حساب: از قله‌ی مرتبط، نه از سرمایه‌ی اولیه
# ======================================================================
def _seed_trades(store, pnls, initial=INITIAL):
    """چند معامله‌ی بسته‌شده‌ی ساختگی با سود/زیانِ معین و زمانِ صعودی.

    اینجا عمداً مستقیم در پایگاه نوشته می‌شود، نه از مسیر سفارش: هدف
    سنجیدنِ **منحنی افت** است و ساختن هر عدد از راه دفتر سفارش، تست را
    به چیزی که موضوعش نیست گره می‌زد.
    """
    store.init_account(initial)
    for index, pnl in enumerate(pnls, start=1):
        store.record_trade({
            "trade_id": f"t{index}",
            "symbol": SYMBOL,
            "quantity": 1,
            "entry_price": 1_000.0,
            "exit_price": 1_000.0,
            "fee_paid": 0.0,
            "entry_fee": 0.0,
            "exit_fee": 0.0,
            "gross_pnl": pnl,
            "pnl_absolute": pnl,
            "pnl_pct": 0.0,
            "return_on_cost_pct": 0.0,
            "contract_size": CONTRACT_SIZE,
            "opened_at": f"2026-01-{index:02d}T09:00:00",
            "closed_at": f"2026-01-{index:02d}T12:00:00",
            "signal_id": None,
            "close_reason": "manual",
        })


def test_drawdown_percentage_is_measured_from_the_relevant_peak(tmp_path):
    """مثال پذیرش: ۱۰۰ → ۲۰۰ → ۱۵۰.

    افتِ مبلغی ۵۰ است و افتِ درصدی **۲۵٪** (۵۰ از قله‌ی ۲۰۰)، نه ۵۰٪
    که نسبت به سرمایه‌ی اولیه‌ی ۱۰۰ در می‌آمد. نسبت‌دادن به سرمایه‌ی
    اولیه، افتِ یک حسابِ رشدکرده را کوچک‌تر از واقع نشان می‌دهد.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0), balance=100.0)
    _seed_trades(broker.store, [100.0, -50.0], initial=100.0)

    drawdown = broker.realized_drawdown()

    assert drawdown["opening_balance"] == pytest.approx(100.0)
    assert drawdown["max_drawdown_currency"] == pytest.approx(50.0)
    assert drawdown["max_drawdown_pct"] == pytest.approx(25.0)
    assert drawdown["peak_relative"] is True
    assert drawdown["basis"] == "realized_closed_trades"


def test_biggest_currency_and_percent_drawdowns_can_be_different_points(tmp_path):
    """بیشترین افتِ مبلغی و درصدی لزوماً یک‌جا نیستند.

    مسیر: ۱۰۰ → ۱۲۰ → ۶۰ → ۳۶۰ → ۲۶۰.
        افت اول: از قله‌ی ۱۲۰ به ۶۰  → مبلغی ۶۰،  درصدی ۵۰٪
        افت دوم: از قله‌ی ۳۶۰ به ۲۶۰ → مبلغی ۱۰۰، درصدی ۲۷٫۷۸٪

    پس بیشترین مبلغی ۱۰۰ (در گام آخر) و بیشترین درصدی ۵۰٪ (در گام دوم)
    است. گزارشِ یک عدد به‌جای هر دو، یکی از این دو را پنهان می‌کرد.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0), balance=100.0)
    _seed_trades(broker.store, [20.0, -60.0, 300.0, -100.0], initial=100.0)

    drawdown = broker.realized_drawdown()

    assert drawdown["max_drawdown_currency"] == pytest.approx(100.0)
    assert drawdown["max_drawdown_currency_at"] == "2026-01-04T12:00:00"
    assert drawdown["max_drawdown_pct"] == pytest.approx(50.0)
    assert drawdown["max_drawdown_pct_at"] == "2026-01-02T12:00:00"
    assert drawdown["max_drawdown_currency_at"] != drawdown["max_drawdown_pct_at"]


def test_windowed_drawdown_starts_from_the_balance_at_the_window_start(tmp_path):
    """با فیلتر بازه، منحنی از مانده‌ی ابتدای همان بازه شروع می‌شود.

    دو معامله‌ی قدیمی (+۹۰۰ و +۱۰۰) حساب را از ۱۰۰ به ۱۱۰۰ می‌برند و
    بیرون بازه‌اند. داخل بازه یک زیانِ ۱۱۰ هست. افت باید ۱۱۰ از قله‌ی
    ۱۱۰۰ باشد (۱۰٪) — نه چیزی که از سرمایه‌ی اولیه‌ی ۱۰۰ حساب شود، که
    عددی بی‌معنا (۱۱۰٪) می‌داد.
    """
    from datetime import datetime, timedelta

    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0), balance=100.0)
    store = broker.store
    store.init_account(100.0)
    old = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    recent = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")

    def add(trade_id, pnl, closed_at):
        store.record_trade({
            "trade_id": trade_id, "symbol": SYMBOL, "quantity": 1,
            "entry_price": 1_000.0, "exit_price": 1_000.0, "fee_paid": 0.0,
            "entry_fee": 0.0, "exit_fee": 0.0, "gross_pnl": pnl,
            "pnl_absolute": pnl, "pnl_pct": 0.0, "return_on_cost_pct": 0.0,
            "contract_size": CONTRACT_SIZE, "opened_at": closed_at,
            "closed_at": closed_at, "signal_id": None, "close_reason": "manual",
        })

    add("old1", 900.0, old)
    add("old2", 100.0, old)
    add("new1", -110.0, recent)

    windowed = broker.realized_drawdown(days=7)

    assert windowed["opening_balance"] == pytest.approx(1_100.0)
    assert windowed["max_drawdown_currency"] == pytest.approx(110.0)
    assert windowed["max_drawdown_pct"] == pytest.approx(10.0)

    # بدون فیلتر، همان حساب از ۱۰۰ شروع می‌شود و افتش همان ۱۱۰ است،
    # ولی نسبت به قله‌ی ۱۱۰۰ — نه به ۱۰۰.
    full = broker.realized_drawdown()
    assert full["opening_balance"] == pytest.approx(100.0)
    assert full["max_drawdown_pct"] == pytest.approx(10.0)


def test_a_rising_account_reports_no_drawdown(tmp_path):
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0), balance=100.0)
    _seed_trades(broker.store, [10.0, 20.0, 30.0], initial=100.0)

    drawdown = broker.realized_drawdown()

    assert drawdown["max_drawdown_currency"] == 0.0
    assert drawdown["max_drawdown_pct"] == 0.0
    assert drawdown["max_drawdown_currency_at"] is None


def test_performance_summary_keeps_account_and_signal_metrics_apart(tmp_path):
    """افتِ حساب و معیارِ سطحِ سیگنال دو چیزند و قاطی نمی‌شوند."""
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=100))
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)

    summary = broker.performance_summary()

    assert summary["account_drawdown"] == broker.realized_drawdown()
    assert summary["account_drawdown"]["basis"] == "realized_closed_trades"
    assert "signal_level_note" in summary
    # معیارِ سطحِ سیگنال هنوز سر جایش است و جای افتِ حساب را نمی‌گیرد.
    assert "max_drawdown_pct" in summary


# ======================================================================
# سناریو ۹ — تطبیق روی یک دنباله‌ی مخلوط
# ======================================================================
def test_reconciliation_holds_through_a_mixed_sequence(tmp_path):
    """چند خرید و فروشِ جزئی و کامل؛ اتحادِ تطبیق باید برقرار بماند."""
    contract = _contract()
    store = PaperTradingStore(tmp_path / "paper.db")
    books: dict[str, OrderBook | None] = {INS_CODE: _book(bid=950.0, ask=1_000.0, qty=100)}
    broker = PaperBroker(
        store=store,
        order_book_client=FakeBooks(books),
        resolve_contract=lambda s: contract if s == SYMBOL else None,
        initial_balance=INITIAL,
        fees=FEES,
    )

    broker.place_order(SYMBOL, "buy", 10)
    books[INS_CODE] = _book(bid=1_150.0, ask=1_200.0, qty=100)
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 7)
    books[INS_CODE] = _book(bid=900.0, ask=1_000.0, qty=100)
    broker.place_order(SYMBOL, "sell", 3)

    snapshot = broker.account_snapshot()
    reconciliation = snapshot.reconciliation

    assert reconciliation["applicable"] is True
    assert reconciliation["ok"] is True, reconciliation
    assert snapshot.equity == pytest.approx(
        snapshot.initial_balance
        + snapshot.realized.net
        + snapshot.unrealized_net
    )


# ======================================================================
# رگرسیون بازبینی دوم PR #5
# ======================================================================
def test_settlement_rejects_non_finite_prices(tmp_path):
    """`inf` و `nan` باید در لایه‌ی مالی رد شوند، نه اینکه پخش شوند.

    `inf` نقد را بی‌نهایت می‌کند و `nan` هر مقایسه‌ای را `False` — یعنی
    کنترل‌های بعدی بی‌صدا از کار می‌افتند. خرابیِ فوری بهتر است.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0))
    broker.place_order(SYMBOL, "buy", 4)
    expired = _contract(expiry_days=-1)
    broker.resolve_contract = lambda s: expired if s == SYMBOL else None
    cash_before = broker.get_account_balance()["cash"]

    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="متناهی"):
            broker.settle_position(SYMBOL, settlement_price=bad)

    assert broker.get_account_balance()["cash"] == pytest.approx(cash_before)
    assert broker.store.get_position(SYMBOL)["quantity"] == 4


def test_settlement_price_is_per_unit_not_per_contract(tmp_path):
    """واحدِ ورودی همان مبنای بقیه‌ی قیمت‌هاست: پرمیوم هر واحد.

    دستی: تسویه با ۵۰۰ روی ۴ قرارداد × اندازه ۱۰۰۰
        دریافتی = ۵۰۰ × ۴ × ۱۰۰۰ = ۲٬۰۰۰٬۰۰۰
        کارمزد خروج = ۲٬۰۰۰٬۰۰۰ × ۰٫۰۰۳ = ۶٬۰۰۰
    اگر عدد «قیمت هر قرارداد» تفسیر می‌شد، دریافتی ۲٬۰۰۰ می‌شد —
    هزار برابر کمتر.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0))
    broker.place_order(SYMBOL, "buy", 4)
    expired = _contract(expiry_days=-1)
    broker.resolve_contract = lambda s: expired if s == SYMBOL else None
    cash_before = broker.get_account_balance()["cash"]

    trade = broker.settle_position(SYMBOL, settlement_price=500.0)

    assert trade["exit_price"] == 500.0
    assert trade["contract_size"] == CONTRACT_SIZE
    assert trade["exit_fee"] == pytest.approx(6_000.0)
    assert broker.get_account_balance()["cash"] == pytest.approx(
        cash_before + 2_000_000.0 - 6_000.0
    )


@pytest.mark.parametrize("side", ["buy", "sell"])
def test_orders_on_an_expired_contract_are_refused(tmp_path, side):
    """سفارش عادی روی قرارداد گذشته از سررسید، حتی با دفتر سفارشِ موجود.

    منبع ممکن است هنوز برای نماد سررسیدشده مظنه بدهد؛ پر کردنِ سفارش
    یعنی حسابی که با قراردادی معامله کرده که دیگر وجود ندارد.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0, qty=100))
    broker.place_order(SYMBOL, "buy", 4)  # موقعیتِ باز، پیش از سررسید
    expired = _contract(expiry_days=-1)
    broker.resolve_contract = lambda s: expired if s == SYMBOL else None

    cash_before = broker.get_account_balance()["cash"]
    position_before = broker.store.get_position(SYMBOL)

    order = broker.place_order(SYMBOL, side, 2)

    assert order.status.value == "rejected"
    assert "سررسید" in order.metadata["reason"]
    assert broker.get_account_balance()["cash"] == pytest.approx(cash_before)
    assert broker.store.get_position(SYMBOL) == position_before
    assert broker.store.list_trades() == [], "معامله‌ای نباید ثبت شده باشد"


def test_the_manual_path_still_works_for_an_expired_contract(tmp_path):
    """مسیر تعیین تکلیف دستی از سفارش عادی جداست و بسته نمی‌شود."""
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0))
    broker.place_order(SYMBOL, "buy", 4)
    expired = _contract(expiry_days=-1)
    broker.resolve_contract = lambda s: expired if s == SYMBOL else None

    assert broker.place_order(SYMBOL, "sell", 4).status.value == "rejected"
    trade = broker.settle_position(SYMBOL, settlement_price=0.0)

    assert trade["exit_price"] == 0.0
    assert broker.get_positions() == []


def test_todays_rate_does_not_make_yesterdays_unknown_cost_known(tmp_path):
    """تنظیم نرخ امروز نباید هزینه‌ی نامعلومِ معامله‌ی قدیمی را معلوم کند.

    معامله‌ی مهاجرت‌شده `entry_fee IS NULL` دارد. با نرخِ **تنظیم‌شده**،
    رفتار قبلی `costs_known` را `True` می‌کرد و «خالص»ی نشان می‌داد که
    یک جزء ناشناخته داشت.
    """
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0))
    broker.store.record_trade({
        "trade_id": "legacy", "symbol": SYMBOL, "quantity": 1,
        "entry_price": 1_000.0, "exit_price": 1_200.0, "fee_paid": 30.0,
        "entry_fee": None, "exit_fee": 30.0, "gross_pnl": 200_000.0,
        "pnl_absolute": 199_970.0, "pnl_pct": 0.0, "return_on_cost_pct": None,
        "contract_size": CONTRACT_SIZE, "opened_at": "2026-01-01T09:00:00",
        "closed_at": "2026-01-02T12:00:00", "signal_id": None,
        "close_reason": "manual",
    })

    snapshot = broker.account_snapshot()

    assert snapshot.rates_configured is True, "نرخ امروز تنظیم شده است"
    assert snapshot.costs_known is False, "ولی هزینه‌ی آن معامله دانسته نیست"
    assert snapshot.realized.net is None, "خالصِ قطعی نباید داده شود"
    # ناخالص و هزینه‌های ثبت‌شده همچنان قابل نمایش‌اند.
    assert snapshot.realized.gross == pytest.approx(200_000.0)
    assert snapshot.realized.costs == pytest.approx(30.0)
    assert snapshot.reconciliation["applicable"] is False


def test_a_migrated_position_never_reports_a_confident_net(tmp_path):
    """کارمزد ورودِ نامعلومِ یک موقعیت، صفرِ قطعی فرض نمی‌شود."""
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=100))
    broker.store.upsert_position(
        SYMBOL, 5, 1_000.0, "2026-01-01T09:00:00",
        entry_fees=0.0, entry_fees_known=False,
    )

    snapshot = broker.account_snapshot()
    valuation = snapshot.positions[0]

    assert valuation.entry_fees_known is False
    assert valuation.unrealized_gross == pytest.approx(1_000_000.0), "ناخالص معلوم است"
    assert valuation.unrealized_net is None, "خالص نه"
    assert valuation.cost_basis is None
    assert snapshot.unrealized_net is None
    assert snapshot.costs_known is False
    # ارزش کل حساب به هزینه وابسته نیست و همچنان عدد دارد.
    assert snapshot.equity is not None
    assert snapshot.reconciliation["applicable"] is False


def test_closing_a_migrated_position_keeps_the_cost_unknown(tmp_path):
    """سهمی از عددی که دانسته نیست، خودش هم دانسته نیست."""
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0, qty=100))
    broker.store.upsert_position(
        SYMBOL, 10, 1_000.0, "2026-01-01T09:00:00",
        entry_fees=0.0, entry_fees_known=False,
    )

    broker.place_order(SYMBOL, "sell", 4)

    trade = broker.store.list_trades()[0]
    assert trade["entry_fee"] is None
    assert trade["gross_pnl"] == pytest.approx(800_000.0)
    # موقعیتِ باقی‌مانده هم همچنان نامعلوم می‌ماند.
    assert broker.store.get_position(SYMBOL)["entry_fees_known"] == 0
    assert broker.account_snapshot().costs_known is False


def test_buying_into_a_migrated_position_keeps_the_cost_unknown(tmp_path):
    """خرید تازه روی موقعیتِ نامعلوم، جمع را معلوم نمی‌کند."""
    broker = _broker(tmp_path, _book(bid=950.0, ask=1_000.0, qty=100))
    broker.store.upsert_position(
        SYMBOL, 5, 1_000.0, "2026-01-01T09:00:00",
        entry_fees=0.0, entry_fees_known=False,
    )

    broker.place_order(SYMBOL, "buy", 5)

    assert broker.store.get_position(SYMBOL)["entry_fees_known"] == 0
    assert broker.account_snapshot().costs_known is False

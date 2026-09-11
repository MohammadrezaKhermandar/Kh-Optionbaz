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
    assert snapshot.total_costs_paid == pytest.approx(23_000.0)
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
    assert snapshot.total_costs_paid == pytest.approx(10_000.0 + 14_400.0)
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
def test_costs_unknown_when_no_rate_is_configured(tmp_path):
    """با نرخ صفر، «خالص» فقط تکرارِ «ناخالص» است و باید علامت بخورد."""
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0), fees=FeeSchedule())
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)

    snapshot = broker.account_snapshot()
    assert snapshot.costs_known is False
    assert snapshot.realized.gross == pytest.approx(1_000_000.0)
    assert snapshot.realized.costs == 0.0


def test_costs_known_once_a_rate_is_configured(tmp_path):
    broker = _broker(tmp_path, _book(bid=1_200.0, ask=1_000.0))
    assert broker.account_snapshot().costs_known is True


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
# سناریو ۸ — افت حساب از ریال، نه از جمع درصدها
# ======================================================================
def test_account_drawdown_is_measured_in_currency_not_summed_percentages(tmp_path):
    """دو معامله: اولی +۹۷۷٬۰۰۰ و دومی زیان‌ده.

    افتِ حساب باید از منحنیِ ریالیِ حساب بیرون بیاید. جمعِ درصدها
    اندازه‌ی موقعیت را نمی‌بیند و عددش با افتِ واقعیِ سرمایه نسبتی
    ندارد.
    """
    contract = _contract()
    store = PaperTradingStore(tmp_path / "paper.db")
    books: dict[str, OrderBook | None] = {INS_CODE: _book(bid=1_200.0, ask=1_000.0, qty=100)}
    broker = PaperBroker(
        store=store,
        order_book_client=FakeBooks(books),
        resolve_contract=lambda s: contract if s == SYMBOL else None,
        initial_balance=INITIAL,
        fees=FEES,
    )
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)  # سود

    books[INS_CODE] = _book(bid=400.0, ask=1_000.0, qty=100)
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)  # زیان

    drawdown = broker.realized_drawdown()
    assert drawdown["basis"] == "realized_closed_trades"
    assert drawdown["max_drawdown_currency"] > 0
    # درصد افت نسبت به سرمایه‌ی اولیه است، نه جمع درصد معاملات.
    assert drawdown["max_drawdown_pct"] == pytest.approx(
        drawdown["max_drawdown_currency"] / INITIAL * 100.0
    )

    summary = broker.performance_summary()
    assert summary["account_drawdown"] == drawdown
    assert "signal_level_note" in summary


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

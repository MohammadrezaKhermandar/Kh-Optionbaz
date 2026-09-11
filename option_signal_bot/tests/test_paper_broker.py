"""تست‌های کارگزار کاغذی (`PaperBroker`).

همه‌ی داده‌های عمق و قرارداد **ساخته‌شده به‌صورت صریح در تست** هستند
(نه پاسخ TSETMC ضبط‌شده)، چون این‌جا خودِ منطق fill/کارمزد/میانگین‌گیری
پوزیشن سنجیده می‌شود، نه پارسر پاسخ واقعی — همان الگویی که
`tests/test_order_book.py` برای `OrderBook.fill_price` به کار می‌برد.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from data.option_chain_client import OptionContract
from data.order_book import BookLevel, OrderBook
from execution.order_executor_interface import OrderStatus
from execution.paper_account import ValuationStatus
from execution.paper_broker import CLOSE_REASON_EXPIRY, CLOSE_REASON_MANUAL, PaperBroker
from risk.fees import FeeSchedule
from storage.paper_trading_store import PaperTradingStore

SYMBOL = "ضخود7001"
INS_CODE = "12345"

#: موجودی اولیه‌ی تست‌ها. با اندازه‌ی قرارداد ۱۰۰۰ و پرمیوم ۱۰۰۰، هر
#: قرارداد یک میلیون ریال است؛ این عدد آن‌قدر هست که سناریوهای عادی به
#: کنترلِ کفایتِ وجه نخورند. تست‌های خودِ آن کنترل، عمداً عدد کوچک
#: می‌دهند.
INITIAL_BALANCE = 100_000_000.0


class FakeOrderBookClient:
    """کلاینت جعلیِ عمق مظنه — دفتر ثابت برای هر ins_code."""

    def __init__(self, books: dict[str, OrderBook]) -> None:
        self.books = books

    def get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook:
        del symbol
        return self.books[ins_code]

    def try_get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook | None:
        return self.books.get(ins_code)


def _contract(expiry_days: int = 30, last_price: float = 1000.0) -> OptionContract:
    return OptionContract(
        symbol=SYMBOL,
        underlying="خودرو",
        option_type="call",
        strike=2000.0,
        expiry=date.today() + timedelta(days=expiry_days),
        last_price=last_price,
        contract_size=1_000,
        ins_code=INS_CODE,
    )


def _broker(tmp_path, book: OrderBook, contract: OptionContract | None = None, fees=None):
    store = PaperTradingStore(tmp_path / "paper.db")
    contract = contract or _contract()
    resolve_contract = lambda symbol: contract if symbol == SYMBOL else None  # noqa: E731
    order_book_client = FakeOrderBookClient({INS_CODE: book})
    return PaperBroker(
        store=store,
        order_book_client=order_book_client,
        resolve_contract=resolve_contract,
        initial_balance=INITIAL_BALANCE,
        fees=fees,
    )


def _deep_book(price: float = 1000.0, qty: int = 100) -> OrderBook:
    return OrderBook(
        SYMBOL,
        bids=(BookLevel(price - 10, qty),),
        asks=(BookLevel(price, qty),),
    )


# ---------------------------------------------------------------------------
def test_full_fill_at_best_ask_when_depth_is_sufficient(tmp_path):
    broker = _broker(tmp_path, _deep_book(price=1000.0, qty=100))
    order = broker.place_order(SYMBOL, "buy", 5)

    assert order.status == OrderStatus.FILLED
    assert order.filled_quantity == 5
    assert order.price == 1000.0


def test_partial_fill_when_depth_is_insufficient(tmp_path):
    book = OrderBook(SYMBOL, asks=(BookLevel(1000.0, 3),))
    broker = _broker(tmp_path, book)
    order = broker.place_order(SYMBOL, "buy", 10)

    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 3
    assert order.price == 1000.0


def test_rejected_when_book_has_zero_depth(tmp_path):
    broker = _broker(tmp_path, OrderBook(SYMBOL))
    order = broker.place_order(SYMBOL, "buy", 5)

    assert order.status == OrderStatus.REJECTED
    assert order.filled_quantity == 0


def test_rejected_when_order_book_is_unavailable(tmp_path):
    """خطای شبکه در گرفتن عمق باید رد سفارش بدهد، نه استثنا/۵۰۰."""
    store = PaperTradingStore(tmp_path / "paper.db")
    contract = _contract()
    resolve_contract = lambda symbol: contract if symbol == SYMBOL else None  # noqa: E731
    broker = PaperBroker(
        store=store,
        order_book_client=FakeOrderBookClient({}),  # ins_code هیچ‌وقت پیدا نمی‌شود
        resolve_contract=resolve_contract,
        initial_balance=INITIAL_BALANCE,
    )
    order = broker.place_order(SYMBOL, "buy", 5)

    assert order.status == OrderStatus.REJECTED
    assert "دفتر سفارش" in order.metadata["reason"]


def test_rejected_when_symbol_unknown(tmp_path):
    broker = _broker(tmp_path, _deep_book())
    order = broker.place_order("نامعتبر", "buy", 5)

    assert order.status == OrderStatus.REJECTED
    assert "یافت نشد" in order.metadata["reason"]


def test_rejected_when_selling_without_a_position(tmp_path):
    broker = _broker(tmp_path, _deep_book())
    order = broker.place_order(SYMBOL, "sell", 1)

    assert order.status == OrderStatus.REJECTED
    assert "long تک‌پایه" in order.metadata["reason"]


def test_buy_reduces_cash_by_notional_and_fee(tmp_path):
    fees = FeeSchedule(buy_rate=0.001)
    broker = _broker(tmp_path, _deep_book(price=1000.0, qty=100), fees=fees)
    broker.place_order(SYMBOL, "buy", 2)

    notional = 1000.0 * 2 * 1_000
    expected_fee = notional * 0.001
    balance = broker.get_account_balance()
    assert balance["cash"] == pytest.approx(INITIAL_BALANCE - notional - expected_fee)


def test_repeated_buys_average_the_position_price(tmp_path):
    """دو سفارش پشت‌سرهم، هرکدام روی عمق (و بنابراین قیمت) متفاوت خودشان."""
    store = PaperTradingStore(tmp_path / "paper.db")
    contract = _contract()
    resolve_contract = lambda symbol: contract if symbol == SYMBOL else None  # noqa: E731
    order_book_client = FakeOrderBookClient(
        {INS_CODE: OrderBook(SYMBOL, asks=(BookLevel(1000.0, 5),))}
    )
    broker = PaperBroker(
        store=store,
        order_book_client=order_book_client,
        resolve_contract=resolve_contract,
        initial_balance=INITIAL_BALANCE,
    )

    broker.place_order(SYMBOL, "buy", 5)  # همه در ۱۰۰۰ پر می‌شود
    order_book_client.books[INS_CODE] = OrderBook(SYMBOL, asks=(BookLevel(1200.0, 5),))
    broker.place_order(SYMBOL, "buy", 5)  # همه در ۱۲۰۰ پر می‌شود

    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].quantity == 10
    assert positions[0].average_price == pytest.approx(1100.0)


def test_closing_sell_realizes_pnl_and_frees_position(tmp_path):
    book = OrderBook(
        SYMBOL,
        bids=(BookLevel(1100.0, 10),),
        asks=(BookLevel(1000.0, 10),),
    )
    broker = _broker(tmp_path, book)
    broker.place_order(SYMBOL, "buy", 5)
    broker.place_order(SYMBOL, "sell", 5)

    assert broker.get_positions() == []
    summary = broker.performance_summary()
    assert summary["total"] == 1
    assert summary["wins"] == 1


def test_sell_cannot_exceed_held_quantity(tmp_path):
    book = OrderBook(
        SYMBOL,
        bids=(BookLevel(1100.0, 10),),
        asks=(BookLevel(1000.0, 10),),
    )
    broker = _broker(tmp_path, book)
    broker.place_order(SYMBOL, "buy", 3)
    order = broker.place_order(SYMBOL, "sell", 5)

    assert order.status == OrderStatus.REJECTED
    assert broker.get_positions()[0].quantity == 3


def test_valuation_uses_the_exit_side_book(tmp_path):
    book = OrderBook(
        SYMBOL,
        bids=(BookLevel(1200.0, 10),),
        asks=(BookLevel(1000.0, 10),),
    )
    broker = _broker(tmp_path, book)
    broker.place_order(SYMBOL, "buy", 5)

    valuation = broker.value_positions()[0]
    assert valuation.status is ValuationStatus.OK
    assert valuation.mark_price == 1200.0
    assert valuation.market_value == pytest.approx(1200.0 * 5 * 1_000)
    assert valuation.unrealized_gross == pytest.approx(200.0 * 5 * 1_000)


def test_expiry_is_never_settled_automatically(tmp_path):
    """سررسید نباید خودکار با آخرین پرمیومِ معامله‌شده تسویه شود.

    نسخه‌ی قبلی همین کار را می‌کرد و `last_price` را پول واقعی حساب
    می‌کرد؛ یک اختیارِ بی‌ارزش این‌طور برای حساب پول می‌ساخت. حالا
    موقعیت دست‌نخورده می‌ماند و وضعیتش صریح است.
    """
    expired_contract = _contract(expiry_days=-1, last_price=1300.0)
    broker = _broker(tmp_path, _deep_book(), contract=expired_contract)
    broker.place_order(SYMBOL, "buy", 4)
    cash_before = broker.get_account_balance()["cash"]

    expired = broker.expired_positions()

    assert [row["symbol"] for row in expired] == [SYMBOL]
    assert broker.get_positions()[0].quantity == 4, "موقعیت نباید بسته شده باشد"
    assert broker.get_account_balance()["cash"] == cash_before, "نقد نباید تکان بخورد"
    assert broker.store.list_trades() == [], "معامله‌ی ساختگی ثبت نشود"

    valuation = broker.value_positions()[0]
    assert valuation.status is ValuationStatus.EXPIRED_UNSETTLED
    assert valuation.market_value is None


def test_manual_settlement_uses_the_price_the_user_gives(tmp_path):
    """تسویه فقط با قیمتی که کاربر می‌دهد — از جمله صفر."""
    expired_contract = _contract(expiry_days=-1, last_price=1300.0)
    broker = _broker(tmp_path, _deep_book(), contract=expired_contract)
    broker.place_order(SYMBOL, "buy", 4)
    cash_before = broker.get_account_balance()["cash"]

    trade = broker.settle_position(SYMBOL, settlement_price=0.0)

    assert trade["exit_price"] == 0.0
    assert trade["close_reason"] == CLOSE_REASON_EXPIRY
    assert broker.get_positions() == []
    # انقضای بی‌ارزش: هیچ پولی برنمی‌گردد.
    assert broker.get_account_balance()["cash"] == pytest.approx(cash_before)


def test_settling_a_live_position_is_refused(tmp_path):
    """قرارداد سررسیدنشده با «تسویه» بسته نمی‌شود؛ باید سفارش فروش بخورد."""
    broker = _broker(tmp_path, _deep_book(), contract=_contract(expiry_days=30))
    broker.place_order(SYMBOL, "buy", 2)

    with pytest.raises(ValueError, match="هنوز سررسید نشده"):
        broker.settle_position(SYMBOL, settlement_price=1000.0)


def test_manual_close_reason_is_recorded(tmp_path):
    book = OrderBook(
        SYMBOL,
        bids=(BookLevel(1100.0, 10),),
        asks=(BookLevel(1000.0, 10),),
    )
    broker = _broker(tmp_path, book)
    broker.place_order(SYMBOL, "buy", 2)
    broker.place_order(SYMBOL, "sell", 2)

    trades = broker.store.list_trades()
    assert trades[0]["close_reason"] == CLOSE_REASON_MANUAL


def test_reset_restores_initial_balance_and_clears_state(tmp_path):
    broker = _broker(tmp_path, _deep_book())
    broker.place_order(SYMBOL, "buy", 2)

    account = broker.reset()

    assert account["cash"] == INITIAL_BALANCE
    assert broker.get_positions() == []


def test_performance_summary_delegates_to_shared_metrics(tmp_path):
    book = OrderBook(
        SYMBOL,
        bids=(BookLevel(1100.0, 10),),
        asks=(BookLevel(1000.0, 10),),
    )
    broker = _broker(tmp_path, book)
    broker.place_order(SYMBOL, "buy", 2)
    broker.place_order(SYMBOL, "sell", 2)

    summary = broker.performance_summary()
    assert "sharpe_per_signal" in summary
    assert "max_drawdown_pct" in summary

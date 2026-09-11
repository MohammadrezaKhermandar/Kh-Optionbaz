"""کارگزار شبیه‌سازی‌شده (معاملات کاغذی) — پیاده‌سازی واقعیِ `OrderExecutorInterface`.

هیچ تماس شبکه‌ای به کارگزاری واقعی برقرار نمی‌شود. پرشدن هر سفارش از
**عمق واقعی دفتر سفارش** (`data/order_book.py`) حساب می‌شود، هرگز از
آخرین قیمت معامله یا عددی حدسی. سفارش‌ها فقط **فوری** هستند: در برابر
عمق لحظه‌ی ثبت پر می‌شوند یا رد می‌شوند — سفارش باز/معلق وجود ندارد.

**دامنه‌ی مدل‌شده — صریح، نه ضمنی**

فقط پوزیشن **long تک‌پایه** مدل شده است: خرید برای باز کردن، فروش برای
بستن. فروش استقراضی و ساختارهای چندپایه (استردل، کولار، اسپرد) مدل
نشده‌اند، چون وجه تضمین و سرمایه‌ی لازمِ آن‌ها در این پروژه پیاده‌سازی
نشده و نرخ رسمی‌اش هم حدس زده نمی‌شود. ریسکِ یک ساختار کامل با ریسکِ
پایه‌ی فروشِ تنها یکی نیست؛ پس به‌جای وانمود کردن با ضریبی ساختگی،
سفارشش **رد** می‌شود.

**حسابداری** (`execution/paper_account.py`)

ارزش کل حساب `نقد + ارزش روز موقعیت‌ها` است. کارمزد ورود روی خودِ
موقعیت نگه داشته می‌شود و با هر خروج (کامل یا جزئی) به نسبت به معامله‌ی
بسته‌شده منتقل می‌شود، پس هر هزینه دقیقاً یک بار در حساب می‌نشیند.

هر عملیات مالی داخل **یک تراکنش** انجام می‌شود: نقد، موقعیت و معامله
با هم می‌نشینند یا اصلاً نمی‌نشینند.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import date, datetime

from data.option_chain_client import OptionContract
from data.order_book import OrderBook
from execution.order_executor_interface import (
    Order,
    OrderExecutorInterface,
    OrderStatus,
    Position,
)
from execution.paper_account import (
    AccountSnapshot,
    PositionValuation,
    RealizedTotals,
    ValuationStatus,
    allocate_entry_fee,
    check_affordable,
    return_on_cost_pct,
    summarize_trades,
)
from risk.fees import FeeSchedule
from storage.paper_trading_store import PaperTradingStore

logger = logging.getLogger(__name__)

#: علت بسته‌شدن یک معامله
CLOSE_REASON_MANUAL = "manual"
CLOSE_REASON_EXPIRY = "expiry_settlement"

#: چون فروش استقراضی و سفارش معلق مدل نشده‌اند، هیچ پولی مسدود نمی‌شود.
#: این متن در رابط دیده می‌شود تا «صفر» با «فراموش‌شده» اشتباه نشود.
BLOCKED_REASON = (
    "هیچ مبلغی مسدود نمی‌شود: سفارش معلق وجود ندارد (هر سفارش فوری حل "
    "می‌شود) و فروش استقراضی مدل نشده، پس وجه تضمینی هم در کار نیست."
)


class PaperBroker(OrderExecutorInterface):
    """کارگزار کاغذی: پر شدن فوری روی عمق واقعی، پوزیشن‌های long، کارمزد.

    Args:
        store: لایه ماندگاری (`PaperTradingStore`)
        order_book_client: هر شیء با متد
            `try_get_order_book(ins_code, symbol) -> OrderBook | None`
            (مثل `data.order_book.OrderBookClient`؛ خطای شبکه را می‌بلعد و
            `None` برمی‌گرداند تا رد سفارش، نه ۵۰۰، نتیجه‌ی آن باشد)
        resolve_contract: نگاشت نماد آپشن به `OptionContract` (برای `ins_code`
            و `contract_size`)؛ معمولاً `OptionChainClient.get_contract`
        initial_balance: موجودی اولیه حساب کاغذی (ریال)
        fees: نرخ کارمزد و مالیات؛ پیش‌فرض صفر
    """

    def __init__(
        self,
        store: PaperTradingStore,
        order_book_client: object,
        resolve_contract: Callable[[str], OptionContract | None],
        initial_balance: float,
        fees: FeeSchedule | None = None,
    ) -> None:
        self.store = store
        self.order_book_client = order_book_client
        self.resolve_contract = resolve_contract
        self.fees = fees or FeeSchedule()
        self.store.init_account(initial_balance)

    # -- OrderExecutorInterface --------------------------------------------
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float | None = None,
        **kwargs: object,
    ) -> Order:
        """ثبت سفارش کاغذی — فوری، در برابر عمق واقعی دفتر سفارش.

        `price` صرفاً مرجع/نمایشی است؛ پرشدن همیشه از عمق واقعی محاسبه
        می‌شود، نه از این عدد (این کارگزار سفارش limit ندارد).

        ترتیب بررسی‌ها عمدی است: هرچه **پیش از** تغییر حساب رد شدنی است،
        پیش از آن رد می‌شود — تعداد، سمت، موجودیِ پوزیشن برای فروش،
        قرارداد، عمق، و در آخر **کفایت وجه**. هیچ‌کدام حساب را نیمه‌کاره
        رها نمی‌کنند.
        """
        del price  # مرجع/نمایشی؛ پرشدن همیشه از عمق واقعی است
        now = datetime.now().isoformat(timespec="seconds")
        order_id = str(uuid.uuid4())

        if quantity <= 0:
            return self._rejected(order_id, symbol, side, quantity, now, "تعداد نامعتبر")

        side_normalized = side.lower()
        if side_normalized not in ("buy", "sell"):
            return self._rejected(order_id, symbol, side, quantity, now, f"سمت نامعتبر: {side}")

        if side_normalized == "sell":
            position = self.store.get_position(symbol)
            held = position["quantity"] if position else 0
            if held < quantity:
                return self._rejected(
                    order_id,
                    symbol,
                    side,
                    quantity,
                    now,
                    f"پوزیشنی برای فروش کافی نیست (موجود: {held}, درخواست: {quantity}). "
                    "این کارگزاری فقط پوزیشن long تک‌پایه را مدل می‌کند؛ فروش برای "
                    "باز کردن (و پرمیومی که از آن دریافت می‌شود) سود تحقق‌یافته "
                    "نیست و وجه تضمینش هم مدل نشده است.",
                )

        contract = self.resolve_contract(symbol)
        if contract is None or not contract.ins_code:
            reason = "نماد یا ins_code یافت نشد"
            return self._rejected(order_id, symbol, side, quantity, now, reason)

        book = self.order_book_client.try_get_order_book(contract.ins_code, symbol)
        if book is None:
            reason = "دفتر سفارش در دسترس نیست (خطای شبکه یا داده)"
            return self._rejected(order_id, symbol, side, quantity, now, reason)

        avg_price, filled_qty = book.fill_price(side_normalized, quantity)
        if avg_price is None or filled_qty <= 0:
            reason = "عمق کافی در دفتر سفارش نیست"
            return self._rejected(order_id, symbol, side, quantity, now, reason)

        notional = avg_price * filled_qty * contract.contract_size
        is_buy = side_normalized == "buy"
        fee = (
            self.fees.entry_cost(notional, is_buy)
            if is_buy
            else self.fees.exit_cost(notional, was_buy=True)
        )

        if is_buy:
            # ⚠️ کنترل سرمایه **پیش از** هر نوشتنی. نقدِ منفی یعنی اهرمی
            # که در واقعیت وجود نداشت به حساب داده شده و کل ارزیابیِ
            # استراتژی بی‌معنا شده است.
            rejection = check_affordable(
                required=notional + fee,
                available=self._available_cash(),
                symbol=symbol,
                quantity=filled_qty,
            )
            if rejection is not None:
                return self._rejected(
                    order_id, symbol, side, quantity, now, rejection.reason
                )

        status = OrderStatus.FILLED if filled_qty == quantity else OrderStatus.PARTIALLY_FILLED
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side_normalized,
            quantity=quantity,
            price=avg_price,
            status=status,
            filled_quantity=filled_qty,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            metadata={"ins_code": contract.ins_code, "fee_paid": fee},
        )

        signal_id = kwargs.get("signal_id")
        # نقد، موقعیت، معامله و خودِ سفارش با هم می‌نشینند یا هیچ‌کدام.
        with self.store.transaction():
            if is_buy:
                self._apply_buy(symbol, filled_qty, avg_price, fee, now, contract)
            else:
                self._apply_sell(symbol, filled_qty, avg_price, fee, now, contract, signal_id)
            self._save_order(order, fee, signal_id)
        return order

    def cancel_order(self, order_id: str) -> bool:
        """هر سفارش کاغذی همان لحظه‌ی ثبت، پر یا رد می‌شود؛ چیزی برای لغو نمی‌ماند."""
        order = self.store.get_order(order_id)
        return order is not None and order["status"] == OrderStatus.OPEN.value

    def modify_order(
        self,
        order_id: str,  # noqa: ARG002 — امضا از OrderExecutorInterface می‌آید
        price: float | None = None,  # noqa: ARG002
        quantity: int | None = None,  # noqa: ARG002
    ) -> Order:
        """این کارگزاری سفارش باز/قابل‌ویرایش ندارد — هر سفارش فوری حل می‌شود."""
        raise ValueError("این کارگزاری سفارش باز/قابل‌ویرایش ندارد")

    def get_order_status(self, order_id: str) -> OrderStatus:
        order = self.store.get_order(order_id)
        if order is None:
            raise ValueError(f"سفارش {order_id} یافت نشد")
        return OrderStatus(order["status"])

    def get_positions(self) -> list[Position]:
        return [
            Position(
                symbol=row["symbol"],
                quantity=row["quantity"],
                average_price=row["average_price"],
            )
            for row in self.store.list_positions()
        ]

    def get_account_balance(self) -> dict[str, float]:
        """موجودی حساب.

        `buying_power` برابر وجه قابل استفاده است. چون سفارش معلق و وجه
        تضمین مدل نشده‌اند، مبلغ مسدود صفر است و این دو عدد یکی می‌شوند
        — ولی هر دو صریح گزارش می‌شوند تا «صفر» با «حساب‌نشده» اشتباه
        نشود.
        """
        account = self.store.get_account()
        cash = account["cash"] if account else 0.0
        return {
            "cash": cash,
            "blocked": 0.0,
            "available": cash,
            "buying_power": cash,
        }

    # -- کمکی‌های داخلی -------------------------------------------------------
    def _available_cash(self) -> float:
        """وجهی که واقعاً می‌شود با آن خرید کرد."""
        return self.get_account_balance()["available"]

    def _apply_buy(
        self,
        symbol: str,
        filled_qty: int,
        avg_price: float,
        fee: float,
        now: str,
        contract: OptionContract,
    ) -> None:
        account = self.store.get_account()
        notional = avg_price * filled_qty * contract.contract_size
        self.store.update_cash(account["cash"] - notional - fee)

        existing = self.store.get_position(symbol)
        if existing is None:
            self.store.upsert_position(symbol, filled_qty, avg_price, now, entry_fees=fee)
            return
        new_qty = existing["quantity"] + filled_qty
        old_cost = existing["quantity"] * existing["average_price"]
        new_avg = (old_cost + filled_qty * avg_price) / new_qty
        self.store.upsert_position(
            symbol,
            new_qty,
            new_avg,
            existing["opened_at"],
            entry_fees=self._entry_fees_of(existing) + fee,
        )

    def _apply_sell(
        self,
        symbol: str,
        filled_qty: int,
        avg_price: float,
        fee: float,
        now: str,
        contract: OptionContract,
        signal_id: object,
    ) -> None:
        position = self.store.get_position(symbol)
        entry_price = position["average_price"]
        opened_at = position["opened_at"]
        held = position["quantity"]
        entry_fees_total = self._entry_fees_of(position)

        notional = avg_price * filled_qty * contract.contract_size
        account = self.store.get_account()
        self.store.update_cash(account["cash"] + notional - fee)

        # سهمِ کارمزد ورودِ همین تعداد با معامله می‌رود؛ بقیه روی موقعیتِ
        # باقی‌مانده می‌ماند. بدون این تسهیم، خروج جزئی یا هزینه را دو بار
        # می‌شمرد یا برای همیشه گمش می‌کند.
        entry_fee_share = allocate_entry_fee(entry_fees_total, filled_qty, held)
        remaining = held - filled_qty
        self.store.upsert_position(
            symbol,
            remaining,
            entry_price,
            opened_at,
            entry_fees=entry_fees_total - entry_fee_share,
        )

        self._record_close(
            symbol=symbol,
            quantity=filled_qty,
            entry_price=entry_price,
            exit_price=avg_price,
            entry_fee=entry_fee_share,
            exit_fee=fee,
            contract_size=contract.contract_size,
            opened_at=opened_at,
            closed_at=now,
            signal_id=signal_id,
            close_reason=CLOSE_REASON_MANUAL,
        )

    def _record_close(
        self,
        *,
        symbol: str,
        quantity: int,
        entry_price: float,
        exit_price: float,
        entry_fee: float,
        exit_fee: float,
        contract_size: int,
        opened_at: str,
        closed_at: str,
        signal_id: object,
        close_reason: str,
    ) -> dict[str, object]:
        """ثبت یک معامله‌ی بسته‌شده با تفکیک کاملِ ناخالص و هزینه‌ها."""
        gross = (exit_price - entry_price) * quantity * contract_size
        net = gross - entry_fee - exit_fee
        cost_basis = entry_price * quantity * contract_size + entry_fee
        trade = {
            "trade_id": str(uuid.uuid4()),
            "symbol": symbol,
            "quantity": quantity,
            "entry_price": entry_price,
            "exit_price": exit_price,
            # `fee_paid` برای سازگاری با ردیف‌های قدیمی می‌ماند و حالا
            # **کل** هزینه‌ی همین معامله است، نه فقط کارمزد خروج.
            "fee_paid": entry_fee + exit_fee,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "gross_pnl": gross,
            "pnl_absolute": net,
            # درصدِ معنادار: نسبت به پولی که واقعاً درگیر شد.
            "pnl_pct": return_on_cost_pct(net, cost_basis) or 0.0,
            "return_on_cost_pct": return_on_cost_pct(net, cost_basis),
            "contract_size": contract_size,
            "opened_at": opened_at,
            "closed_at": closed_at,
            "signal_id": signal_id,
            "close_reason": close_reason,
        }
        self.store.record_trade(trade)
        return trade

    @staticmethod
    def _entry_fees_of(position: dict[str, object]) -> float:
        """کارمزد ورودِ ثبت‌شده‌ی یک موقعیت (صفر برای ردیف‌های نسخه‌ی ۱)."""
        return float(position.get("entry_fees") or 0.0)

    def _rejected(
        self, order_id: str, symbol: str, side: str, quantity: int, now: str, reason: str
    ) -> Order:
        order = Order(
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=0.0,
            status=OrderStatus.REJECTED,
            filled_quantity=0,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
            metadata={"reason": reason},
        )
        self._save_order(order, fee=0.0, signal_id=None)
        logger.info("سفارش کاغذی رد شد (%s): %s", symbol, reason)
        return order

    def _save_order(self, order: Order, fee: float, signal_id: object) -> None:
        self.store.save_order(
            {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.quantity,
                "filled_quantity": order.filled_quantity,
                "avg_fill_price": order.price,
                "status": order.status.value,
                "fee_paid": fee,
                "signal_id": signal_id,
                "created_at": order.created_at.isoformat(timespec="seconds"),
                "updated_at": order.updated_at.isoformat(timespec="seconds"),
                "metadata": json.dumps(order.metadata, ensure_ascii=False),
            }
        )

    # -- ارزش‌گذاری و حساب ----------------------------------------------------
    def value_positions(self, today: date | None = None) -> list[PositionValuation]:
        """ارزش‌گذاری همه‌ی موقعیت‌های باز، **با وضعیت صریح**.

        موقعیتی که قیمت نمی‌خورد حذف نمی‌شود و صفر هم نمی‌گیرد: با
        وضعیتِ خودش برمی‌گردد تا حساب بتواند بگوید ارزش‌گذاری ناقص است.
        """
        valuations: list[PositionValuation] = []
        for row in self.store.list_positions():
            valuations.append(self._value_one(row, today))
        return valuations

    def _value_one(self, row: dict[str, object], today: date | None) -> PositionValuation:
        symbol = str(row["symbol"])
        quantity = int(row["quantity"])
        entry_fees = self._entry_fees_of(row)
        average_price = float(row["average_price"])
        contract = self.resolve_contract(symbol)
        contract_size = contract.contract_size if contract else 1

        def build(
            status: ValuationStatus,
            mark: float | None = None,
            reference: float | None = None,
            fillable: int = 0,
        ) -> PositionValuation:
            return PositionValuation(
                symbol=symbol,
                quantity=quantity,
                contract_size=contract_size,
                average_price=average_price,
                entry_fees_open=entry_fees,
                status=status,
                mark_price=mark,
                reference_price=reference,
                fillable_quantity=fillable,
            )

        if contract is None or not contract.ins_code:
            return build(ValuationStatus.UNAVAILABLE)

        # سررسیدگذشته **پیش از** مظنه بررسی می‌شود: قیمت دفترِ یک نماد
        # سررسیدشده، تسویه نیست.
        if contract.expiry < (today or date.today()):
            return build(ValuationStatus.EXPIRED_UNSETTLED)

        book: OrderBook | None = self.order_book_client.try_get_order_book(
            contract.ins_code, symbol
        )
        if book is None:
            return build(ValuationStatus.UNAVAILABLE)

        mark_price, fillable = book.fill_price("sell", quantity)
        best_bid = book.best_bid
        if mark_price is None or fillable <= 0:
            return build(ValuationStatus.NO_DEPTH, reference=best_bid)
        if fillable < quantity:
            # قیمتِ میانگینِ یک خروجِ ناقص، ارزشِ کل موقعیت نیست. عدد
            # مرجع داده می‌شود ولی در جمع حساب نمی‌آید.
            return build(
                ValuationStatus.PARTIAL_DEPTH, reference=best_bid, fillable=fillable
            )
        return build(ValuationStatus.OK, mark=mark_price, reference=best_bid, fillable=fillable)

    def realized_totals(self, days: int | None = None) -> RealizedTotals:
        """جمعِ معاملات بسته‌شده، با تفکیک ناخالص و هزینه."""
        return summarize_trades(self.store.list_trades(days))

    def costs_known(self) -> bool:
        """آیا «خالص» عدد معناداری است؟

        اگر هیچ نرخی تنظیم نشده و هیچ هزینه‌ای هم پرداخت نشده، «خالص»
        فقط تکرارِ «ناخالص» است و نمایشش به‌عنوان سود قطعی، دقتِ کاذب
        می‌سازد.
        """
        if not self.fees.is_zero:
            return True
        return self.realized_totals().costs > 0

    def account_snapshot(self, today: date | None = None) -> AccountSnapshot:
        """عکسِ کامل و قابل تطبیق حساب — همان چیزی که داشبورد نشان می‌دهد."""
        account = self.store.get_account() or {"cash": 0.0, "initial_balance": 0.0}
        return AccountSnapshot(
            initial_balance=float(account["initial_balance"]),
            cash=float(account["cash"]),
            positions=tuple(self.value_positions(today)),
            realized=self.realized_totals(),
            costs_known=self.costs_known(),
            blocked=0.0,
            blocked_reason=BLOCKED_REASON,
            priced_at=datetime.now().isoformat(timespec="seconds"),
        )

    # -- سررسید --------------------------------------------------------------
    def expired_positions(self, today: date | None = None) -> list[dict[str, object]]:
        """موقعیت‌هایی که سررسیدشان گذشته و هنوز تسویه نشده‌اند.

        ⚠️ اینجا **هیچ تسویه‌ای انجام نمی‌شود**. نسخه‌ی قبلی این موقعیت‌ها
        را خودکار با «آخرین قیمت معامله‌شده‌ی اختیار» می‌بست و نتیجه را
        پول واقعی حساب می‌کرد. آن عدد تسویه نیست: قواعد رسمی تسویه‌ی
        بورس تهران در این پروژه نیست، و اختیارِ بی‌ارزش با آخرین پرمیومِ
        معامله‌شده، پولِ موهوم می‌ساخت. تسویه فقط با قیمتی که کاربر
        صریحاً بدهد انجام می‌شود (`settle_position`).
        """
        moment = today or date.today()
        expired = []
        for row in self.store.list_positions():
            contract = self.resolve_contract(str(row["symbol"]))
            if contract is None or contract.expiry >= moment:
                continue
            expired.append({**row, "expiry": contract.expiry.isoformat()})
        return expired

    def settle_position(
        self,
        symbol: str,
        settlement_price: float,
        today: date | None = None,
    ) -> dict[str, object]:
        """تسویه‌ی دستیِ یک موقعیتِ سررسیدشده با قیمتی که **کاربر** می‌دهد.

        قیمت از بیرون می‌آید چون منبعِ درستش (قواعد تسویه‌ی بورس) در این
        پروژه نیست. صفر هم قیمتِ معتبری است — «اختیار بی‌ارزش منقضی شد»
        همان تسویه‌ی صفر است، و این با «قیمت نداریم» فرق دارد.
        """
        position = self.store.get_position(symbol)
        if position is None:
            raise ValueError(f"موقعیتی روی {symbol} باز نیست.")
        if settlement_price < 0:
            raise ValueError("قیمت تسویه نمی‌تواند منفی باشد.")

        contract = self.resolve_contract(symbol)
        if contract is None:
            raise ValueError(f"قرارداد {symbol} یافت نشد؛ اندازه‌ی قرارداد دانسته نیست.")
        if contract.expiry >= (today or date.today()):
            raise ValueError(
                f"{symbol} هنوز سررسید نشده ({contract.expiry.isoformat()}). "
                "برای بستنِ موقعیتِ باز از سفارش فروش استفاده کنید."
            )

        quantity = int(position["quantity"])
        contract_size = contract.contract_size
        notional = settlement_price * quantity * contract_size
        fee = self.fees.exit_cost(notional, was_buy=True)
        now = datetime.now().isoformat(timespec="seconds")
        entry_fees_total = self._entry_fees_of(position)

        with self.store.transaction():
            account = self.store.get_account()
            self.store.update_cash(account["cash"] + notional - fee)
            self.store.delete_position(symbol)
            trade = self._record_close(
                symbol=symbol,
                quantity=quantity,
                entry_price=float(position["average_price"]),
                exit_price=settlement_price,
                entry_fee=entry_fees_total,
                exit_fee=fee,
                contract_size=contract_size,
                opened_at=str(position["opened_at"]),
                closed_at=now,
                signal_id=None,
                close_reason=CLOSE_REASON_EXPIRY,
            )
        return trade

    # -- گزارش ----------------------------------------------------------------
    def performance_summary(self, days: int | None = None) -> dict[str, object]:
        """معیارهای معاملات کاغذی بسته‌شده.

        دو تفاوت با نسخه‌ی قبل:

        * درصدِ ورودیِ معیارها حالا **بازده نسبت به سرمایه‌ی درگیر** است
          (`return_on_cost_pct`)، نه `(خروج−ورود)/ورود` که نه کارمزد
          داشت نه اندازه‌ی موقعیت.
        * `account_drawdown` جداگانه از **ریال** حساب می‌شود، نه از جمعِ
          درصدها. `max_drawdown_pct` معیارِ سطحِ سیگنال است و با افتِ
          حساب یکی نیست؛ حالا هر دو هستند و قاطی نمی‌شوند.
        """
        from backtest import metrics

        trades = self.store.list_trades(days)
        returns = [
            float(t["return_on_cost_pct"])
            for t in trades
            if t.get("return_on_cost_pct") is not None
        ]
        summary = metrics.summarize(returns)
        summary["signal_level_note"] = (
            "درصدها سطحِ معامله‌اند (بازده نسبت به سرمایه‌ی درگیر). "
            "افتِ حساب جدا در account_drawdown آمده است."
        )
        summary["account_drawdown"] = self.realized_drawdown(days)
        summary["trades_missing_entry_cost"] = summarize_trades(
            trades
        ).trades_missing_entry_cost
        return summary

    def realized_drawdown(self, days: int | None = None) -> dict[str, object]:
        """بیشترین افتِ **حساب** روی منحنی نقدیِ معاملات بسته‌شده.

        از سرمایه‌ی اولیه شروع می‌شود و سود/زیانِ خالصِ هر معامله را به
        ترتیب زمان جمع می‌کند. این افتِ ریالیِ حساب است، نه جمعِ درصدِ
        معاملات — آن یکی با افتِ واقعیِ سرمایه هیچ نسبتی ندارد.

        ⚠️ فقط معاملاتِ **بسته‌شده** را می‌بیند: افتی که در دلِ یک
        موقعیتِ باز اتفاق افتاده و هنوز تحقق نیافته، اینجا نیست.
        """
        account = self.store.get_account()
        start = float(account["initial_balance"]) if account else 0.0
        equity = start
        peak = start
        worst = 0.0
        worst_at: str | None = None
        for trade in self.store.list_trades(days):
            equity += float(trade["pnl_absolute"])
            peak = max(peak, equity)
            if peak - equity > worst:
                worst = peak - equity
                worst_at = str(trade["closed_at"])
        return {
            "max_drawdown_currency": worst,
            "max_drawdown_pct": (worst / start * 100.0) if start > 0 else None,
            "at": worst_at,
            "basis": "realized_closed_trades",
        }

    def reset(self) -> dict[str, object]:
        """پاک‌کردن کامل حساب کاغذی و بازگرداندن موجودی به مقدار اولیه."""
        account = self.store.get_account()
        initial_balance = account["initial_balance"] if account else 0.0
        return self.store.reset(initial_balance)

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
import math
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
#: تعیین تکلیفِ سررسید با **فرضِ کاربر** — نه تسویه‌ی رسمی. مقدارش برای
#: سازگاری با ردیف‌های موجود عوض نشده، ولی معنایش همین است.
CLOSE_REASON_EXPIRY = "expiry_settlement"

#: چون فروش استقراضی و سفارش معلق مدل نشده‌اند، هیچ پولی مسدود نمی‌شود.
#: این متن در رابط دیده می‌شود تا «صفر» با «فراموش‌شده» اشتباه نشود.
BLOCKED_REASON = (
    "هیچ مبلغی مسدود نمی‌شود: سفارش معلق وجود ندارد (هر سفارش فوری حل "
    "می‌شود) و فروش استقراضی مدل نشده، پس وجه تضمینی هم در کار نیست."
)


def _finite(value: object, label: str) -> float:
    """عدد را به `float` تبدیل می‌کند و `nan`/`inf` را **رد** می‌کند.

    `float("inf")` در محاسبات مالی سر و صدا نمی‌کند: نقد را بی‌نهایت
    می‌کند و `nan` هر مقایسه‌ای را `False` می‌کند، پس کنترل‌ها بی‌صدا رد
    می‌شوند. بهتر است همان‌جا بشکند.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} باید یک عدد باشد.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} باید یک عدد متناهی باشد، نه {number}.")
    return number


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
        today: date | None = None,
        **kwargs: object,
    ) -> Order:
        """ثبت سفارش کاغذی — فوری، در برابر عمق واقعی دفتر سفارش.

        `price` صرفاً مرجع/نمایشی است؛ پرشدن همیشه از عمق واقعی محاسبه
        می‌شود، نه از این عدد (این کارگزار سفارش limit ندارد).

        ترتیب بررسی‌ها عمدی است: هرچه **پیش از** تغییر حساب رد شدنی است،
        پیش از آن رد می‌شود — تعداد، سمت، موجودیِ پوزیشن برای فروش،
        قرارداد، **سررسید**، عمق، و در آخر کفایت وجه. هیچ‌کدام حساب را
        نیمه‌کاره رها نمی‌کنند.
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

        # ⚠️ سررسید **پیش از** دفتر سفارش بررسی می‌شود. منبع ممکن است
        # هنوز برای نماد سررسیدشده مظنه بدهد (ردیف کهنه، یا نمادی که
        # هنوز از دیده‌بان حذف نشده)، ولی معامله‌ی عادی روی آن ممکن نیست.
        # پرکردنش یعنی حسابی که با قراردادی معامله کرده که وجود ندارد.
        # تعیین تکلیف موقعیتِ سررسیدشده مسیر جداگانه‌ای دارد
        # (`settle_position`) که قیمتش را از کاربر می‌گیرد.
        if contract.expiry < (today or date.today()):
            return self._rejected(
                order_id, symbol, side, quantity, now,
                f"{symbol} در {contract.expiry.isoformat()} سررسید شده است؛ "
                "سفارش عادی روی قرارداد گذشته از سررسید ثبت نمی‌شود. "
                "موقعیتِ باز را از مسیر «تسویه» تعیین تکلیف کنید.",
            )

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
            metadata=self._order_metadata(contract, fee, kwargs.get("decision")),
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

    @staticmethod
    def _order_metadata(
        contract: OptionContract, fee: float, decision: object | None
    ) -> dict[str, object]:
        """چیزی که باید **همراهِ خودِ سفارش** بماند.

        `decision` عکسِ ارزیابیِ لحظه‌ی تصمیم است (اگر فراخواننده داده
        باشد). بدون آن، بعداً هیچ راهی نیست بفهمیم معامله با چه
        اطلاعاتی باز شد — و ارزیابیِ بعدی روی حدس بنا می‌شود.
        """
        metadata: dict[str, object] = {"ins_code": contract.ins_code, "fee_paid": fee}
        if decision:
            metadata["decision"] = decision
        return metadata

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

        # ⚠️ «دانسته بودن» در همان لحظه‌ی عملیات قفل می‌شود. اگر نرخی
        # تنظیم نشده باشد، عددِ صفرِ `fee` پیش‌فرضِ پروژه است نه هزینه‌ی
        # واقعی — پس نامعلوم ثبت می‌شود. تنظیم نرخ در آینده این سابقه را
        # معلوم نمی‌کند.
        fee_known = self.fees.rates_known
        existing = self.store.get_position(symbol)
        if existing is None:
            self.store.upsert_position(
                symbol, filled_qty, avg_price, now,
                entry_fees=fee, entry_fees_known=fee_known,
            )
            return
        new_qty = existing["quantity"] + filled_qty
        old_cost = existing["quantity"] * existing["average_price"]
        new_avg = (old_cost + filled_qty * avg_price) / new_qty
        # افزودن به موقعیتی که کارمزد ورودش دانسته نیست، جمعِ تازه را هم
        # نامعلوم می‌کند: بخشی از آن عدد همچنان ناشناخته است.
        self.store.upsert_position(
            symbol,
            new_qty,
            new_avg,
            existing["opened_at"],
            entry_fees=self._entry_fees_of(existing) + fee,
            # خرید افزایشی: جمع فقط وقتی دانسته است که **هر دو** بخشش
            # دانسته باشند.
            entry_fees_known=self._entry_fees_known_of(existing) and fee_known,
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
        entry_fees_known = self._entry_fees_known_of(position)
        entry_fee_share = allocate_entry_fee(entry_fees_total, filled_qty, held)
        remaining = held - filled_qty
        self.store.upsert_position(
            symbol,
            remaining,
            entry_price,
            opened_at,
            entry_fees=entry_fees_total - entry_fee_share,
            entry_fees_known=entry_fees_known,
        )

        self._record_close(
            symbol=symbol,
            quantity=filled_qty,
            entry_price=entry_price,
            exit_price=avg_price,
            # سهمی از عددی که دانسته نیست، خودش هم دانسته نیست.
            entry_fee=entry_fee_share if entry_fees_known else None,
            exit_fee=fee if self.fees.rates_known else None,
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
        entry_fee: float | None,
        exit_fee: float | None,
        contract_size: int,
        opened_at: str,
        closed_at: str,
        signal_id: object,
        close_reason: str,
    ) -> dict[str, object]:
        """ثبت یک معامله‌ی بسته‌شده با تفکیک کاملِ ناخالص و هزینه‌ها."""
        gross = (exit_price - entry_price) * quantity * contract_size
        # `None` در هرکدام از دو هزینه یعنی دانسته نیست — چه موقعیتِ
        # مهاجرت‌شده باشد و چه نرخی که هنگام عملیات تنظیم نشده بود.
        # آن‌وقت خالص و درصدش هم دانسته نیستند و صفر گرفتنشان یعنی ادعای
        # بی‌هزینه بودن.
        costs_known = entry_fee is not None and exit_fee is not None
        recorded_costs = (entry_fee or 0.0) + (exit_fee or 0.0)
        net = gross - recorded_costs if costs_known else None
        cost_basis = (
            entry_price * quantity * contract_size + entry_fee
            if entry_fee is not None
            else None
        )
        trade = {
            "trade_id": str(uuid.uuid4()),
            "symbol": symbol,
            "quantity": quantity,
            "entry_price": entry_price,
            "exit_price": exit_price,
            # `fee_paid` برای سازگاری با ردیف‌های قدیمی می‌ماند و حالا
            # **کل** هزینه‌ی همین معامله است، نه فقط کارمزد خروج.
            # `fee_paid` فقط هزینه‌های **ثبت‌شده** را جمع می‌زند.
            "fee_paid": recorded_costs,
            "entry_fee": entry_fee,
            "exit_fee": exit_fee,
            "gross_pnl": gross,
            # ستون `pnl_absolute` در اسکیما NOT NULL است؛ وقتی خالص
            # دانسته نیست، **ناخالص منهای هزینه‌های ثبت‌شده** می‌نشیند —
            # همان کفِ زیان. `NULL` بودنِ یکی از دو ستون هزینه، علامتِ
            # ناقص بودنش است.
            "pnl_absolute": gross - recorded_costs if net is None else net,
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
        """کارمزد ورودِ **ثبت‌شده‌ی** یک موقعیت.

        برای موقعیتِ مهاجرت‌شده صفر است، ولی آن صفر «دانسته» نیست —
        همیشه کنار `_entry_fees_known_of` خوانده شود.
        """
        return float(position.get("entry_fees") or 0.0)

    @staticmethod
    def _entry_fees_known_of(position: dict[str, object]) -> bool:
        """آیا کارمزد ورودِ این موقعیت دانسته است؟

        ستون در نسخه‌ی ۳ آمده؛ نبودنش محافظه‌کارانه «نامعلوم» گرفته
        می‌شود — ادعای دانستن، گران‌تر از اعتراف به ندانستن است.
        """
        value = position.get("entry_fees_known")
        return bool(value) if value is not None else False

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
        entry_fees_known = self._entry_fees_known_of(row)
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
                entry_fees_known=entry_fees_known,
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

    def rates_configured(self) -> bool:
        """آیا کاربر نرخ کارمزد/مالیاتی تنظیم کرده است؟

        ⚠️ این فقط درباره‌ی معامله‌های **بعدی** حرف می‌زند. عمداً در
        محاسبه‌ی «هزینه دانسته است یا نه» دخالت نمی‌کند: نرخی که امروز
        وارد شده، چیزی درباره‌ی هزینه‌ی معامله‌ی دیروز نمی‌گوید.
        """
        return self.fees.rates_known

    def account_snapshot(self, today: date | None = None) -> AccountSnapshot:
        """عکسِ کامل و قابل تطبیق حساب — همان چیزی که داشبورد نشان می‌دهد."""
        account = self.store.get_account() or {"cash": 0.0, "initial_balance": 0.0}
        return AccountSnapshot(
            initial_balance=float(account["initial_balance"]),
            cash=float(account["cash"]),
            positions=tuple(self.value_positions(today)),
            realized=self.realized_totals(),
            rates_configured=self.rates_configured(),
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
        """ثبتِ **فرضِ کاربر** برای تعیین تکلیف یک موقعیتِ سررسیدشده.

        این «تسویه‌ی رسمی» نیست و وانمود هم نمی‌کند که هست. قواعد اعمال
        و تسویه‌ی بورس تهران در این پروژه پیاده نشده‌اند و هزینه‌ی خودِ
        تسویه هم مدل نشده است — تنها چیزی که اینجا اتفاق می‌افتد این است
        که عددی که **کاربر فرض می‌کند** در شبیه‌سازی ثبت می‌شود تا حساب
        بسته شود و ارزش‌گذاری معلق نماند.

        Args:
            settlement_price: پرمیوم تسویه **به ازای هر واحد** — همان
                مبنایی که بقیه‌ی قیمت‌های این پروژه با آن کار می‌کنند.
                ارزش کل از `قیمت × تعداد × اندازه‌ی قرارداد` در می‌آید.
                صفر مقدارِ معتبری است («اختیار بی‌ارزش منقضی شد») و با
                «قیمت نداریم» فرق دارد — ولی باید **صریح** داده شود.
        """
        position = self.store.get_position(symbol)
        if position is None:
            raise ValueError(f"موقعیتی روی {symbol} باز نیست.")
        price = _finite(settlement_price, "قیمت تسویه")
        if price < 0:
            raise ValueError("قیمت تسویه نمی‌تواند منفی باشد.")
        settlement_price = price

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
        # تسویه هم یک خروج است و همان قاعده را دارد: نه هزینه‌ی ورودِ
        # نامعلوم را معلوم می‌کند، نه صفرِ پیش‌فرضِ نرخ را هزینه‌ی دانسته.
        entry_fees_known = self._entry_fees_known_of(position)

        with self.store.transaction():
            account = self.store.get_account()
            self.store.update_cash(account["cash"] + notional - fee)
            self.store.delete_position(symbol)
            trade = self._record_close(
                symbol=symbol,
                quantity=quantity,
                entry_price=float(position["average_price"]),
                exit_price=settlement_price,
                entry_fee=entry_fees_total if entry_fees_known else None,
                exit_fee=fee if self.fees.rates_known else None,
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

        درصدِ افت از **قله‌ی مربوط به همان افت** حساب می‌شود، نه از
        سرمایه‌ی اولیه: ۱۰۰ → ۲۰۰ → ۱۵۰ یعنی افتِ مبلغی ۵۰ و افتِ درصدیِ
        ۲۵٪ (نه ۵۰٪). نسبت‌دادن به سرمایه‌ی اولیه، افتِ یک حسابِ رشدکرده
        را کوچک نشان می‌دهد.

        بیشترین افتِ مبلغی و بیشترین افتِ درصدی **لزوماً یک‌جا نیستند** —
        افتِ کوچک از یک قله‌ی کوچک می‌تواند درصدِ بزرگ‌تری بدهد. پس هر دو
        جدا نگه داشته و زمانشان جدا گزارش می‌شود.

        با فیلتر بازه، منحنی از **مانده‌ی ابتدای همان بازه** شروع می‌شود
        (سرمایه‌ی اولیه به‌علاوه‌ی سود/زیانِ معاملاتِ پیش از بازه)، نه از
        سرمایه‌ی اولیه — وگرنه افتِ یک ماهِ خاص با قله‌ای سنجیده می‌شد که
        اصلاً در آن ماه وجود نداشت.

        ⚠️ فقط معاملاتِ **بسته‌شده** را می‌بیند: افتی که در دلِ یک
        موقعیتِ باز اتفاق افتاده و هنوز تحقق نیافته، اینجا نیست.

        ⚠️ مبنای هر گام `pnl_absolute`ِ ثبت‌شده است. برای معامله‌هایی که
        کارمزد ورودشان دانسته نیست، آن عدد کفِ زیان است نه قطعیِ آن.
        """
        account = self.store.get_account()
        initial = float(account["initial_balance"]) if account else 0.0

        window = self.store.list_trades(days)
        if days is None:
            opening = initial
        else:
            # هر دو فهرست بر اساس `closed_at` صعودی‌اند و فیلتر یک
            # «پسوند» است، پس معاملاتِ پیش از بازه همان ابتدای فهرست کل‌اند.
            everything = self.store.list_trades(None)
            prior = everything[: len(everything) - len(window)]
            opening = initial + sum(float(t["pnl_absolute"]) for t in prior)

        equity = peak = opening
        worst_currency = 0.0
        worst_currency_at: str | None = None
        worst_pct = 0.0
        worst_pct_at: str | None = None
        for trade in window:
            equity += float(trade["pnl_absolute"])
            peak = max(peak, equity)
            drop = peak - equity
            if drop > worst_currency:
                worst_currency = drop
                worst_currency_at = str(trade["closed_at"])
            if peak > 0:
                pct = drop / peak * 100.0
                if pct > worst_pct:
                    worst_pct = pct
                    worst_pct_at = str(trade["closed_at"])

        return {
            "opening_balance": opening,
            "max_drawdown_currency": worst_currency,
            "max_drawdown_currency_at": worst_currency_at,
            "max_drawdown_pct": worst_pct,
            "max_drawdown_pct_at": worst_pct_at,
            "peak_relative": True,
            "basis": "realized_closed_trades",
        }

    def reset(self) -> dict[str, object]:
        """پاک‌کردن کامل حساب کاغذی و بازگرداندن موجودی به مقدار اولیه."""
        account = self.store.get_account()
        initial_balance = account["initial_balance"] if account else 0.0
        return self.store.reset(initial_balance)

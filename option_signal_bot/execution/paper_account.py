"""ارزش‌گذاری و تطبیق حساب کاغذی — توابع خالص، بدون شبکه و بدون پایگاه.

**مسئله‌ای که این ماژول حل می‌کند**

«ارزش کل حساب» پیش از این `نقد + سود شناور` بود. آن رابطه ارزشِ خودِ
موقعیت را جا می‌انداخت، پس لحظه‌ی باز کردن پوزیشن، حساب به اندازه‌ی کلِ
پول پرداختی زیان نشان می‌داد. رابطه‌ی درست یکی است و همین‌جاست:

    ارزش کل حساب = نقد + ارزش روز موقعیت‌های باز

و از دل همان، این اتحادِ **تطبیق** بیرون می‌آید (بدون جریان خارجی):

    ارزش کل حساب − سرمایه‌ی اولیه = سود تحقق‌یافته‌ی خالص
                                     + سود تحقق‌نیافته‌ی خالص

«خالص» یعنی هزینه‌هایی که **واقعاً پرداخت شده‌اند** کم شده باشند — نه
هزینه‌ی حدسیِ آینده. کارمزد ورودِ یک موقعیت باز، پول رفته است؛ پس از
سود شناورِ همان موقعیت کم می‌شود، و وقتی موقعیت (یا بخشی از آن) بسته
شد، همان سهم به معامله‌ی بسته‌شده منتقل می‌شود. به این ترتیب هر هزینه
**دقیقاً یک بار** در حساب می‌نشیند؛ نه دو بار، نه هیچ بار.

**چیزی که این ماژول عمداً نمی‌کند**

قیمتِ نداشته را با صفر جایگزین نمی‌کند. موقعیتی که قیمت خروج ندارد،
`market_value` اش `None` است و ارزش کل حساب **نامشخص** اعلام می‌شود، نه
عددی که بخشی از دارایی را جا انداخته و سالم به نظر می‌رسد. عددِ غلطِ
آرام از «نمی‌دانم»ِ صریح بدتر است.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

#: خطای گِردکردن ممیز شناور که در تطبیق «صفر» شمرده می‌شود (ریال).
RECONCILIATION_TOLERANCE = 0.01


class ValuationStatus(str, Enum):
    """چرا این موقعیت قیمت خورد یا نخورد.

    `str, Enum` عمدی است و نه `StrEnum`: این مقدار مستقیم در JSON پاسخ
    می‌نشیند و در SQLite ذخیره می‌شود، پس عضو باید واقعاً یک رشته باشد.
    """

    #: عمق سمت خرید برای خروجِ **کل** موقعیت کافی بود
    OK = "ok"
    #: دفتر هست ولی عمقش همه‌ی موقعیت را نمی‌خرد
    PARTIAL_DEPTH = "partial_depth"
    #: دفتر هست و سمت خریدش خالی است
    NO_DEPTH = "no_depth"
    #: دفتر سفارش اصلاً در دسترس نبود (خطای شبکه یا داده)
    UNAVAILABLE = "unavailable"
    #: سررسید گذشته و تسویه هنوز انجام نشده — قیمتش حدس زده نمی‌شود
    EXPIRED_UNSETTLED = "expired_unsettled"

    @property
    def is_priced(self) -> bool:
        """آیا این وضعیت اجازه می‌دهد ارزش روز در جمعِ حساب بیاید؟"""
        return self is ValuationStatus.OK


#: توضیح انسان‌خوانِ هر وضعیت — یک جا، تا رابط و لاگ یک حرف بزنند.
STATUS_LABELS: dict[ValuationStatus, str] = {
    ValuationStatus.OK: "قیمت‌خورده",
    ValuationStatus.PARTIAL_DEPTH: "عمق کافی برای خروج نیست",
    ValuationStatus.NO_DEPTH: "سمت خریدِ دفتر خالی است",
    ValuationStatus.UNAVAILABLE: "دفتر سفارش در دسترس نیست",
    ValuationStatus.EXPIRED_UNSETTLED: "سررسید گذشته — تسویه نامشخص",
}


@dataclass(frozen=True)
class PositionValuation:
    """ارزش‌گذاری یک موقعیت باز در یک لحظه.

    `mark_price` قیمتِ **خروج** است (سمت خریدِ دفتر، برای کل تعداد)، نه
    آخرین معامله و نه میانه‌ی مظنه: حساب باید بگوید اگر همین حالا
    ببندیم چه می‌شود.
    """

    symbol: str
    quantity: int
    contract_size: int
    average_price: float
    #: کارمزد ورودی که به همین تعدادِ باقی‌مانده تعلق دارد (پرداخت‌شده)
    entry_fees_open: float
    status: ValuationStatus
    #: آیا عددِ بالا **دانسته** است؟ موقعیتی که از اسکیمای قدیمی مهاجرت
    #: کرده، کارمزد ورودش ثبت نشده بود؛ صفرِ پیش‌فرض نباید صفرِ قطعی
    #: خوانده شود، وگرنه «خالص»ش بی‌سروصدا خوش‌بینانه می‌شود.
    entry_fees_known: bool = True
    mark_price: float | None = None
    #: بهترین مظنه‌ی خرید — فقط **مرجع**؛ در جمعِ حساب نمی‌آید
    reference_price: float | None = None
    #: چه تعدادی از موقعیت با عمقِ فعلی واقعاً بسته می‌شود
    fillable_quantity: int = 0

    @property
    def gross_cost(self) -> float:
        """پرداختیِ خالصِ پرمیوم برای همین تعداد، **بدون** کارمزد."""
        return self.average_price * self.quantity * self.contract_size

    @property
    def cost_basis(self) -> float | None:
        """پولی که برای همین تعداد رفته است، شاملِ کارمزد ورود.

        `None` وقتی کارمزد ورود دانسته نیست — عددی که یک جزء ناشناخته
        دارد، «سرمایه‌ی درگیر» نیست.
        """
        if not self.entry_fees_known:
            return None
        return self.gross_cost + self.entry_fees_open

    @property
    def market_value(self) -> float | None:
        """ارزش روز؛ `None` یعنی قیمت‌گذاری ممکن نشد — و صفر **نیست**."""
        if not self.status.is_priced or self.mark_price is None:
            return None
        return self.mark_price * self.quantity * self.contract_size

    @property
    def unrealized_gross(self) -> float | None:
        """سود/زیان شناور **پیش از** هزینه."""
        value = self.market_value
        if value is None:
            return None
        return value - self.average_price * self.quantity * self.contract_size

    @property
    def unrealized_net(self) -> float | None:
        """سود/زیان شناور پس از کسر کارمزدِ ورودِ **پرداخت‌شده**.

        کارمزد خروج اینجا کم نمی‌شود: هنوز پرداخت نشده و مقدارش به قیمتِ
        خروجِ آینده بستگی دارد. کم کردنش یعنی خرج‌نکرده را خرج‌شده نشان
        دادن.

        `None` وقتی کارمزد ورود دانسته نیست. صفر گرفتنش «خالص» را به
        «ناخالص» تبدیل می‌کرد و کاربر تفاوتش را نمی‌دید.
        """
        gross = self.unrealized_gross
        if gross is None or not self.entry_fees_known:
            return None
        return gross - self.entry_fees_open

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


@dataclass(frozen=True)
class RealizedTotals:
    """جمعِ معاملات بسته‌شده. ناخالص و هزینه عمداً جدا می‌مانند."""

    gross: float = 0.0
    entry_costs: float = 0.0
    exit_costs: float = 0.0
    trade_count: int = 0
    #: معامله‌هایی که پیش از نسخه‌ی ۲ ثبت شده‌اند و سهمِ کارمزد ورودشان
    #: ثبت نشده است. ناخالصشان درست است ولی خالصشان دانسته نیست.
    trades_missing_entry_cost: int = 0
    #: همان، برای کارمزد خروج (در عمل نباید پیش بیاید؛ برای کامل‌بودن).
    trades_missing_exit_cost: int = 0

    @property
    def costs(self) -> float:
        """هزینه‌های **ثبت‌شده**. همیشه قابل نمایش است، حتی ناقص."""
        return self.entry_costs + self.exit_costs

    @property
    def net(self) -> float | None:
        """سود خالصِ **قطعی**؛ `None` وقتی هزینه‌ی معامله‌ای دانسته نیست.

        معلوم‌بودن هزینه به اطلاعاتِ **همان معامله** بستگی دارد، نه به
        نرخی که امروز در تنظیمات نشسته: نرخ امروز چیزی درباره‌ی هزینه‌ی
        معامله‌ی دیروز نمی‌گوید.
        """
        if not self.costs_complete:
            return None
        return self.gross - self.costs

    @property
    def costs_complete(self) -> bool:
        return self.trades_missing_entry_cost == 0 and self.trades_missing_exit_cost == 0


@dataclass(frozen=True)
class AccountSnapshot:
    """یک عکسِ کامل و قابل تطبیق از حساب کاغذی."""

    initial_balance: float
    cash: float
    positions: tuple[PositionValuation, ...]
    realized: RealizedTotals
    #: آیا کاربر نرخ کارمزدی **تنظیم کرده** است؟ این فقط درباره‌ی
    #: معامله‌های **بعدی** حرف می‌زند. عمداً از `costs_known` جداست:
    #: تنظیم نرخ امروز، هزینه‌ی نامعلومِ معامله‌ی دیروز را معلوم نمی‌کند.
    rates_configured: bool = False
    blocked: float = 0.0
    blocked_reason: str = ""
    #: لحظه‌ی قیمت‌گذاری (ISO). رابط با این می‌فهمد عدد چقدر تازه است.
    priced_at: str | None = None

    # -- نقد -------------------------------------------------------------
    @property
    def available(self) -> float:
        """وجه قابل استفاده برای خرید تازه."""
        return self.cash - self.blocked

    # -- موقعیت‌ها ---------------------------------------------------------
    @property
    def priced_positions(self) -> tuple[PositionValuation, ...]:
        return tuple(p for p in self.positions if p.status.is_priced)

    @property
    def unpriced_positions(self) -> tuple[PositionValuation, ...]:
        return tuple(p for p in self.positions if not p.status.is_priced)

    @property
    def valuation_complete(self) -> bool:
        """آیا همه‌ی موقعیت‌های باز قیمت خورده‌اند؟"""
        return not self.unpriced_positions

    @property
    def market_value_priced(self) -> float:
        """ارزش روزِ همان موقعیت‌هایی که قیمت خورده‌اند."""
        return sum(p.market_value or 0.0 for p in self.priced_positions)

    @property
    def market_value(self) -> float | None:
        """ارزش روزِ کل موقعیت‌ها؛ `None` اگر حتی یکی قیمت نخورده باشد."""
        return self.market_value_priced if self.valuation_complete else None

    # -- سود و زیان --------------------------------------------------------
    @property
    def unrealized_gross(self) -> float | None:
        if not self.valuation_complete:
            return None
        return sum(p.unrealized_gross or 0.0 for p in self.priced_positions)

    @property
    def unrealized_net(self) -> float | None:
        """`None` وقتی ارزش‌گذاری ناقص است **یا** کارمزد ورودی نامعلوم."""
        if not self.valuation_complete:
            return None
        nets = [p.unrealized_net for p in self.priced_positions]
        if any(n is None for n in nets):
            return None
        return sum(n or 0.0 for n in nets)

    @property
    def open_entry_costs(self) -> float:
        """کارمزد ورودیِ **ثبت‌شده** روی موقعیت‌های باز."""
        return sum(p.entry_fees_open for p in self.positions)

    @property
    def positions_with_unknown_cost(self) -> tuple[PositionValuation, ...]:
        """موقعیت‌هایی که کارمزد ورودشان دانسته نیست (مهاجرت‌شده)."""
        return tuple(p for p in self.positions if not p.entry_fees_known)

    @property
    def costs_known(self) -> bool:
        """آیا هزینه‌ی **همه‌ی** عملیات‌های این حساب دانسته است؟

        فقط از داده‌ی ثبت‌شده می‌آید، نه از نرخِ امروز. تا این `False`
        باشد، هیچ عددِ «خالصِ قطعی» نمایش داده نمی‌شود — ولی ناخالص و
        هزینه‌های ثبت‌شده همچنان قابل نمایش‌اند.
        """
        return self.realized.costs_complete and not self.positions_with_unknown_cost

    @property
    def total_costs_recorded(self) -> float:
        """همه‌ی هزینه‌ی **ثبت‌شده** — بسته و باز، بدون شمارشِ دوباره.

        اگر `costs_known` نادرست باشد، این عدد کفِ هزینه است نه کلِ آن.
        """
        return self.realized.costs + self.open_entry_costs

    # -- ارزش کل ----------------------------------------------------------
    @property
    def equity(self) -> float | None:
        """ارزش کل حساب = نقد + ارزش روز موقعیت‌ها.

        وقتی حتی یک موقعیت قیمت نخورده باشد `None` است. عمداً به
        `equity_priced_part` تنزل داده نمی‌شود: عددی که بخشی از دارایی را
        جا انداخته، «ارزش کل حساب» نیست.
        """
        value = self.market_value
        return None if value is None else self.cash + value

    @property
    def equity_priced_part(self) -> float:
        """کفِ ارزش حساب: نقد + هرچه قیمت خورده. همیشه عدد است."""
        return self.cash + self.market_value_priced

    @property
    def total_return_pct(self) -> float | None:
        """بازده کل نسبت به سرمایه‌ی اولیه.

        از **تغییر ارزش حساب** به دست می‌آید، نه از جمعِ درصد معاملات:
        جمعِ درصدها اندازه‌ی موقعیت را نادیده می‌گیرد و با افتِ واقعیِ
        حساب هیچ نسبتی ندارد.
        """
        equity = self.equity
        if equity is None or self.initial_balance <= 0:
            return None
        return (equity - self.initial_balance) / self.initial_balance * 100.0

    # -- تطبیق ------------------------------------------------------------
    @property
    def reconciliation(self) -> dict[str, object]:
        """اتحادِ «تغییر ارزش حساب = تحقق‌یافته + تحقق‌نیافته» را می‌سنجد.

        این عدد برای اعتماد کردن به بقیه‌ی اعداد است: اگر `ok` نباشد
        یعنی جایی از حسابداری خراب است و نباید به ارقام تکیه کرد.
        """
        equity = self.equity
        unrealized = self.unrealized_net
        realized_net = self.realized.net
        if equity is None or unrealized is None or realized_net is None:
            # اتحاد فقط وقتی سنجیدنی است که هر دو طرفش دانسته باشند.
            # سنجیدنش با عددِ ناقص، «نخواند» را به گردنِ حسابداری
            # می‌انداخت، در حالی که مسئله نبودِ داده است.
            return {"applicable": False, "difference": None, "ok": None}
        actual = equity - self.initial_balance
        expected = realized_net + unrealized
        difference = actual - expected
        return {
            "applicable": True,
            "actual_change": actual,
            "expected_change": expected,
            "difference": difference,
            "ok": abs(difference) <= RECONCILIATION_TOLERANCE,
        }


@dataclass(frozen=True)
class OrderRejection:
    """دلیلِ ردِ یک سفارش که به **سرمایه** مربوط است."""

    reason: str
    required: float = 0.0
    available: float = 0.0


def check_affordable(
    required: float, available: float, symbol: str, quantity: int
) -> OrderRejection | None:
    """آیا وجه قابل استفاده کفافِ این خرید را می‌دهد؟

    `None` یعنی می‌دهد. کنترل **پیش از** هر تغییری در حساب انجام می‌شود:
    نقدِ منفی در حساب کاغذی یعنی کل ارزیابیِ استراتژی بی‌معنا شده، چون
    اهرمی که در واقعیت وجود نداشت به آن داده شده است.
    """
    if required <= available + RECONCILIATION_TOLERANCE:
        return None
    return OrderRejection(
        reason=(
            f"وجه قابل استفاده کافی نیست: برای {quantity} قرارداد {symbol} "
            f"{required:,.0f} ریال لازم است ولی {available:,.0f} ریال موجود است."
        ),
        required=required,
        available=available,
    )


def allocate_entry_fee(total_entry_fees: float, closing: int, held: int) -> float:
    """سهمِ کارمزدِ ورودِ تعدادی که دارد بسته می‌شود (تسهیم خطی).

    خروج جزئی باید همان نسبت از هزینه‌ی ورود را با خودش ببرد، وگرنه یا
    دو بار شمرده می‌شود یا در موقعیتِ باقی‌مانده گیر می‌افتد و هرگز در
    هیچ سود و زیانی دیده نمی‌شود.
    """
    if held <= 0:
        return 0.0
    if closing >= held:
        return total_entry_fees
    return total_entry_fees * closing / held


def summarize_trades(trades: list[dict[str, object]]) -> RealizedTotals:
    """جمع‌بندی معاملات بسته‌شده.

    ردیف‌های نسخه‌ی ۱ ستونِ `entry_fee` ندارند (`None`). ناخالصشان درست
    است ولی هزینه‌ی ورودشان دانسته نیست — شمرده می‌شوند تا رابط بتواند
    صریح بگوید خالصِ کدام بخش کامل نیست.
    """
    gross = entry_costs = exit_costs = 0.0
    missing_entry = missing_exit = 0
    for trade in trades:
        gross += float(trade.get("gross_pnl") or 0.0)
        entry_fee = trade.get("entry_fee")
        if entry_fee is None:
            missing_entry += 1
        else:
            entry_costs += float(entry_fee)
        exit_fee = trade.get("exit_fee")
        if exit_fee is None:
            missing_exit += 1
        else:
            exit_costs += float(exit_fee)
    return RealizedTotals(
        gross=gross,
        entry_costs=entry_costs,
        exit_costs=exit_costs,
        trade_count=len(trades),
        trades_missing_entry_cost=missing_entry,
        trades_missing_exit_cost=missing_exit,
    )


def return_on_cost_pct(net_pnl: float | None, cost_basis: float | None) -> float | None:
    """بازده نسبت به **پولی که واقعاً درگیر شد**، نه نسبت به پرمیوم.

    `pnl_pct` قدیمی `(خروج−ورود)/ورود` بود: نه کارمزد داشت، نه اندازه‌ی
    موقعیت. دو معامله با درصدِ یکسان ولی حجم‌های خیلی متفاوت، اثر یکسانی
    روی حساب نمی‌گذارند.
    """
    if net_pnl is None or cost_basis is None or cost_basis <= 0:
        return None
    return net_pnl / cost_basis * 100.0

"""بازارِ آزمایشی — یک دیتاستِ کوچکِ برچسب‌دار برای تمرینِ کلِ مسیر.

**چرا وجود دارد**

مسیرِ واقعیِ محصول درست کار می‌کند ولی امروز **خالی** است: تا وقتی
recorder چند جلسه‌ی معاملاتی ثبت نکند، سنجه‌ی «تداوم معامله» نامعلوم
می‌ماند و غربال هیچ قراردادی را `tradable` نمی‌کند. این رفتارِ درست
است و برای پرشدنِ صفحه شل نمی‌شود — ولی یعنی کاربر نمی‌تواند حتی یک
بار جریانِ «فرصت ← بررسی ← ثبت کاغذی ← نتیجه» را ببیند.

این ماژول همان جریان را با دادهٔ **ساختگیِ صریحاً برچسب‌خورده** قابل
تمرین می‌کند: بدون شبکه، بدون ساعت بازار، و روی حساب و پایگاهِ **جدا**.

**قاعده‌هایی که اینجا شکسته نمی‌شوند**

* دادهٔ آزمایشی هیچ‌جا با دادهٔ واقعی مخلوط نمی‌شود: پایگاه جدا
  (`paper_trading.sandbox_sqlite_path`)، پرچمِ صریح در هر درخواست، و
  برچسب در هر پاسخ.
* عددها **با هم می‌خوانند**: قیمتِ بررسیِ پیش‌از‌ورود، قیمتِ پرشدنِ
  سفارش و ارزش‌گذاریِ موقعیت همه از **همین یک دفتر** می‌آیند. برچسبِ
  آزمایشی جای سازگاریِ عددها را نمی‌گیرد.
* نمونه‌ها عمداً شاملِ گزینه‌های **کنارگذاشتنی** هم هستند (اسپرد مرده،
  ورودِ اجراناپذیر، فروش، چندپایه) تا کاربر ببیند دروازه‌ها واقعاً کار
  می‌کنند، نه اینکه فقط فهرستی از گزینه‌های خوب ببیند.

**آنچه اینجا دیده نمی‌شود:** حالتِ «نرخ کارمزد نامعلوم». حسابِ آزمایشی
نرخِ اعلام‌شده دارد تا عددهایش کامل باشند؛ آن حالت در مسیر واقعی
(نرخِ تنظیم‌نشده) دیده می‌شود و تست‌های `test_opportunity_ranking.py`
پوششش می‌دهند.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from data.option_chain_client import OptionContract
from data.order_book import BookLevel, OrderBook
from market.tradability import HistoryStats, LiquidityObservation

#: برچسبی که همراهِ هر پاسخِ آزمایشی می‌رود. رابط هم همین را نشان می‌دهد.
SANDBOX_LABEL = (
    "دادهٔ آزمایشی — بازار واقعی نیست. حساب، پایگاه و مظنه‌های این مسیر "
    "کاملاً از مسیر واقعی جدا هستند و هیچ سفارشی به بازار نمی‌رود."
)

#: نماد پایه‌ی نمونه و قیمتش. عمداً اسم واقعیِ هیچ سهمی نیست.
SANDBOX_UNDERLYING = "نمونه‌پایه"
SANDBOX_SPOT = 10_500.0
SANDBOX_CONTRACT_SIZE = 1_000
SANDBOX_DAYS_TO_EXPIRY = 45
#: موجودی اولیه‌ی حسابِ آزمایشی (ریال) — مستقل از تنظیماتِ حساب واقعی.
SANDBOX_INITIAL_BALANCE = 1_000_000_000.0
#: نرخِ **اعلام‌شده**ی حسابِ آزمایشی: ۰٫۲۵٪ هر سمت.
SANDBOX_FEE_RATE = 0.0025


@dataclass(frozen=True)
class SandboxSample:
    """یک قراردادِ نمونه، با دفترِ سفارشِ خودش.

    `note` می‌گوید این نمونه **برای دیدنِ چه رفتاری** اینجاست؛ بدون آن،
    فهرست فقط چند عدد است.
    """

    symbol: str
    strike: float
    option_type: str
    bids: tuple[tuple[float, int], ...]
    asks: tuple[tuple[float, int], ...]
    open_interest: int
    trades_today: int
    note: str
    side: str = "buy"
    leg_group_id: str | None = None

    @property
    def ins_code(self) -> str:
        """کدِ یکتای نمونه — با پیشوندی که واقعی‌بودنش را رد می‌کند."""
        return f"sandbox-{self.symbol}"


#: نمونه‌ها. ترتیبشان مهم نیست؛ رتبه‌بندی خودش می‌چیند.
SAMPLES: tuple[SandboxSample, ...] = (
    SandboxSample(
        symbol="نمونه‌الف",
        strike=10_000.0,
        option_type="call",
        bids=((995.0, 200), (700.0, 1_000)),
        asks=((1_005.0, 200),),
        open_interest=500,
        trades_today=40,
        note=(
            "نقدشونده و تنگ. عمقِ دورِ ۷۰۰ عمداً هست تا ببینید در سنجه‌ی "
            "ظرفیتِ خروج شمرده نمی‌شود."
        ),
    ),
    SandboxSample(
        symbol="نمونه‌ب",
        strike=11_000.0,
        option_type="call",
        bids=((950.0, 15),),
        asks=((1_050.0, 15),),
        open_interest=220,
        trades_today=12,
        note="اسپردِ بازتر و عمقِ کمتر — همان گزینه با اجرای بدتر.",
    ),
    SandboxSample(
        symbol="نمونه‌پ",
        strike=10_500.0,
        option_type="call",
        bids=((980.0, 100),),
        asks=((1_020.0, 100),),
        open_interest=310,
        trades_today=25,
        note="حدِ وسط — برای مقایسه‌ی رتبه با دو نمونه‌ی دیگر.",
    ),
    SandboxSample(
        symbol="نمونه‌چ",
        strike=10_200.0,
        option_type="call",
        bids=((980.0, 100),),
        asks=((1_020.0, 1),),
        open_interest=300,
        trades_today=20,
        note=(
            "عمقِ سمت خرید فقط ۱ قرارداد: ورود برای سفارشِ چند قراردادی "
            "اجراپذیر نیست، پس رتبه نمی‌گیرد — کمبودِ قطعیِ عمق."
        ),
    ),
    SandboxSample(
        symbol="نمونه‌ت",
        strike=12_000.0,
        option_type="call",
        bids=((500.0, 100),),
        asks=((1_500.0, 100),),
        open_interest=150,
        trades_today=5,
        note="اسپردِ ۱۰۰٪ — غربال ردش می‌کند، هر چقدر هم جذاب به نظر برسد.",
    ),
    SandboxSample(
        symbol="نمونه‌ث",
        strike=10_800.0,
        option_type="call",
        bids=((980.0, 100),),
        asks=((1_020.0, 100),),
        open_interest=280,
        trades_today=18,
        note="پایه‌ی یک ساختار چندپایه — بیرون از دامنه‌ی نسخه‌ی اول.",
        leg_group_id="grp-نمونه",
    ),
    SandboxSample(
        symbol="نمونه‌ج",
        strike=9_800.0,
        option_type="call",
        bids=((980.0, 100),),
        asks=((1_020.0, 100),),
        open_interest=260,
        trades_today=22,
        note="موقعیتِ فروش — با فرمولِ خرید امتیاز نمی‌گیرد.",
        side="sell",
    ),
)

BY_SYMBOL: dict[str, SandboxSample] = {s.symbol: s for s in SAMPLES}


def expiry(today: date | None = None) -> date:
    """سررسیدِ نمونه‌ها — همیشه در آینده، تا سفارش به‌خاطر سررسید رد نشود."""
    return (today or date.today()) + timedelta(days=SANDBOX_DAYS_TO_EXPIRY)


def book(sample: SandboxSample) -> OrderBook:
    """دفترِ سفارشِ همین نمونه — تنها منبعِ قیمت در کلِ مسیر آزمایشی."""
    return OrderBook(
        sample.symbol,
        bids=tuple(BookLevel(p, q) for p, q in sample.bids),
        asks=tuple(BookLevel(p, q) for p, q in sample.asks),
    )


def contract(sample: SandboxSample, today: date | None = None) -> OptionContract:
    """قراردادِ نمونه، با همان مظنه‌هایی که در دفترش هست."""
    quote = book(sample)
    return OptionContract(
        symbol=sample.symbol,
        underlying=SANDBOX_UNDERLYING,
        option_type=sample.option_type,
        strike=sample.strike,
        expiry=expiry(today),
        bid=quote.best_bid,
        ask=quote.best_ask,
        last_price=quote.best_bid,
        open_interest=sample.open_interest,
        # حجمِ معاملات مدل نشده است؛ صفر یعنی «این نمونه حجم نمی‌گوید».
        # هیچ مصرف‌کننده‌ای در این مسیر به آن تکیه نمی‌کند.
        volume=0,
        contract_size=SANDBOX_CONTRACT_SIZE,
        ins_code=sample.ins_code,
        trade_count=sample.trades_today,
        # **سطح اول**، نه عمقِ کل — همان تعریفی که منبع واقعی می‌دهد.
        bid_quantity=sample.bids[0][1] if sample.bids else None,
        ask_quantity=sample.asks[0][1] if sample.asks else None,
    )


def contracts(today: date | None = None) -> dict[str, OptionContract]:
    return {s.symbol: contract(s, today) for s in SAMPLES}


def history() -> HistoryStats:
    """تاریخچه‌ی نمونه — «کافی و سالم»، تا دروازه‌ی تداوم باز باشد.

    ⚠️ این عدد **ساختگی** است و فقط در مسیرِ آزمایشی استفاده می‌شود.
    در مسیرِ واقعی تاریخچه از `storage/market_history.py` می‌آید و اگر
    نباشد، سنجه نامعلوم می‌ماند — همان‌طور که باید.
    """
    return HistoryStats(
        sessions=20,
        sessions_with_trades=18,
        sessions_with_both_quotes=20,
        known=True,
        sessions_verified=True,
    )


def observation(
    sample: SandboxSample,
    quantity: int,
    now: datetime | None = None,
    max_exit_slippage_pct: float = 10.0,
) -> LiquidityObservation:
    """مشاهده‌ی نقدشوندگی از **همان** دفتر — پس عددها نمی‌توانند واگرا شوند."""
    quote = book(sample)
    moment = now or datetime.now()
    exit_side = "sell" if sample.side == "buy" else "buy"
    entry_side = "buy" if sample.side == "buy" else "sell"
    exit_price, exit_filled = quote.fill_price(exit_side, quantity)
    entry_price, entry_filled = quote.fill_price(entry_side, quantity)
    return LiquidityObservation(
        symbol=sample.symbol,
        position_side=sample.side,
        quantity=quantity,
        observed_at=moment,
        bid=quote.best_bid,
        ask=quote.best_ask,
        exit_depth_contracts=quote.real_depth(exit_side),
        exit_depth_within_band_contracts=quote.depth_within(
            exit_side, max_exit_slippage_pct
        ),
        exit_fill_price=exit_price if exit_filled >= quantity else None,
        entry_fill_price=entry_price if entry_filled >= quantity else None,
        entry_depth_contracts=quote.real_depth(entry_side),
        best_exit_price=quote.best_bid if exit_side == "sell" else quote.best_ask,
        open_interest=sample.open_interest,
        trades_today=sample.trades_today,
        days_to_expiry=SANDBOX_DAYS_TO_EXPIRY,
        quote_age_seconds=5.0,
    )


class SandboxOrderBookClient:
    """دفترِ سفارشِ مسیر آزمایشی — هم‌شکلِ `OrderBookClient`، بدون شبکه.

    فقط همان دو متدی را دارد که مصرف‌کننده‌ها (غربالگر و کارگزارِ کاغذی)
    واقعاً صدا می‌زنند.
    """

    #: عمرِ اعلام‌شده‌ی مظنه. ثابت است چون دادهٔ نمونه کهنه نمی‌شود؛
    #: کهنگیِ **تصمیم** با عمرِ بلیتِ بررسی کنترل می‌شود، نه با این عدد.
    age_seconds = 5.0

    def try_get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook | None:
        sample = BY_SYMBOL.get(symbol) or next(
            (s for s in SAMPLES if s.ins_code == ins_code), None
        )
        return None if sample is None else book(sample)

    def get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook:
        found = self.try_get_order_book(ins_code, symbol)
        if found is None:
            raise ValueError(f"نمونه‌ی آزمایشی برای «{symbol or ins_code}» نیست.")
        return found

    def cache_age_seconds(self, ins_code: str) -> float:
        del ins_code
        return self.age_seconds


def resolve_contract(symbol: str, today: date | None = None) -> OptionContract | None:
    """قراردادِ نمونه با نام — همان امضایی که کارگزارِ کاغذی می‌خواهد."""
    sample = BY_SYMBOL.get(symbol)
    return None if sample is None else contract(sample, today)

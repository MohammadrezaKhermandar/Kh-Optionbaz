"""اینترفیس زنجیره آپشن: استرایک‌ها، سررسیدها و پرمیوم لحظه‌ای.

تنها پیاده‌سازی‌ها روی داده‌ی **واقعی** کار می‌کنند:
`TsetmcOptionChainClient` (زنده) و `FilePayloadSource` (پاسخ ضبط‌شده).
زنجیره‌ی مصنوعی وجود ندارد.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

from pricing.black_scholes import years_to_expiry

# اندازه استاندارد قرارداد آپشن در بورس تهران (تعداد سهم پایه در هر قرارداد)
DEFAULT_CONTRACT_SIZE = 1_000


@dataclass(frozen=True)
class OptionContract:
    """یک نماد آپشن در زنجیره."""

    symbol: str
    underlying: str
    option_type: str  # "call" | "put"
    strike: float
    expiry: date
    bid: float | None = None
    ask: float | None = None
    last_price: float | None = None
    open_interest: int = 0
    volume: int = 0
    contract_size: int = DEFAULT_CONTRACT_SIZE
    #: کد یکتای نماد در TSETMC — کلیدِ گرفتن عمق مظنه برای همین قرارداد.
    #: خالی می‌ماند اگر منبع آن را ندهد؛ آن‌وقت فقط عمق در دسترس نیست.
    ins_code: str = ""

    # --- سنجه‌های نقدشوندگی؛ همه `None`-پذیر ---
    #: تعداد **معامله‌ی** امروز. با `volume` یکی نیست: یک معامله‌ی بزرگ
    #: حجم می‌سازد ولی تداوم نه. `None` یعنی منبع نداده، نه صفر.
    trade_count: int | None = None
    #: حجمِ سطح اولِ سمت خرید (تعداد قرارداد). برای خروج از موقعیت long
    #: همین سمت است که باید بخرد.
    bid_quantity: int | None = None
    #: حجمِ سطح اولِ سمت فروش. برای بستنِ موقعیت فروش لازم است.
    ask_quantity: int | None = None

    @property
    def mid_price(self) -> float | None:
        """میانه مظنه خرید/فروش؛ اگر نبود، آخرین معامله."""
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2.0
        return self.last_price

    def days_to_expiry(self, today: date | None = None) -> int:
        return max((self.expiry - (today or date.today())).days, 0)

    def time_to_expiry(self, today: date | None = None) -> float:
        """زمان تا سررسید بر حسب سال."""
        return years_to_expiry(self.days_to_expiry(today))

    def is_liquid(self, min_open_interest: int = 1, min_volume: int = 0) -> bool:
        return self.open_interest >= min_open_interest and self.volume >= min_volume


@dataclass(frozen=True)
class OptionChain:
    """زنجیره کامل آپشن یک نماد پایه در یک لحظه."""

    underlying: str
    spot_price: float
    as_of: datetime
    contracts: tuple[OptionContract, ...]

    def expiries(self) -> list[date]:
        return sorted({c.expiry for c in self.contracts})

    def filter(
        self,
        option_type: str | None = None,
        expiry: date | None = None,
        min_days: int | None = None,
        max_days: int | None = None,
    ) -> list[OptionContract]:
        """فیلتر ساده روی نوع، سررسید و بازه روز باقی‌مانده."""
        today = self.as_of.date()
        result = list(self.contracts)
        if option_type:
            result = [c for c in result if c.option_type == option_type.lower()]
        if expiry:
            result = [c for c in result if c.expiry == expiry]
        if min_days is not None:
            result = [c for c in result if c.days_to_expiry(today) >= min_days]
        if max_days is not None:
            result = [c for c in result if c.days_to_expiry(today) <= max_days]
        return result

    def nearest_strike(
        self, option_type: str, target_strike: float, expiry: date | None = None
    ) -> OptionContract | None:
        """نزدیک‌ترین استرایک به مقدار هدف (مثلاً برای انتخاب ATM)."""
        candidates = self.filter(option_type=option_type, expiry=expiry)
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(c.strike - target_strike))


class OptionChainClient(ABC):
    """قرارداد دریافت زنجیره آپشن."""

    #: برای برچسب‌زدن منبع داده روی هر سیگنال
    source_name: str = "unknown"

    @abstractmethod
    def get_chain(self, underlying: str) -> OptionChain:
        """زنجیره آپشن نماد پایه در لحظه فعلی."""

    @abstractmethod
    def get_contract(self, option_symbol: str) -> OptionContract | None:
        """یک نماد آپشن مشخص (برای رفرش پرمیوم قبل از صدور سیگنال)."""



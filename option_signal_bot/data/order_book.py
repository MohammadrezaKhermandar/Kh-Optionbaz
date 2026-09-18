"""عمق مظنه (دفتر سفارش چندسطحی) از TSETMC.

**مسئله‌ای که حل می‌کند**

تا اینجا فقط **بهترین** مظنه‌ی خرید/فروش را داشتیم. با یک سطح، این سؤال
بی‌جواب می‌ماند:

> اگر بخواهم ۱۰ قرارداد بخرم، واقعاً به چه قیمتی پر می‌شود؟

در بازار آپشن تهران این سؤال حیاتی است، چون عمق واقعاً کم است. یک نماد
ممکن است بهترین فروش را روی ۲۶٬۵۰۰ نشان دهد ولی فقط **۱ قرارداد** در آن
سطح باشد؛ قرارداد دوم به بعد از ۳۱٬۹۹۹ پر می‌شود. سیگنالی که با مظنه‌ی
سطح‌اول اندازه‌گیری شده باشد، روی کاغذ سودده است و در عمل با ۲۰٪ لغزش
اجرا می‌شود.

**منبع**

    GET https://cdn.tsetmc.com/api/BestLimits/{insCode}

پاسخ: `bestLimits[]` با ۵ سطح، هر سطح شامل قیمت، حجم و **تعداد سفارش**
در هر دو سمت. بدون احراز هویت، مثل بقیه‌ی منابع این پروژه.

نگاشتِ خودِ پاسخ در `data/tsetmc_quote_client.py::parse_best_limits`
است و اینجا فقط صدا زده می‌شود. این ماژول **منطق** را اضافه می‌کند
(«با این حجم چه قیمتی پر می‌شود»)، نه یک نگاشت دوم — دو نگاشت موازی از
یک پاسخ دیر یا زود واگرا می‌شوند و بعد دو عدد مختلف برای «بهترین مظنه»
می‌دهند.

**چرا اختیاری است**

این endpoint هر بار فقط یک نماد می‌دهد. زنجیره‌ی کامل ۶۹۳ ردیف دارد؛
گرفتن عمق برای همه یعنی ۶۹۳ درخواست. پس عمق فقط برای قراردادهایی گرفته
می‌شود که واقعاً به سیگنال رسیده‌اند — همان الگویی که `enrich_with_broker`
هم دارد.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from data.quote_source import RealtimeQuoteSource
from data.tsetmc_http import DEFAULT_USER_AGENT, fetch_json

logger = logging.getLogger(__name__)

BEST_LIMITS_URL = "https://cdn.tsetmc.com/api/BestLimits/{ins_code}"
BEST_LIMITS_KEY = "bestLimits"


@dataclass(frozen=True)
class BookLevel:
    """یک سطح از دفتر سفارش، در یک سمت."""

    price: float
    quantity: int
    orders: int = 0

    @property
    def is_real(self) -> bool:
        """سطحی که قیمت یا حجمش صفر است، سطح نیست — جای خالی است."""
        return self.price > 0 and self.quantity > 0


@dataclass(frozen=True)
class OrderBook:
    """دفتر سفارش چندسطحی یک نماد.

    هر دو سمت **مرتب** نگه داشته می‌شوند: خرید نزولی (بهترین=بالاترین)،
    فروش صعودی (بهترین=کمترین). به این ترتیب `bids[0]` و `asks[0]` همیشه
    بهترین مظنه‌اند، فارغ از اینکه منبع به چه ترتیبی داده باشد.
    """

    symbol: str
    bids: tuple[BookLevel, ...] = field(default_factory=tuple)
    asks: tuple[BookLevel, ...] = field(default_factory=tuple)

    # -- بهترین مظنه ---------------------------------------------------
    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid

    @property
    def relative_spread(self) -> float | None:
        """اسپرد نسبت به میانه — معیار مقایسه‌پذیر بین نمادهای گران و ارزان."""
        if self.best_bid is None or self.best_ask is None:
            return None
        mid = (self.best_bid + self.best_ask) / 2.0
        return None if mid <= 0 else (self.best_ask - self.best_bid) / mid

    # -- عمق ------------------------------------------------------------
    def depth(self, side: str) -> int:
        """مجموع حجم قابل معامله در یک سمت (`buy` یا `sell`)."""
        return sum(level.quantity for level in self._levels(side))

    def real_depth(self, side: str) -> int:
        """همان `depth`، ولی فقط روی سطوح **واقعی**.

        سطحی که قیمت یا حجمش صفر است جای خالی است و `fill_price` هم
        نادیده‌اش می‌گیرد. اگر ظرفیت خروج را با `depth` بسنجیم و
        پرشدن را با `fill_price`، دو عدد ناسازگار می‌گیریم.
        """
        return sum(level.quantity for level in self._levels(side) if level.is_real)

    def _levels(self, side: str) -> tuple[BookLevel, ...]:
        """سطوحی که باید بخوریم تا سفارشِ `side` پر شود.

        خریدِ ما روی **فروش**های دفتر پر می‌شود و برعکس. این وارونگی
        منبع رایج اشتباه است، پس در یک جا متمرکز شده.
        """
        normalized = side.lower()
        if normalized in ("buy", "خرید"):
            return self.asks
        if normalized in ("sell", "فروش"):
            return self.bids
        raise ValueError(f"سمت نامعتبر: «{side}» (buy یا sell)")

    def fill_price(self, side: str, quantity: int) -> tuple[float | None, int]:
        """قیمت میانگین وزنیِ پر شدن `quantity` قرارداد.

        Returns:
            `(میانگین وزنی, حجمی که واقعاً پر می‌شود)`. اگر عمق کافی نباشد،
            حجم پرشده **کمتر** از درخواست برمی‌گردد و میانگین فقط برای همان
            بخش است. عمداً میانگینِ خوش‌بینانه‌ی سطح‌اول را برنمی‌گرداند:
            بهتر است بدانیم سفارش پر نمی‌شود تا اینکه فکر کنیم ارزان پر شده.
        """
        if quantity <= 0:
            return None, 0

        remaining = quantity
        cost = 0.0
        for level in self._levels(side):
            if not level.is_real or remaining <= 0:
                continue
            take = min(remaining, level.quantity)
            cost += take * level.price
            remaining -= take

        filled = quantity - remaining
        return (cost / filled if filled else None), filled

    def slippage(self, side: str, quantity: int) -> float | None:
        """فاصله‌ی نسبیِ قیمت پر شدن از بهترین مظنه.

        `0.05` یعنی ۵٪ بدتر از سطح اول. اگر سفارش اصلاً پر نشود، `None`.
        """
        avg, filled = self.fill_price(side, quantity)
        if avg is None or filled < quantity:
            return None
        levels = self._levels(side)
        best = next((lv.price for lv in levels if lv.is_real), None)
        if not best:
            return None
        return abs(avg - best) / best

    def can_fill(self, side: str, quantity: int) -> bool:
        """آیا عمق موجود برای این حجم کافی است؟"""
        return self.fill_price(side, quantity)[1] >= quantity

    # -- ساخت -----------------------------------------------------------
    @classmethod
    def from_depth(cls, symbol: str, levels: Iterable[Any]) -> OrderBook:
        """از سطوح `DepthLevel` (خروجی `data/tsetmc_quote_client.py`).

        نگاشت خامِ `BestLimits` **یک جا** انجام می‌شود — در
        `TsetmcQuoteClient.get_depth`. این کلاس فقط منطق «با این حجم چه
        قیمتی پر می‌شود» را اضافه می‌کند. دو نگاشتِ موازی از یک پاسخ،
        دیر یا زود با هم واگرا می‌شوند.
        """
        bids: list[BookLevel] = []
        asks: list[BookLevel] = []

        for level in levels:
            bid = BookLevel(
                price=_number(getattr(level, "bid_price", 0)),
                quantity=int(_number(getattr(level, "bid_quantity", 0))),
                orders=int(_number(getattr(level, "bid_orders", 0))),
            )
            ask = BookLevel(
                price=_number(getattr(level, "ask_price", 0)),
                quantity=int(_number(getattr(level, "ask_quantity", 0))),
                orders=int(_number(getattr(level, "ask_orders", 0))),
            )
            if bid.is_real:
                bids.append(bid)
            if ask.is_real:
                asks.append(ask)

        # ترتیب را خودمان تضمین می‌کنیم؛ به ترتیب منبع تکیه نمی‌کنیم.
        bids.sort(key=lambda lv: lv.price, reverse=True)
        asks.sort(key=lambda lv: lv.price)
        return cls(symbol=symbol, bids=tuple(bids), asks=tuple(asks))

    @classmethod
    def from_tsetmc(cls, symbol: str, payload: dict) -> OrderBook:
        """از پاسخ خام `BestLimits`.

        نگاشت فیلدها را به `parse_best_limits` واگذار می‌کند تا همان
        کدی اجرا شود که `TsetmcQuoteClient` استفاده می‌کند.
        """
        from data.tsetmc_quote_client import parse_best_limits

        return cls.from_depth(symbol, parse_best_limits(payload, symbol))

    def to_dict(self) -> dict:
        """برای داشبورد و لاگ."""
        return {
            "symbol": self.symbol,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "relative_spread": self.relative_spread,
            "bid_depth": self.depth("sell"),
            "ask_depth": self.depth("buy"),
            "bids": [
                {"price": lv.price, "quantity": lv.quantity, "orders": lv.orders}
                for lv in self.bids
            ],
            "asks": [
                {"price": lv.price, "quantity": lv.quantity, "orders": lv.orders}
                for lv in self.asks
            ],
        }


def _number(value: object) -> float:
    """عدد یا صفر — پاسخ TSETMC گاهی `null` می‌دهد."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


class OrderBookClient(RealtimeQuoteSource):
    """عمق مظنه از TSETMC (پولینگ)، با کش کوتاه.

    کش عمداً کوتاه است (پیش‌فرض ۱۰ ثانیه): دفتر سفارش سریع‌ترین چیزِ
    متغیر بازار است و عمقِ کهنه بدتر از عمقِ نداشته است — چون اعتماد
    کاذب می‌سازد.

    Args:
        ttl_seconds: عمر کش هر نماد.
        timeout / retries / user_agent: مثل بقیه‌ی کلاینت‌های TSETMC.
    """

    source_name = "tsetmc"
    name = "tsetmc"
    #: پولینگ است، نه push: هر بار یک درخواست HTTP. مسیر push
    #: (Lightstreamer ایزی‌تریدر) پیاده‌سازی جداگانه‌ای از همین قرارداد
    #: خواهد بود.
    is_push = False

    def __init__(
        self,
        ttl_seconds: float = 10.0,
        timeout: int = 15,
        retries: int = 2,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent
        self._cache: dict[str, tuple[float, OrderBook]] = {}

    def cache_age_seconds(self, ins_code: str) -> float | None:
        """عمرِ نسخه‌ی کش‌شده‌ی این دفتر، بر حسب ثانیه.

        `None` یعنی چیزی در کش نیست. ⚠️ این عمرِ **دریافتِ ما**ست، نه
        زمانِ بازار: منبع برای دفتر سفارش مهر زمانی نمی‌دهد، پس
        نمی‌شود گفت داده در بازار چقدر کهنه است. جای هم گذاشتنشان یعنی
        ادعای چیزی که نمی‌دانیم.
        """
        cached = self._cache.get(ins_code)
        if cached is None:
            return None
        return max(time.monotonic() - cached[0], 0.0)

    def get_order_book(self, ins_code: str, symbol: str = "") -> OrderBook:
        """دفتر سفارش یک نماد. خطای شبکه را **بالا می‌برد**."""
        cached = self._cache.get(ins_code)
        if cached is not None and (time.monotonic() - cached[0]) < self.ttl_seconds:
            return cached[1]

        payload = fetch_json(
            BEST_LIMITS_URL.format(ins_code=ins_code),
            timeout=self.timeout,
            retries=self.retries,
            user_agent=self.user_agent,
        )
        book = OrderBook.from_tsetmc(symbol or ins_code, payload)
        self._cache[ins_code] = (time.monotonic(), book)
        return book


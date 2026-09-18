"""جمع‌آوری داده‌ی زنده برای غربالِ قابلیت معامله.

`market/tradability.py` منطقِ خالص است و هیچ‌جا نگاه نمی‌کند؛ این فایل
همان منطق را به داده‌ی واقعی وصل می‌کند:

* **مظنه و عمق** از `OptionChainClient` و — اگر در دسترس باشد — از دفتر
  چندسطحیِ `data/order_book.py`. عمقِ چندسطحی مهم است چون سطح اول
  معمولاً کوچک است و سفارش بزرگ‌تر از آن، همان‌جا گیر می‌کند.
* **تداوم معامله** از `storage/market_history.py` که فقط‌خواندنی روی
  پایگاه recorder کار می‌کند.

⚠️ این لایه هرگز داده نمی‌سازد. هر چیزی که پیدا نشود `None` می‌ماند و
منطقِ خالص آن را «نامعلوم» می‌خواند، نه «صفر».
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from market.tradability import (
    HistoryStats,
    LiquidityObservation,
    Thresholds,
    TradabilityReport,
    evaluate,
)

logger = logging.getLogger(__name__)


class TradabilityScreener:
    """غربالِ قابلیت معامله روی داده‌ی واقعیِ بازار.

    Args:
        resolve_contract: نام نماد آپشن → `OptionContract` (برای مظنه،
            موقعیت باز، سررسید و `ins_code`)
        thresholds: آستانه‌ها؛ از تنظیمات می‌آید
        order_book_client: اختیاری. هر شیء با
            `try_get_order_book(ins_code, symbol)`. نبودش یعنی عمق فقط
            از سطح اولِ زنجیره خوانده می‌شود.
        history: اختیاری. `MarketHistoryReader`؛ نبودش یعنی تداومِ
            معامله «نامعلوم» می‌ماند.
    """

    def __init__(
        self,
        resolve_contract: Any,
        thresholds: Thresholds | None = None,
        order_book_client: Any | None = None,
        history: Any | None = None,
    ) -> None:
        self.resolve_contract = resolve_contract
        self.thresholds = thresholds or Thresholds()
        self.order_book_client = order_book_client
        self.history = history

    # ------------------------------------------------------------------
    def evaluate_signal(self, signal: Any) -> TradabilityReport:
        """غربالِ یک سیگنال با **تعداد پیشنهادیِ خودش**."""
        return self.evaluate_symbol(
            symbol=signal.symbol,
            position_side=signal.side.value,
            quantity=max(int(signal.suggested_qty or 0), 1),
            now=signal.created_at,
        )

    def evaluate_symbol(
        self,
        symbol: str,
        position_side: str,
        quantity: int,
        now: datetime | None = None,
    ) -> TradabilityReport:
        """غربالِ یک نماد آپشن برای اندازه‌ی سفارشِ مشخص."""
        moment = now or datetime.now()
        contract = self._safe_contract(symbol)
        observation = self._observe(symbol, position_side, quantity, contract, moment)
        stats = self._history_for(contract)
        return evaluate(observation, stats, self.thresholds)

    # ------------------------------------------------------------------
    def _safe_contract(self, symbol: str) -> Any | None:
        try:
            return self.resolve_contract(symbol)
        except Exception as exc:  # نبودِ قرارداد نباید پاس رصد را بخواباند
            logger.warning("قرارداد %s خوانده نشد: %s", symbol, exc)
            return None

    def _observe(
        self,
        symbol: str,
        position_side: str,
        quantity: int,
        contract: Any | None,
        moment: datetime,
    ) -> LiquidityObservation:
        if contract is None:
            return LiquidityObservation(
                symbol=symbol,
                position_side=position_side,
                quantity=quantity,
                observed_at=moment,
            )

        exit_side = "sell" if position_side.lower() == "buy" else "buy"
        entry_side = "buy" if exit_side == "sell" else "sell"
        book_data = self._exit_book(contract, exit_side, quantity, entry_side)
        # مظنه‌ی دفترِ چندسطحی تازه‌تر از زنجیره است؛ اگر بود، همان.
        bid = book_data["bid"] if book_data["bid"] is not None else getattr(
            contract, "bid", None
        )
        ask = book_data["ask"] if book_data["ask"] is not None else getattr(
            contract, "ask", None
        )

        return LiquidityObservation(
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            observed_at=moment,
            bid=bid,
            ask=ask,
            exit_depth_contracts=book_data["depth"],
            exit_fill_price=book_data["fill_price"],
            best_exit_price=book_data["best_exit"],
            entry_fill_price=book_data["entry_fill_price"],
            exit_depth_within_band_contracts=book_data["depth_in_band"],
            open_interest=getattr(contract, "open_interest", None),
            trades_today=self._trades_today(contract),
            days_to_expiry=self._days_to_expiry(contract, moment),
            # عمرِ **واقعیِ** نسخه‌ای که به ما رسید — از کشِ کلاینت.
            # پیش از این صفر ثابت بود، پس آستانه‌ی کهنگی هرگز اثر
            # نمی‌کرد و سنجه‌ای بود که همیشه می‌گذشت.
            quote_age_seconds=book_data["age"],
            # ⚠️ منبع مهر زمانی نمی‌دهد؛ زمانِ دریافت جایش گذاشته نمی‌شود.
            source_time=self._source_time(contract),
        )

    def _exit_book(
        self, contract: Any, exit_side: str, quantity: int, entry_side: str
    ) -> dict[str, Any]:
        """ظرفیت و قیمتِ **هر دو سمت** برای همین اندازه‌ی سفارش.

        * `depth` — عمقِ **کل** سمت خروج. عمداً به اندازه‌ی سفارش بریده
          نمی‌شود (اشکالِ نسخه‌ی قبل، که نسبت را هرگز بالای ۱ نمی‌برد).
        * `depth_in_band` — همان عمق، ولی فقط سطوحی که تا آستانه‌ی لغزش
          از بهترین مظنه فاصله دارند. حجمِ قیمت‌های دور حاشیه‌ی امن
          نیست.
        * `fill_price` / `entry_fill_price` — میانگین وزنیِ پرشدنِ
          **همین** سفارش در سمت خروج و ورود. داشتنِ هر دو لازم است تا
          هزینه‌ی رفت‌وبرگشت از قیمتِ اجراپذیر بیاید، نه از نصفِ اسپرد.
        * `best_exit` — بهترین مظنه‌ی سمت خروج.

        اختلاف `fill_price` و `best_exit` همان لغزش است.
        """
        ins_code = getattr(contract, "ins_code", "")
        if self.order_book_client is not None and ins_code:
            try:
                book = self.order_book_client.try_get_order_book(ins_code, contract.symbol)
            except Exception as exc:
                logger.warning("دفتر سفارش %s خوانده نشد: %s", contract.symbol, exc)
                book = None
            if book is not None:
                needed = max(quantity, 1)
                fill_price, filled = book.fill_price(exit_side, needed)
                entry_price, entry_filled = book.fill_price(entry_side, needed)
                best_exit = book.best_bid if exit_side == "sell" else book.best_ask
                return {
                    "depth": int(book.real_depth(exit_side)),
                    # قیمتِ پرشدن فقط وقتی معنا دارد که سفارش **کامل** پر
                    # شود؛ میانگینِ یک پرشدنِ ناقص، لغزشِ واقعی را
                    # کم‌برآورد می‌کند. همین قاعده برای سمت ورود هم هست.
                    "fill_price": fill_price if filled >= needed else None,
                    "entry_fill_price": entry_price if entry_filled >= needed else None,
                    "depth_in_band": book.depth_within(
                        exit_side, self.thresholds.max_exit_slippage_pct
                    ),
                    "best_exit": best_exit,
                    "bid": book.best_bid,
                    "ask": book.best_ask,
                    "age": self._book_age(ins_code),
                }

        # بدون دفترِ چندسطحی فقط سطح اولِ زنجیره در دست است: عمق همان
        # سطح، و چون تک‌سطحی است لغزشی هم ندارد (قیمت پرشدن = بهترین
        # مظنه) — مشروط بر اینکه سفارش در همان سطح جا شود.
        level_one = getattr(
            contract, "bid_quantity" if exit_side == "sell" else "ask_quantity", None
        )
        best_exit = getattr(contract, "bid" if exit_side == "sell" else "ask", None)
        entry_level_one = getattr(
            contract, "ask_quantity" if entry_side == "buy" else "bid_quantity", None
        )
        best_entry = getattr(contract, "ask" if entry_side == "buy" else "bid", None)
        needed = max(quantity, 1)
        fits = level_one is not None and level_one >= needed
        entry_fits = entry_level_one is not None and entry_level_one >= needed
        return {
            "depth": None if level_one is None else int(level_one),
            "fill_price": best_exit if fits else None,
            "entry_fill_price": best_entry if entry_fits else None,
            # تک‌سطحی است: همان سطح، اگر اصلاً سطحی باشد. فاصله‌اش از
            # بهترین مظنه صفر است، پس کلِ آن در محدوده می‌نشیند.
            "depth_in_band": None if level_one is None else int(level_one),
            "best_exit": best_exit,
            "bid": getattr(contract, "bid", None),
            "ask": getattr(contract, "ask", None),
            # زنجیره عمرِ کش را اعلام نمی‌کند؛ «نامعلوم» می‌ماند، نه صفر.
            "age": None,
        }

    def _book_age(self, ins_code: str) -> float | None:
        """عمرِ واقعیِ نسخه‌ی کش‌شده‌ی دفتر، اگر کلاینت اعلامش کند."""
        getter = getattr(self.order_book_client, "cache_age_seconds", None)
        if getter is None:
            return None
        try:
            return getter(ins_code)
        except Exception as exc:
            logger.warning("عمر کش دفتر %s خوانده نشد: %s", ins_code, exc)
            return None

    @staticmethod
    def _source_time(contract: Any) -> Any | None:
        """مهر زمانیِ بازار، فقط اگر منبع واقعاً بدهد.

        دیده‌بان اختیار TSETMC نمی‌دهد — همان دلیلی که در recorder هم
        `source_time` همیشه `NULL` است. اینجا هیچ‌چیز جایش ساخته
        نمی‌شود.
        """
        return getattr(contract, "source_time", None)

    @staticmethod
    def _trades_today(contract: Any) -> int | None:
        for attribute in ("trade_count", "trades_today"):
            value = getattr(contract, attribute, None)
            if value is not None:
                return int(value)
        # `volume` جای «تعداد معامله» را نمی‌گیرد: یک معامله‌ی بزرگ حجم
        # می‌سازد ولی تداوم نه. نبودش «نامعلوم» است.
        return None

    @staticmethod
    def _days_to_expiry(contract: Any, moment: datetime) -> int | None:
        expiry = getattr(contract, "expiry", None)
        if expiry is None:
            return None
        return (expiry - moment.date()).days

    def _history_for(self, contract: Any | None) -> HistoryStats:
        ins_code = getattr(contract, "ins_code", "") if contract is not None else ""
        if self.history is None or not ins_code:
            return HistoryStats(known=False)
        try:
            return self.history.stats_for(str(ins_code))
        except Exception as exc:  # تاریخچه‌ی خراب نباید پاس را بخواباند
            logger.warning("تاریخچه‌ی %s خوانده نشد: %s", ins_code, exc)
            return HistoryStats(known=False)

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
        depth, book_bid, book_ask = self._depth(contract, exit_side, quantity)
        # مظنه‌ی دفترِ چندسطحی تازه‌تر از زنجیره است؛ اگر بود، همان.
        bid = book_bid if book_bid is not None else getattr(contract, "bid", None)
        ask = book_ask if book_ask is not None else getattr(contract, "ask", None)

        return LiquidityObservation(
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            observed_at=moment,
            bid=bid,
            ask=ask,
            exit_depth_contracts=depth,
            open_interest=getattr(contract, "open_interest", None),
            trades_today=self._trades_today(contract),
            days_to_expiry=self._days_to_expiry(contract, moment),
            # داده از همین لحظه آمده؛ کهنگی‌اش صفر است. عددِ واقعیِ کهنگی
            # وقتی معنا دارد که کش عمر داشته باشد — آن را منبع می‌داند،
            # نه اینجا.
            quote_age_seconds=0.0,
        )

    def _depth(
        self, contract: Any, exit_side: str, quantity: int
    ) -> tuple[int | None, float | None, float | None]:
        """عمقِ قابل اجرا در سمت خروج، به‌علاوه‌ی بهترین مظنه‌های دفتر."""
        ins_code = getattr(contract, "ins_code", "")
        if self.order_book_client is not None and ins_code:
            try:
                book = self.order_book_client.try_get_order_book(ins_code, contract.symbol)
            except Exception as exc:
                logger.warning("دفتر سفارش %s خوانده نشد: %s", contract.symbol, exc)
                book = None
            if book is not None:
                # `fill_price` می‌گوید چه تعدادی واقعاً پر می‌شود — همان
                # چیزی که «امکان خروج» یعنی آن.
                _, fillable = book.fill_price(exit_side, max(quantity, 1))
                return int(fillable), book.best_bid, book.best_ask

        # بدون دفترِ چندسطحی، فقط سطح اولِ زنجیره در دست است. اگر حجمش
        # هم نباشد، عمق **نامعلوم** می‌ماند و صفر فرض نمی‌شود.
        level_one = getattr(
            contract, "bid_quantity" if exit_side == "sell" else "ask_quantity", None
        )
        return (
            None if level_one is None else int(level_one),
            getattr(contract, "bid", None),
            getattr(contract, "ask", None),
        )

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

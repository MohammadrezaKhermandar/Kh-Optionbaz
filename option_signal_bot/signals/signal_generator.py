"""ارکستریتور تولید سیگنال: دیتا → استراتژی‌ها → ریسک → لیست سیگنال.

⚠️ قرارداد معماری (مهم‌ترین نکته این مایل‌استون):
این ماژول **هیچ‌گاه** لایه `execution` را import یا صدا نمی‌زند. خروجی آن فقط
لیستی از `Signal` است و تصمیم اجرا کاملاً بیرون از این‌جا (و دستی) گرفته می‌شود.
تست `tests/test_signal_generator.py` این جداسازی را بررسی می‌کند.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from data.market_data_client import MarketDataClient
from data.option_chain_client import OptionChainClient
from market.tradability import ScreeningRecord, ScreeningSummary, Verdict
from risk.risk_calculator import RiskCalculator
from signals.signal_model import Signal
from strategies.base_strategy import BaseStrategy, StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class GeneratorConfig:
    """تنظیمات حلقه تولید سیگنال."""

    symbols: list[str] = field(default_factory=lambda: ["خودرو", "فولاد"])
    risk_free_rate: float = 0.25
    history_days: int = 90
    #: تا این مدت، سیگنال تکراری برای همان نماد/سمت دوباره صادر نمی‌شود
    dedupe_window_minutes: int = 60
    #: حداقل اعتماد لازم برای انتشار سیگنال (None = بدون فیلتر)
    min_confidence: float | None = None
    signal_validity_minutes: int = 30


class SignalGenerator:
    """استراتژی‌ها را روی نمادهای هدف اجرا و سیگنال‌های نهایی را برمی‌گرداند."""

    def __init__(
        self,
        market_data: MarketDataClient,
        option_chain: OptionChainClient,
        strategies: list[BaseStrategy],
        risk_calculator: RiskCalculator | None = None,
        config: GeneratorConfig | None = None,
        holdings_provider: Callable[[], dict[str, int]] | None = None,
        iv_history: Any | None = None,
        tradability: Any | None = None,
    ) -> None:
        self.market_data = market_data
        self.option_chain = option_chain
        self.strategies = strategies
        self.risk_calculator = risk_calculator or RiskCalculator()
        self.config = config or GeneratorConfig()
        #: غربالِ قابلیت معامله (`TradabilityScreener`). `None` یعنی خاموش —
        #: آن‌وقت هیچ سیگنالی به‌خاطر نقدشوندگی رد نمی‌شود و این در
        #: خلاصه‌ی پاس هم اعلام می‌شود، تا «غربال نشد» با «همه قبول شدند»
        #: اشتباه نشود.
        self.tradability = tradability
        #: نتیجه‌ی غربالِ آخرین پاس — رابط از همین می‌خواند.
        self.screening = ScreeningSummary()
        self._last_emitted: dict[str, datetime] = {}
        #: تابعی که «نام نماد → تعداد سهم» می‌دهد. عمداً یک callable است و
        #: نه کلاینت کارگزاری: این لایه هم مثل استراتژی‌ها نباید بداند
        #: کارگزاری کیست.
        self.holdings_provider = holdings_provider
        #: کش یک‌پاسی. بدون این، هر نماد یک درخواست به کارگزاری می‌زند
        #: در حالی که یک پاسخ همه‌ی دارایی‌ها را دارد.
        self._holdings: dict[str, int] | None = None
        #: تاریخچه‌ی IV هر نماد (`IVHistory`) — برای تشخیص گران/ارزان
        #: نسبت به گذشته‌ی **خودِ** نماد، نه نسبت به یک آستانه‌ی سراسری.
        self.iv_history = iv_history

        # ------------------------------------------------------------------
        # شمارنده‌های آخرین پاس — برای پایش سلامت.
        #
        # این کلاس عمداً خطای یک نماد یا یک استراتژی را می‌بلعد تا بقیه
        # بمانند؛ رفتار درستی است، ولی یعنی یک استراتژیِ **کاملاً مرده**
        # هم بی‌صدا نادیده گرفته می‌شود. پس شکست‌ها شمرده می‌شوند تا
        # `HealthMonitor` بتواند سکوت را بشکند.
        # ------------------------------------------------------------------
        #: نام نمادهایی که در آخرین پاس شکست خوردند
        self.failed_symbols: list[str] = []
        #: نام استراتژی → تعداد خطا در آخرین پاس
        self.strategy_errors: dict[str, int] = {}

    def reset_holdings_cache(self) -> None:
        """کش دارایی را خالی می‌کند تا پاس بعدی از نو بخواند."""
        self._holdings = None

    def _holding_for(self, symbol: str) -> int | None:
        """تعداد سهمِ یک نماد — یا `None` اگر معلوم نباشد.

        تفاوت `None` و `0` تصمیم‌ساز است: اولی «نمی‌دانم» و دومی «نداری».
        پس شکستِ خواندن هرگز به صفر تبدیل نمی‌شود؛ صفر فقط وقتی برمی‌گردد
        که واقعاً لیستِ دارایی را دیده باشیم و این نماد در آن نباشد.
        """
        if self.holdings_provider is None:
            return None

        if self._holdings is None:
            try:
                self._holdings = self.holdings_provider()
            except Exception as exc:  # نبود دارایی نباید پاس رصد را بخواباند
                logger.warning("دارایی سهم خوانده نشد؛ مالکیت نامعلوم ماند: %s", exc)
                return None

        return self._holdings.get(symbol, 0)

    # ------------------------------------------------------------------
    def run_once(self, symbols: list[str] | None = None) -> list[Signal]:
        """یک پاس کامل روی همه نمادها؛ خطای یک نماد بقیه را متوقف نمی‌کند."""
        # دارایی بین نمادهای یک پاس مشترک است، ولی بین پاس‌ها نه — کاربر
        # ممکن است وسط دو پاس سهم بخرد یا بفروشد.
        self.reset_holdings_cache()
        self.failed_symbols = []
        self.strategy_errors = {}
        self.screening = ScreeningSummary()

        signals: list[Signal] = []
        for symbol in symbols or self.config.symbols:
            try:
                signals.extend(self.generate_for_symbol(symbol))
            except Exception:  # یک نماد خراب، حلقه اصلی را نکشد
                logger.exception("تولید سیگنال برای نماد %s شکست خورد.", symbol)
                self.failed_symbols.append(symbol)
        return signals

    @property
    def all_symbols_failed(self) -> bool:
        """آیا **همه‌ی** نمادهای آخرین پاس شکست خوردند؟

        یک نماد خراب طبیعی است (نماد متوقف، داده‌ی ناقص). شکست همه یعنی
        منبع داده قطع است — و آن، خرابیِ بی‌صدایی است که ربات را «سالم»
        نشان می‌دهد.
        """
        planned = list(self.config.symbols)
        return bool(planned) and len(self.failed_symbols) >= len(planned)

    def generate_for_symbol(self, symbol: str) -> list[Signal]:
        """ساخت context و اجرای همه استراتژی‌ها روی یک نماد پایه."""
        context = self.build_context(symbol)
        collected: list[Signal] = []

        for strategy in self.strategies:
            try:
                raw_signals = strategy.generate(context)
            except Exception:
                logger.exception("استراتژی %s روی %s خطا داد.", strategy.name, symbol)
                self.strategy_errors[strategy.name] = (
                    self.strategy_errors.get(strategy.name, 0) + 1
                )
                continue

            collected.extend(self._process_batch(raw_signals))
        return collected

    def _process_batch(self, raw_signals: list[Signal]) -> list[Signal]:
        """پردازش خروجی یک استراتژی، با احترام به گروه‌های چندپایه.

        سیگنال‌های تک‌پایه مستقل پردازش می‌شوند. ولی پایه‌های یک ساختار
        چندپایه باید **با هم** قبول یا رد شوند: اگر یک پایه‌ی استردل رد
        شود و دیگری بماند، نتیجه یک کال تنهاست که پروفایل ریسکش کاملاً
        فرق دارد.
        """
        singles: list[Signal] = []
        groups: dict[str, list[Signal]] = {}

        for signal in raw_signals:
            group_id = signal.metadata.get("leg_group_id")
            if group_id:
                groups.setdefault(str(group_id), []).append(signal)
            else:
                singles.append(signal)

        result: list[Signal] = []
        for signal in singles:
            final = self._post_process(signal)
            if final is not None:
                result.append(final)
                logger.info("سیگنال صادر شد: %s", final.summary())

        for group_id, legs in groups.items():
            accepted = self._process_leg_group(group_id, legs)
            result.extend(accepted)
        return result

    def _process_leg_group(self, group_id: str, legs: list[Signal]) -> list[Signal]:
        """یک گروه چندپایه را **اتمی** پردازش می‌کند."""
        expected = int(legs[0].metadata.get("leg_count", len(legs)))
        if len(legs) != expected:
            logger.warning(
                "گروه %s ناقص است (%s از %s پایه)؛ کل ساختار رد شد.",
                group_id, len(legs), expected,
            )
            return []

        sized: list[Signal] = []
        for leg in legs:
            final = self._post_process(leg, allow_dedupe=False)
            if final is None:
                logger.info(
                    "پایه %s از ساختار %s رد شد؛ کل ساختار صرف‌نظر شد "
                    "(اجرای ناقص بدتر از اجرا نکردن است).",
                    leg.symbol, legs[0].strategy_name,
                )
                # یک پایه‌ی نقدشونده نباید ضعف پایه‌ی دیگر را بپوشاند:
                # کنار گذاشتنِ کل ساختار در رابط هم دیده می‌شود.
                self.screening.dropped_groups.append({
                    "leg_group_id": group_id,
                    "strategy": legs[0].strategy_name,
                    "legs": [s.symbol for s in legs],
                    "blocked_by": leg.symbol,
                })
                return []
            sized.append(final)

        # همه‌ی پایه‌ها باید تعداد یکسان (× نسبت) داشته باشند، وگرنه
        # ساختار چیز دیگری است. کمینه تعیین‌کننده است.
        base_qty = min(
            s.suggested_qty // max(int(s.metadata.get("leg_ratio", 1)), 1) for s in sized
        )
        if base_qty <= 0:
            logger.info(
                "ساختار %s با حدود ریسک جا نشد (تعداد پایه صفر شد).",
                legs[0].strategy_name,
            )
            return []

        for signal in sized:
            signal.suggested_qty = base_qty * int(signal.metadata.get("leg_ratio", 1))

        # حذف تکراری روی **کل ساختار** انجام می‌شود، نه تک‌تک پایه‌ها
        key = self._group_dedupe_key(sized)
        if self._is_duplicate_key(key, sized[0].created_at):
            logger.debug("ساختار تکراری %s نادیده گرفته شد.", sized[0].strategy_name)
            return []
        self._last_emitted[key] = sized[0].created_at

        for signal in sized:
            logger.info("سیگنال صادر شد: %s", signal.summary())
        return sized

    @staticmethod
    def _group_dedupe_key(legs: list[Signal]) -> str:
        """کلید حذف تکراری برای یک ساختار چندپایه."""
        parts = sorted(f"{s.symbol}:{s.side.value}:{s.strike}" for s in legs)
        return "|".join([legs[0].strategy_name, "GROUP", *parts])

    @property
    def data_source(self) -> str:
        """برچسب منبع داده، تا سیگنال ساخته‌شده از داده mock قابل تشخیص باشد."""
        market = getattr(self.market_data, "source_name", "unknown")
        chain = getattr(self.option_chain, "source_name", "unknown")
        return f"{market}+{chain}"

    def build_context(self, symbol: str) -> StrategyContext:
        """جمع‌آوری داده پایه و زنجیره آپشن در یک شیء فقط-خواندنی."""
        quote = self.market_data.get_quote(symbol)
        history = self.market_data.get_history(symbol, self.config.history_days)
        chain = self.option_chain.get_chain(symbol)

        base = StrategyContext(
            underlying=symbol,
            quote=quote,
            history=history,
            chain=chain,
            risk_free_rate=self.config.risk_free_rate,
            now=datetime.now(),
            data_source=self.data_source,
            underlying_holding=self._holding_for(symbol),
        )

        rank = self._iv_rank_for(base)
        if rank is None:
            return base
        # `StrategyContext` فریز است، پس نسخه‌ی تازه ساخته می‌شود.
        return dataclasses.replace(base, iv_rank=rank)

    def _iv_rank_for(self, context: StrategyContext) -> Any | None:
        """IV ATM را ثبت و جایگاهش را در تاریخچه‌ی همین نماد برمی‌گرداند.

        دو کار در یک جا انجام می‌شود چون به هم وابسته‌اند: تاریخچه‌ی IV از
        هیچ endpoint عمومی در دسترس نیست، پس باید خودمان هر پاس ثبتش کنیم
        تا روزی صدک معنا پیدا کند.

        شکست اینجا کشنده نیست: بدون رتبه، استراتژی به معیار قبلی
        (`iv/realized`) برمی‌گردد.
        """
        if self.iv_history is None:
            return None

        try:
            from pricing.iv_surface import IVSurface

            surface = IVSurface.from_chain(
                context.chain, context.implied_vol, today=context.today()
            )
            atm = surface.atm_iv()
            if atm is None:
                return None

            self.iv_history.record(context.underlying, atm, context.today())
            self.iv_history.save()
            return self.iv_history.rank(context.underlying, atm)
        except Exception as exc:  # رتبه‌ی IV یک افزونه است، نه پیش‌نیاز
            logger.warning("رتبه‌ی IV %s حساب نشد: %s", context.underlying, exc)
            return None

    # ------------------------------------------------------------------
    def _post_process(
        self, signal: Signal, allow_dedupe: bool = True
    ) -> Signal | None:
        """اعمال فیلتر اعتماد، محاسبه ریسک و حذف تکراری‌ها.

        `allow_dedupe=False` برای پایه‌های چندپایه است: حذف تکراری آنجا
        روی کل ساختار انجام می‌شود، نه تک‌تک پایه‌ها.
        """
        min_conf = self.config.min_confidence
        if min_conf is not None and (signal.confidence or 0.0) < min_conf:
            logger.debug("سیگنال %s به‌دلیل اعتماد کم رد شد.", signal.symbol)
            return None

        contract_size = int(signal.metadata.get("contract_size", 1_000))
        sized = self.risk_calculator.apply(signal, contract_size)
        if sized is None:
            logger.info("سیگنال %s از حدود ریسک عبور کرد و صرف‌نظر شد.", signal.symbol)
            return None

        sized.valid_until = sized.created_at + timedelta(
            minutes=self.config.signal_validity_minutes
        )

        # ⚠️ غربالِ قابلیت معامله **پس از** اندازه‌گیری ریسک می‌آید و پیش
        # از انتشار: عمقِ لازم به همین `suggested_qty` بستگی دارد، پس
        # پیش از دانستن تعداد نمی‌شد سنجیدش. امتیاز استراتژی اینجا هیچ
        # وزنی ندارد — نبودِ امکان خروج جبران‌شدنی نیست.
        if self.tradability is not None and not self._is_tradable(sized):
            return None

        if allow_dedupe:
            if self._is_duplicate(sized):
                logger.debug("سیگنال تکراری %s نادیده گرفته شد.", sized.symbol)
                return None
            self._last_emitted[self._dedupe_key(sized)] = sized.created_at
        return sized

    def _is_tradable(self, signal: Signal) -> bool:
        """غربال یک سیگنال و ثبت نتیجه برای رابط.

        فقط `tradable` عبور می‌کند. `needs_review` هم رد می‌شود چون
        «حداقل قابلیت معامله» شرط ورود است و نبودِ داده تأییدش نمی‌کند —
        ولی جدا از `rejected` ثبت می‌شود تا کاربر بداند مسئله کمبود
        داده بوده، نه نقدشوندگیِ بد.
        """
        try:
            report = self.tradability.evaluate_signal(signal)
        except Exception:
            logger.exception(
                "غربال قابلیت معامله برای %s شکست خورد؛ سیگنال رد شد.", signal.symbol
            )
            return False
        group_id = signal.metadata.get("leg_group_id")
        self.screening.records.append(ScreeningRecord(
            symbol=signal.symbol,
            strategy=signal.strategy_name,
            side=signal.side.value,
            quantity=signal.suggested_qty,
            report=report,
            leg_group_id=str(group_id) if group_id else None,
        ))
        if report.verdict is Verdict.TRADABLE:
            return True
        logger.info(
            "سیگنال %s غربال نشد (%s): %s",
            signal.symbol, report.verdict_label, report.reason,
        )
        return False

    @staticmethod
    def _dedupe_key(signal: Signal) -> str:
        return "|".join(
            [signal.strategy_name, signal.symbol, signal.side.value, str(signal.strike)]
        )

    def _is_duplicate_key(self, key: str, now) -> bool:
        """بررسی تکراری بودن با یک کلید دلخواه (تک‌پایه یا گروه)."""
        window = timedelta(minutes=self.config.dedupe_window_minutes)
        previous = self._last_emitted.get(key)
        return previous is not None and (now - previous) < window

    def _is_duplicate(self, signal: Signal) -> bool:
        window = timedelta(minutes=self.config.dedupe_window_minutes)
        previous = self._last_emitted.get(self._dedupe_key(signal))
        return previous is not None and signal.created_at - previous < window

    def reset_dedupe_cache(self) -> None:
        """پاک کردن حافظه ضدتکرار (برای تست و بک‌تست)."""
        self._last_emitted.clear()

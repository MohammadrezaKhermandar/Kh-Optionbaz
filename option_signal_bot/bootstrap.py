"""لایه wiring: از تنظیمات، اجزای برنامه را می‌سازد (Dependency Injection).

چرا جدا از `main.py`؟ تا هر entrypoint دیگری (داشبورد وب، اسکریپت بک‌تست،
نوتبوک تحلیل) بتواند همین اجزا را با یک خط بسازد و منطق wiring تکرار نشود.

برای افزودن یک provider یا notifier جدید، فقط یک ورودی به دیکشنری‌های
`MARKET_DATA_PROVIDERS`, `OPTION_CHAIN_PROVIDERS` یا `NOTIFIER_BUILDERS` اضافه کنید.

⚠️ این ماژول هم مثل بقیه، لایه `execution` را نمی‌سازد و صدا نمی‌زند.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from backtest.signal_backtester import SignalBacktester
from config.loader import build_dataclass, resolve_path, section
from data.market_data_client import (
    MarketDataClient,
    PytseMarketDataClient,
)
from data.option_chain_client import OptionChainClient
from data.tsetmc_market_data_client import TsetmcMarketDataClient
from data.tsetmc_option_chain_client import (
    DataQualityRules,
    FilePayloadSource,
    HttpPayloadSource,
    PayloadSource,
    TsetmcOptionChainClient,
)
from market.trading_calendar import TradingCalendar, format_jalali
from monitoring.health import AlertThrottle, HealthMonitor
from monitoring.periodic_report import ReportSchedule
from notifiers.base_notifier import BaseNotifier
from notifiers.console_notifier import ConsoleNotifier
from notifiers.telegram_commands import (
    CommandContext,
    MuteState,
    TelegramCommandBot,
)
from notifiers.telegram_notifier import TelegramNotifier
from pricing.iv_surface import IVHistory
from risk.fees import FeeSchedule
from risk.risk_calculator import RiskCalculator, RiskLimits
from signals.signal_generator import GeneratorConfig, SignalGenerator
from storage.signal_log import SignalLog
from strategies.registry import create_strategies

logger = logging.getLogger(__name__)

def _payload_source(config: dict[str, Any]) -> PayloadSource:
    """منبع پاسخ TSETMC از تنظیمات؛ `fixture_path` یعنی پخش پاسخ ضبط‌شده."""
    fixture_path = config.get("fixture_path")
    if fixture_path:
        return FilePayloadSource(resolve_path(fixture_path))
    return HttpPayloadSource(
        market=config.get("market", 0),
        timeout=config.get("timeout", 15),
        retries=config.get("retries", 3),
    )


#: نام provider در تنظیمات → سازنده کلاینت داده پایه
MARKET_DATA_PROVIDERS: dict[str, Callable[[dict[str, Any]], MarketDataClient]] = {
    "pytse": lambda config: PytseMarketDataClient(
        allow_fallback=config.get("allow_fallback", True)
    ),
    "tsetmc": lambda config: TsetmcMarketDataClient(
        source=_payload_source(config),
        timeout=config.get("timeout", 20),
        retries=config.get("retries", 3),
        history_ttl_seconds=config.get("history_ttl_seconds", 900.0),
        # پوشه‌ی تاریخچه‌ی ضبط‌شده. کلاینت از قبل پشتیبانی‌اش می‌کرد ولی به
        # تنظیمات وصل نبود، پس تست‌ها راهی نداشتند جز رفتن به شبکه.
        history_dir=(
            resolve_path(config["history_dir"]) if config.get("history_dir") else None
        ),
    ),
}


def _build_tsetmc_chain(config, market_data, _risk_free_rate) -> OptionChainClient:
    """زنجیره زنده از API عمومی TSETMC (بدون احراز هویت).

    اگر کلاینت داده پایه هم TSETMC باشد، **همان منبع** به اشتراک گذاشته می‌شود تا
    قیمت پایه و مظنه آپشن از یک پاسخ (و یک لحظه) بیایند و درخواست تکراری نرود.
    """
    shared = getattr(market_data, "source", None)
    if isinstance(shared, HttpPayloadSource):
        return TsetmcOptionChainClient(
            shared,
            cache_ttl_seconds=config.get("cache_ttl_seconds", 20.0),
            quality=build_dataclass(
                DataQualityRules, config.get("quality") or {}, "option_chain.quality"
            ),
        )
    return TsetmcOptionChainClient(
        HttpPayloadSource(
            market=config.get("market", 0),
            timeout=config.get("timeout", 15),
            retries=config.get("retries", 3),
        ),
        cache_ttl_seconds=config.get("cache_ttl_seconds", 20.0),
        quality=build_dataclass(
            DataQualityRules, config.get("quality") or {}, "option_chain.quality"
        ),
    )


def _build_fixture_chain(config, market_data, _risk_free_rate) -> OptionChainClient:
    """پخش مجدد یک پاسخ ضبط‌شده TSETMC — تست آفلاین و بازتولید یک روز خاص."""
    path = config.get("fixture_path")
    if not path:
        raise ValueError(
            "برای provider «fixture» باید مقدار option_chain.fixture_path تنظیم شود."
        )
    return TsetmcOptionChainClient(
        FilePayloadSource(resolve_path(path)),
        cache_ttl_seconds=float("inf"),  # فایل ثابت است؛ یک بار خوانده می‌شود
        quality=build_dataclass(
            DataQualityRules, config.get("quality") or {}, "option_chain.quality"
        ),
    )


#: نام provider در تنظیمات → سازنده کلاینت زنجیره آپشن
OPTION_CHAIN_PROVIDERS: dict[str, Callable[..., OptionChainClient]] = {
    "tsetmc": _build_tsetmc_chain,
    "fixture": _build_fixture_chain,
}


@dataclass
class AppContext:
    """اجزای ساخته‌شده برنامه، آماده استفاده در حلقه اصلی."""

    settings: dict[str, Any]
    market_data: MarketDataClient
    option_chain: OptionChainClient
    generator: SignalGenerator
    notifiers: list[BaseNotifier] = field(default_factory=list)
    signal_log: SignalLog | None = None
    trading_calendar: TradingCalendar | None = None
    #: آداپتر کارگزاری (فقط‌خواندنی) — `None` اگر خاموش یا در دسترس نباشد
    account_source: Any | None = None

    def close(self) -> None:
        """آزادسازی منابع (اتصال SQLite)."""
        if self.signal_log is not None:
            self.signal_log.close()


# ----------------------------------------------------------------------
# سازنده‌های تک‌تک اجزا
# ----------------------------------------------------------------------

def _pick(registry: dict[str, Any], name: str, kind: str):
    """انتخاب provider از رجیستری با خطای خوانا در صورت نام اشتباه."""
    builder = registry.get(str(name).lower())
    if builder is None:
        raise ValueError(
            f"{kind} ناشناخته: «{name}». گزینه‌های موجود: {', '.join(sorted(registry))}"
        )
    return builder


def build_market_data(settings: dict[str, Any]) -> MarketDataClient:
    config = section(settings, "market_data")
    provider = config.get("provider", "tsetmc")
    return _pick(MARKET_DATA_PROVIDERS, provider, "provider داده بازار")(config)


def build_account_source(settings: dict[str, Any]):
    """آداپتر کارگزاری، اگر در تنظیمات فعال باشد — وگرنه `None`.

    شکست اینجا **خطا نمی‌دهد**: نبودِ داده‌ی حساب نباید کل ربات را
    بخواباند، چون سیگنال‌دهی به آن وابسته نیست. فقط لاگ هشدار می‌دهد.
    """
    broker = section(settings, "broker")
    if not broker.get("enabled"):
        return None

    try:
        from brokers.emofid import EmofidAccountClient

        common = {
            "base_url": broker.get("base_url", "https://api-mts.orbis.easytrader.ir"),
            "timeout": broker.get("timeout", 15),
            "retries": broker.get("retries", 3),
        }
        token = (broker.get("token") or "").strip()
        if token:
            return EmofidAccountClient(token=token, **common)
        return EmofidAccountClient.from_session_file(
            resolve_path(broker.get("session_file", "var/emofid/session.json")),
            **common,
        )
    except Exception as exc:  # نبود حساب نباید ربات را بخواباند
        logger.warning(
            "اتصال به کارگزاری برقرار نشد؛ ربات بدون داده‌ی حساب ادامه می‌دهد: %s", exc
        )
        return None


def build_option_chain(
    settings: dict[str, Any],
    market_data: MarketDataClient,
    account_source: Any | None = None,
) -> OptionChainClient:
    """زنجیره‌ی آپشن.

    Args:
        account_source: آداپتر کارگزاریِ **از قبل ساخته‌شده**. اگر داده نشود
            و غنی‌سازی خواسته شده باشد، خودش یکی می‌سازد. پاس‌دادنش باعث
            می‌شود در یک اجرا فقط یک سشن کارگزاری باز شود، نه چند تا.
    """
    config = section(settings, "option_chain")
    provider = config.get("provider", "tsetmc")
    risk_free_rate = section(settings, "market_data").get("risk_free_rate", 0.25)
    builder = _pick(OPTION_CHAIN_PROVIDERS, provider, "provider زنجیره آپشن")
    chain = builder(config, market_data, risk_free_rate)

    # غنی‌سازی با داده‌ی کارگزاری، اگر خواسته شده باشد.
    # عمداً *روی* منبع انتخاب‌شده می‌نشیند، نه به‌جایش: ایزی‌تریدر
    # مشخصات قرارداد را فقط تک‌به‌تک می‌دهد (~۱۳۸۶ درخواست برای کل
    # بازار)، پس نمی‌تواند منبع زنجیره باشد — ولی برای چند قرارداد
    # نزدیک به قیمت پایه، معتبرترین منبع است.
    if config.get("enrich_with_broker"):
        from data.enriched_option_chain import BrokerEnrichedOptionChain

        account = account_source or build_account_source(settings)
        if account is None:
            logger.warning(
                "enrich_with_broker روشن است ولی کارگزاری در دسترس نیست؛ "
                "زنجیره بدون غنی‌سازی استفاده می‌شود."
            )
        else:
            chain = BrokerEnrichedOptionChain(
                chain, account, enrich_limit=config.get("enrich_limit", 20)
            )
    return chain


def build_risk_calculator(
    settings: dict[str, Any], account_source: Any | None = None
) -> RiskCalculator:
    """`RiskCalculator` با دارایی حساب.

    اگر `risk.use_broker_equity` روشن باشد و آداپتر کارگزاری موجودی بدهد،
    آن عدد جای `account_equity` دستیِ yaml را می‌گیرد. عدد دستی به‌سرعت
    کهنه می‌شود و اندازه‌گیری ریسک را روی دارایی‌ای انجام می‌دهد که وجود
    ندارد.

    سه قید عمدی:

    * موجودی **صفر یا منفی** اعمال **نمی‌شود.** حساب صفر یعنی هر سیگنال
      صفر قرارداد می‌گیرد، که فرقی با نبودِ سیگنال ندارد ولی شبیه یک
      اشکال نرم‌افزاری به نظر می‌آید. در آن حالت مقدار yaml می‌ماند.
    * خطای شبکه کشنده نیست: به مقدار yaml برمی‌گردد و لاگ هشدار می‌دهد.
    * پیش‌فرض **خاموش** است، تا رفتار فعلی کسی بی‌خبر عوض نشود.
    """
    config = section(settings, "risk")
    limits = build_dataclass(
        RiskLimits, config, "risk", ignore={"use_broker_equity", "fees"}
    )
    # نرخ کارمزد **حدس زده نمی‌شود**: تا کاربر ندهد صفر است و همه‌ی
    # اعداد مثل قبل می‌مانند. نرخ حدسی، دقتِ کاذب می‌سازد.
    fees = build_dataclass(FeeSchedule, config.get("fees") or {}, "risk.fees")

    if account_source is None or not config.get("use_broker_equity", False):
        return RiskCalculator(limits, fees)

    try:
        equity = account_source.get_balance().equity
    except Exception as exc:  # نبود موجودی نباید ربات را بخواباند
        logger.warning(
            "موجودی کارگزاری خوانده نشد؛ `account_equity` تنظیمات استفاده می‌شود: %s",
            exc,
        )
        return RiskCalculator(limits, fees)

    if equity <= 0:
        logger.warning(
            "موجودی کارگزاری %s است؛ مقدار yaml (%s) نگه داشته شد.",
            f"{equity:,.0f}",
            f"{limits.account_equity:,.0f}",
        )
        return RiskCalculator(limits, fees)

    logger.info(
        "دارایی حساب از کارگزاری خوانده شد: %s ریال (جای %s در yaml).",
        f"{equity:,.0f}",
        f"{limits.account_equity:,.0f}",
    )
    return RiskCalculator(
        dataclasses.replace(limits, account_equity=equity), fees
    )


def build_generator_config(settings: dict[str, Any]) -> GeneratorConfig:
    market = section(settings, "market_data")
    signals = section(settings, "signals")
    return build_dataclass(
        GeneratorConfig,
        {
            "symbols": list(market.get("symbols", [])),
            "risk_free_rate": market.get("risk_free_rate", 0.25),
            "history_days": market.get("history_days", 90),
            "dedupe_window_minutes": signals.get("dedupe_window_minutes", 60),
            "min_confidence": signals.get("min_confidence"),
            "signal_validity_minutes": signals.get("validity_minutes", 30),
        },
        "signals",
    )


def build_holdings_provider(account_source: Any | None):
    """تابعِ «نام نماد → تعداد سهم»، یا `None` اگر در دسترس نباشد.

    `None` برگرداندن مهم است و با «دیکشنری خالی» یکی نیست: اولی به
    استراتژی می‌گوید «نمی‌دانم چه سهمی داری» و دومی «هیچ سهمی نداری».
    شرط Covered Call بر همین تفاوت بنا شده.

    کلیدها **نام فارسی نماد** است (مثل «خودرو») چون استراتژی‌ها با همان
    کار می‌کنند؛ ISIN هم به‌عنوان کلید دوم گذاشته می‌شود تا اگر جایی با
    ISIN پرسیده شد هم جواب بدهد.
    """
    if account_source is None:
        return None
    if not getattr(account_source, "supports_share_positions", False):
        logger.info(
            "آداپتر کارگزاری «%s» دارایی سهم نمی‌دهد؛ شرط مالکیت Covered Call "
            "نامعلوم می‌ماند.",
            getattr(account_source, "name", "?"),
        )
        return None

    def provider() -> dict[str, int]:
        holdings: dict[str, int] = {}
        for position in account_source.get_share_positions():
            if position.symbol_name:
                holdings[position.symbol_name] = position.quantity
            if position.symbol_isin:
                holdings[position.symbol_isin] = position.quantity
        logger.info("دارایی سهم خوانده شد: %d نماد.", len(holdings))
        return holdings

    return provider


def build_iv_history(settings: dict[str, Any]) -> IVHistory | None:
    """تاریخچه‌ی IV هر نماد، یا `None` اگر خاموش باشد.

    IV تاریخی از هیچ endpoint عمومی در دسترس نیست، پس هر پاس خودمان
    ثبتش می‌کنیم. تا نمونه‌ی کافی جمع نشود، صدک `None` است و استراتژی به
    معیار قبلی (`iv/realized`) برمی‌گردد — پس روشن بودنش از روز اول هم
    رفتار کسی را عوض نمی‌کند، فقط تاریخچه می‌سازد.
    """
    config = section(settings, "iv_history")
    if not config.get("enabled", True):
        return None
    return IVHistory(
        path=resolve_path(config.get("path", "var/iv_history.json")),
        max_days=int(config.get("max_days", 365)),
        min_samples=int(config.get("min_samples", 20)),
    )


def build_generator(
    settings: dict[str, Any],
    market_data: MarketDataClient,
    option_chain: OptionChainClient,
    account_source: Any | None = None,
) -> SignalGenerator:
    return SignalGenerator(
        market_data=market_data,
        option_chain=option_chain,
        strategies=create_strategies(section(settings, "strategies")),
        risk_calculator=build_risk_calculator(settings, account_source),
        config=build_generator_config(settings),
        holdings_provider=build_holdings_provider(account_source),
        iv_history=build_iv_history(settings),
        tradability=build_tradability_screener(settings, option_chain),
    )


def build_tradability_screener(
    settings: dict[str, Any], option_chain: OptionChainClient
) -> Any | None:
    """غربالِ قابلیت معامله. `None` یعنی در تنظیمات خاموش است.

    تاریخچه‌ی تداوم معامله از پایگاه **خام** recorder می‌آید و
    `MarketHistoryReader` آن را فقط‌خواندنی باز می‌کند. نبودِ آن پایگاه
    خطا نیست: تداوم «نامعلوم» می‌ماند و سیگنال به‌جای «پذیرفته»،
    «نیازمند بررسی» می‌شود.
    """
    config = section(settings, "tradability")
    if not config.get("enabled", True):
        logger.info("غربال قابلیت معامله خاموش است؛ هیچ سیگنالی بابت نقدشوندگی رد نمی‌شود.")
        return None

    from data.order_book import OrderBookClient
    from market.tradability import Thresholds
    from market.tradability_screener import TradabilityScreener
    from storage.market_history import MarketHistoryReader

    thresholds = Thresholds(**{
        key: config[key]
        for key in (
            "min_open_interest_contracts", "min_trades_today_count",
            "max_relative_spread_pct", "min_exit_depth_ratio",
            "min_sessions_with_trades_pct", "min_history_sessions",
            "min_days_to_expiry", "max_quote_age_seconds",
        )
        if key in config
    })
    history = MarketHistoryReader(
        resolve_path(config.get("history_db_path", "var/recorder/market.db")),
        lookback_sessions=int(config.get("history_lookback_sessions", 20)),
    )
    return TradabilityScreener(
        resolve_contract=option_chain.get_contract,
        thresholds=thresholds,
        order_book_client=OrderBookClient(),
        history=history,
    )


def _build_console(config: dict[str, Any], as_json: bool) -> BaseNotifier | None:
    return ConsoleNotifier(as_json=as_json or config.get("as_json", False))


#: وضعیت `/mute` باید **یک نمونه** برای کل برنامه باشد. اگر notifier و
#: ربات دستورها هر کدام نمونه‌ی خودشان را بسازند، `/mute` روی ارسال اثر
#: نمی‌کند: کاربر تأیید می‌گیرد ولی پیام‌ها همچنان می‌آیند.
_MUTE_STATES: dict[Any, MuteState] = {}


def telegram_credentials(config: dict[str, Any]) -> tuple[str, str]:
    """توکن و chat_id تلگرام. متغیر محیطی بر فایل اولویت دارد.

    اولویت محیط برای این است که توکن هرگز در گیت نیفتد.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN") or str(config.get("bot_token", ""))
    chat_id = os.getenv("TELEGRAM_CHAT_ID") or str(config.get("chat_id", ""))
    if token.startswith("<"):  # مقدار نمونه‌ی settings.example
        token = ""
    return token.strip(), chat_id.strip()


def build_mute_state(settings: dict[str, Any]) -> MuteState:
    """وضعیت `/mute`، مشترک بین notifier و ربات دستورها."""
    config = section(settings, "notifiers").get("telegram") or {}
    path = resolve_path(config.get("mute_state_path", "var/telegram_mute.json"))
    mute = _MUTE_STATES.get(path)
    if mute is None:
        mute = MuteState(path=path)
        _MUTE_STATES[path] = mute
    return mute


def _build_telegram(config: dict[str, Any], _as_json: bool) -> BaseNotifier | None:
    token, chat_id = telegram_credentials(config)
    if not token:
        logger.warning(
            "توکن تلگرام تنظیم نشده (TELEGRAM_BOT_TOKEN)؛ این کانال ساخته نشد."
        )
        return None
    path = resolve_path(config.get("mute_state_path", "var/telegram_mute.json"))
    mute = _MUTE_STATES.get(path)
    if mute is None:
        mute = MuteState(path=path)
        _MUTE_STATES[path] = mute
    return TelegramNotifier(
        bot_token=token,
        chat_id=chat_id,
        mute=mute,
        ack_buttons=config.get("ack_buttons", True),
    )


#: نام کانال در تنظیمات → سازنده آن
NOTIFIER_BUILDERS: dict[str, Callable[[dict[str, Any], bool], BaseNotifier | None]] = {
    "console": _build_console,
    "telegram": _build_telegram,
}


def build_notifiers(
    settings: dict[str, Any], dry_run: bool = False, as_json: bool = False
) -> list[BaseNotifier]:
    """ساخت کانال‌های فعال. در حالت dry-run فقط کنسول ساخته می‌شود."""
    config = section(settings, "notifiers")
    if dry_run:
        return [ConsoleNotifier(as_json=as_json)]

    notifiers: list[BaseNotifier] = []
    for name, channel_config in config.items():
        channel_config = channel_config or {}
        if not channel_config.get("enabled", False):
            continue
        builder = NOTIFIER_BUILDERS.get(name)
        if builder is None:
            logger.warning("کانال اطلاع‌رسانی ناشناخته نادیده گرفته شد: %s", name)
            continue
        notifier = builder(channel_config, as_json)
        if notifier is not None:
            notifiers.append(notifier)

    if not notifiers:
        logger.warning("هیچ کانال فعالی نیست؛ کنسول به‌عنوان پشتیبان اضافه شد.")
        notifiers.append(ConsoleNotifier(as_json=as_json))
    return notifiers


def build_signal_log(settings: dict[str, Any], dry_run: bool = False) -> SignalLog | None:
    """در dry-run هیچ چیزی روی دیسک نوشته نمی‌شود."""
    config = section(settings, "storage")
    if dry_run or not config.get("enabled", True):
        return None
    jsonl = config.get("jsonl_path")
    return SignalLog(
        db_path=resolve_path(config.get("sqlite_path", "var/signals.db")),
        jsonl_path=resolve_path(jsonl) if jsonl else None,
    )


def build_health_monitor(settings: dict[str, Any]) -> HealthMonitor | None:
    """پایشگر سلامت، یا `None` اگر خاموش باشد."""
    config = section(settings, "monitoring")
    if not config.get("health_enabled", True):
        return None
    return HealthMonitor(config.get("thresholds") or {})


def build_alert_throttle(settings: dict[str, Any]) -> AlertThrottle:
    """ضدهرزنامه‌ی هشدارها. هشداری که هر پاس تکرار شود، خوانده نمی‌شود."""
    config = section(settings, "monitoring")
    return AlertThrottle(
        path=resolve_path(config.get("alert_state_path", "var/health_alerts.json")),
        cooldown_hours=float(config.get("alert_cooldown_hours", 6.0)),
    )


def build_report_schedule(settings: dict[str, Any]) -> ReportSchedule | None:
    """زمان‌بندی گزارش دوره‌ای، یا `None` اگر خاموش باشد."""
    config = section(settings, "monitoring")
    if not config.get("periodic_report_enabled", False):
        return None
    return ReportSchedule(
        path=resolve_path(config.get("report_state_path", "var/report_schedule.json"))
    )


def build_command_bot(
    settings: dict[str, Any], context: AppContext | None = None
) -> TelegramCommandBot | None:
    """ربات دستورهای تلگرام، یا `None` اگر خاموش/بی‌توکن باشد.

    تابع‌های خواندن داده به‌صورت closure پاس داده می‌شوند، نه کلاینت: این
    لایه نباید بتواند چیزی جز خواندن انجام بدهد. `/mute` تنها دستور
    نویسنده است و فقط جلوی *ارسال اعلان* را می‌گیرد.
    """
    config = section(settings, "notifiers").get("telegram") or {}
    if not config.get("enabled") or not config.get("commands_enabled", False):
        return None

    token, chat_id = telegram_credentials(config)
    if not token or not chat_id:
        logger.warning(
            "دستورهای تلگرام روشن است ولی توکن/chat_id نیست؛ ساخته نشد."
        )
        return None

    storage = section(settings, "storage")
    db_path = resolve_path(storage.get("sqlite_path", "var/signals.db"))

    def recent_signals(limit: int) -> list[dict[str, Any]]:
        from storage.reporting import SignalReporter

        with SignalReporter(db_path) as reporter:
            return reporter.recent(limit=limit)

    def performance() -> dict[str, Any]:
        from storage.reporting import SignalReporter

        with SignalReporter(db_path) as reporter:
            return reporter.performance_metrics()

    def status() -> dict[str, Any]:
        market_open = None
        today_jalali = next_day = None
        if context is not None:
            try:
                market_open = context.market_data.is_market_open()
                calendar = context.trading_calendar
                if calendar is not None:
                    today = date.today()
                    today_jalali = format_jalali(today)
                    if not market_open:
                        nxt = (
                            today
                            if calendar.is_trading_day(today)
                            else calendar.next_trading_day(today)
                        )
                        next_day = f"{nxt.isoformat()} ({format_jalali(nxt)})"
            except Exception as exc:  # وضعیت نباید ربات را بخواباند
                logger.warning("وضعیت بازار برای تلگرام خوانده نشد: %s", exc)

        from storage.signal_log import SignalLog

        with SignalLog(db_path=db_path, jsonl_path=None) as log:
            count = log.count()

        return {
            "market_open": market_open,
            "signal_count": count,
            "market_data_provider": section(settings, "market_data").get("provider"),
            "option_chain_provider": section(settings, "option_chain").get("provider"),
            "today_jalali": today_jalali,
            "next_trading_day": next_day,
        }

    return TelegramCommandBot(
        bot_token=token,
        allowed_chat_id=chat_id,
        context=CommandContext(
            recent_signals=recent_signals,
            status=status,
            performance=performance,
        ),
        mute=build_mute_state(settings),
        state_path=resolve_path(
            config.get("command_state_path", "var/telegram_offset.json")
        ),
        ack_store=build_ack_store(settings),
    )


def build_ack_store(settings: dict[str, Any]):
    """محل ثبت تأیید دریافت — روی همان دیتابیس سیگنال‌ها.

    اگر ذخیره‌سازی خاموش باشد `None` برمی‌گردد: بدون جدول سیگنال، تأییدی
    هم معنا ندارد (کلید خارجی به `signals` می‌خورد).
    """
    storage = section(settings, "storage")
    if not storage.get("enabled", True):
        return None

    try:
        from storage.acknowledgement import AckStore

        return AckStore(resolve_path(storage.get("sqlite_path", "var/signals.db")))
    except Exception as exc:  # نبود تأیید نباید ربات را بخواباند
        logger.warning("ثبت تأیید دریافت در دسترس نیست: %s", exc)
        return None


def build_trading_calendar(
    settings: dict[str, Any], market_data: MarketDataClient | None = None
) -> TradingCalendar:
    """تقویم معاملاتی؛ تعطیلات را از تاریخچه‌ی **واقعی** بازار یاد می‌گیرد.

    نماد مرجع باید پرمعامله باشد تا «نبودِ کندل» واقعاً یعنی تعطیلی، نه
    توقف نماد. پیش‌فرض «خودرو» است که عملاً هیچ‌وقت بسته نیست.

    شکست یادگیری خطا نمی‌دهد: تقویمِ فقط-آخرهفته از نداشتنِ تقویم بهتر است.
    """
    config = section(settings, "trading_calendar")
    calendar = TradingCalendar(
        holidays=[
            date.fromisoformat(str(d)) for d in config.get("extra_holidays", []) or []
        ],
        cache_path=resolve_path(config.get("cache_path", "var/trading_calendar.json")),
    )

    if market_data is not None and config.get("learn_from_market", True):
        symbol = config.get("reference_symbol", "خودرو")
        days = int(config.get("learn_days", 365))

        def _learn(cal: TradingCalendar) -> None:
            if cal.learn_from_client(market_data, symbol=symbol, days=days):
                cal.save()

        # عقب‌انداخته می‌شود، نه همین‌جا: `create_app` را هر کسی صدا می‌زند
        # که فقط wiring می‌خواهد — تست‌ها، `--dry-run`، ساختِ داشبورد. اگر
        # اینجا یاد بگیریم، همه‌ی آن‌ها یک سال تاریخچه از شبکه می‌کشند.
        calendar.defer_learning(_learn)
    return calendar


def build_backtester(
    settings: dict[str, Any],
    market_data: MarketDataClient,
    option_chain: OptionChainClient,
) -> SignalBacktester:
    config = section(settings, "backtest")
    return SignalBacktester(
        market_data=market_data,
        option_chain=option_chain,
        strategies=create_strategies(section(settings, "strategies")),
        horizon_days=config.get("horizon_days", 10),
        warmup_days=config.get("warmup_days", 30),
        step_days=config.get("step_days", 1),
        risk_free_rate=section(settings, "market_data").get("risk_free_rate", 0.25),
        adjust_corporate_actions=config.get("adjust_corporate_actions", True),
        option_history=build_option_history(settings)
        if config.get("use_real_premiums", True)
        else None,
    )


def build_option_history(settings: dict[str, Any]):
    """منبع تاریخچه‌ی پرمیوم آپشن — برای بک‌تست روی سود و زیان **واقعی**.

    شکست اینجا کشنده نیست: بدونش بک‌تست به جهت‌دهی نماد پایه برمی‌گردد،
    که رفتار قبلی پروژه بود.
    """
    try:
        from data.option_history import OptionHistoryClient

        market = section(settings, "market_data")
        return OptionHistoryClient(
            timeout=market.get("timeout", 20),
            retries=market.get("retries", 2),
            history_dir=(
                resolve_path(market["option_history_dir"])
                if market.get("option_history_dir")
                else None
            ),
        )
    except Exception as exc:  # نبود تاریخچه نباید بک‌تست را بخواباند
        logger.warning("تاریخچه‌ی پرمیوم در دسترس نیست: %s", exc)
        return None


# ----------------------------------------------------------------------
def create_app(
    settings: dict[str, Any], dry_run: bool = False, as_json: bool = False
) -> AppContext:
    """ساخت کل برنامه از تنظیمات — تنها تابعی که entrypoint‌ها لازم دارند."""
    market_data = build_market_data(settings)
    # تقویم روی خودِ کلاینت می‌نشیند تا `is_market_open` همه‌جا — CLI و
    # داشبورد — بدون تغییر امضا تعطیلات رسمی را ببیند.
    calendar = build_trading_calendar(settings, market_data)
    market_data.trading_calendar = calendar

    # یک بار ساخته می‌شود و بین غنی‌سازی زنجیره و دارایی حساب مشترک است،
    # تا یک اجرا بیش از یک سشن کارگزاری باز نکند.
    account_source = build_account_source(settings)
    option_chain = build_option_chain(settings, market_data, account_source)
    return AppContext(
        settings=settings,
        market_data=market_data,
        option_chain=option_chain,
        trading_calendar=calendar,
        account_source=account_source,
        generator=build_generator(settings, market_data, option_chain, account_source),
        notifiers=build_notifiers(settings, dry_run=dry_run, as_json=as_json),
        signal_log=build_signal_log(settings, dry_run=dry_run),
    )

"""بارگذاری، ادغام و اعتبارسنجی تنظیمات پروژه.

تنها جایی که «مقدار پیش‌فرض» تعریف می‌شود همین ماژول است؛ بقیه کد فقط از
دیکشنری تنظیمات می‌خواند. نبودن `settings.yaml` یا نصب نبودن PyYAML خطا نیست،
تا `python main.py --dry-run` همیشه بدون هیچ تنظیمی کار کند.
"""

from __future__ import annotations

import copy
import dataclasses
import logging
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: ریشه پروژه (پوشه‌ای که main.py در آن است)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.example.yaml"


def default_settings() -> dict[str, Any]:
    """تنظیمات کمینه‌ای که اجرای بدون فایل yaml را ممکن می‌کند."""
    return {
        "general": {
            "log_level": "INFO",
            "poll_interval_seconds": 300,
            "run_only_when_market_open": False,
        },
        # پیش‌فرض عمداً داده‌ی **واقعی** است. پروژه داده‌ی ساختگی ندارد،
        # پس هیچ خطای تنظیماتی نمی‌تواند بی‌صدا به قیمت جعلی منجر شود.
        "market_data": {
            "provider": "tsetmc",
            "symbols": ["خودرو", "شستا"],
            "history_days": 90,
            "risk_free_rate": 0.25,
        },
        "option_chain": {
            "provider": "tsetmc",
            # غنی‌سازی با داده‌ی کارگزاری (وجه تضمین، اندازه‌ی دقیق قرارداد).
            # ایزی‌تریدر مشخصات را فقط تک‌به‌تک می‌دهد، پس فقط چند قرارداد
            # نزدیک به قیمت پایه غنی می‌شوند، نه کل بازار.
            "enrich_with_broker": False,
            "enrich_limit": 20,
        },
        "signals": {
            "validity_minutes": 30,
            "dedupe_window_minutes": 60,
            "min_confidence": None,
        },
        # `use_broker_equity` روشن = دارایی حساب از کارگزاری خوانده
        # می‌شود، جای عدد دستیِ `account_equity` که سریع کهنه می‌شود.
        # پیش‌فرض خاموش، تا رفتار فعلی کسی بی‌خبر عوض نشود.
        "risk": {
            "use_broker_equity": False,
            # کارمزد و مالیات، به‌صورت **کسر** (۰٫۰۰۱ = ۰٫۱٪).
            #
            # پیش‌فرض **صفر** است و حدس زده نمی‌شود — همان قاعده‌ای
            # که برای داده‌ی بازار رعایت می‌شود. یک نرخ حدسی، دقتِ
            # کاذب می‌سازد که از نبودش بدتر است.
            "fees": {
                "buy_rate": 0.0,
                "sell_rate": 0.0,
                "sell_tax_rate": 0.0,
                "per_order": 0.0,
            },
        },
        # خالی = همه استراتژی‌های ثبت‌شده با پارامترهای پیش‌فرض خودشان
        "strategies": {},
        "notifiers": {
            "console": {"enabled": True, "as_json": False},
            "telegram": {
                "enabled": False,
                # دستورهای /signals، /status، /report، /mute.
                # جدا از `enabled` است: کسی می‌تواند اعلان بخواهد
                # ولی ربات دوطرفه نخواهد.
                "commands_enabled": False,
                "mute_state_path": "var/telegram_mute.json",
                "command_state_path": "var/telegram_offset.json",
            },
        },
        "storage": {
            "enabled": True,
            "sqlite_path": "var/signals.db",
            "jsonl_path": "var/signals.jsonl",
        },
        # اتصال به حساب کارگزاری: پیش‌فرض خاموش. حتی روشن هم فقط می‌خواند.
        "broker": {
            "enabled": False,
            "provider": "emofid",
            "session_file": "var/emofid/session.json",
            "base_url": "https://api-mts.orbis.easytrader.ir",
            "timeout": 15,
            "retries": 3,
        },
        # تقویم معاملاتی: تعطیلات را از تاریخچه‌ی **واقعی** یک نماد
        # پرمعامله یاد می‌گیرد، نه از یک جدول دستیِ رو به کهنگی.
        "trading_calendar": {
            "learn_from_market": True,
            "reference_symbol": "خودرو",
            "learn_days": 365,
            "cache_path": "var/trading_calendar.json",
            # تعطیلی اضطراریِ اعلام‌شده که هنوز در تاریخچه نیامده (YYYY-MM-DD)
            "extra_holidays": [],
        },
        # پایش سلامت و گزارش دوره‌ای.
        #
        # هشدار سلامت پیش‌فرض **روشن** است چون خرابی‌هایی را می‌گیرد که
        # بی‌صدا هستند (قطعی داده، استراتژی مرده، سکوت طولانی) و ربات
        # در همه‌شان «سالم» به نظر می‌رسد.
        #
        # گزارش دوره‌ای پیش‌فرض خاموش است: پیام دوره‌ای فرستادن باید
        # انتخاب صریح کاربر باشد، نه اتفاقی.
        # تاریخچه‌ی IV هر نماد: IV تاریخی از هیچ endpoint عمومی
        # در دست نیست، پس هر پاس خودمان ثبتش می‌کنیم. تا نمونه‌ی
        # کافی جمع نشود، صدک None است و استراتژی به معیار قبلی
        # برمی‌گردد — پس روشن بودنش رفتار کسی را عوض نمی‌کند.
        "iv_history": {
            "enabled": True,
            "path": "var/iv_history.json",
            "max_days": 365,
            "min_samples": 20,
        },
        "monitoring": {
            "health_enabled": True,
            "periodic_report_enabled": False,
            "alert_cooldown_hours": 6.0,
            "alert_state_path": "var/health_alerts.json",
            "report_state_path": "var/report_schedule.json",
            "thresholds": {
                "drought_warning_days": 3,
                "drought_critical_days": 7,
                "data_warning_failures": 2,
                "data_critical_failures": 5,
                "strategy_error_threshold": 3,
                "missing_quote_ratio": 0.8,
            },
        },
        "backtest": {
            "history_days": 180,
            "horizon_days": 10,
            "warmup_days": 30,
            "step_days": 1,
            # بدون تعدیل، روز افزایش سرمایه یک ریزش ساختگی است و هر
            # استراتژی تکنیکالی آن را سیگنال نزولی قوی می‌فهمد.
            "adjust_corporate_actions": True,
            # پرمیوم **واقعی** آپشن به‌جای فقط جهت‌دهی نماد پایه.
            # خاموشش کنید اگر شبکه در دسترس نیست یا سرعت مهم‌تر است.
            "use_real_premiums": True,
        },
        # اجرای سفارش **واقعی** (اتصال به API کارگزاری) هنوز پیاده‌سازی
        # نشده و این مقدار باید false بماند؛ نیاز به تصمیم صریح آینده‌ی
        # کاربر دارد. معاملات کاغذی (`paper_trading` پایین) از این جداست:
        # یک کارگزار شبیه‌سازی‌شده بدون اتصال واقعی، که مجوزش را کاربر
        # داده است.
        "execution": {"enabled": False},
        # معاملات کاغذی (Paper Trading): کارگزار شبیه‌سازی‌شده، بدون اتصال
        # واقعی. پیش‌فرض خاموش — انتخاب صریح کاربر، مثل بقیه‌ی سوئیچ‌های
        # این پروژه. کارمزد پیش‌فرض **صفر** است و حدس زده نمی‌شود؛ همان
        # قاعده‌ای که `risk.fees` رعایت می‌کند — کاربر نرخ واقعی کارگزاری
        # خودش را از داشبورد وارد می‌کند.
        "paper_trading": {
            "enabled": False,
            # ریال. معادل ۵۰۰ میلیون تومان.
            "initial_balance": 5_000_000_000.0,
            "sqlite_path": "var/paper_trading.db",
            # حسابِ **آزمایشی** پایگاهِ خودش را دارد تا تمرین هیچ‌وقت با
            # حسابِ واقعیِ کاغذی قاطی نشود. ریست و گزارشش هم جداست.
            "sandbox_sqlite_path": "var/paper_trading_sandbox.db",
            "fees": {
                "buy_rate": 0.0,
                "sell_rate": 0.0,
                "sell_tax_rate": 0.0,
                "per_order": 0.0,
                # ⚠️ صفرِ بالا «پیش‌فرضِ پروژه» است، نه ادعای بی‌هزینه
                # بودن. تا این پرچم `true` نشود، هزینه‌ی هر معامله‌ای که
                # زیر همین نرخ‌ها انجام شود **نامعلوم** ثبت می‌شود و
                # «خالصِ قطعی» نمایش داده نمی‌شود. اگر کارگزاری شما
                # واقعاً کارمزد ندارد، همین را `true` کنید.
                "declared": False,
            },
            # مثل مقدار پیش‌فرض خودِ OrderBookClient
            "order_book_ttl_seconds": 10.0,
            # عمرِ بلیتِ «بررسی پیش از ورود» (ثانیه). کوتاه است چون کارش
            # همین است: تأییدی که با دادهٔ کهنه انجام شود، تأییدِ چیزِ
            # دیگری است.
            "ticket_ttl_seconds": 90.0,
            # قیمتِ اجراییِ ورود تا این درصد بتواند بین بررسی و تأیید
            # تکان بخورد. بیشتر از این ⇒ بررسی دوباره.
            "ticket_price_tolerance_pct": 0.5,
        },
        # غربالِ قابلیت معامله — دروازه‌ی ورود به پیشنهادها.
        #
        # ⚠️ این آستانه‌ها **اثبات‌شده نیستند**. هیچ پژوهشی پشتشان نیست؛
        # نقطه‌ی شروعی محافظه‌کارانه‌اند تا قراردادِ آشکارا مرده پیشنهاد
        # نشود. واحد هر کدام در نامش آمده. جزئیات در
        # `docs/strategy-research.md`.
        "tradability": {
            "enabled": True,
            "min_open_interest_contracts": 50,
            "min_trades_today_count": 1,
            "max_relative_spread_pct": 25.0,
            "min_exit_depth_ratio": 1.0,
            # عمقی که فقط در قیمت‌های دور هست، سفارش را پر می‌کند ولی
            # «ظرفیت خروج» نیست.
            "max_exit_slippage_pct": 10.0,
            "min_sessions_with_trades_pct": 60.0,
            "min_history_sessions": 5,
            "min_days_to_expiry": 3,
            "max_quote_age_seconds": 120.0,
            # تاریخچه‌ی تداوم معامله از پایگاه **خام** recorder خوانده
            # می‌شود — فقط‌خواندنی. نبودش یعنی «نامعلوم»، نه «بد».
            "history_db_path": "var/recorder/market.db",
            "history_lookback_sessions": 20,
        },
        # رتبه‌بندیِ «اولویت بررسی» روی گزینه‌هایی که از غربال گذشته‌اند.
        #
        # ⚠️ این وزن‌ها **فرضِ اولیه‌اند**، نه نتیجه‌ی پژوهش یا بهینه‌سازی.
        # امتیازِ خروجی احتمال برد یا بازده مورد انتظار **نیست**؛ فقط
        # می‌گوید با دادهٔ موجود کدام گزینه ارزشِ بررسیِ دقیق‌تر دارد.
        # جزئیات در `market/opportunity_ranking.py`.
        "ranking": {
            "enabled": True,
            # --- وزن مؤلفه‌ها (جمعشان لازم نیست ۱۰۰ باشد؛ نسبی‌اند) ---
            "weight_round_trip_cost": 30.0,
            "weight_exit_capacity": 20.0,
            "weight_time_to_expiry": 15.0,
            "weight_required_move": 25.0,
            "weight_fee_cost": 10.0,
            # --- نقطه‌ی اشباع هر سنجه ---
            "depth_comfort_multiple": 3.0,
            "days_to_expiry_comfort": 30,
            "max_required_move_pct": 25.0,
            "max_round_trip_cost_pct": 30.0,
            "max_fee_cost_pct": 5.0,
        },
        # ثبت تاریخچه‌ی خام بازار (`scripts/record_market.py`).
        #
        # این بخش فقط **پیش‌فرض مسیرها** را نگه می‌دارد؛ خودِ ابزار از CLI
        # اجرا می‌شود و به حلقه‌ی اصلی ربات وصل نیست. حلقه‌ی زمان‌بندی در
        # مرحله‌ی بعد اضافه می‌شود، پس `enabled` هنوز چیزی را روشن
        # نمی‌کند و صرفاً نیت کاربر را ثبت می‌کند.
        "recorder": {
            "enabled": False,
            "sqlite_path": "var/recorder/market.db",
            # پایگاه آزمایشی عمداً مسیر جداست: داده‌ی fixture و زنده
            # هرگز در یک فایل مخلوط نمی‌شوند (لایه‌ی ذخیره‌سازی هم این
            # را مستقل از تنظیمات اعمال می‌کند).
            "test_sqlite_path": "var/recorder/test.db",
            "market": 0,
            "timeout": 15,
            "retries": 3,
            # --- زمان‌بندی (`--loop`) ---
            # فاصله‌ی نوبت‌ها. نوبت‌ها روی ساعت دیوار هم‌تراز می‌شوند،
            # پس با ۳۰۰ روی ۰۹:۰۰، ۰۹:۰۵، … می‌افتند.
            "interval_seconds": 300,
            # تصمیم‌های زمانی با این منطقه گرفته می‌شوند، نه با ساعت
            # محلیِ بی‌نام سیستم. اگر پایگاه tz در دسترس نباشد، به
            # آفست ثابت +۰۳:۳۰ برمی‌گردد (ایران DST ندارد) و هشدار
            # می‌دهد.
            "timezone": "Asia/Tehran",
            # ساعت جلسه. جدا از `MarketSession` تقویم نگه داشته شده تا
            # تغییرش رفتار تولید سیگنال را عوض نکند.
            "session_start": "09:00",
            "session_end": "12:30",
            # چند ثانیه پس از پایان جلسه هم ثبت ادامه یابد.
            # ⚠️ این snapshotها «قیمت پایانی» نیستند — منبع هیچ مهر
            # زمانی نمی‌دهد، پس فقط مشاهده‌ی عادی‌اند.
            "closing_grace_seconds": 300,
        },
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """ادغام بازگشتی دو دیکشنری (مقادیر `override` برنده‌اند).

    ادغام عمیق لازم است تا مثلاً بازنویسی یک پارامتر استراتژی، بقیه پارامترهای
    همان استراتژی را پاک نکند.
    """
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = deep_merge(current, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class SettingsError(RuntimeError):
    """خطای تنظیمات که باید اجرا را متوقف کند، نه اینکه بی‌صدا رد شود."""


def load_settings(config_path: Path | str | None = None) -> dict[str, Any]:
    """خواندن تنظیمات yaml و ادغام عمیق آن با پیش‌فرض‌ها."""
    defaults = default_settings()
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH

    if not path.exists():
        logger.info("فایل تنظیمات %s پیدا نشد؛ از مقادیر پیش‌فرض استفاده می‌شود.", path)
        return defaults

    try:
        import yaml  # وابستگی نرم
    except ImportError:
        # فایل تنظیمات **وجود دارد** ولی خوانده نمی‌شود. حالا که داده‌ی
        # ساختگی حذف شده، خطرِ «قیمت جعلی» نیست — ولی همچنان یعنی نمادها،
        # سقف ریسک و پارامترهای استراتژی شما نادیده گرفته می‌شوند و ربات
        # با پیش‌فرض‌های دیگری کار می‌کند. این خطاست، نه هشدار.
        raise SettingsError(
            f"فایل تنظیمات {path} وجود دارد ولی PyYAML نصب نیست، پس خوانده نشد.\n"
            "یعنی نمادها، سقف ریسک و پارامترهای استراتژی شما اعمال نمی‌شود.\n"
            "راه‌حل:  .venv\\Scripts\\python.exe -m pip install PyYAML"
        ) from None

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"ساختار فایل تنظیمات {path} باید یک دیکشنری باشد.")

    logger.info("تنظیمات از %s خوانده شد.", path)
    return deep_merge(defaults, loaded)


def section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    """یک بخش از تنظیمات را همیشه به‌صورت دیکشنری برمی‌گرداند."""
    value = settings.get(name)
    return value if isinstance(value, dict) else {}


def resolve_path(value: str | Path, root: Path | None = None) -> Path:
    """مسیر نسبی را نسبت به ریشه پروژه حل می‌کند تا cwd روی خروجی اثر نگذارد."""
    path = Path(value)
    return path if path.is_absolute() else (root or PROJECT_ROOT) / path


def build_dataclass(
    cls: type[T],
    values: dict[str, Any],
    label: str = "",
    ignore: set[str] | None = None,
) -> T:
    """ساخت یک dataclass از دیکشنری تنظیمات، با نادیده‌گرفتن کلیدهای ناشناخته.

    یک کلید اضافه یا غلط‌املایی در yaml نباید کل ربات را با TypeError بخواباند؛
    فقط هشدار می‌دهیم تا در لاگ دیده شود.

    Args:
        ignore: کلیدهایی که **عمداً** فیلد این dataclass نیستند ولی در همان
            بخش yaml می‌نشینند (مثل سوئیچ‌های رفتاری). بدون این، هشدارِ
            «کلید ناشناخته» که برای گرفتن غلط‌املایی است، روی یک کلید
            درست هم روشن می‌شود و اعتبارش را از دست می‌دهد.
    """
    field_names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
    unknown = sorted(set(values or {}) - field_names - (ignore or set()))
    if unknown:
        logger.warning(
            "کلیدهای ناشناخته در بخش %s نادیده گرفته شدند: %s",
            label or cls.__name__,
            ", ".join(unknown),
        )
    return cls(**{k: v for k, v in (values or {}).items() if k in field_names})

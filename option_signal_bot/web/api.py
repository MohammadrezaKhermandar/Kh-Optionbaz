"""لایه API داشبورد.

مرزهای این ماژول عمداً تنگ است:

* **هیچ سفارش واقعی ثبت نمی‌شود.** این ماژول به کارگزاری واقعی وصل نمی‌شود.
  تنها استثنای صریح: endpoint های `/api/paper-trading/*` که `PaperBroker`
  (کارگزار **شبیه‌سازی‌شده**، بدون اتصال واقعی) را از `execution/` صدا
  می‌زنند — با تصمیم صریح کاربر. این ماژول تنها فایل خارج از `execution/`
  است که تست گارد سراسری پروژه اجازه‌ی import کردن `execution` را به آن
  می‌دهد؛ هیچ فایل دیگری این اجازه را ندارد.
* نوشتن فقط روی `config/settings.yaml` است، نه چیز دیگر.
* پاس رصد بازار از همان `run_cycle` در `main.py` می‌آید، نه یک نسخه‌ی موازی؛
  تا داشبورد و ترمینال هرگز دو روایت مختلف از یک پاس نگویند.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import fields
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from bootstrap import create_app
from config import force_utf8_stdio
from config.loader import PROJECT_ROOT, deep_merge, load_settings, resolve_path, section
from market.trading_calendar import format_jalali
from signals.signal_model import Signal
from storage.signal_log import SignalLog
from strategies.registry import available_strategies, get_strategy_class

# اینجا `main()` نداریم که اول کار صدایش بزنیم؛ سرور با
# `uvicorn web.api:app` بالا می‌آید. یک پاس رصد، سیگنال‌ها را به
# ConsoleNotifier می‌دهد که فارسی چاپ می‌کند، و stdout پیش‌فرض ویندوز
# cp1252 است — بدون این، خودِ پاس رصد با UnicodeEncodeError می‌افتد.
force_utf8_stdio()

logger = logging.getLogger("option_signal_bot.web")

STATIC_DIR = Path(__file__).parent / "static"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"
EXAMPLE_PATH = PROJECT_ROOT / "config" / "settings.example.yaml"

app = FastAPI(title="GarnetTrader — داشبورد سیگنال", docs_url="/api/docs")

#: قفل، تا دو درخواست همزمان یک پاس رصد را دوبار اجرا نکنند
_scan_lock = asyncio.Lock()


@app.exception_handler(RequestValidationError)
async def _validation_error(
    request: Request,  # noqa: ARG001 — امضا را خودِ FastAPI تعیین می‌کند
    exc: RequestValidationError,
) -> JSONResponse:
    """۴۲۲ تمیز، حتی وقتی ورودیِ رد‌شده خودش JSON نمی‌شود.

    پاسخ پیش‌فرض FastAPI مقدارِ ورودی را در بدنه بازمی‌تاباند. برای
    `inf`/`nan` همان بازتاباندن با `Out of range float values are not
    JSON compliant` می‌شکست و کاربر به‌جای «رد شد» یک خطای سرور می‌گرفت.
    """
    errors = []
    for error in exc.errors():
        safe = {k: v for k, v in error.items() if k != "input"}
        value = error.get("input")
        try:
            # ⚠️ `allow_nan=False` لازم است: `json.dumps` پیش‌فرض برای
            # `inf` رشته‌ی غیراستاندارد `Infinity` می‌سازد و بی‌صدا رد
            # می‌شود، بعد خودِ پاسخ سر سریالایز شدن می‌شکند.
            json.dumps(value, allow_nan=False)
            safe["input"] = value
        except (TypeError, ValueError):
            safe["input"] = repr(value)
        errors.append(safe)
    return JSONResponse(status_code=422, content={"detail": errors})


# ----------------------------------------------------------------------
# کمکی‌ها
# ----------------------------------------------------------------------
def _settings() -> dict[str, Any]:
    """تنظیمات فعلی. اگر `settings.yaml` نبود، از فایل نمونه ساخته می‌شود."""
    if not SETTINGS_PATH.exists() and EXAMPLE_PATH.exists():
        SETTINGS_PATH.write_text(
            EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8", newline="\n"
        )
        logger.info("settings.yaml از فایل نمونه ساخته شد.")
    return load_settings(SETTINGS_PATH)


def _signal_log(settings: dict[str, Any]) -> SignalLog:
    storage = section(settings, "storage")
    return SignalLog(
        db_path=resolve_path(storage.get("sqlite_path", "var/signals.db")),
        jsonl_path=resolve_path(storage.get("jsonl_path", "var/signals.jsonl")),
    )


def _paper_broker(settings: dict[str, Any], sandbox: bool = False):
    """می‌سازد `PaperBroker` را با اجزای واقعی (عمق مظنه، زنجیره آپشن).

    با `sandbox=True` همان کارگزار روی دادهٔ نمونه و پایگاهِ جدا ساخته
    می‌شود؛ هیچ‌کدام از دو مسیر پایگاه یا مظنه‌ی دیگری را نمی‌بینند.

    این تنها جای مجاز import کردن `execution` خارج از خودِ آن پوشه است
    (تصمیم صریح کاربر، تست گارد `test_only_execution_layer_imports_execution`
    همین یک فایل را استثنا کرده).
    """
    from data.option_chain_client import OptionChainClient
    from data.order_book import OrderBookClient
    from execution.paper_broker import PaperBroker
    from risk.fees import FeeSchedule
    from storage.paper_trading_store import PaperTradingStore

    if sandbox:
        return _sandbox_broker(settings)

    config = section(settings, "paper_trading")
    store = PaperTradingStore(resolve_path(config.get("sqlite_path", "var/paper_trading.db")))
    order_book_client = OrderBookClient(
        ttl_seconds=config.get("order_book_ttl_seconds", 10.0)
    )
    fee_config = config.get("fees") or {}
    fees = FeeSchedule(
        buy_rate=fee_config.get("buy_rate", 0.0),
        sell_rate=fee_config.get("sell_rate", 0.0),
        sell_tax_rate=fee_config.get("sell_tax_rate", 0.0),
        per_order=fee_config.get("per_order", 0.0),
        # صفرِ اعلام‌شده از صفرِ پیش‌فرض جدا می‌ماند: اولی هزینه‌ی دانسته
        # است، دومی یعنی «نمی‌دانیم».
        declared=bool(fee_config.get("declared", False)),
    )

    context = create_app(settings, dry_run=True, as_json=False)
    option_chain: OptionChainClient = context.option_chain

    def resolve_contract(symbol: str):
        return option_chain.get_contract(symbol)

    return PaperBroker(
        store=store,
        order_book_client=order_book_client,
        resolve_contract=resolve_contract,
        initial_balance=config.get("initial_balance", 0.0),
        fees=fees,
    ), context


class _NullContext:
    """جای `AppContext` در مسیر آزمایشی — چیزی برای بستن نیست.

    مسیر آزمایشی هیچ کلاینت شبکه‌ای نمی‌سازد، ولی فراخواننده‌ها همه
    `context.close()` را صدا می‌زنند؛ همین یک متد کافی است تا دو مسیر
    شکلِ یکسانی داشته باشند.
    """

    def close(self) -> None:
        return None


def _sandbox_broker(settings: dict[str, Any]):
    """`PaperBroker` روی دادهٔ نمونه و **پایگاهِ جدا**.

    هیچ‌چیزِ این مسیر از تنظیماتِ حساب واقعی نمی‌آید جز مسیر پایگاه
    (که خودش هم پیش‌فرضِ جداگانه دارد): موجودی و نرخ کارمزد ثابت و
    **اعلام‌شده**‌اند تا عددهای تمرین کامل باشند و کاربر با تغییر
    تنظیماتِ واقعی، نتیجه‌ی تمرین را عوض نکند — و برعکس.
    """
    from execution.paper_broker import PaperBroker
    from market import sandbox
    from risk.fees import FeeSchedule
    from storage.paper_trading_store import PaperTradingStore

    config = section(settings, "paper_trading")
    store = PaperTradingStore(
        resolve_path(config.get("sandbox_sqlite_path", "var/paper_trading_sandbox.db"))
    )
    return PaperBroker(
        store=store,
        order_book_client=sandbox.SandboxOrderBookClient(),
        resolve_contract=sandbox.resolve_contract,
        initial_balance=sandbox.SANDBOX_INITIAL_BALANCE,
        fees=FeeSchedule(
            buy_rate=sandbox.SANDBOX_FEE_RATE,
            sell_rate=sandbox.SANDBOX_FEE_RATE,
            declared=True,
        ),
    ), _NullContext()


def _require_paper_trading_enabled(settings: dict[str, Any], sandbox: bool = False) -> None:
    # مسیر آزمایشی عمداً به کلیدِ حساب واقعی گره نمی‌خورد: کسی که هنوز
    # معاملات کاغذی را روشن نکرده، دقیقاً همان کسی است که می‌خواهد اول
    # جریان را یک‌بار تمرین کند.
    if sandbox:
        return
    if not section(settings, "paper_trading").get("enabled"):
        raise HTTPException(
            status_code=400,
            detail="معاملات کاغذی خاموش است. از تب «معاملات کاغذی» فعالش کنید.",
        )


def _is_multi_leg(strategy_name: str) -> bool:
    """آیا این استراتژی خروجی‌اش یک **ساختار چندپایه** است؟

    از رجیستریِ موجود و سلسله‌مراتب کلاس‌ها خوانده می‌شود، نه از فهرستی
    دستی که با اضافه شدن استراتژی بعدی بی‌صدا کهنه شود.
    """
    from strategies.multi_leg import MultiLegStrategy

    strategy_class = get_strategy_class(strategy_name)
    return strategy_class is not None and issubclass(strategy_class, MultiLegStrategy)


def _reject_multi_leg(strategy_name: str) -> None:
    """اجرای یک **پایه‌ی تنها** از یک ساختار چندپایه را رد می‌کند.

    ریسک و سرمایه‌ی لازمِ یک استردل یا کولار با ریسکِ یکی از پایه‌هایش
    یکی نیست، و این کارگزار فقط long تک‌پایه را مدل می‌کند. اجرای نیمِ
    ساختار، حسابی می‌سازد که عددهایش درست‌اند ولی چیزی را می‌سنجند که
    کاربر قصدش را نداشته.
    """
    if _is_multi_leg(strategy_name):
        raise HTTPException(
            status_code=400,
            detail=(
                f"سیگنال «{strategy_name}» یک پایه از یک ساختار چندپایه است. "
                "معاملات کاغذی فعلاً فقط پوزیشن long تک‌پایه را مدل می‌کند؛ "
                "وجه تضمین و سرمایه‌ی لازمِ ساختار کامل پیاده‌سازی نشده و "
                "حدس هم زده نمی‌شود. اجرای یک پایه‌ی تنها، ریسکی متفاوت از "
                "خودِ ساختار دارد."
            ),
        )


def _entry_decisions(broker: Any) -> dict[str, dict[str, Any]]:
    """آخرین «عکسِ تصمیم» برای هر نماد، از سفارش‌های خریدِ پرشده.

    روی خودِ موقعیت ستونی برایش نیست و ساختنِ ستونِ تازه هم لازم نبود:
    سفارشِ ورود همان‌جاست و `metadata` دارد.
    """
    decisions: dict[str, dict[str, Any]] = {}
    for order in broker.store.list_orders(limit=200):
        if order.get("side") != "buy" or order.get("status") != "filled":
            continue
        symbol = order.get("symbol")
        if symbol in decisions:
            continue  # فهرست از جدید به قدیم است؛ اولی تازه‌ترین است
        metadata = order.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = None
        if isinstance(metadata, dict) and metadata.get("decision"):
            decisions[symbol] = metadata["decision"]
    return decisions


def _serialize_order(order: Any) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side,
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "price": order.price,
        "status": order.status.value,
        "remaining_quantity": order.remaining_quantity,
        "metadata": order.metadata,
        "created_at": order.created_at.isoformat(timespec="seconds"),
    }


def _signal_dict(signal: Signal) -> dict[str, Any]:
    """سیگنال را برای UI سریالایز می‌کند، با دو مقدار محاسبه‌شده."""
    data: dict[str, Any] = json.loads(signal.to_json())
    # هر دو property هستند، نه متد
    data["days_to_expiry"] = signal.days_to_expiry
    data["notional"] = signal.notional
    return data


def _patch_settings(patch: dict[str, Any]) -> None:
    """ادغام عمیق `patch` در `settings.yaml`.

    کامنت‌های فارسی فایل با بازنویسی از دست می‌روند، ولی ساختار و همه‌ی
    مقادیر دیگر حفظ می‌شوند. نوشتن با UTF-8 بدون BOM انجام می‌شود، چون
    PyYAML فایل double-encode شده را نمی‌خواند.
    """
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - در requirements هست
        raise HTTPException(
            status_code=500, detail="PyYAML نصب نیست؛ ویرایش تنظیمات ممکن نیست."
        ) from exc

    current: dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        with SETTINGS_PATH.open("r", encoding="utf-8") as handle:
            current = yaml.safe_load(handle) or {}

    merged = deep_merge(current, patch)
    text = yaml.safe_dump(merged, allow_unicode=True, sort_keys=False, indent=2)
    SETTINGS_PATH.write_text(text, encoding="utf-8", newline="\n")
    logger.info("settings.yaml به‌روزرسانی شد: %s", list(patch))


# ----------------------------------------------------------------------
# مدل‌های ورودی
# ----------------------------------------------------------------------
class SymbolsUpdate(BaseModel):
    symbols: list[str] = Field(..., description="نمادهای پایه برای رصد")


class StrategyUpdate(BaseModel):
    enabled: bool | None = None
    params: dict[str, Any] | None = None


class RiskUpdate(BaseModel):
    account_equity: float | None = None
    risk_per_trade_pct: float | None = None
    max_position_pct: float | None = None
    max_contracts: int | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    #: دارایی از کارگزاری خوانده شود؟ (نیاز به broker.enabled)
    use_broker_equity: bool | None = None


# ----------------------------------------------------------------------
# سیگنال‌ها
# ----------------------------------------------------------------------
@app.get("/api/signals")
def get_signals(
    limit: int = 100,
    strategy: str | None = None,
    underlying: str | None = None,
) -> dict[str, Any]:
    """سیگنال‌های ذخیره‌شده، جدیدترین اول، با فیلتر اختیاری."""
    settings = _settings()
    with _signal_log(settings) as log:
        signals = log.all_signals(limit=None)

    if strategy:
        signals = [s for s in signals if s.strategy_name == strategy]
    if underlying:
        signals = [s for s in signals if s.underlying == underlying]

    signals.sort(key=lambda s: s.created_at, reverse=True)
    return {
        "total": len(signals),
        "signals": [_signal_dict(s) for s in signals[:limit]],
    }


@app.post("/api/scan")
async def run_scan() -> dict[str, Any]:
    """یک پاس رصد بازار.

    سفارشی ثبت نمی‌شود؛ فقط سیگنال تولید، ذخیره و به notifierها فرستاده
    می‌شود — دقیقاً همان کاری که `main.py --once` می‌کند.
    """
    if _scan_lock.locked():
        raise HTTPException(status_code=409, detail="یک پاس رصد در حال اجراست.")

    async with _scan_lock:
        settings = _settings()

        def _work() -> tuple[list[Signal], dict[str, Any]]:
            # از run_cycle خودِ main.py استفاده می‌کنیم تا منطق پاس رصد
            # در دو جا تکرار (و با هم واگرا) نشود.
            from main import run_cycle

            context = create_app(settings, dry_run=False, as_json=False)
            try:
                produced = run_cycle(context)
                generator = context.generator
                screening = generator.screening.to_dict()
                # «غربال خاموش بود» با «همه قبول شدند» یکی نیست.
                screening["enabled"] = generator.tradability is not None
                screening["history"] = _history_state(generator.tradability)
                # رکوردهای خام فقط برای رتبه‌بندی لازم‌اند و به UI
                # نمی‌روند؛ نسخه‌ی سریالایزشده‌شان از قبل در `records` هست.
                screening["_records"] = generator.screening.records
                return produced, screening
            finally:
                context.close()

        try:
            signals, screening = await asyncio.to_thread(_work)
        except Exception as exc:  # پیام خطا به UI برگردانده می‌شود
            logger.exception("پاس رصد ناموفق بود.")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    try:
        regime = await asyncio.to_thread(_scan_regime, settings, signals)
    except Exception as exc:  # تحلیل وضعیت نباید پاس رصد را بخواباند
        logger.exception("تحلیل وضعیت شکست خورد؛ بقیه‌ی پاس دست‌نخورده ماند.")
        regime = {"enabled": True, "error": str(exc)}

    try:
        ranking = _build_ranking(settings, signals, screening, regime)
    except Exception as exc:  # رتبه‌بندی یک لایه‌ی نمایشی است، نه پیش‌نیاز
        logger.exception("رتبه‌بندی فرصت‌ها شکست خورد؛ پاس رصد دست‌نخورده ماند.")
        ranking = {"enabled": True, "error": str(exc)}
    screening.pop("_records", None)

    return {
        "generated": len(signals),
        "signals": [_signal_dict(s) for s in signals],
        "screening": screening,
        "ranking": ranking,
    }


def _ranking_weights(settings: dict[str, Any]) -> Any:
    """`RankingWeights` از تنظیمات؛ کلیدهای ناشناخته بی‌صدا نادیده می‌روند."""
    from market.opportunity_ranking import RankingWeights

    config = section(settings, "ranking")
    known = {f.name for f in fields(RankingWeights)}
    return RankingWeights(**{k: v for k, v in config.items() if k in known})


def _ranking_candidate(
    record: Any, signal: Signal | None, fees: Any
) -> dict[str, Any]:
    """ورودیِ رتبه‌بندی برای یک رکوردِ غربال.

    `signal is None` فقط برای رکوردهایی است که اصلاً منتشر نمی‌شوند
    (ردشده/نیازمند بررسی)؛ آن‌ها پیش از هر محاسبه‌ای در دروازه‌ی اولِ
    رتبه‌بندی کنار می‌روند، پس عددهای قرارداد لازمشان نیست.
    """
    return {
        "report": record.report,
        "symbol": record.symbol,
        "signal_id": record.signal_id,
        "strategy": record.strategy,
        "side": record.side,
        "quantity": record.quantity,
        "leg_group_id": record.leg_group_id,
        "option_type": signal.option_type.value if signal else "",
        "strike": signal.strike if signal else 0.0,
        "contract_size": signal.units_per_contract if signal else 1,
        "underlying_price": signal.underlying_price if signal else None,
        # ⚠️ **قیمتِ** حد ضرر می‌رود، نه مبلغِ زیانِ ماژول ریسک: آن مبلغ
        # از پرمیومِ *پیشنهادی* ساخته شده و با سرمایه و سر‌به‌سرِ
        # رتبه‌بندی — که از قیمتِ اجرایی می‌آیند — هم‌مبنا نیست.
        "stop_loss_price": signal.stop_loss if signal else None,
        # نرخ، نه مبلغ: کارمزدِ ورود از قیمتِ اجراییِ ورود و کارمزدِ
        # خروجِ فرضی از قیمتِ اجراییِ خروجِ همان تعداد حساب می‌شود.
        "fees": fees,
    }


def _fee_schedule(settings: dict[str, Any]) -> Any:
    """نرخ کارمزدِ ماژول ریسک، همان‌طور که تنظیم شده.

    صفرِ اعلام‌شده هزینه‌ی دانسته است؛ صفرِ پیش‌فرضِ پروژه یعنی
    «نمی‌دانیم» (`FeeSchedule.rates_known`). بدون این تفکیک، هر دو
    یک‌جور خوانده می‌شدند.
    """
    from risk.fees import FeeSchedule

    config = section(settings, "risk").get("fees") or {}
    known = {f.name for f in fields(FeeSchedule)}
    return FeeSchedule(**{k: v for k, v in config.items() if k in known})


def _scan_regime(settings: dict[str, Any], signals: list[Signal]) -> dict[str, Any]:
    """وضعیتِ بازار و نمادهای پایه‌ی همین پاس.

    شکستش کشنده نیست: رتبه‌بندی و غربال بدونش هم کار می‌کنند و فقط این
    بخش «نامشخص» می‌ماند.
    """
    symbols = [s.underlying for s in signals if s.underlying]
    if not symbols:
        symbols = list(section(settings, "market_data").get("symbols") or [])
    context = create_app(settings, dry_run=True, as_json=False)
    try:
        return _regime_block(settings, context, symbols, date.today())
    finally:
        context.close()


def _attach_fit(
    payload: dict[str, Any],
    regime: dict[str, Any] | None,
    by_signal_id: dict[str, Signal],
) -> None:
    """تناسبِ جهتِ هر فرصت با وضعیت — **کنارِ** ردیف، نه داخلِ امتیاز.

    امتیاز و ترتیبِ رتبه دست نمی‌خورند؛ اگر این تحلیل روزی امتیاز بگیرد
    باید اول اعتبارسنجی شود، وگرنه یک مؤلفه‌ی ناسنجیده بی‌صدا در رتبه
    می‌نشیند.
    """
    if not regime or not regime.get("enabled", False):
        return
    from market.opportunity_fit import assess_fit

    reports = regime.get("_reports") or {}
    market = reports.get("market")
    underlyings = reports.get("underlyings") or {}
    for row in payload.get("ranked", []):
        signal = by_signal_id.get(row.get("signal_id") or "")
        if signal is None:
            continue
        fit = assess_fit(
            option_type=signal.option_type.value,
            side=row.get("side", "buy"),
            underlying=underlyings.get(signal.underlying),
            market=market,
            days_to_expiry=signal.days_to_expiry,
        )
        row["underlying"] = signal.underlying
        row["fit"] = fit.to_dict()


def _build_ranking(
    settings: dict[str, Any],
    signals: list[Signal],
    screening: dict[str, Any],
    regime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """رتبه‌بندیِ همین پاس، از رکوردهای غربال و خودِ سیگنال‌ها.

    داده‌ی قرارداد (استرایک، پرمیوم، قیمت پایه) روی `Signal` است و
    نتیجه‌ی غربال روی `ScreeningRecord`؛ با نماد به هم وصل می‌شوند.
    """
    config = section(settings, "ranking")
    if not config.get("enabled", True):
        return {"enabled": False, "reason": "رتبه‌بندی در تنظیمات خاموش است."}

    from market.opportunity_ranking import rank_opportunities
    from market.tradability import Thresholds, Verdict

    records = screening.get("_records") or []
    if not records:
        return {
            "enabled": True,
            "ranked": [],
            "excluded": [],
            "reason": (
                "غربالی اجرا نشد یا هیچ گزینه‌ای ارزیابی نشد؛ چیزی برای "
                "رتبه‌بندی نیست."
            ),
        }

    # ⚠️ با **شناسه‌ی سیگنال** وصل می‌شوند، نه با نماد: روی یک نماد
    # می‌تواند چند سیگنال از چند استراتژی، با سمت و تعدادِ متفاوت، در
    # یک پاس باشد و وصل‌کردن با نماد عددهای بی‌ربط را قاطی می‌کند.
    by_signal_id = {s.signal_id: s for s in signals}
    thresholds_config = section(settings, "tradability")
    known_thresholds = {f.name for f in fields(Thresholds)}
    thresholds = Thresholds(**{
        k: v for k, v in thresholds_config.items() if k in known_thresholds
    })

    fees = _fee_schedule(settings)
    candidates: list[dict[str, Any]] = []
    unpublished: list[dict[str, str]] = []
    for record in records:
        signal = by_signal_id.get(record.signal_id) if record.signal_id else None
        if signal is None:
            if record.report.verdict is not Verdict.TRADABLE:
                # ردشده و نیازمند بررسی اصلاً منتشر نمی‌شوند؛ ولی حکم و
                # علتشان باید در کنارگذاشته‌ها بماند. بدون سیگنال هم
                # همه‌ی چیزی که برای گفتنِ «چرا» لازم است روی خودِ رکورد
                # هست، پس با گزارش کامل به رتبه‌بندی می‌روند.
                candidates.append(_ranking_candidate(record, None, fees))
                continue
            # از غربال گذشت ولی منتشر نشد — مثلاً تکراریِ بازه‌ی ضدتکرار.
            # بدون خودِ سیگنال، استرایک و قیمت در دست نیست و رتبه‌دادن
            # یعنی امتیازی که کاربر نمی‌تواند اجرایش کند.
            unpublished.append({
                "symbol": record.symbol,
                "strategy": record.strategy,
                "reason": (
                    "از غربال گذشت ولی سیگنالش منتشر نشد (تکراری در بازه‌ی "
                    "ضدتکرار، یا پایه‌ی ساختاری که کنار رفت)."
                ),
            })
            continue
        candidates.append(_ranking_candidate(record, signal, fees))

    result = rank_opportunities(
        candidates=candidates,
        thresholds=thresholds,
        weights=_ranking_weights(settings),
        evaluated_at=datetime.now(),
    )
    payload = result.to_dict()
    _attach_fit(payload, regime, by_signal_id)
    if regime is not None:
        payload["regime"] = {k: v for k, v in regime.items() if k != "_reports"}
    # کنارگذاشته‌های این لایه هم باید دیده شوند، نه اینکه بی‌صدا گم شوند.
    payload["excluded"] = [
        *payload["excluded"],
        # همان شکلِ ردیف‌های رتبه‌بندی، تا مصرف‌کننده‌ی پاسخ کلیدِ
        # جاافتاده نبیند.
        *(
            {**row, "verdict": "tradable", "code": "not_published"}
            for row in unpublished
        ),
    ]
    return {"enabled": True, **payload}


def _history_state(screener: Any | None) -> dict[str, Any]:
    """وضعیت تاریخچه‌ی نقدشوندگی — تا «نداریم» با «خوب است» اشتباه نشود."""
    history = getattr(screener, "history", None)
    if history is None:
        return {"available": False, "reason": "تاریخچه‌ای به غربالگر داده نشده"}
    return {
        "available": bool(history.available),
        "reason": history.unavailable_reason,
        "db_path": str(getattr(history, "db_path", "")),
        "lookback_sessions": getattr(history, "lookback_sessions", None),
    }


class TradabilityUpdate(BaseModel):
    """ویرایش آستانه‌های غربال. هر فیلد اختیاری است."""

    enabled: bool | None = None
    min_open_interest_contracts: int | None = Field(default=None, ge=0)
    min_trades_today_count: int | None = Field(default=None, ge=0)
    max_relative_spread_pct: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    min_exit_depth_ratio: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_exit_slippage_pct: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    min_sessions_with_trades_pct: float | None = Field(
        default=None, ge=0, le=100, allow_inf_nan=False
    )
    min_history_sessions: int | None = Field(default=None, ge=1)
    min_days_to_expiry: int | None = Field(default=None, ge=0)
    max_quote_age_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


#: واحد و توضیح هر آستانه — رابط از همین می‌خواند تا واحدها در یک جا
#: بمانند و با کد واگرا نشوند.
TRADABILITY_FIELDS: tuple[dict[str, Any], ...] = (
    {"key": "min_open_interest_contracts", "label": "حداقل موقعیت باز",
     "unit": "قرارداد", "step": 10},
    {"key": "min_trades_today_count", "label": "حداقل معاملات امروز",
     "unit": "معامله", "step": 1},
    {"key": "max_relative_spread_pct", "label": "حداکثر اسپرد نسبی",
     "unit": "٪ از میانه‌ی مظنه", "step": 1},
    {"key": "min_exit_depth_ratio", "label": "حداقل عمق سمت خروج",
     "unit": "برابرِ اندازه‌ی سفارش", "step": 0.5},
    {"key": "max_exit_slippage_pct", "label": "حداکثر لغزش خروج",
     "unit": "٪ فاصله‌ی قیمتِ پرشدن از بهترین مظنه", "step": 1},
    {"key": "min_sessions_with_trades_pct", "label": "حداقل تداوم معامله",
     "unit": "٪ از جلسه‌های ثبت‌شده", "step": 5},
    {"key": "min_history_sessions", "label": "حداقل جلسه برای قضاوت",
     "unit": "جلسه (کمتر = نیازمند بررسی)", "step": 1},
    {"key": "min_days_to_expiry", "label": "حداقل فاصله تا سررسید",
     "unit": "روز", "step": 1},
    {"key": "max_quote_age_seconds", "label": "حداکثر عمر دادهٔ دریافتی",
     "unit": "ثانیه — نه زمان بازار", "step": 30},
)


@app.get("/api/tradability")
def get_tradability() -> dict[str, Any]:
    """آستانه‌های غربال، همراه واحد و هشدارِ اثبات‌نشده بودن."""
    config = section(_settings(), "tradability")
    return {
        "settings": config,
        "fields": list(TRADABILITY_FIELDS),
        "note": (
            "این آستانه‌ها اثبات‌شده نیستند؛ نقطه‌ی شروعی محافظه‌کارانه‌اند. "
            "با تاریخچه‌ی خودتان تنظیمشان کنید."
        ),
    }


@app.put("/api/tradability")
def update_tradability(update: TradabilityUpdate) -> dict[str, Any]:
    """ویرایش آستانه‌های غربال؛ از پاس بعدی اعمال می‌شود."""
    patch = {k: v for k, v in update.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")
    _patch_settings({"tradability": patch})
    return {"ok": True, "applied": patch}


# ----------------------------------------------------------------------
# رتبه‌بندی اولویت بررسی
# ----------------------------------------------------------------------
#: واحد و توضیح هر تنظیم رتبه‌بندی — رابط از همین می‌خواند.
RANKING_FIELDS: tuple[dict[str, Any], ...] = (
    {"key": "weight_round_trip_cost", "label": "وزن هزینه‌ی رفت‌وبرگشت",
     "unit": "٪ از پرمیومِ پرداختی (ورود تا خروج)", "step": 5},
    {"key": "weight_exit_capacity", "label": "وزن ظرفیت خروج",
     "unit": "عمقِ درونِ محدوده‌ی قیمتی، برابرِ سفارش", "step": 5},
    {"key": "weight_time_to_expiry", "label": "وزن فاصله تا سررسید",
     "unit": "روز تا سررسید", "step": 5},
    {"key": "weight_required_move", "label": "وزن حرکت لازم تا سر‌به‌سر",
     "unit": "٪ حرکت پایه", "step": 5},
    {"key": "weight_fee_cost", "label": "وزن سهم کارمزد",
     "unit": "٪ از سرمایه‌ی درگیر", "step": 5},
    {"key": "depth_comfort_multiple", "label": "عمقِ «راحت»",
     "unit": "برابرِ حداقلِ غربال", "step": 0.5},
    {"key": "days_to_expiry_comfort", "label": "سررسیدِ «راحت»",
     "unit": "روز", "step": 5},
    {"key": "max_required_move_pct", "label": "سقف حرکت لازم",
     "unit": "٪ حرکت پایه", "step": 5},
    {"key": "max_round_trip_cost_pct", "label": "سقف هزینه‌ی رفت‌وبرگشت",
     "unit": "٪ از پرمیومِ پرداختی", "step": 5},
    {"key": "max_fee_cost_pct", "label": "سقف سهم کارمزد",
     "unit": "٪ از سرمایه‌ی درگیر", "step": 1},
)


class RankingUpdate(BaseModel):
    """ویرایش تنظیمات رتبه‌بندی. هر فیلد اختیاری است."""

    enabled: bool | None = None
    weight_round_trip_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    weight_exit_capacity: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    weight_time_to_expiry: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    weight_required_move: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    weight_fee_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    depth_comfort_multiple: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    days_to_expiry_comfort: int | None = Field(default=None, ge=0)
    max_required_move_pct: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_round_trip_cost_pct: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    max_fee_cost_pct: float | None = Field(default=None, gt=0, allow_inf_nan=False)


@app.get("/api/ranking")
def get_ranking_settings() -> dict[str, Any]:
    """تنظیمات رتبه‌بندی، همراه واحد و هشدارِ فرض‌بودنِ وزن‌ها."""
    config = section(_settings(), "ranking")
    return {
        "settings": config,
        "fields": list(RANKING_FIELDS),
        "note": (
            "خروجی «امتیاز اولویت بررسی» است — نه احتمال برد، نه بازده مورد "
            "انتظار و نه توصیه‌ی خرید. وزن‌ها فرضِ اولیه‌اند و هیچ پژوهشی "
            "پشتشان نیست. رتبه با امتیازِ محافظه‌کارانه چیده می‌شود: مؤلفه‌ی "
            "نامعلوم صفر حساب می‌شود تا نبودِ داده کسی را بالا نبرد."
        ),
    }


@app.put("/api/ranking")
def update_ranking_settings(update: RankingUpdate) -> dict[str, Any]:
    """ویرایش تنظیمات رتبه‌بندی؛ از ارزیابیِ بعدی اعمال می‌شود."""
    patch = {k: v for k, v in update.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")
    _patch_settings({"ranking": patch})
    return {"ok": True, "applied": patch}


# ----------------------------------------------------------------------
# وضعیت بازار و نماد پایه
# ----------------------------------------------------------------------
def _regime_thresholds(settings: dict[str, Any]) -> Any:
    """آستانه‌های تشخیص وضعیت از تنظیمات؛ کلیدِ ناشناخته بی‌صدا رد می‌شود."""
    from market.regime import RegimeThresholds

    config = section(settings, "regime")
    known = {f.name for f in fields(RegimeThresholds)}
    return RegimeThresholds(**{k: v for k, v in config.items() if k in known})


def _market_regime(settings: dict[str, Any], as_of: date) -> Any | None:
    """وضعیتِ «بازار» از شاخص کل. `None` یعنی اصلاً نشد حساب کرد.

    شاخص تعدیل نمی‌خواهد (خودش سریِ سطح است)، پس وضعیتِ تعدیل صریحاً
    `not_needed` گزارش می‌شود، نه «نامعلوم».
    """
    from data.tsetmc_index_client import (
        TSE_ALL_SHARE_INS_CODE,
        TSE_ALL_SHARE_LABEL,
        TsetmcIndexClient,
    )
    from market.regime import Adjustment, PricePoint, assess_regime

    config = section(settings, "regime")
    ins_code = str(config.get("market_ins_code") or TSE_ALL_SHARE_INS_CODE)
    label = str(config.get("market_label") or TSE_ALL_SHARE_LABEL)
    history_dir = config.get("index_history_dir")
    client = TsetmcIndexClient(
        timeout=section(settings, "market_data").get("timeout", 20),
        history_dir=resolve_path(history_dir) if history_dir else None,
    )
    try:
        points = [
            PricePoint(p.date, p.close)
            for p in client.get_history(
                ins_code, days=int(config.get("history_days", 200)), label=label
            )
        ]
    except Exception as exc:
        logger.warning("تاریخچه‌ی شاخص خوانده نشد: %s", exc)
        return None

    return assess_regime(
        points,
        subject=label,
        subject_kind="market",
        as_of=as_of,
        thresholds=_regime_thresholds(settings),
        adjustment=Adjustment.NOT_NEEDED,
    )


def _underlying_regime(
    settings: dict[str, Any], context: Any, symbol: str, as_of: date
) -> Any | None:
    """وضعیتِ یک نماد پایه، روی قیمتِ **تعدیل‌شده** اگر بشود.

    سه حالتِ تعدیل از هم جدا می‌مانند: اعمال شد، رویدادی نبود، یا اصلاً
    نشد پرسید. حالتِ سوم با «رویدادی نبود» یکی نیست و در گزارش هم
    یکی نمی‌شود.
    """
    from market.corporate_actions import fetch_corporate_actions
    from market.regime import Adjustment, PricePoint, assess_regime

    config = section(settings, "regime")
    days = int(config.get("history_days", 200))
    try:
        candles = context.market_data.get_history(symbol, days)
    except Exception as exc:
        logger.warning("تاریخچه‌ی %s خوانده نشد: %s", symbol, exc)
        return None
    if not candles:
        return None

    adjustment = Adjustment.UNKNOWN
    if config.get("corporate_actions", True):
        try:
            ins_code = context.market_data.resolve_ins_code(symbol)
            log = fetch_corporate_actions(ins_code, symbol)
            if log.actions:
                candles = log.adjust_history(candles)
                adjustment = Adjustment.APPLIED
            else:
                adjustment = Adjustment.NOT_NEEDED
        except Exception as exc:
            # نشد بپرسیم ⇒ «نامعلوم»، نه «رویدادی نبود».
            logger.warning("رویدادهای شرکتی %s در دسترس نبود: %s", symbol, exc)

    return assess_regime(
        [PricePoint(c.date, c.close) for c in candles],
        subject=symbol,
        subject_kind="underlying",
        as_of=as_of,
        thresholds=_regime_thresholds(settings),
        adjustment=adjustment,
    )


def _regime_block(
    settings: dict[str, Any], context: Any, symbols: list[str], as_of: date
) -> dict[str, Any]:
    """وضعیتِ بازار و هر نمادِ خواسته‌شده، در یک بسته برای رابط."""
    config = section(settings, "regime")
    if not config.get("enabled", True):
        return {"enabled": False, "reason": "تحلیل وضعیت در تنظیمات خاموش است."}

    market = _market_regime(settings, as_of)
    underlyings: dict[str, Any] = {}
    for symbol in dict.fromkeys(symbols):  # یکتا، با حفظ ترتیب
        report = _underlying_regime(settings, context, symbol, as_of)
        if report is not None:
            underlyings[symbol] = report

    return {
        "enabled": True,
        "as_of": as_of.isoformat(),
        "market": None if market is None else market.to_dict(),
        "underlyings": {k: v.to_dict() for k, v in underlyings.items()},
        "note": (
            "وضعیتِ **فعلی** بازار و نماد پایه، جدا از هم. این تشخیص در "
            "امتیازِ رتبه‌بندی وارد نمی‌شود و پیش‌بینی تا سررسید هم نیست."
        ),
        "_reports": {"market": market, "underlyings": underlyings},
    }


@app.get("/api/regime")
async def get_regime(underlyings: str | None = None) -> dict[str, Any]:
    """وضعیتِ بازار و نمادهای پایه — بدون نیاز به پاس رصد.

    `underlyings` فهرستِ نمادها با ویرگول؛ خالی یعنی همان نمادهای تحت
    رصد در تنظیمات.
    """
    settings = _settings()
    symbols = (
        [s.strip() for s in underlyings.split(",") if s.strip()]
        if underlyings
        else list(section(settings, "market_data").get("symbols") or [])
    )

    def _work() -> dict[str, Any]:
        context = create_app(settings, dry_run=True, as_json=False)
        try:
            block = _regime_block(settings, context, symbols, date.today())
        finally:
            context.close()
        block.pop("_reports", None)
        return block

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.exception("تحلیل وضعیت ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/ranking/demo")
def get_ranking_demo(quantity: int = 10) -> dict[str, Any]:
    """فرصت‌های **مسیر آزمایشی** — همان دیتاستی که می‌شود رویش معامله کرد.

    ⚠️ این داده بازار نیست و هیچ‌وقت با دادهٔ واقعی مخلوط نمی‌شود: مسیرش
    جداست، `demo`/`sandbox` را `true` برمی‌گرداند و رابط بالای فهرست
    برچسب می‌زند. لازم است چون تا وقتی تاریخچه‌ی recorder ساخته نشده،
    خروجیِ واقعی به‌درستی خالی است و کاربر حتی یک بار هم جریانِ کامل را
    نمی‌بیند.

    عمداً **همان** دیتاستِ `market/sandbox.py` است که بررسیِ پیش‌از‌ورود
    و کارگزارِ کاغذیِ آزمایشی هم از آن می‌خوانند: قیمتی که اینجا می‌بینید
    همان قیمتی است که آنجا پر می‌شود.
    """
    from datetime import timedelta

    from market import sandbox as sb
    from market.opportunity_ranking import rank_opportunities
    from market.tradability import Thresholds, evaluate
    from risk.fees import FeeSchedule

    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity باید مثبت باشد.")

    now = datetime.now()
    thresholds = Thresholds()
    healthy = sb.history()
    fees = FeeSchedule(
        buy_rate=sb.SANDBOX_FEE_RATE, sell_rate=sb.SANDBOX_FEE_RATE, declared=True
    )

    candidates: list[dict[str, Any]] = []
    for sample in sb.SAMPLES:
        observation = sb.observation(
            sample,
            quantity,
            now=now,
            max_exit_slippage_pct=thresholds.max_exit_slippage_pct,
        )
        entry = observation.entry_fill_price
        candidates.append({
            "report": evaluate(observation, healthy, thresholds),
            "symbol": sample.symbol,
            "strategy": "نمونه‌ی آزمایشی",
            "side": sample.side,
            "quantity": quantity,
            "leg_group_id": sample.leg_group_id,
            "option_type": sample.option_type,
            "strike": sample.strike,
            "contract_size": sb.SANDBOX_CONTRACT_SIZE,
            "underlying_price": sb.SANDBOX_SPOT,
            # حد ضررِ ۳۵٪ روی قیمتِ اجراییِ همین دفتر، تا عددها با هم بخوانند
            "stop_loss_price": round(entry * 0.65, 1) if entry else None,
            "fees": fees,
        })

    result = rank_opportunities(
        candidates=candidates,
        thresholds=thresholds,
        weights=_ranking_weights(_settings()),
        evaluated_at=now - timedelta(seconds=1),
        demo=True,
    )
    payload = result.to_dict()
    # وضعیت و تناسب در تمرین هم دیده می‌شوند — با همان منطقِ مسیر واقعی،
    # روی سریِ قیمتِ نمونه.
    regime = sb.regime_block(_regime_thresholds(_settings()))
    from market.opportunity_fit import assess_fit

    reports = regime.pop("_reports")
    for row in payload["ranked"]:
        sample = sb.BY_SYMBOL.get(row["symbol"])
        if sample is None:
            continue
        row["underlying"] = sb.SANDBOX_UNDERLYING
        row["fit"] = assess_fit(
            option_type=sample.option_type,
            side=row["side"],
            underlying=reports["underlyings"].get(sb.SANDBOX_UNDERLYING),
            market=reports["market"],
            days_to_expiry=sb.SANDBOX_DAYS_TO_EXPIRY,
        ).to_dict()
    payload["regime"] = regime
    notes = {s.symbol: s.note for s in sb.SAMPLES}
    for row in payload["ranked"]:
        row["sample_note"] = notes.get(row["symbol"])
    for row in payload["excluded"]:
        row["sample_note"] = notes.get(row["symbol"])
    return {
        "enabled": True,
        "sandbox": True,
        "sandbox_label": sb.SANDBOX_LABEL,
        "underlying": sb.SANDBOX_UNDERLYING,
        "spot_price": sb.SANDBOX_SPOT,
        "quantity": quantity,
        **payload,
    }


# ----------------------------------------------------------------------
# استراتژی‌ها
# ----------------------------------------------------------------------
@app.get("/api/strategies")
def get_strategies() -> dict[str, Any]:
    """استراتژی‌های ثبت‌شده، با پارامترهای پیش‌فرض و مقدار فعلی."""
    settings = _settings()
    configured = section(settings, "strategies")

    result = []
    for name in available_strategies():
        cls = get_strategy_class(name)
        defaults = cls.default_params() if cls else {}
        entry = dict(configured.get(name) or {})
        # `create_strategies` فقط `entry["params"]` را می‌خواند؛ هر کلید
        # دیگری در این سطح را استراتژی نمی‌بیند. پس فقط همان را بخوان،
        # وگرنه داشبورد مقداری را نشان می‌دهد که هیچ اثری ندارد.
        stored_params = entry.get("params") or {}
        doc = ""
        if cls and cls.__doc__:
            doc = cls.__doc__.strip().split("\n")[0]
        result.append(
            {
                "name": name,
                "enabled": entry.get("enabled", True),
                "doc": doc,
                "defaults": defaults,
                "params": {**defaults, **stored_params},
            }
        )
    return {"strategies": result}


@app.put("/api/strategies/{name}")
def update_strategy(name: str, update: StrategyUpdate) -> dict[str, Any]:
    """فعال/غیرفعال کردن یا تغییر پارامترهای یک استراتژی."""
    cls = get_strategy_class(name)
    if cls is None:
        raise HTTPException(status_code=404, detail=f"استراتژی «{name}» وجود ندارد.")

    patch: dict[str, Any] = {}
    if update.enabled is not None:
        patch["enabled"] = update.enabled

    if update.params:
        known = set(cls.default_params())
        unknown = set(update.params) - known
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"پارامتر ناشناخته: {', '.join(sorted(unknown))}",
            )
        # زیر کلید `params` نوشته می‌شود، چون `create_strategies` فقط
        # همان را به استراتژی پاس می‌دهد. نوشتن مسطح، بی‌صدا بی‌اثر است.
        patch["params"] = dict(update.params)

    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")

    _patch_settings({"strategies": {name: patch}})
    return {"ok": True, "strategy": name, "applied": patch}


# ----------------------------------------------------------------------
# نمادها
# ----------------------------------------------------------------------
@app.get("/api/symbols")
def get_symbols() -> dict[str, Any]:
    """نمادهای تحت رصد، و نمادهایی که واقعاً در بازار آپشن دارند."""
    settings = _settings()
    watched = section(settings, "market_data").get("symbols") or []

    available: list[str] = []
    error: str | None = None
    try:
        # کلاینت را خودمان نمی‌سازیم؛ از همان wiring در bootstrap استفاده
        # می‌کنیم تا اگر امضای سازنده عوض شد اینجا نشکند و تنظیمات (کش،
        # fixture، گیت‌های کیفیت) هم همان چیزی باشد که CLI استفاده می‌کند.
        context = create_app(settings, dry_run=False, as_json=False)
        try:
            chain = context.option_chain
            getter = getattr(chain, "available_underlyings", None)
            if getter is None:
                error = f"منبع زنجیره فعلی ({type(chain).__name__}) لیست نمادها را نمی‌دهد."
            else:
                available = sorted(getter())
        finally:
            context.close()
    except Exception as exc:
        error = str(exc)
        logger.warning("دریافت نمادهای بازار ناموفق بود: %s", exc)

    return {"watched": watched, "available": available, "error": error}


@app.put("/api/symbols")
def update_symbols(update: SymbolsUpdate) -> dict[str, Any]:
    """جایگزینی لیست نمادهای تحت رصد."""
    cleaned = [s.strip() for s in update.symbols if s.strip()]
    if not cleaned:
        raise HTTPException(status_code=400, detail="لیست نمادها خالی است.")
    _patch_settings({"market_data": {"symbols": cleaned}})
    return {"ok": True, "symbols": cleaned}


# ----------------------------------------------------------------------
# ریسک و وضعیت
# ----------------------------------------------------------------------
@app.get("/api/risk")
def get_risk() -> dict[str, Any]:
    return section(_settings(), "risk")


@app.put("/api/risk")
def update_risk(update: RiskUpdate) -> dict[str, Any]:
    patch = {k: v for k, v in update.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")
    _patch_settings({"risk": patch})
    return {"ok": True, "applied": patch}


def _attach_depth(structures: dict[str, list[dict[str, Any]]]) -> None:
    """عمق مظنه‌ی هر پایه را به نقشه‌ی سفارش اضافه می‌کند (درجا).

    این جواب سؤالی است که مظنه‌ی تک‌سطحی نمی‌تواند بدهد: «اگر این حجم را
    بزنم، واقعاً چقدر پر می‌شود و با چه لغزشی؟» یک ساختار که روی کاغذ
    سودده است ولی پایه‌اش فقط ۱ قرارداد عمق دارد، اجرا نمی‌شود.

    خطا اینجا کشنده نیست: عمق یک افزونه است، و نبودش نباید کل اسکن را
    بی‌نتیجه کند. پایه‌ی بدون عمق فقط `depth: null` می‌گیرد.
    """
    from data.order_book import OrderBookClient

    client = OrderBookClient()
    for found in structures.values():
        for structure in found:
            for leg in structure.get("legs") or []:
                ins_code = leg.get("ins_code")
                quantity = int(leg.get("quantity") or 0)
                if not ins_code or quantity <= 0:
                    leg["depth"] = None
                    continue

                book = client.try_get_order_book(ins_code, leg.get("symbol", ""))
                if book is None:
                    leg["depth"] = None
                    continue

                side = "buy" if leg.get("action") == "BUY" else "sell"
                avg, filled = book.fill_price(side, quantity)
                leg["depth"] = {
                    "levels": len(book.asks if side == "buy" else book.bids),
                    "available": book.depth(side),
                    "fill_price": avg,
                    "filled_quantity": filled,
                    "fully_fillable": filled >= quantity,
                    "slippage": book.slippage(side, quantity),
                }


@app.get("/api/structures")
async def scan_structures(
    underlying: str,
    kind: str = "all",
    limit: int = 10,
    rank_by: str = "roi",
    min_open_interest: int = 50,
    with_depth: bool = False,
) -> dict[str, Any]:
    """اسکن ساختارهای چندپایه روی زنجیره‌ی **واقعی**.

    `with_depth=true` عمق مظنه‌ی هر پایه را هم می‌گیرد و می‌گوید سفارش
    واقعاً به چه قیمتی پر می‌شود. عمداً پیش‌فرض خاموش است: هر پایه یک
    درخواست جداگانه به TSETMC می‌خورد.

    ⚠️ خروجی فقط تحلیل و نقشه‌ی سفارش است؛ هیچ سفارشی ثبت نمی‌شود.
    """
    from strategies.scanner import (
        RANK_KEYS,
        SCAN_KINDS,
        ScanFilters,
        StrategyScanner,
        rank_strategies,
    )

    if rank_by not in RANK_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"معیار ناشناخته: «{rank_by}». موجود: {', '.join(sorted(RANK_KEYS))}",
        )

    settings = _settings()

    def _work() -> dict[str, Any]:
        context = create_app(settings, dry_run=True, as_json=False)
        try:
            chain = context.option_chain.get_chain(underlying)
        finally:
            context.close()

        scanner = StrategyScanner(
            ScanFilters(min_open_interest=min_open_interest)
        )
        # `scan_all` تنها منبع حقیقتِ فهرست ساختارها است. نگه‌داشتن یک
        # دیکشنری موازی اینجا یعنی اسکنر تازه اضافه می‌شود ولی داشبورد
        # هرگز نشانش نمی‌دهد — و هیچ تستی هم متوجه نمی‌شود.
        scans = {
            name: getattr(scanner, f"scan_{name}")
            for name in SCAN_KINDS
        }
        wanted = scans if kind == "all" else {kind: scans.get(kind)}
        if None in wanted.values():
            raise HTTPException(
                status_code=400,
                detail=f"ساختار ناشناخته: «{kind}». موجود: {', '.join(scans)}, all",
            )

        result = {}
        for name, fn in wanted.items():
            found = rank_strategies(fn(chain, limit=limit * 3), rank_by)[:limit]
            result[name] = [s.to_dict() for s in found]

        if with_depth:
            _attach_depth(result)

        return {
            "underlying": underlying,
            "spot_price": chain.spot_price,
            "ranked_by": rank_by,
            "with_depth": with_depth,
            "structures": result,
        }

    try:
        return await asyncio.to_thread(_work)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("اسکن ساختارها ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/structures/rank-keys")
def get_rank_keys() -> dict[str, Any]:
    """معیارهای قابل استفاده برای مرتب‌سازی."""
    from strategies.scanner import RANK_KEYS

    return {
        "keys": [
            {"key": k, "bigger_is_better": v} for k, v in sorted(RANK_KEYS.items())
        ]
    }


@app.get("/api/iv-surface")
async def get_iv_surface(underlying: str) -> dict[str, Any]:
    """سطح IV یک نماد: سطح ATM، اسکیو، ساختار زمانی، و رتبه‌ی تاریخی.

    اسکیو مثبت یعنی پوت‌ها گران‌ترند — حالت عادی بازار سهام. ساختار
    زمانیِ **نزولی** یعنی نگرانی کوتاه‌مدت، که اسپرد تقویمی را جذاب
    می‌کند.
    """

    def _work() -> dict[str, Any]:
        from pricing.iv_surface import IVSurface

        settings = _settings()
        context = create_app(settings, dry_run=True)
        try:
            built = context.generator.build_context(underlying)
            surface = IVSurface.from_chain(
                built.chain, built.implied_vol, today=built.today()
            )
            data = surface.to_dict()
            rank = built.iv_rank
            # `None` یعنی تاریخچه کافی نیست — نه «متوسط»
            data["iv_rank"] = (
                None
                if rank is None or not rank.is_known
                else {
                    "current": rank.current,
                    "percentile": round(rank.percentile, 1),
                    "rank": round(rank.rank, 1),
                    "low": rank.low,
                    "high": rank.high,
                    "samples": rank.samples,
                }
            )
            data["history_samples"] = (
                context.generator.iv_history.sample_count(underlying)
                if context.generator.iv_history is not None
                else 0
            )
            return data
        finally:
            context.close()

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.exception("ساخت سطح IV ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/structures/kinds")
def get_structure_kinds() -> dict[str, Any]:
    """ساختارهای قابل اسکن، با برچسب فارسی.

    داشبورد فهرستش را از اینجا می‌گیرد تا با اسکنرهای واقعی هم‌گام بماند
    و یک ساختار تازه بی‌صدا از UI جا نماند.
    """
    from strategies.scanner import SCAN_KINDS

    return {"kinds": [{"key": k, "label": v} for k, v in SCAN_KINDS.items()]}


@app.get("/api/report")
def get_report(days: int | None = None) -> dict[str, Any]:
    """گزارش عملکرد سیگنال‌ها.

    `days=None` یعنی کل تاریخچه. `win_rate` وقتی هیچ سیگنالی نتیجه
    نگرفته `null` است، نه صفر — این دو یکی نیستند.
    """
    from storage.reporting import SignalReporter

    settings = _settings()
    storage = section(settings, "storage")
    path = resolve_path(storage.get("sqlite_path", "var/signals.db"))

    with SignalReporter(path) as reporter:
        return {
            "summary": reporter.summary(days),
            "by_strategy": [
                {
                    "strategy": s.strategy,
                    "total": s.total,
                    "wins": s.wins,
                    "losses": s.losses,
                    "pending": s.pending,
                    "win_rate": s.win_rate,
                    "avg_pnl_pct": s.avg_pnl_pct,
                    "best_pnl_pct": s.best_pnl_pct,
                    "worst_pnl_pct": s.worst_pnl_pct,
                }
                for s in reporter.by_strategy(days)
            ],
            "by_underlying": reporter.by_underlying(days),
            "daily": reporter.daily_counts(days or 30),
            "recent": reporter.recent(limit=50, days=days),
            # معیارهای حرفه‌ای روی نتیجه‌ی **واقعی**؛ همان تابعی که
            # بک‌تست هم استفاده می‌کند، تا دو عدد مختلف نگویند.
            "metrics": reporter.performance_metrics(days),
            "equity_curve": reporter.equity_curve(days),
            # تأیید دریافت: «نرخ اجرا» در کنار «نرخ برد». سیگنالی که
            # کاربر ندیده و ضرر داده، شکستِ استراتژی نیست.
            "acknowledgement": _ack_stats(path, days),
        }


def _ack_stats(db_path, days: int | None) -> dict[str, Any] | None:
    """آمار تأیید دریافت، یا `None` اگر در دسترس نباشد.

    نبودش نباید کل گزارش را بی‌نتیجه کند — بقیه‌ی اعداد مستقل‌اند.
    """
    try:
        from storage.acknowledgement import AckStore

        with AckStore(db_path) as store:
            return store.stats(days)
    except Exception as exc:  # آمار تأیید نباید گزارش را بخواباند
        logger.warning("آمار تأیید دریافت خوانده نشد: %s", exc)
        return None


@app.post("/api/report/evaluate")
async def evaluate_pending() -> dict[str, Any]:
    """نتیجه‌ی سیگنال‌های در انتظار را با **قیمت واقعی** بازار می‌سنجد.

    قیمت از TSETMC خوانده می‌شود. اگر نمادی قیمت نداشته باشد، رد می‌شود
    و نتیجه‌ی جعلی ثبت نمی‌شود.
    """
    from storage.reporting import SignalReporter, evaluate_signal

    settings = _settings()
    storage = section(settings, "storage")
    path = resolve_path(storage.get("sqlite_path", "var/signals.db"))

    def _work() -> dict[str, Any]:
        context = create_app(settings, dry_run=True, as_json=False)
        evaluated = skipped = 0
        try:
            with SignalReporter(path) as reporter:
                pending = reporter.pending_signals()
                for row in pending:
                    payload = json.loads(row["payload"])
                    underlying = payload.get("underlying")
                    symbol = payload.get("symbol")
                    if not underlying or not symbol:
                        skipped += 1
                        continue
                    try:
                        chain = context.option_chain.get_chain(underlying)
                        match = next(
                            (c for c in chain.contracts if c.symbol == symbol), None
                        )
                        price = match.last_price or match.bid if match else None
                    except Exception:
                        price = None

                    if not price:
                        skipped += 1
                        continue

                    outcome, pnl, hit_t, hit_s = evaluate_signal(payload, float(price))
                    reporter.record_outcome(
                        row["signal_id"], float(price), pnl, outcome, hit_t, hit_s
                    )
                    evaluated += 1
        finally:
            context.close()
        return {"evaluated": evaluated, "skipped": skipped}

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.exception("ارزیابی سیگنال‌ها ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/report/export")
def export_report(days: int | None = None) -> FileResponse:
    """خروجی CSV برای اکسل."""
    from storage.reporting import SignalReporter

    settings = _settings()
    storage = section(settings, "storage")
    path = resolve_path(storage.get("sqlite_path", "var/signals.db"))
    out = resolve_path("var/signal_report.csv")

    with SignalReporter(path) as reporter:
        count = reporter.export_csv(out, days)
    logger.info("گزارش CSV با %s ردیف ساخته شد.", count)

    return FileResponse(
        out, media_type="text/csv", filename=f"garnet-signals-{date.today()}.csv"
    )


class DataSourceUpdate(BaseModel):
    market_data_provider: str | None = None
    option_chain_provider: str | None = None
    enrich_with_broker: bool | None = None
    enrich_limit: int | None = None


class BrokerUpdate(BaseModel):
    enabled: bool | None = None
    token: str | None = None
    session_file: str | None = None


class PaperTradingUpdate(BaseModel):
    enabled: bool | None = None
    initial_balance: float | None = None
    #: نرخ‌ها به‌علاوه‌ی پرچم بولیِ `declared`، پس مقدارها هم‌جنس نیستند.
    fees: dict[str, float | bool] | None = None


class PaperOrderRequest(BaseModel):
    #: نماد دستی؛ اگر `signal_id` داده شده باشد نادیده گرفته می‌شود
    symbol: str | None = None
    #: اجرای یک سیگنال موجود با یک کلیک؛ symbol/side از خودِ سیگنال می‌آید
    signal_id: str | None = None
    side: str | None = None
    quantity: int | None = None
    #: بلیتِ بررسیِ پیش‌از‌ورود (`POST /api/trade-check`).
    #:
    #: برای ورودی که از دلِ یک **فرصت/سیگنال** می‌آید اجباری است: بدون
    #: آن، معامله با ارزیابیِ قدیمی تأیید می‌شد. فرمِ دستیِ زنجیره بلیت
    #: نمی‌خواهد، چون اصلاً ارزیابیِ قبلی‌ای پشتش نیست.
    ticket: str | None = None
    #: مسیر آزمایشی: حساب، پایگاه و مظنه‌های جدا از دادهٔ واقعی.
    sandbox: bool = False


class TradeCheckRequest(BaseModel):
    """بررسیِ «همین حالا و برای همین تعداد، ورود ممکن است؟»"""

    symbol: str | None = None
    signal_id: str | None = None
    quantity: int = Field(gt=0)
    side: str | None = None
    sandbox: bool = False
    #: امتیازی که کاربر روی کارت دیده بود — فقط برای مقایسه و هشدار.
    previous_score: float | None = None


class PaperSettleRequest(BaseModel):
    """ثبتِ **فرضِ کاربر** برای تعیین تکلیف یک موقعیتِ سررسیدشده.

    این تسویه‌ی رسمی نیست: قواعد اعمال و تسویه‌ی بورس تهران پیاده نشده‌اند
    و هزینه‌ی خودِ تسویه هم مدل نشده است.
    """

    symbol: str
    #: پرمیوم تسویه **به ازای هر واحد** — همان مبنای بقیه‌ی قیمت‌های
    #: پروژه. ارزش کل = قیمت × تعداد × اندازه‌ی قرارداد.
    #:
    #: صفر مجاز است (انقضای بی‌ارزش) ولی پیش‌فرض ندارد: حدس زدنش همان
    #: کاری است که این تغییر جلویش را گرفت. `allow_inf_nan=False` چون
    #: `inf` نقد را بی‌نهایت می‌کند و `nan` هر کنترلی را بی‌صدا رد.
    settlement_price: float = Field(ge=0, allow_inf_nan=False)
    #: مسیر آزمایشی — حساب و پایگاهِ جدا.
    sandbox: bool = False


@app.get("/api/datasource")
def get_datasource() -> dict[str, Any]:
    """منبع داده‌ی فعلی و گزینه‌های موجود."""
    import bootstrap

    settings = _settings()
    market = section(settings, "market_data")
    chain = section(settings, "option_chain")
    return {
        "market_data_provider": market.get("provider"),
        "option_chain_provider": chain.get("provider"),
        "enrich_with_broker": bool(chain.get("enrich_with_broker")),
        "enrich_limit": chain.get("enrich_limit", 20),
        "available_market_data": sorted(bootstrap.MARKET_DATA_PROVIDERS),
        "available_option_chain": sorted(bootstrap.OPTION_CHAIN_PROVIDERS),
        "broker_enabled": bool(section(settings, "broker").get("enabled")),
    }


@app.put("/api/datasource")
def update_datasource(update: DataSourceUpdate) -> dict[str, Any]:
    """تغییر منبع داده از پنل.

    ⚠️ ایزی‌تریدر گزینه‌ی زنجیره نیست: مشخصات قرارداد را فقط تک‌به‌تک
    می‌دهد (~۱۳۸۶ درخواست برای کل بازار). به‌جایش `enrich_with_broker`
    را روشن کنید تا روی زنجیره‌ی TSETMC سوار شود.
    """
    import bootstrap

    market_patch: dict[str, Any] = {}
    chain_patch: dict[str, Any] = {}

    if update.market_data_provider is not None:
        name = update.market_data_provider.strip().lower()
        if name not in bootstrap.MARKET_DATA_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"provider ناشناخته: «{name}». "
                f"موجود: {', '.join(sorted(bootstrap.MARKET_DATA_PROVIDERS))}",
            )
        market_patch["provider"] = name

    if update.option_chain_provider is not None:
        name = update.option_chain_provider.strip().lower()
        if name not in bootstrap.OPTION_CHAIN_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"provider ناشناخته: «{name}». "
                f"موجود: {', '.join(sorted(bootstrap.OPTION_CHAIN_PROVIDERS))}",
            )
        chain_patch["provider"] = name

    if update.enrich_with_broker is not None:
        if update.enrich_with_broker and not section(_settings(), "broker").get("enabled"):
            raise HTTPException(
                status_code=400,
                detail="برای غنی‌سازی، اول اتصال کارگزاری را در تب «حساب» فعال کنید.",
            )
        chain_patch["enrich_with_broker"] = update.enrich_with_broker

    if update.enrich_limit is not None:
        if not 0 <= update.enrich_limit <= 200:
            raise HTTPException(
                status_code=400,
                detail="enrich_limit باید بین ۰ تا ۲۰۰ باشد؛ هر واحد یک درخواست شبکه است.",
            )
        chain_patch["enrich_limit"] = update.enrich_limit

    if not market_patch and not chain_patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")

    patch: dict[str, Any] = {}
    if market_patch:
        patch["market_data"] = market_patch
    if chain_patch:
        patch["option_chain"] = chain_patch
    _patch_settings(patch)
    return {"ok": True, "applied": patch}


@app.put("/api/broker")
def update_broker(update: BrokerUpdate) -> dict[str, Any]:
    """تنظیم اتصال حساب کارگزاری از خود پنل.

    ⚠️ توکن در `settings.yaml` ذخیره می‌شود که در `.gitignore` است. عمر
    کوتاهی دارد و باید هر چند ساعت تازه شود.
    """
    patch: dict[str, Any] = {}
    if update.enabled is not None:
        patch["enabled"] = update.enabled
    if update.token is not None:
        patch["token"] = update.token.strip()
    if update.session_file is not None:
        patch["session_file"] = update.session_file.strip()

    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")

    _patch_settings({"broker": patch})
    # توکن هرگز برنمی‌گردد
    return {"ok": True, "applied": sorted(k for k in patch if k != "token")}


@app.get("/api/account")
def get_account() -> dict[str, Any]:
    """پوزیشن‌های واقعی حساب کارگزاری — **فقط خواندن**.

    پیش‌فرض خاموش است. اگر روشن نباشد یا سشن منقضی شده باشد، به‌جای
    خطای خام، وضعیت روشن با راهنمای رفع برمی‌گردد تا داشبورد نشکند.
    """
    settings = _settings()
    broker_cfg = section(settings, "broker")

    if not broker_cfg.get("enabled"):
        return {
            "enabled": False,
            "reason": "اتصال به حساب کارگزاری خاموش است. "
            "از همین صفحه «فعال باشد» را تیک بزنید و توکن را وارد کنید.",
            "positions": [],
        }

    try:
        from brokers.emofid import EmofidAccountClient

        common = {
            "base_url": broker_cfg.get("base_url", "https://api-mts.orbis.easytrader.ir"),
            "timeout": broker_cfg.get("timeout", 15),
            "retries": broker_cfg.get("retries", 3),
        }
        # توکن صریح مقدم است: API آپشن هدر authorization می‌خواهد و فایل
        # سشن (که فقط کوکی دارد) برای آن کافی نیست.
        token = (broker_cfg.get("token") or "").strip()
        if token:
            client = EmofidAccountClient(token=token, **common)
        else:
            client = EmofidAccountClient.from_session_file(
                resolve_path(broker_cfg.get("session_file", "var/emofid/session.json")),
                **common,
            )
        positions = client.get_positions()
        # موجودی جدا try می‌شود: اگر این endpoint در دسترس نباشد،
        # پوزیشن‌ها که خوانده شده‌اند نباید با آن از دست بروند.
        try:
            balance = client.get_balance()
        except Exception as exc:  # موجودی نباید پوزیشن‌ها را ببرد
            logger.warning("خواندن موجودی حساب ناموفق بود: %s", exc)
            balance = None
    except Exception as exc:
        logger.warning("خواندن حساب کارگزاری ناموفق بود: %s", exc)
        return {"enabled": True, "reason": str(exc), "positions": []}

    return {
        "enabled": True,
        "reason": None,
        "balance": (
            None
            if balance is None
            else {
                "equity": balance.equity,
                "cash_t0": balance.cash_t0,
                "cash_t1": balance.cash_t1,
                "cash_t2": balance.cash_t2,
                "buy_power_t0": balance.buy_power_t0,
                "buy_power_t2": balance.buy_power_t2,
                "blocked": balance.blocked,
                "margin_blocked": balance.margin_blocked,
                "credit": balance.credit,
            }
        ),
        "use_broker_equity": bool(
            section(settings, "risk").get("use_broker_equity", False)
        ),
        "positions": [
            {
                "symbol_name": p.symbol_name,
                "symbol_isin": p.symbol_isin,
                "quantity": p.quantity,
                "is_long": p.is_long,
                "strike_price": p.strike_price,
                "total_margin": p.total_margin,
                "buy_average_price": p.buy_average_price,
                "sell_average_price": p.sell_average_price,
                "closed_pnl": p.closed_pnl,
                "open_buy_quantity": p.open_buy_quantity,
                "open_sell_quantity": p.open_sell_quantity,
                "cash_settlement_date": (
                    p.cash_settlement_date.isoformat() if p.cash_settlement_date else None
                ),
            }
            for p in positions
        ],
    }


@app.get("/api/paper-trading/settings")
def get_paper_trading_settings() -> dict[str, Any]:
    """تنظیمات فعلی معاملات کاغذی."""
    return section(_settings(), "paper_trading")


@app.put("/api/paper-trading/settings")
def update_paper_trading_settings(update: PaperTradingUpdate) -> dict[str, Any]:
    """ویرایش تنظیمات معاملات کاغذی از پنل."""
    patch: dict[str, Any] = {}
    if update.enabled is not None:
        patch["enabled"] = update.enabled
    if update.initial_balance is not None:
        if update.initial_balance <= 0:
            raise HTTPException(status_code=400, detail="موجودی اولیه باید مثبت باشد.")
        patch["initial_balance"] = update.initial_balance
    if update.fees is not None:
        patch["fees"] = update.fees

    if not patch:
        raise HTTPException(status_code=400, detail="هیچ مقداری برای تغییر داده نشد.")

    _patch_settings({"paper_trading": patch})
    return {"ok": True, "applied": patch}


@app.get("/api/paper-trading/chain")
async def get_paper_trading_chain(underlying: str) -> dict[str, Any]:
    """زنجیره‌ی اختیار **واقعی** یک نماد پایه — برای پرکردن dropdown نماد آپشن.

    فرم سفارش دستی به‌جای تایپ آزاد نماد، اول نماد پایه را از کاربر
    می‌گیرد و بعد این لیست را برای انتخاب دقیق قرارداد نشان می‌دهد.
    """
    settings = _settings()

    def _work() -> dict[str, Any]:
        context = create_app(settings, dry_run=True, as_json=False)
        try:
            chain = context.option_chain.get_chain(underlying)
        finally:
            context.close()
        return {
            "underlying": underlying,
            "spot_price": chain.spot_price,
            "contracts": [
                {
                    "symbol": c.symbol,
                    "option_type": c.option_type,
                    "strike": c.strike,
                    "expiry": c.expiry.isoformat(),
                }
                for c in sorted(chain.contracts, key=lambda c: (c.expiry, c.strike, c.option_type))
            ],
        }

    try:
        return await asyncio.to_thread(_work)
    except ValueError as exc:
        # نماد پایه نامعتبر — خطای کاربر، نه خرابی سرور
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("خواندن زنجیره اختیار ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ----------------------------------------------------------------------
# بررسی پیش از ورود — و بلیتی که تأیید روی آن بسته می‌شود
# ----------------------------------------------------------------------
#: بلیت‌های صادرشده. در حافظه‌اند و با ری‌استارتِ سرور پاک می‌شوند — عمدی:
#: عمرشان کمتر از دو دقیقه است و ماندگاری‌شان هیچ‌چیز را نجات نمی‌دهد.
_TRADE_TICKETS: dict[str, dict[str, Any]] = {}

#: عمرِ بلیت. کوتاه است چون کارش همین است: تأییدی که با دادهٔ کهنه
#: انجام شود، تأییدِ چیزِ دیگری است.
DEFAULT_TICKET_TTL_SECONDS = 90.0
#: قیمتِ ورود تا این درصد بتواند تکان بخورد و بلیت معتبر بماند.
DEFAULT_TICKET_PRICE_TOLERANCE_PCT = 0.5


def _ticket_settings(settings: dict[str, Any]) -> tuple[float, float]:
    config = section(settings, "paper_trading")
    return (
        float(config.get("ticket_ttl_seconds", DEFAULT_TICKET_TTL_SECONDS)),
        float(
            config.get(
                "ticket_price_tolerance_pct", DEFAULT_TICKET_PRICE_TOLERANCE_PCT
            )
        ),
    )


def _issue_ticket(check: Any, sandbox: bool, ttl_seconds: float) -> dict[str, Any]:
    """بلیتِ تأیید برای همین بررسی. بدونش سفارشِ فرصت ثبت نمی‌شود."""
    import uuid
    from datetime import timedelta

    now = datetime.now()
    ticket_id = str(uuid.uuid4())
    # بلیت‌های منقضی همین‌جا پاک می‌شوند؛ صفِ جدایی لازم نیست.
    for key, row in list(_TRADE_TICKETS.items()):
        if row["expires_at"] < now:
            _TRADE_TICKETS.pop(key, None)
    _TRADE_TICKETS[ticket_id] = {
        "symbol": check.symbol,
        "side": check.side,
        "quantity": check.quantity,
        "sandbox": sandbox,
        "fingerprint": check.fingerprint,
        "entry_price": check.entry_price,
        "signal_id": check.record.signal_id,
        "issued_at": now,
        "expires_at": now + timedelta(seconds=ttl_seconds),
    }
    return {
        "id": ticket_id,
        "ttl_seconds": ttl_seconds,
        "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(
            timespec="seconds"
        ),
    }


def _claim_ticket(
    ticket_id: str, symbol: str, side: str, quantity: int, sandbox: bool
) -> dict[str, Any]:
    """بلیت را بررسی و **مصرف** می‌کند. هر ناسازگاری یعنی رد.

    مصرفِ یک‌باره عمدی است: هر تأیید به یک بررسیِ خودش گره می‌خورد، پس
    یک بلیت نمی‌تواند پشتِ سرِ هم چند سفارش را امضا کند.
    """
    row = _TRADE_TICKETS.pop(ticket_id, None)
    if row is None:
        raise HTTPException(
            status_code=409,
            detail="بلیتِ بررسی پیدا نشد یا قبلاً مصرف شده؛ دوباره «بررسی با دادهٔ تازه» را بزنید.",
        )
    if row["expires_at"] < datetime.now():
        raise HTTPException(
            status_code=409,
            detail="بلیتِ بررسی منقضی شده است؛ دادهٔ آن لحظه دیگر تازه نیست. دوباره بررسی کنید.",
        )
    if row["sandbox"] != sandbox:
        raise HTTPException(
            status_code=409,
            detail=(
                "بلیتِ مسیرِ دیگری است؛ بررسی و ثبت باید هر دو در یک مسیر "
                "(واقعی یا آزمایشی) باشند."
            ),
        )
    if row["symbol"] != symbol or row["side"] != side.lower():
        raise HTTPException(
            status_code=409,
            detail=f"بلیت برای {row['symbol']}/{row['side']} صادر شده، نه {symbol}/{side}.",
        )
    if row["quantity"] != quantity:
        raise HTTPException(
            status_code=409,
            detail=(
                f"تعداد عوض شده است: بررسی برای {row['quantity']} قرارداد بود و "
                f"سفارش برای {quantity}. با همین تعداد دوباره بررسی کنید."
            ),
        )
    return row


def _reject_if_price_moved(
    ticket_price: float | None, fresh_price: float | None, tolerance_pct: float
) -> None:
    """اگر قیمتِ اجراییِ ورود از زمانِ بررسی جابه‌جا شده، تأیید باطل است.

    بلیت عمرِ کوتاه دارد، ولی در همان چند ثانیه هم دفتر می‌تواند عوض
    شود. آستانه از تنظیمات می‌آید تا کاربر خودش سخت‌گیری‌اش را انتخاب
    کند؛ صفر یعنی «هر تکانی یعنی بررسی دوباره».
    """
    if ticket_price is None or fresh_price is None:
        return
    if ticket_price <= 0:
        return
    moved = abs(fresh_price - ticket_price) / ticket_price * 100.0
    if moved > tolerance_pct:
        raise HTTPException(
            status_code=409,
            detail=(
                f"قیمتِ اجراییِ ورود از زمان بررسی {moved:,.2f}٪ جابه‌جا شده "
                f"({ticket_price:,.0f} ← {fresh_price:,.0f}) و از آستانه‌ی "
                f"{tolerance_pct:,.2f}٪ گذشته است. دوباره بررسی کنید."
            ),
        )


def _signal_by_id(settings: dict[str, Any], signal_id: str) -> Signal:
    with _signal_log(settings) as log:
        match = next(
            (s for s in log.all_signals(limit=None) if s.signal_id == signal_id), None
        )
    if match is None:
        raise HTTPException(status_code=404, detail=f"سیگنال {signal_id} یافت نشد.")
    return match


def _run_entry_check(
    settings: dict[str, Any],
    *,
    symbol: str | None,
    signal_id: str | None,
    side: str | None,
    quantity: int,
    sandbox: bool,
    previous_score: float | None = None,
) -> Any:
    """بررسیِ کاملِ ورود با دادهٔ **همین لحظه** — واقعی یا آزمایشی.

    هر دو مسیر از یک تابعِ مشترک (`market/entry_check.py`) رد می‌شوند تا
    عددها و دلیل‌ها در تمرین و در واقعیت یک شکل داشته باشند.
    """
    from market.entry_check import check_entry
    from market.tradability import Thresholds

    thresholds_config = section(settings, "tradability")
    known_thresholds = {f.name for f in fields(Thresholds)}
    thresholds = Thresholds(**{
        k: v for k, v in thresholds_config.items() if k in known_thresholds
    })
    weights = _ranking_weights(settings)

    signal: Signal | None = None
    if signal_id:
        signal = _signal_by_id(settings, signal_id)
        _reject_multi_leg(signal.strategy_name)
        symbol = signal.symbol
        side = signal.side.value

    if not symbol:
        raise HTTPException(status_code=400, detail="یا symbol یا signal_id باید داده شود.")
    side = (side or "buy").lower()

    broker, context = _paper_broker(settings, sandbox=sandbox)
    try:
        available_cash = broker.account_snapshot().available
    finally:
        context.close()

    if sandbox:
        check = _sandbox_entry_check(
            symbol=symbol,
            side=side,
            quantity=quantity,
            thresholds=thresholds,
            weights=weights,
            available_cash=available_cash,
            previous_score=previous_score,
            signal_id=signal_id,
        )
        return check

    context = create_app(settings, dry_run=True, as_json=False)
    try:
        screener = context.generator.tradability
        if screener is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "غربال قابلیت معامله خاموش است؛ بدونش بررسیِ ورود مبنا "
                    "ندارد. از تب «سیگنال‌ها» روشنش کنید."
                ),
            )
        contract = context.option_chain.get_contract(symbol)
        if contract is None:
            raise HTTPException(status_code=404, detail=f"قرارداد «{symbol}» پیدا نشد.")
        report = screener.evaluate_symbol(
            symbol=symbol, position_side=side, quantity=quantity
        )
        try:
            spot = context.option_chain.get_chain(contract.underlying).spot_price
        except Exception:  # قیمت پایه نبود ⇒ «حرکت لازم» نامعلوم، نه خطا
            logger.warning("قیمت پایه‌ی %s خوانده نشد.", contract.underlying)
            spot = None
    finally:
        context.close()

    return check_entry(
        symbol=symbol,
        side=side,
        quantity=quantity,
        report=report,
        strategy=signal.strategy_name if signal else "ورود دستی",
        option_type=contract.option_type,
        strike=contract.strike,
        contract_size=contract.contract_size,
        expiry=contract.expiry,
        underlying_price=spot,
        stop_loss_price=signal.stop_loss if signal else None,
        # نرخِ همان حسابی که قرار است پول از آن کم شود، نه نرخِ ماژول ریسک:
        # عددِ بررسی باید با چیزی که کارگزارِ کاغذی برمی‌دارد یکی باشد.
        fees=_paper_fee_schedule(settings),
        thresholds=thresholds,
        weights=weights,
        available_cash=available_cash,
        previous_score=previous_score,
        signal_id=signal_id,
    )


def _sandbox_entry_check(
    *,
    symbol: str,
    side: str,
    quantity: int,
    thresholds: Any,
    weights: Any,
    available_cash: float | None,
    previous_score: float | None,
    signal_id: str | None,
) -> Any:
    """همان بررسی، روی دادهٔ نمونه‌ی برچسب‌دار."""
    from market import sandbox as sb
    from market.entry_check import check_entry
    from market.tradability import evaluate
    from risk.fees import FeeSchedule

    sample = sb.BY_SYMBOL.get(symbol)
    if sample is None:
        raise HTTPException(
            status_code=404, detail=f"نمونه‌ی آزمایشی «{symbol}» وجود ندارد."
        )
    observation = sb.observation(
        sample, quantity, max_exit_slippage_pct=thresholds.max_exit_slippage_pct
    )
    report = evaluate(observation, sb.history(), thresholds)
    entry = observation.entry_fill_price
    return check_entry(
        symbol=symbol,
        side=side,
        quantity=quantity,
        report=report,
        strategy="نمونه‌ی آزمایشی",
        option_type=sample.option_type,
        strike=sample.strike,
        contract_size=sb.SANDBOX_CONTRACT_SIZE,
        expiry=sb.expiry(),
        underlying_price=sb.SANDBOX_SPOT,
        # حد ضررِ ۳۵٪ روی قیمتِ اجراییِ همین دفتر — مثل بقیه‌ی عددها.
        stop_loss_price=round(entry * 0.65, 1) if entry else None,
        fees=FeeSchedule(
            buy_rate=sb.SANDBOX_FEE_RATE, sell_rate=sb.SANDBOX_FEE_RATE, declared=True
        ),
        thresholds=thresholds,
        weights=weights,
        available_cash=available_cash,
        previous_score=previous_score,
        signal_id=signal_id,
    )


def _paper_fee_schedule(settings: dict[str, Any]) -> Any:
    """نرخ کارمزدِ **حساب کاغذی** — همانی که موقع پرشدن برداشته می‌شود."""
    from risk.fees import FeeSchedule

    config = section(settings, "paper_trading").get("fees") or {}
    known = {f.name for f in fields(FeeSchedule)}
    return FeeSchedule(**{k: v for k, v in config.items() if k in known})


@app.post("/api/trade-check")
async def check_trade_entry(request: TradeCheckRequest) -> dict[str, Any]:
    """«همین حالا، برای همین تعداد، ورود ممکن است؟»

    رتبه‌ای که کاربر روی کارت دیده عکسِ یک لحظه است. اینجا همه‌چیز با
    دادهٔ تازه و برای تعدادِ انتخابیِ خودش دوباره حساب می‌شود: غربال،
    اجراپذیریِ ورود، قیمتِ اجرایی، وجهِ لازم و خودِ امتیاز.

    اگر مانعی نباشد یک **بلیتِ کوتاه‌عمر** صادر می‌شود که ثبتِ سفارش
    بدون آن انجام نمی‌گیرد.
    """
    settings = _settings()
    ttl, _tolerance = _ticket_settings(settings)

    def _work() -> dict[str, Any]:
        check = _run_entry_check(
            settings,
            symbol=request.symbol,
            signal_id=request.signal_id,
            side=request.side,
            quantity=request.quantity,
            sandbox=request.sandbox,
            previous_score=request.previous_score,
        )
        payload = check.to_dict()
        payload["sandbox"] = request.sandbox
        if request.sandbox:
            from market import sandbox as sb

            payload["sandbox_label"] = sb.SANDBOX_LABEL
        payload["ticket"] = (
            _issue_ticket(check, request.sandbox, ttl) if check.ok else None
        )
        return payload

    try:
        return await asyncio.to_thread(_work)
    except HTTPException:
        raise
    except ValueError as exc:  # نماد نامعتبر — خطای کاربر، نه خرابی سرور
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("بررسی پیش از ورود ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/paper-trading/orders")
async def place_paper_order(request: PaperOrderRequest) -> dict[str, Any]:
    """ثبت یک سفارش کاغذی — فوری، در برابر عمق واقعی دفتر سفارش.

    سه راهِ ورودی، با یک قاعده‌ی مشترک: **هیچ ورودی‌ای با ارزیابیِ قدیمی
    تأیید نمی‌شود.**

    * از دلِ یک فرصت/سیگنال (`signal_id`) یا در مسیر آزمایشی → بلیتِ
      `POST /api/trade-check` اجباری است. بلیت با تعداد و نمادِ همین
      سفارش سنجیده می‌شود و یک بار بیشتر مصرف نمی‌شود؛ بعد هم بررسی
      **دوباره** و با دادهٔ تازه تکرار می‌شود تا قیمت از زمانِ تأیید
      جابه‌جا نشده باشد.
    * فرمِ دستیِ زنجیره (symbol + side) بلیت نمی‌خواهد، چون ارزیابیِ
      قبلی‌ای پشتش نیست؛ پرشدنش هم مثل همیشه از عمقِ **زنده** است.
    * فروش (بستنِ موقعیت) بلیتِ ورود نمی‌خواهد — بررسیِ ورود درباره‌ی
      خروج چیزی نمی‌گوید.
    """
    settings = _settings()
    _require_paper_trading_enabled(settings, sandbox=request.sandbox)

    symbol = request.symbol
    side = request.side
    quantity = request.quantity
    signal_id = request.signal_id

    if signal_id:
        match = _signal_by_id(settings, signal_id)
        _reject_multi_leg(match.strategy_name)
        symbol = match.symbol
        side = match.side.value
        if quantity is None:
            quantity = match.suggested_qty
    elif not symbol or not side:
        raise HTTPException(
            status_code=400, detail="یا symbol+side یا signal_id باید داده شود."
        )

    if quantity is None or quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity باید یک عدد مثبت باشد.")

    side = (side or "").lower()
    is_entry = side == "buy"
    needs_ticket = is_entry and (bool(signal_id) or request.sandbox)
    if needs_ticket and not request.ticket:
        raise HTTPException(
            status_code=409,
            detail=(
                "ورود از روی یک فرصت باید اول با دادهٔ تازه بررسی شود. "
                "«بررسی با دادهٔ تازه» را بزنید و بعد تأیید کنید."
            ),
        )

    decision: dict[str, Any] | None = None
    entry_guard: dict[str, Any] | None = None
    if request.ticket:
        claimed = _claim_ticket(request.ticket, symbol, side, quantity, request.sandbox)
        _ttl, tolerance = _ticket_settings(settings)

        def _recheck() -> Any:
            return _run_entry_check(
                settings,
                symbol=symbol,
                signal_id=signal_id,
                side=side,
                quantity=quantity,
                sandbox=request.sandbox,
            )

        try:
            fresh = await asyncio.to_thread(_recheck)
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("بررسی دوباره پیش از ثبت ناموفق بود.")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        if not fresh.ok:
            raise HTTPException(
                status_code=409,
                detail=(
                    "شرایط از زمان بررسی عوض شده است: "
                    + "؛ ".join(b.message for b in fresh.blockers)
                ),
            )
        # این بررسیِ اول **زودهنگام** است و فقط برای پیامِ روشن: گاردِ
        # اصلی داخلِ کارگزار و روی همان مظنه‌ای است که حساب را عوض
        # می‌کند. اینجا رد کردن یعنی کاربر زودتر بفهمد، نه اینکه
        # کنترلِ دوم لازم نباشد.
        _reject_if_price_moved(claimed["entry_price"], fresh.entry_price, tolerance)
        from market.entry_check import decision_snapshot

        decision = decision_snapshot(fresh, request.ticket)
        entry_guard = {
            "ticket_id": request.ticket,
            # آنچه کاربر دید و تأیید کرد
            "confirmed_at": claimed["issued_at"].isoformat(timespec="seconds"),
            "confirmed_price": claimed["entry_price"],
            "confirmed_quantity": claimed["quantity"],
            # آنچه بررسیِ نهایی، لحظه‌ی ثبت، دید
            "final_check_at": fresh.checked_at.isoformat(timespec="seconds"),
            "final_check_price": fresh.entry_price,
            # قیدی که کارگزار روی پرشدنِ واقعی اعمال می‌کند
            "tolerance_pct": tolerance,
            "require_full_fill": True,
        }

    def _work() -> dict[str, Any]:
        broker, context = _paper_broker(settings, sandbox=request.sandbox)
        try:
            order = broker.place_order(
                symbol,
                side,
                quantity,
                signal_id=signal_id,
                decision=decision,
                entry_guard=entry_guard,
            )
            return _serialize_order(order)
        finally:
            context.close()

    try:
        payload = await asyncio.to_thread(_work)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("ثبت سفارش کاغذی ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    payload["sandbox"] = request.sandbox
    return payload


@app.get("/api/paper-trading/orders")
def get_paper_orders(limit: int | None = None, sandbox: bool = False) -> dict[str, Any]:
    """تاریخچه سفارش‌های کاغذی، جدیدترین اول."""
    settings = _settings()
    broker, context = _paper_broker(settings, sandbox=sandbox)
    try:
        orders = broker.store.list_orders(limit=limit)
    finally:
        context.close()
    return {"total": len(orders), "orders": orders, "sandbox": sandbox}


def _serialize_valuation(v: Any) -> dict[str, Any]:
    """یک موقعیت ارزش‌گذاری‌شده، با وضعیت صریح.

    `market_value` و `unrealized_*` وقتی قیمت نخورده باشد `None` می‌مانند
    و صفر **نمی‌شوند**: رابط باید بتواند «نمی‌دانم» را از «صفر» جدا کند.
    """
    return {
        "symbol": v.symbol,
        "quantity": v.quantity,
        "contract_size": v.contract_size,
        "average_price": v.average_price,
        "entry_fees_open": v.entry_fees_open,
        "entry_fees_known": v.entry_fees_known,
        "gross_cost": v.gross_cost,
        "cost_basis": v.cost_basis,
        "status": v.status.value,
        "status_label": v.status_label,
        "mark_price": v.mark_price,
        "reference_price": v.reference_price,
        "fillable_quantity": v.fillable_quantity,
        "market_value": v.market_value,
        "unrealized_gross": v.unrealized_gross,
        "unrealized_net": v.unrealized_net,
    }


def _serialize_snapshot(snapshot: Any) -> dict[str, Any]:
    """عکسِ حساب به شکلی که داشبورد مستقیم نشانش می‌دهد."""
    realized = snapshot.realized
    return {
        # --- نقد ---
        "initial_balance": snapshot.initial_balance,
        "cash": snapshot.cash,
        "blocked": snapshot.blocked,
        "blocked_reason": snapshot.blocked_reason,
        "available": snapshot.available,
        # --- موقعیت‌ها ---
        "market_value": snapshot.market_value,
        "market_value_priced": snapshot.market_value_priced,
        "valuation_complete": snapshot.valuation_complete,
        "unpriced_count": len(snapshot.unpriced_positions),
        "unpriced": [
            {"symbol": p.symbol, "status": p.status.value, "status_label": p.status_label}
            for p in snapshot.unpriced_positions
        ],
        "priced_at": snapshot.priced_at,
        # --- سود و زیان ---
        "realized_gross": realized.gross,
        "realized_costs": realized.costs,
        "realized_entry_costs": realized.entry_costs,
        "realized_exit_costs": realized.exit_costs,
        "realized_net": realized.net,
        "realized_trade_count": realized.trade_count,
        "realized_costs_complete": realized.costs_complete,
        "trades_missing_entry_cost": realized.trades_missing_entry_cost,
        "trades_missing_exit_cost": realized.trades_missing_exit_cost,
        "unrealized_gross": snapshot.unrealized_gross,
        "unrealized_net": snapshot.unrealized_net,
        "open_entry_costs": snapshot.open_entry_costs,
        "total_costs_recorded": snapshot.total_costs_recorded,
        # «هزینه دانسته است» فقط از داده‌ی ثبت‌شده می‌آید؛ «نرخ تنظیم شده»
        # جداست و فقط درباره‌ی معامله‌های بعدی حرف می‌زند.
        "costs_known": snapshot.costs_known,
        "rates_configured": snapshot.rates_configured,
        "positions_with_unknown_cost": [
            p.symbol for p in snapshot.positions_with_unknown_cost
        ],
        # --- ارزش کل ---
        "equity": snapshot.equity,
        "equity_priced_part": snapshot.equity_priced_part,
        "total_return_pct": snapshot.total_return_pct,
        "reconciliation": snapshot.reconciliation,
    }


@app.get("/api/paper-trading/positions")
async def get_paper_positions(sandbox: bool = False) -> dict[str, Any]:
    """پوزیشن‌های باز کاغذی، همراه با ارزش روز و **وضعیت ارزش‌گذاری**.

    ⚠️ دیگر هیچ موقعیتی خودکار تسویه نمی‌شود. موقعیتِ سررسیدشده با
    وضعیت `expired_unsettled` برمی‌گردد تا کاربر خودش تعیین تکلیف کند.
    """
    settings = _settings()

    def _work() -> dict[str, Any]:
        broker, context = _paper_broker(settings, sandbox=sandbox)
        try:
            positions = [_serialize_valuation(v) for v in broker.value_positions()]
            decisions = _entry_decisions(broker)
            for position in positions:
                # «چرا وارد شدم» باید کنارِ خودِ موقعیت دیده شود، نه در
                # تاریخچه‌ی سفارش‌ها که کاربر باید دنبالش بگردد.
                position["entry_decision"] = decisions.get(position["symbol"])
            return {
                "positions": positions,
                "expired_unsettled": [
                    p["symbol"] for p in positions if p["status"] == "expired_unsettled"
                ],
                "sandbox": sandbox,
            }
        finally:
            context.close()

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.exception("خواندن پوزیشن‌های کاغذی ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/paper-trading/account")
async def get_paper_account(sandbox: bool = False) -> dict[str, Any]:
    """وضعیت کامل و **قابل تطبیق** حساب کاغذی.

    رابطه‌ی مبنا: `ارزش کل حساب = نقد + ارزش روز موقعیت‌ها`. نسخه‌ی قبلی
    `نقد + سود شناور` می‌داد که ارزشِ خودِ موقعیت را جا می‌انداخت و
    لحظه‌ی باز کردن پوزیشن، زیانِ موهوم نشان می‌داد.
    """
    settings = _settings()

    def _work() -> dict[str, Any]:
        broker, context = _paper_broker(settings, sandbox=sandbox)
        try:
            payload = _serialize_snapshot(broker.account_snapshot())
            payload["sandbox"] = sandbox
            if sandbox:
                from market import sandbox as sb

                payload["sandbox_label"] = sb.SANDBOX_LABEL
            return payload
        finally:
            context.close()

    try:
        return await asyncio.to_thread(_work)
    except Exception as exc:
        logger.exception("خواندن حساب کاغذی ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/paper-trading/settle")
async def settle_paper_position(request: PaperSettleRequest) -> dict[str, Any]:
    """ثبتِ فرضِ کاربر برای تعیین تکلیف یک موقعیتِ سررسیدشده.

    ⚠️ این «تسویه‌ی رسمی» نیست و وانمود هم نمی‌کند که هست: قواعد اعمال و
    تسویه‌ی بورس تهران در این پروژه پیاده نشده‌اند و هزینه‌ی خودِ تسویه
    هم مدل نشده است. تنها کاری که می‌کند ثبتِ عددی است که **کاربر فرض
    می‌کند**، تا موقعیت بسته شود و ارزش‌گذاری معلق نماند.

    `settlement_price` پرمیوم **هر واحد** است، نه ارزش کل قرارداد؛ ارزش
    کل از `قیمت × تعداد × اندازه‌ی قرارداد` در می‌آید.
    """
    settings = _settings()
    _require_paper_trading_enabled(settings, sandbox=request.sandbox)

    def _work() -> dict[str, Any]:
        broker, context = _paper_broker(settings, sandbox=request.sandbox)
        try:
            return broker.settle_position(request.symbol, request.settlement_price)
        finally:
            context.close()

    try:
        trade = await asyncio.to_thread(_work)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("تسویه‌ی موقعیت کاغذی ناموفق بود.")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "ok": True,
        "trade": trade,
        "basis": "user_assumption",
        "note": (
            "این عدد فرضِ شماست، نه تسویه‌ی رسمی: قواعد اعمال و تسویه‌ی "
            "بورس و هزینه‌ی خودِ تسویه در این شبیه‌ساز پیاده نشده‌اند."
        ),
    }


@app.get("/api/paper-trading/report")
def get_paper_report(days: int | None = None, sandbox: bool = False) -> dict[str, Any]:
    """معیارهای عملکرد معاملات کاغذی بسته‌شده — همان تابع بک‌تست/گزارش زنده."""
    settings = _settings()
    broker, context = _paper_broker(settings, sandbox=sandbox)
    try:
        return {
            "metrics": broker.performance_summary(days),
            "recent": broker.store.list_trades(days),
            "sandbox": sandbox,
        }
    finally:
        context.close()


@app.post("/api/paper-trading/reset")
def reset_paper_account(sandbox: bool = False) -> dict[str, Any]:
    """پاک‌کردن کامل حساب کاغذی و بازگرداندن موجودی به مقدار اولیه.

    ⚠️ `sandbox=true` فقط حسابِ آزمایشی را پاک می‌کند و به حساب واقعی
    دست نمی‌زند — و برعکس. دو پایگاهِ جدا، دو ریستِ جدا.
    """
    settings = _settings()
    _require_paper_trading_enabled(settings, sandbox=sandbox)
    broker, context = _paper_broker(settings, sandbox=sandbox)
    try:
        account = broker.reset()
    finally:
        context.close()
    return {"ok": True, "account": account, "sandbox": sandbox}


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    """وضعیت کلی برای نوار بالای داشبورد."""
    settings = _settings()
    with _signal_log(settings) as log:
        total = log.count()

    market_open: bool | None = None
    today_jalali: str | None = None
    next_trading_day: str | None = None
    known_holidays: int | None = None
    try:
        context = create_app(settings, dry_run=False, as_json=False)
        try:
            market_open = context.market_data.is_market_open()
            calendar = context.trading_calendar
            if calendar is not None:
                today = date.today()
                today_jalali = format_jalali(today)
                known_holidays = len(calendar.holidays)
                # وقتی بازار بسته است، «کِی باز می‌شود» مفیدترین چیزی است
                # که نوار وضعیت می‌تواند بگوید.
                if not market_open:
                    nxt = (
                        today
                        if calendar.is_trading_day(today)
                        else calendar.next_trading_day(today)
                    )
                    next_trading_day = f"{nxt.isoformat()} ({format_jalali(nxt)})"
        finally:
            context.close()
    except Exception as exc:  # نبود شبکه نباید داشبورد را بخواباند
        logger.warning("تشخیص وضعیت بازار ناموفق بود: %s", exc)

    return {
        "market_open": market_open,
        "signal_count": total,
        "market_data_provider": section(settings, "market_data").get("provider"),
        "option_chain_provider": section(settings, "option_chain").get("provider"),
        "settings_path": str(SETTINGS_PATH),
        "today_jalali": today_jalali,
        "next_trading_day": next_trading_day,
        "known_holidays": known_holidays,
        # زمان **همین پاسخ**، نه زمان آخرین پاس رصد. کاربر با این
        # می‌فهمد صفحه تازه است یا مانده.
        "server_time": datetime.now().isoformat(timespec="seconds"),
        # زمان آخرین سیگنالِ ثبت‌شده — یعنی «آخرین باری که ربات واقعاً
        # چیزی پیدا کرد». `None` یعنی هنوز هیچ سیگنالی نیست.
        "last_signal_at": _last_signal_at(settings),
    }


def _last_signal_at(settings: dict[str, Any]) -> str | None:
    """زمان جدیدترین سیگنال ذخیره‌شده، یا `None` اگر هیچ نباشد."""
    try:
        with _signal_log(settings) as log:
            signals = log.all_signals(limit=None)
        if not signals:
            return None
        return max(s.created_at for s in signals).isoformat(timespec="seconds")
    except Exception as exc:  # نبود این عدد نباید نوار وضعیت را بخواباند
        logger.warning("زمان آخرین سیگنال خوانده نشد: %s", exc)
        return None


# ----------------------------------------------------------------------
# فایل‌های استاتیک
# ----------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

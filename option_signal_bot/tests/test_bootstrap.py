"""تست‌های لایه تنظیمات، رجیستری استراتژی، wiring و ذخیره‌سازی."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

import bootstrap
from config.loader import (
    SettingsError,
    build_dataclass,
    deep_merge,
    default_settings,
    load_settings,
    section,
)
from notifiers.console_notifier import ConsoleNotifier
from risk.risk_calculator import RiskLimits
from signals.signal_model import OptionType, Side, Signal
from storage.signal_log import SignalLog
from strategies.base_strategy import BaseStrategy
from strategies.registry import available_strategies, create_strategies, create_strategy


@pytest.fixture
def settings() -> dict:
    return default_settings()


# ----------------------------------------------------------------------
# تنظیمات
# ----------------------------------------------------------------------
def test_deep_merge_keeps_untouched_siblings():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    merged = deep_merge(base, {"a": {"y": 99}})
    assert merged == {"a": {"x": 1, "y": 99}, "b": 3}
    assert base["a"]["y"] == 2  # ورودی تغییر نمی‌کند


def test_deep_merge_replaces_lists():
    merged = deep_merge({"symbols": ["الف", "ب"]}, {"symbols": ["ج"]})
    assert merged["symbols"] == ["ج"]


def test_load_settings_falls_back_to_defaults_when_missing():
    loaded = load_settings(Path("این-مسیر-وجود-ندارد.yaml"))
    assert loaded == default_settings()


def test_section_always_returns_dict():
    assert section({"risk": None}, "risk") == {}
    assert section({}, "ناموجود") == {}
    assert section({"risk": {"max_contracts": 3}}, "risk")["max_contracts"] == 3


def test_build_dataclass_ignores_unknown_keys():
    limits = build_dataclass(
        RiskLimits, {"max_contracts": 7, "کلید_اشتباه": 1}, "risk"
    )
    assert limits.max_contracts == 7
    assert limits.risk_per_trade_pct == RiskLimits().risk_per_trade_pct


def test_default_settings_keeps_execution_disabled():
    assert default_settings()["execution"]["enabled"] is False


def test_default_settings_keeps_paper_trading_disabled():
    """معاملات کاغذی هم مثل بقیه‌ی سوئیچ‌های پروژه، پیش‌فرض خاموش است."""
    defaults = default_settings()
    assert defaults["paper_trading"]["enabled"] is False


def test_default_settings_paper_trading_fees_are_not_guessed():
    """کارمزد معاملات کاغذی هم از قاعده‌ی «حدس نزدن نرخ» پیروی می‌کند.

    `declared: False` جزء همین قاعده است: صفرهای بالا پیش‌فرضِ پروژه‌اند،
    نه ادعای بی‌هزینه بودن — پس هزینه‌ی معامله‌ای که با آن‌ها انجام شود
    «نامعلوم» ثبت می‌شود، نه «صفر».
    """
    fees = default_settings()["paper_trading"]["fees"]
    assert fees == {
        "buy_rate": 0.0,
        "sell_rate": 0.0,
        "sell_tax_rate": 0.0,
        "per_order": 0.0,
        "declared": False,
    }


# ----------------------------------------------------------------------
# رجیستری استراتژی‌ها
# ----------------------------------------------------------------------
def test_builtin_strategies_are_registered():
    names = available_strategies()
    assert "directional_ma_cross" in names
    assert "neutral_iv_spread" in names


def test_empty_config_activates_all_strategies():
    assert len(create_strategies({})) == len(available_strategies())


def test_disabled_strategy_is_skipped():
    strategies = create_strategies(
        {"directional_ma_cross": {"enabled": False}, "neutral_iv_spread": {"enabled": True}}
    )
    assert [s.name for s in strategies] == ["neutral_iv_spread"]


def test_unknown_strategy_is_ignored_not_fatal():
    strategies = create_strategies({"استراتژی_ناموجود": {"enabled": True}})
    assert strategies == []


def test_params_from_config_override_defaults():
    strategy = create_strategies(
        {"directional_ma_cross": {"params": {"fast_window": 3}}}
    )[0]
    assert strategy.params["fast_window"] == 3
    assert strategy.params["slow_window"] == 20  # پیش‌فرض حفظ می‌شود


def test_create_unknown_strategy_raises():
    with pytest.raises(ValueError):
        create_strategy("هیچ")


def test_registry_rejects_nameless_strategy():
    from strategies.registry import register_strategy

    class Nameless(BaseStrategy):
        def generate(self, context):
            return []

    with pytest.raises(ValueError):
        register_strategy(Nameless)


# ----------------------------------------------------------------------
# wiring
# ----------------------------------------------------------------------
def test_create_app_in_dry_run_uses_console_only(settings):
    app = bootstrap.create_app(settings, dry_run=True)
    assert [type(n) for n in app.notifiers] == [ConsoleNotifier]
    assert app.signal_log is None  # dry-run روی دیسک نمی‌نویسد
    assert app.generator.strategies
    app.close()


def test_create_app_wires_risk_limits_from_settings(settings):
    settings["risk"] = {"max_contracts": 3}
    app = bootstrap.create_app(settings, dry_run=True)
    assert app.generator.risk_calculator.limits.max_contracts == 3
    app.close()


def test_unknown_provider_fails_loudly(settings):
    settings["market_data"]["provider"] = "کارگزاری_ناموجود"
    with pytest.raises(ValueError):
        bootstrap.build_market_data(settings)


def test_option_chain_receives_config(settings):
    """تنظیمات باید به کلاینت زنجیره برسد.

    از provider=fixture استفاده می‌شود چون داده‌ی ساختگی حذف شده و این
    تنها راه ساخت زنجیره بدون شبکه است.
    """
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "tsetmc_option_market_watch.json"
    settings["option_chain"] = {
        "provider": "fixture",
        "fixture_path": str(fixture),
        "quality": {"min_days_to_expiry": 7},
    }
    settings["market_data"] = {"provider": "tsetmc", "fixture_path": str(fixture)}
    market_data = bootstrap.build_market_data(settings)
    chain_client = bootstrap.build_option_chain(settings, market_data)
    assert chain_client.quality.min_days_to_expiry == 7


def test_telegram_is_skipped_without_token(settings):
    settings["notifiers"]["telegram"] = {"enabled": True, "bot_token": "<YOUR_TOKEN>"}
    # متغیرهای محیطی واقعی نباید نتیجه تست را عوض کنند
    saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
    try:
        notifiers = bootstrap.build_notifiers(settings, dry_run=False)
        assert all(n.name != "telegram" for n in notifiers)
    finally:
        os.environ.update({k: v for k, v in saved.items() if v is not None})


def test_notifiers_fall_back_to_console_when_all_disabled(settings):
    settings["notifiers"] = {"console": {"enabled": False}, "telegram": {"enabled": False}}
    notifiers = bootstrap.build_notifiers(settings, dry_run=False)
    assert [type(n) for n in notifiers] == [ConsoleNotifier]


def test_generator_never_holds_an_executor(settings):
    """قرارداد مایل‌استون ۱ در سطح wiring: چیزی به‌نام executor ساخته نمی‌شود."""
    app = bootstrap.create_app(settings, dry_run=True)
    assert not hasattr(app, "executor")
    assert not hasattr(app, "broker")
    app.close()


# ----------------------------------------------------------------------
# ذخیره‌سازی
# ----------------------------------------------------------------------
def _sample_signal() -> Signal:
    from datetime import date

    return Signal(
        symbol="ضخود-1",
        option_type=OptionType.CALL,
        side=Side.BUY,
        strike=2_500.0,
        expiry=date(2030, 1, 1),
        suggested_price=120.0,
        suggested_qty=5,
        reason="تست ذخیره‌سازی",
        strategy_name="manual",
    )


def test_signal_log_roundtrip_and_status_update():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "signals.db"
        with SignalLog(db_path=db, jsonl_path=Path(tmp) / "signals.jsonl") as log:
            signal = _sample_signal()
            log.save(signal)
            log.save(signal)  # ذخیره دوباره نباید رکورد تکراری بسازد

            assert log.count() == 1
            restored = log.all_signals()[0]
            assert restored.symbol == signal.symbol
            assert restored.suggested_qty == 5
            assert restored.status.value == "new"

            log.update_status(signal.signal_id, "expired")
            # هم ستون و هم payload باید به‌روز شده باشند
            assert log.all_signals()[0].status.value == "expired"

        assert db.exists()
        assert (Path(tmp) / "signals.jsonl").read_text(encoding="utf-8").count("\n") == 2


def test_signal_log_creates_missing_directories():
    with tempfile.TemporaryDirectory() as tmp:
        nested = Path(tmp) / "الف" / "ب" / "signals.db"
        with SignalLog(db_path=nested, jsonl_path=None) as log:
            log.save(_sample_signal())
            assert log.count() == 1
        assert nested.exists()

# ----------------------------------------------------------------------
# تنظیمات ناخوانا نباید بی‌صدا به داده mock سقوط کند
# ----------------------------------------------------------------------


def _hide_yaml(monkeypatch):
    """PyYAML را برای این تست ناموجود کن (شبیه‌سازی محیط بدون نصب)."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("simulated: PyYAML not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


CFG_BODY = "option_chain:\n  provider: tsetmc\n"


def test_existing_config_unreadable_raises_instead_of_mock(monkeypatch, tmp_path):
    """فایل تنظیمات هست ولی خوانده نمی‌شود → خطا، نه سقوط بی‌صدا به mock.

    سقوط بی‌صدا خطرناک است: ربات با قیمت ساختگی سیگنال می‌دهد که از سیگنال
    واقعی قابل تشخیص نیست.
    """
    cfg = tmp_path / "settings.yaml"
    cfg.write_text(CFG_BODY, encoding="utf-8")
    _hide_yaml(monkeypatch)

    with pytest.raises(SettingsError) as err:
        load_settings(cfg)

    assert "PyYAML" in str(err.value)


def test_missing_config_is_not_an_error(monkeypatch, tmp_path):
    """نبودن فایل تنظیمات حالت مجاز است، و پیش‌فرض **داده‌ی واقعی**.

    این مهم‌ترین تضمین این لایه است: هیچ مسیری نباید به داده‌ی ساختگی
    برسد، چون دیگر داده‌ی ساختگی‌ای وجود ندارد.
    """
    _hide_yaml(monkeypatch)
    settings = load_settings(tmp_path / "does_not_exist.yaml")
    assert settings["option_chain"]["provider"] == "tsetmc"
    assert settings["market_data"]["provider"] == "tsetmc"


def test_force_utf8_stdio_is_idempotent_and_safe():
    """صدا زدن چندباره نباید خطا بدهد، حتی وقتی جریان reconfigure ندارد."""
    from config import force_utf8_stdio

    force_utf8_stdio()
    force_utf8_stdio()

# ----------------------------------------------------------------------
# گارد: داده‌ی ساختگی نباید برگردد
# ----------------------------------------------------------------------
def test_no_mock_data_source_exists_anywhere():
    """هیچ کلاس تولیدکننده‌ی داده‌ی ساختگی نباید در مخزن باشد.

    داده‌ی ساختگی یک بار باعث شد ربات با قیمت جعلی سیگنال بدهد، و آن
    سیگنال از سیگنال واقعی قابل تشخیص نبود. حالا که حذف شده، این تست
    مانع برگشتنش می‌شود.
    """
    root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith((".venv/", "tests/")) or "__pycache__" in rel:
            continue
        source = path.read_text(encoding="utf-8")
        for name in ("class MockMarketDataClient", "class MockOptionChainClient",
                     "class MockBroker"):
            if name in source:
                offenders.append(f"{rel}: {name}")
    assert offenders == [], f"داده‌ی ساختگی برگشته است: {offenders}"


def test_default_providers_are_real():
    """پیش‌فرض تنظیمات باید داده‌ی واقعی باشد، در هر شرایطی.

    این همان جایی است که باگ قبلی زندگی می‌کرد: پیش‌فرض `mock` بود، پس
    هر خطای تنظیماتی بی‌صدا به قیمت ساختگی ختم می‌شد.
    """
    defaults = default_settings()
    assert defaults["market_data"]["provider"] == "tsetmc"
    assert defaults["option_chain"]["provider"] == "tsetmc"


def test_no_provider_named_mock_is_registered():
    """رجیستری provider نباید گزینه‌ی mock داشته باشد."""
    import bootstrap

    assert "mock" not in bootstrap.MARKET_DATA_PROVIDERS
    assert "mock" not in bootstrap.OPTION_CHAIN_PROVIDERS


# ----------------------------------------------------------------------
# دارایی حساب از کارگزاری
# ----------------------------------------------------------------------
class _StubAccount:
    """آداپتر حسابِ کمینه — فقط چیزی که `build_risk_calculator` لازم دارد."""

    def __init__(self, equity=None, error=None):
        self._equity = equity
        self._error = error
        self.calls = 0

    def get_balance(self):
        from brokers.base import AccountBalance

        self.calls += 1
        if self._error is not None:
            raise self._error
        return AccountBalance(buy_power_t2=self._equity)


def test_risk_uses_yaml_equity_by_default(settings):
    """پیش‌فرض خاموش است؛ رفتار فعلی کسی نباید بی‌خبر عوض شود."""
    settings["risk"]["account_equity"] = 1_000.0
    account = _StubAccount(equity=999_999.0)

    calculator = bootstrap.build_risk_calculator(settings, account)
    assert calculator.limits.account_equity == 1_000.0
    assert account.calls == 0, "وقتی خاموش است نباید موجودی خوانده شود"


def test_risk_uses_broker_equity_when_enabled(settings):
    settings["risk"]["account_equity"] = 1_000.0
    settings["risk"]["use_broker_equity"] = True

    calculator = bootstrap.build_risk_calculator(
        settings, _StubAccount(equity=50_000.0)
    )
    assert calculator.limits.account_equity == 50_000.0


def test_broker_equity_needs_an_account_source(settings):
    settings["risk"]["account_equity"] = 1_000.0
    settings["risk"]["use_broker_equity"] = True

    calculator = bootstrap.build_risk_calculator(settings, None)
    assert calculator.limits.account_equity == 1_000.0


@pytest.mark.parametrize("equity", [0.0, -5_000.0])
def test_non_positive_broker_equity_keeps_yaml_value(settings, equity):
    """حساب صفر یعنی هر سیگنال صفر قرارداد — شبیه یک باگ، نه یک تصمیم."""
    settings["risk"]["account_equity"] = 1_000.0
    settings["risk"]["use_broker_equity"] = True

    calculator = bootstrap.build_risk_calculator(
        settings, _StubAccount(equity=equity)
    )
    assert calculator.limits.account_equity == 1_000.0


def test_broker_failure_falls_back_to_yaml(settings):
    """قطعی کارگزاری نباید ربات را بخواباند."""
    settings["risk"]["account_equity"] = 1_000.0
    settings["risk"]["use_broker_equity"] = True

    calculator = bootstrap.build_risk_calculator(
        settings, _StubAccount(error=RuntimeError("توکن منقضی"))
    )
    assert calculator.limits.account_equity == 1_000.0


def test_broker_equity_preserves_other_risk_limits(settings):
    """جایگزینی دارایی نباید بقیه‌ی سقف‌ها را پاک کند."""
    settings["risk"].update(
        {"account_equity": 1_000.0, "use_broker_equity": True, "max_contracts": 7}
    )
    limits = bootstrap.build_risk_calculator(
        settings, _StubAccount(equity=50_000.0)
    ).limits

    assert limits.account_equity == 50_000.0
    assert limits.max_contracts == 7


def test_use_broker_equity_is_not_an_unknown_key_warning(settings, caplog):
    """هشدارِ «کلید ناشناخته» برای گرفتن غلط‌املایی است.

    اگر روی یک کلید درست هم روشن شود، اعتبارش را از دست می‌دهد.
    """
    settings["risk"]["use_broker_equity"] = False
    with caplog.at_level("WARNING"):
        bootstrap.build_risk_calculator(settings)
    assert "use_broker_equity" not in caplog.text


# ----------------------------------------------------------------------
# استراتژی جامانده از تنظیمات
# ----------------------------------------------------------------------
def test_a_registered_strategy_missing_from_config_warns(caplog):
    """این دقیقاً همان چیزی است که دو روز سیگنال را خورد.

    بخش `strategies` یک فهرست سفید است؛ استراتژیِ نیامده بی‌صدا ساخته
    نمی‌شود. سکوت اینجا یعنی کاربر هیچ‌وقت نمی‌فهمد چرا فقط یک
    استراتژی سیگنال می‌دهد.
    """
    from strategies.registry import available_strategies, create_strategies

    with caplog.at_level("WARNING"):
        built = create_strategies({"directional_ma_cross": {"enabled": True}})

    assert [s.name for s in built] == ["directional_ma_cross"]
    for name in available_strategies():
        if name != "directional_ma_cross":
            assert name in caplog.text, name


def test_a_complete_config_warns_about_nothing(caplog):
    from strategies.registry import available_strategies, create_strategies

    complete = {name: {"enabled": True} for name in available_strategies()}
    with caplog.at_level("WARNING"):
        built = create_strategies(complete)

    assert len(built) == len(available_strategies())
    assert "نیستند" not in caplog.text


def test_shipped_config_enables_every_strategy():
    """تنظیمات نمونه نباید هیچ استراتژی‌ای را جا بگذارد."""
    import yaml

    from config.loader import EXAMPLE_CONFIG_PATH
    from strategies.registry import available_strategies

    data = yaml.safe_load(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    configured = set(data.get("strategies") or {})
    missing = set(available_strategies()) - configured
    assert not missing, f"در settings.example.yaml نیستند: {sorted(missing)}"

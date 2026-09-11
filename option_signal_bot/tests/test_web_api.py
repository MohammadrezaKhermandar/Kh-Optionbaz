"""تست‌های لایه API داشبورد.

تمرکز روی چیزهایی است که می‌توانند بی‌صدا خراب شوند:

* نوشتن روی `settings.yaml` نباید بقیه‌ی کلیدها را پاک کند یا فارسی را
  خراب کند (یک بار با `Set-Content` همین اتفاق افتاد و PyYAML فایل را
  نخواند).
* پارامتر ناشناخته باید رد شود، نه اینکه در yaml بنشیند و بی‌اثر بماند.
* داشبورد نباید هیچ راهی به لایه‌ی `execution` داشته باشد.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """کلاینت تست با `settings.yaml` موقت، تا تنظیمات واقعی دست‌نخورده بماند.

    تنظیمات به **پاسخ ضبط‌شده** وصل می‌شود، نه TSETMC زنده. بدون این،
    `/api/status` تقویم را می‌پرسد، تقویم یک سال تاریخچه از شبکه می‌کشد و
    تست به ساعت بازار و دسترسی به اینترنت گره می‌خورد — تستی که وقتی
    بازار بسته است بخوابد، تست نیست.
    """
    from web import api as web_api

    example = Path(web_api.EXAMPLE_PATH)
    data = yaml.safe_load(example.read_text(encoding="utf-8")) or {}

    fixture = Path(__file__).parent / "fixtures" / "tsetmc_option_market_watch.json"
    data.setdefault("market_data", {})["fixture_path"] = str(fixture)
    data["market_data"]["history_dir"] = str(Path(__file__).parent / "fixtures" / "history")
    data["market_data"]["symbols"] = ["خودرو", "شستا"]
    data.setdefault("option_chain", {})["provider"] = "fixture"
    data["option_chain"]["fixture_path"] = str(fixture)
    # تقویم هم نباید از شبکه یاد بگیرد
    data.setdefault("trading_calendar", {})["learn_from_market"] = False
    # ⚠️ بدون این، `sqlite_path` نمونه به `var/paper_trading.db` **واقعی**
    # اشاره می‌کند و اجرای تست‌ها حساب کاغذیِ خودِ کاربر را ریست می‌کند.
    data.setdefault("paper_trading", {})["sqlite_path"] = str(tmp_path / "paper_trading.db")

    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    monkeypatch.setattr(web_api, "SETTINGS_PATH", settings)
    with TestClient(web_api.app) as test_client:
        test_client.settings_path = settings  # type: ignore[attr-defined]
        yield test_client


def _load(path: Path) -> dict:
    return yaml.safe_load(open(path, encoding="utf-8")) or {}


# ----------------------------------------------------------------------
# خواندن
# ----------------------------------------------------------------------
def test_status_reports_data_source(client):
    """منبع داده باید در وضعیت بیاید؛ کاربر با همین تشخیص می‌دهد mock است یا نه."""
    body = client.get("/api/status").json()
    assert "market_data_provider" in body
    assert "option_chain_provider" in body
    assert "signal_count" in body


def test_status_exposes_trading_calendar_fields(client):
    """کلیدهای تقویم باید همیشه باشند، حتی وقتی شبکه نیست و مقدارشان None است."""
    body = client.get("/api/status").json()
    for key in ("today_jalali", "next_trading_day", "known_holidays"):
        assert key in body


def test_strategies_expose_defaults_and_current(client):
    body = client.get("/api/strategies").json()
    names = [s["name"] for s in body["strategies"]]
    assert "directional_ma_cross" in names
    for strategy in body["strategies"]:
        assert strategy["defaults"], "پارامترهای پیش‌فرض باید برگردند"
        # هر پیش‌فرض باید در مقدار فعلی هم حاضر باشد
        assert set(strategy["defaults"]) <= set(strategy["params"])


def test_signals_endpoint_returns_computed_fields(client):
    body = client.get("/api/signals?limit=5").json()
    assert "total" in body
    for signal in body["signals"]:
        # این دو property هستند و در asdict نمی‌آیند؛ باید دستی اضافه شوند
        assert "days_to_expiry" in signal
        assert "notional" in signal


# ----------------------------------------------------------------------
# نوشتن
# ----------------------------------------------------------------------
def test_risk_update_preserves_the_rest_of_the_file(client):
    """نوشتن یک کلید نباید بقیه‌ی تنظیمات را قربانی کند."""
    path = client.settings_path
    before = _load(path)

    response = client.put("/api/risk", json={"max_contracts": 7})
    assert response.status_code == 200

    after = _load(path)
    assert after["risk"]["max_contracts"] == 7
    assert after["risk"]["account_equity"] == before["risk"]["account_equity"]
    for key in ("general", "market_data", "option_chain", "strategies", "signals"):
        assert key in after, f"بخش «{key}» پس از نوشتن گم شد"


def test_symbols_update_keeps_persian_readable(client):
    """نماد فارسی باید سالم برگردد و فایل با PyYAML خوانا بماند."""
    path = client.settings_path
    response = client.put("/api/symbols", json={"symbols": ["خودرو", "شستا"]})
    assert response.status_code == 200

    after = _load(path)
    assert after["market_data"]["symbols"] == ["خودرو", "شستا"]


def test_strategy_toggle_round_trips(client):
    response = client.put("/api/strategies/directional_ma_cross", json={"enabled": False})
    assert response.status_code == 200

    body = client.get("/api/strategies").json()
    target = next(s for s in body["strategies"] if s["name"] == "directional_ma_cross")
    assert target["enabled"] is False


def test_strategy_param_is_written_where_the_strategy_reads_it(client):
    """پارامتر باید زیر کلید `params` بنشیند، نه مسطح.

    `create_strategies` فقط `entry["params"]` را به استراتژی پاس می‌دهد.
    نوشتن مسطح بی‌صدا بی‌اثر است: داشبورد «ذخیره شد» می‌گوید ولی رفتار
    استراتژی تغییر نمی‌کند — بدترین نوع خرابی.
    """
    from strategies.registry import create_strategies

    response = client.put(
        "/api/strategies/directional_ma_cross", json={"params": {"min_momentum_pct": 4.25}}
    )
    assert response.status_code == 200

    stored = _load(client.settings_path)["strategies"]["directional_ma_cross"]
    assert stored["params"]["min_momentum_pct"] == 4.25

    # و مهم‌تر: واقعاً به استراتژی ساخته‌شده می‌رسد
    built = create_strategies({"directional_ma_cross": stored})
    assert built, "استراتژی ساخته نشد"
    assert built[0].params["min_momentum_pct"] == 4.25


def test_strategy_params_reflect_stored_values_in_get(client):
    """GET باید مقدار ذخیره‌شده را نشان بدهد، نه پیش‌فرض را."""
    client.put(
        "/api/strategies/directional_ma_cross", json={"params": {"min_momentum_pct": 9.5}}
    )
    body = client.get("/api/strategies").json()
    target = next(s for s in body["strategies"] if s["name"] == "directional_ma_cross")
    assert target["params"]["min_momentum_pct"] == 9.5
    # کلید ساختاری `params` نباید خودش به‌عنوان یک پارامتر ظاهر شود
    assert "params" not in target["params"]


# ----------------------------------------------------------------------
# اعتبارسنجی
# ----------------------------------------------------------------------
def test_unknown_strategy_is_rejected(client):
    assert client.put("/api/strategies/does_not_exist", json={"enabled": True}).status_code == 404


def test_unknown_param_is_rejected_not_silently_stored(client):
    """پارامتر اشتباه باید خطا بدهد.

    اگر بی‌صدا در yaml بنشیند، کاربر فکر می‌کند تنظیمش اعمال شده در حالی
    که استراتژی هیچ‌وقت آن را نمی‌خواند.
    """
    response = client.put(
        "/api/strategies/directional_ma_cross", json={"params": {"totally_bogus": 1}}
    )
    assert response.status_code == 400
    stored = _load(client.settings_path)["strategies"].get("directional_ma_cross") or {}
    assert "totally_bogus" not in stored


def test_empty_symbol_list_is_rejected(client):
    assert client.put("/api/symbols", json={"symbols": []}).status_code == 400
    assert client.put("/api/symbols", json={"symbols": ["  "]}).status_code == 400


def test_empty_risk_patch_is_rejected(client):
    assert client.put("/api/risk", json={}).status_code == 400


# ----------------------------------------------------------------------
# حساب کارگزاری
# ----------------------------------------------------------------------
def test_account_is_disabled_by_default(client):
    """اتصال به حساب باید صریحاً روشن شود، نه اینکه پیش‌فرض باشد."""
    body = client.get("/api/account").json()
    assert body["enabled"] is False
    assert body["positions"] == []
    assert body["reason"], "باید دلیل خاموش بودن را بگوید"


def test_account_reports_broker_failure_without_crashing(client, monkeypatch):
    """سشن منقضی نباید داشبورد را بخواباند؛ پیام روشن باید بدهد."""
    import yaml

    path = client.settings_path
    data = _load(path)
    data["broker"] = {"enabled": True, "session_file": "var/does-not-exist.json"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    body = client.get("/api/account").json()
    assert body["enabled"] is True
    assert body["reason"]  # پیام خطا هست
    assert body["positions"] == []


# ----------------------------------------------------------------------
# ایمنی
# ----------------------------------------------------------------------
def test_web_layer_only_reaches_execution_through_paper_broker():
    """داشبورد فقط از طریق `PaperBroker` (کاغذی/شبیه‌سازی) به execution می‌رسد.

    گارد AST سراسری در `test_signal_generator.py` کل مخزن را می‌پاید و
    یک استثنای تک‌فایلی صریح برای `web/api.py` دارد (تصمیم کاربر برای
    معاملات کاغذی). این تست همان استثنا را صریح و موضعی می‌کند: import
    مجاز است، ولی فقط دقیقاً به `execution.paper_broker`.
    """
    source = Path(importlib.import_module("web.api").__file__).read_text(encoding="utf-8")
    assert "from execution.paper_broker import PaperBroker" in source
    assert "import execution\n" not in source


# ----------------------------------------------------------------------
# معاملات کاغذی
# ----------------------------------------------------------------------
#: نماد و قیمت واقعی از پاسخ ضبط‌شده، برای تست بدون شبکه
PAPER_SYMBOL = "ضهرم6040"
PAPER_PRICE = 1000.0
#: با اندازه‌ی قرارداد ۱۰۰۰، هر قرارداد یک میلیون ریال است. این عدد
#: عمداً بزرگ است تا این تست‌ها به کنترلِ کفایتِ وجه نخورند؛ خودِ آن
#: کنترل در tests/test_paper_accounting.py سنجیده می‌شود.
PAPER_BALANCE = 500_000_000.0


@pytest.fixture
def paper_order_book(monkeypatch):
    """جایگزینی عمق مظنه با یک دفتر ثابت، تا هیچ تماس شبکه‌ای برقرار نشود."""
    from data.order_book import BookLevel, OrderBook, OrderBookClient

    book = OrderBook(
        PAPER_SYMBOL,
        bids=(BookLevel(PAPER_PRICE - 10, 100),),
        asks=(BookLevel(PAPER_PRICE, 100),),
    )
    monkeypatch.setattr(
        OrderBookClient, "try_get_order_book", lambda self, ins_code, symbol="": book
    )
    return book


def _enable_paper_trading(client) -> None:
    response = client.put(
        "/api/paper-trading/settings",
        json={
            "enabled": True,
            "initial_balance": PAPER_BALANCE,
            # صفرِ **اعلام‌شده**: بدون آن هزینه‌ها نامعلوم‌اند و «خالص»
            # عدد نمی‌گیرد، که موضوع این تست‌ها نیست.
            "fees": {"buy_rate": 0.0, "sell_rate": 0.0, "sell_tax_rate": 0.0,
                     "per_order": 0.0, "declared": True},
        },
    )
    assert response.status_code == 200


def test_paper_trading_is_disabled_by_default(client):
    body = client.get("/api/paper-trading/settings").json()
    assert body["enabled"] is False


def test_paper_trading_chain_lists_real_contracts_for_underlying(client):
    """dropdown زنجیره اختیار فرم سفارش دستی، از همین endpoint پر می‌شود."""
    body = client.get("/api/paper-trading/chain?underlying=خودرو").json()
    assert body["contracts"]
    for contract in body["contracts"]:
        assert contract["symbol"]
        assert contract["option_type"] in ("call", "put")


def test_paper_trading_chain_for_unknown_underlying_is_a_client_error(client):
    """نماد پایه‌ی نامعتبر باید ۴۰۰ بدهد، نه ۵۰۰ یا لیست خالیِ گمراه‌کننده."""
    response = client.get("/api/paper-trading/chain?underlying=نامعتبر")
    assert response.status_code == 400


def test_paper_order_rejected_while_disabled(client, paper_order_book):
    response = client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 1},
    )
    assert response.status_code == 400


def test_paper_trading_settings_update_preserves_the_rest_of_the_file(client):
    path = client.settings_path
    before = _load(path)

    _enable_paper_trading(client)

    after = _load(path)
    assert after["paper_trading"]["enabled"] is True
    # کلیدهای بی‌ربط دست‌نخورده می‌مانند
    assert after["market_data"] == before["market_data"]


def test_paper_order_fills_from_the_real_order_book(client, paper_order_book):
    _enable_paper_trading(client)

    response = client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "filled"
    assert body["price"] == PAPER_PRICE

    positions = client.get("/api/paper-trading/positions").json()["positions"]
    assert positions[0]["symbol"] == PAPER_SYMBOL
    assert positions[0]["quantity"] == 2


def test_paper_reset_clears_positions_and_restores_balance(client, paper_order_book):
    _enable_paper_trading(client)
    client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 2},
    )

    response = client.post("/api/paper-trading/reset")
    assert response.status_code == 200
    assert response.json()["account"]["cash"] == PAPER_BALANCE

    positions = client.get("/api/paper-trading/positions").json()["positions"]
    assert positions == []


def test_paper_order_from_unknown_signal_returns_404(client, paper_order_book):
    _enable_paper_trading(client)
    response = client.post(
        "/api/paper-trading/orders", json={"signal_id": "does-not-exist", "quantity": 1}
    )
    assert response.status_code == 404

# ----------------------------------------------------------------------
# انتخاب منبع داده
# ----------------------------------------------------------------------
def test_datasource_lists_only_real_providers(client):
    body = client.get("/api/datasource").json()
    assert "tsetmc" in body["available_market_data"]
    assert "tsetmc" in body["available_option_chain"]
    # داده‌ی ساختگی حذف شده؛ نباید در گزینه‌ها باشد
    assert "mock" not in body["available_market_data"]
    assert "mock" not in body["available_option_chain"]


def test_datasource_change_round_trips(client):
    response = client.put("/api/datasource", json={"market_data_provider": "pytse"})
    assert response.status_code == 200
    assert _load(client.settings_path)["market_data"]["provider"] == "pytse"


def test_unknown_provider_is_rejected(client):
    response = client.put("/api/datasource", json={"option_chain_provider": "nope"})
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


def test_enrichment_requires_broker_to_be_enabled(client):
    """غنی‌سازی بدون کارگزاری بی‌معناست و باید صریح رد شود.

    اگر بی‌صدا پذیرفته شود، کاربر فکر می‌کند وجه تضمین از کارگزاری
    می‌آید در حالی که هیچ‌وقت نمی‌آید.
    """
    response = client.put("/api/datasource", json={"enrich_with_broker": True})
    assert response.status_code == 400
    assert "حساب" in response.json()["detail"]


def test_enrichment_allowed_once_broker_is_on(client):
    import yaml

    path = client.settings_path
    data = _load(path)
    data["broker"] = {"enabled": True, "token": "x"}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    response = client.put("/api/datasource", json={"enrich_with_broker": True})
    assert response.status_code == 200
    assert _load(path)["option_chain"]["enrich_with_broker"] is True


def test_enrich_limit_is_bounded(client):
    """سقف بالا لازم است: هر واحد یک درخواست شبکه در هر پاس است."""
    assert client.put("/api/datasource", json={"enrich_limit": 5000}).status_code == 400
    assert client.put("/api/datasource", json={"enrich_limit": -1}).status_code == 400
    assert client.put("/api/datasource", json={"enrich_limit": 10}).status_code == 200


def test_broker_token_is_never_returned(client):
    """توکن نباید از هیچ endpointی برگردد."""
    client.put("/api/broker", json={"enabled": True, "token": "SECRET_TOKEN_123"})

    for path in ("/api/datasource", "/api/status", "/api/account"):
        assert "SECRET_TOKEN_123" not in client.get(path).text, f"توکن در {path} لو رفت"


def test_risk_exposes_use_broker_equity(client):
    assert "use_broker_equity" in client.get("/api/risk").json()


def test_use_broker_equity_can_be_toggled(client):
    """چک‌باکس باید boolean بنشیند، نه ۰/۱ — وگرنه سوئیچ بی‌اثر است."""
    res = client.put("/api/risk", json={"use_broker_equity": True})
    assert res.status_code == 200

    stored = _load(client.settings_path)
    assert stored["risk"]["use_broker_equity"] is True
    assert client.get("/api/risk").json()["use_broker_equity"] is True


def test_toggling_broker_equity_keeps_other_risk_values(client):
    before = client.get("/api/risk").json()
    client.put("/api/risk", json={"use_broker_equity": True})
    after = client.get("/api/risk").json()

    assert after["account_equity"] == before["account_equity"]
    assert after["max_contracts"] == before["max_contracts"]


def test_structure_kinds_match_the_scanner(client):
    """داشبورد فهرستش را از سرور می‌گیرد؛ اگر عقب بیفتد، ساختار تازه دیده نمی‌شود."""
    from strategies.scanner import SCAN_KINDS

    body = client.get("/api/structures/kinds").json()
    keys = [k["key"] for k in body["kinds"]]
    assert keys == list(SCAN_KINDS)
    assert all(k["label"].strip() for k in body["kinds"])


def test_unknown_structure_kind_is_rejected(client):
    res = client.get("/api/structures", params={"underlying": "خودرو", "kind": "nope"})
    assert res.status_code == 400


def test_rank_keys_are_exposed(client):
    body = client.get("/api/structures/rank-keys").json()
    assert any(k["key"] == "roi" for k in body["keys"])


def test_report_exposes_performance_metrics(client):
    """داشبورد باید معیارها را ببیند، وگرنه فقط نرخ برد را نشان می‌دهد."""
    body = client.get("/api/report").json()
    assert "metrics" in body
    assert "equity_curve" in body
    for key in (
        "expectancy_pct",
        "sharpe_per_signal",
        "sortino_per_signal",
        "max_drawdown_pct",
        "profit_factor",
        "longest_losing_streak",
    ):
        assert key in body["metrics"], key


def test_report_metrics_use_none_for_unknown(client):
    """پایگاه‌داده‌ی تست خالی است؛ معیارها باید `null` باشند، نه صفر."""
    metrics = client.get("/api/report").json()["metrics"]
    if metrics["total"] == 0:
        assert metrics["expectancy_pct"] is None
        assert metrics["sharpe_per_signal"] is None


def test_iv_surface_endpoint_reports_level_skew_and_term(client):
    body = client.get("/api/iv-surface", params={"underlying": "خودرو"}).json()
    for key in ("atm_iv", "mean_iv", "skew", "term_structure", "iv_rank"):
        assert key in body, key


def test_iv_rank_is_null_until_history_is_long_enough(client):
    """`None` یعنی تاریخچه کافی نیست — نه «متوسط»."""
    body = client.get("/api/iv-surface", params={"underlying": "خودرو"}).json()
    if body.get("history_samples", 0) < 20:
        assert body["iv_rank"] is None


# ----------------------------------------------------------------------
# رصد زنده (پولینگ داشبورد)
# ----------------------------------------------------------------------
def test_live_controls_exist_in_the_page():
    """دکمه و بازه‌ی رصد زنده باید در HTML باشند."""
    from web import api as web_api

    html = (Path(web_api.STATIC_DIR) / "index.html").read_text(encoding="utf-8")
    for element in ('id="btn-live"', 'id="live-interval"', 'id="live-status"'):
        assert element in html, element


def test_live_mode_is_wired_in_js():
    from web import api as web_api

    js = (Path(web_api.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    for symbol in ("startLive", "stopLive", "liveRun", "visibilitychange"):
        assert symbol in js, symbol


def test_live_mode_stops_itself_when_the_market_closes():
    """بازار تهران ۹:۰۰ تا ۱۲:۳۰ باز است.

    پولینگ روی بازار بسته فقط قیمت دیروز را دوباره می‌خواند.
    """
    from web import api as web_api

    js = (Path(web_api.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    assert "market_open === false" in js
    assert "stopLive" in js


def test_concurrent_scan_is_refused_not_queued(client):
    """۴۰۹ همان چیزی است که حالت زنده باید بی‌سروصدا رد کند."""
    import web.api as web_api

    assert hasattr(web_api, "_scan_lock")


def test_live_polling_treats_busy_as_normal():
    """پاسِ همپوشان در حالت زنده عادی است، نه خطا.

    اگر مثل خطا نشان داده شود، کاربر فکر می‌کند چیزی خراب است.
    """
    from web import api as web_api

    js = (Path(web_api.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    assert "در حال اجراست" in js


# ----------------------------------------------------------------------
# آخرین به‌روزرسانی
# ----------------------------------------------------------------------
def test_status_reports_when_the_page_was_refreshed(client):
    body = client.get("/api/status").json()
    assert body["server_time"], "زمان پاسخ باید همیشه باشد"


def test_last_signal_time_is_none_not_zero_when_empty(client):
    """«هیچ سیگنالی نیست» با «سیگنال قدیمی» فرق دارد."""
    body = client.get("/api/status").json()
    assert "last_signal_at" in body
    assert body["last_signal_at"] is None or isinstance(body["last_signal_at"], str)


def test_page_freshness_and_signal_age_are_separate_fields(client):
    """یکی گرفتنشان یعنی کاربر فکر کند ربات تازه رصد کرده.

    در حالی که فقط صفحه رفرش شده.
    """
    body = client.get("/api/status").json()
    assert "server_time" in body and "last_signal_at" in body


# ----------------------------------------------------------------------
# حساب کاغذی: همان عددهایی که رابط نشان می‌دهد
# ----------------------------------------------------------------------
def test_account_shows_cash_blocked_available_and_market_value(client, paper_order_book):
    """چهار عددِ نقد و ارزش روز، همه در پاسخ باشند و با هم بخوانند.

    دستی (اندازه‌ی قرارداد ۱۰۰۰، کارمزد صفرِ پیش‌فرض، خرید ۲ در ۱۰۰۰،
    مظنه‌ی خرید ۹۹۰):
        نقد       = ۵۰۰٬۰۰۰٬۰۰۰ − ۲٬۰۰۰٬۰۰۰ = ۴۹۸٬۰۰۰٬۰۰۰
        ارزش روز  = ۹۹۰ × ۲ × ۱۰۰۰           =   ۱٬۹۸۰٬۰۰۰
        ارزش حساب = ۴۹۸٬۰۰۰٬۰۰۰ + ۱٬۹۸۰٬۰۰۰  = ۴۹۹٬۹۸۰٬۰۰۰
    """
    _enable_paper_trading(client)
    client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 2},
    )

    account = client.get("/api/paper-trading/account").json()

    assert account["initial_balance"] == PAPER_BALANCE
    assert account["cash"] == pytest.approx(498_000_000.0)
    assert account["blocked"] == 0.0
    assert account["blocked_reason"]
    assert account["available"] == pytest.approx(498_000_000.0)
    assert account["market_value"] == pytest.approx(1_980_000.0)
    assert account["equity"] == pytest.approx(499_980_000.0)
    assert account["valuation_complete"] is True
    assert account["reconciliation"]["ok"] is True


def test_account_equity_is_not_cash_plus_unrealized(client, paper_order_book):
    """رگرسیون رفتار قبلی: ارزش حساب نباید به اندازه‌ی ارزش موقعیت بپرد."""
    _enable_paper_trading(client)
    client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 2},
    )

    account = client.get("/api/paper-trading/account").json()
    wrong = account["cash"] + account["unrealized_net"]

    assert account["equity"] != pytest.approx(wrong)
    # ارزش حساب باید نزدیک سرمایه‌ی اولیه بماند، نه ۲ میلیون پایین‌تر.
    assert abs(account["equity"] - PAPER_BALANCE) < 100_000


def test_account_separates_gross_from_net(client, paper_order_book):
    _enable_paper_trading(client)
    account = client.get("/api/paper-trading/account").json()

    for key in (
        "realized_gross", "realized_net", "realized_costs",
        "unrealized_gross", "unrealized_net", "costs_known",
    ):
        assert key in account, key


def test_positions_carry_an_explicit_valuation_status(client, paper_order_book):
    _enable_paper_trading(client)
    client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 2},
    )

    position = client.get("/api/paper-trading/positions").json()["positions"][0]

    assert position["status"] == "ok"
    assert position["status_label"]
    assert position["mark_price"] == PAPER_PRICE - 10
    assert position["market_value"] == pytest.approx((PAPER_PRICE - 10) * 2 * 1_000)


def test_insufficient_funds_order_is_rejected_through_the_api(client, paper_order_book):
    """سفارشی که وجه ندارد باید رد شود و حساب دست‌نخورده بماند."""
    response = client.put(
        "/api/paper-trading/settings",
        json={"enabled": True, "initial_balance": 1_000.0},
    )
    assert response.status_code == 200
    client.post("/api/paper-trading/reset")

    body = client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 5},
    ).json()

    assert body["status"] == "rejected"
    assert "وجه قابل استفاده کافی نیست" in body["metadata"]["reason"]
    account = client.get("/api/paper-trading/account").json()
    assert account["cash"] == pytest.approx(1_000.0)


def test_settle_refuses_a_position_that_has_not_expired(client, paper_order_book):
    _enable_paper_trading(client)
    client.post(
        "/api/paper-trading/orders",
        json={"symbol": PAPER_SYMBOL, "side": "buy", "quantity": 1},
    )

    response = client.post(
        "/api/paper-trading/settle",
        json={"symbol": PAPER_SYMBOL, "settlement_price": 1_000.0},
    )

    assert response.status_code == 400
    assert "سررسید" in response.json()["detail"]


def test_settle_rejects_non_finite_prices_at_the_api_boundary(client, paper_order_book):
    """`inf`/`nan` باید در خودِ مرزِ API رد شوند، نه اینکه تا لایه‌ی مالی بروند.

    JSON کلمه‌ی `Infinity` ندارد ولی `1e999` همان می‌شود؛ بدون
    `allow_inf_nan=False` بی‌صدا رد می‌شد و نقد را بی‌نهایت می‌کرد.
    """
    _enable_paper_trading(client)

    for bad in ("1e999", "-1e999"):
        response = client.post(
            "/api/paper-trading/settle",
            content=f'{{"symbol": "{PAPER_SYMBOL}", "settlement_price": {bad}}}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, bad


def test_settle_rejects_a_negative_price_at_the_api_boundary(client, paper_order_book):
    _enable_paper_trading(client)

    response = client.post(
        "/api/paper-trading/settle",
        json={"symbol": PAPER_SYMBOL, "settlement_price": -1.0},
    )

    assert response.status_code == 422


def test_settle_requires_an_explicit_price(client, paper_order_book):
    """قیمت پیش‌فرض ندارد: نبودنش خطاست، نه صفر."""
    _enable_paper_trading(client)

    response = client.post("/api/paper-trading/settle", json={"symbol": PAPER_SYMBOL})

    assert response.status_code == 422


def test_account_separates_costs_known_from_rates_configured(client, paper_order_book):
    """دو پرچم جدا: «هزینه دانسته است» و «نرخ تنظیم شده»."""
    _enable_paper_trading(client)

    account = client.get("/api/paper-trading/account").json()

    assert "costs_known" in account
    assert "rates_configured" in account
    assert "positions_with_unknown_cost" in account
    assert "total_costs_recorded" in account

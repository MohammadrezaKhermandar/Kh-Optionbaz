"""گارد: هیچ تستی نباید به شبکه برود.

**چرا این گارد لازم شد**

این قید از اول ادعای پروژه بود («تست‌ها روی پاسخ ضبط‌شده اجرا می‌شوند»)
ولی چیزی تضمینش نمی‌کرد. و واقعاً شکست: با افزودن تقویم معاملاتی،
`create_app` شروع کرد یک سال تاریخچه از TSETMC بکشد. نتیجه‌اش این بود که
دو فایل تست **هنگ می‌کردند** و کل سوئیت از ۱۸ ثانیه به بیش از ۱۰ دقیقه
رسید — بدون اینکه هیچ تستی fail شود.

خرابیِ بی‌صدا از خرابیِ پرصدا بدتر است. این فایل صدایش می‌کند.

روش: هر تلاش برای ساختِ سوکت را می‌شکنیم و بعد همان مسیرهایی را صدا
می‌زنیم که قبلاً به شبکه می‌رفتند. اگر کسی دوباره شبکه را وارد کند،
اینجا **fail** می‌شود، نه اینکه کند شود.
"""

from __future__ import annotations

import socket
from datetime import date, datetime
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"
CHAIN_FIXTURE = FIXTURE_DIR / "tsetmc_option_market_watch.json"
HISTORY_DIR = FIXTURE_DIR / "history"
INDEX_DIR = FIXTURE_DIR / "index"


class NetworkUsedInTest(AssertionError):
    """یک تست به شبکه رفت — یعنی قید پروژه شکسته."""


#: لوکال‌هاست مسدود **نمی‌شود**. `TestClient` برای حلقه‌ی asyncio خودش
#: `socket.socketpair()` می‌سازد که روی ویندوز از ۱۲۷.۰.۰.۱ رد می‌شود —
#: آن IPC داخلی است، نه رفتن به اینترنت. مسدود کردنش یک هشدار دروغ
#: می‌ساخت و گارد را بی‌اعتبار می‌کرد.
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "0.0.0.0"})


def _is_local(address: object) -> bool:
    if isinstance(address, tuple) and address:
        return str(address[0]) in _LOCAL_HOSTS
    return False


@pytest.fixture
def no_network(monkeypatch):
    """هر اتصال به **بیرون** را به خطا تبدیل می‌کند."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection

    def _guard(real, is_method: bool):
        def wrapper(*args, **kwargs):
            address = args[1] if is_method and len(args) > 1 else (args[0] if args else None)
            if _is_local(address):
                return real(*args, **kwargs)
            raise NetworkUsedInTest(
                f"این تست به شبکه رفت ({address!r}). تست‌ها باید روی پاسخ "
                "ضبط‌شده اجرا شوند (tests/fixtures)."
            )

        return wrapper

    monkeypatch.setattr(socket.socket, "connect", _guard(real_connect, True))
    monkeypatch.setattr(socket.socket, "connect_ex", _guard(real_connect_ex, True))
    monkeypatch.setattr(socket, "create_connection", _guard(real_create, False))


def _offline_settings(tmp_path) -> dict:
    """تنظیماتی که کاملاً به فیکسچر وصل است."""
    from config.loader import default_settings

    settings = default_settings()
    settings["market_data"]["fixture_path"] = str(CHAIN_FIXTURE)
    settings["market_data"]["history_dir"] = str(HISTORY_DIR)
    settings["market_data"]["symbols"] = ["خودرو"]
    settings["option_chain"]["provider"] = "fixture"
    settings["option_chain"]["fixture_path"] = str(CHAIN_FIXTURE)
    settings["storage"]["sqlite_path"] = str(tmp_path / "signals.db")
    settings["storage"]["jsonl_path"] = str(tmp_path / "signals.jsonl")
    settings["trading_calendar"]["cache_path"] = str(tmp_path / "calendar.json")
    # تحلیل وضعیت هم باید آفلاین باشد: شاخص از نمونه‌ی ضبط‌شده، و
    # رویدادهای شرکتی اصلاً پرسیده نشوند.
    settings["regime"]["index_history_dir"] = str(INDEX_DIR)
    settings["regime"]["corporate_actions"] = False
    return settings


# ======================================================================
# خودِ گارد کار می‌کند؟
# ======================================================================
def test_the_guard_actually_blocks_network(no_network):
    """اگر گارد خودش کار نکند، بقیه‌ی تست‌های این فایل بی‌معنا می‌شوند."""
    with pytest.raises(NetworkUsedInTest):
        socket.create_connection(("cdn.tsetmc.com", 443), timeout=1)


# ======================================================================
# مسیرهایی که واقعاً شکستند
# ======================================================================
def test_create_app_does_not_touch_the_network(no_network, tmp_path):
    """این همان چیزی است که شکست.

    `create_app` را هر کسی صدا می‌زند که فقط wiring می‌خواهد — تست،
    `--dry-run`، ساختِ داشبورد. یادگیریِ تقویم اینجا یعنی همه‌ی آن‌ها
    یک سال تاریخچه از شبکه می‌کشند.
    """
    from bootstrap import create_app

    context = create_app(_offline_settings(tmp_path), dry_run=True)
    context.close()


def test_building_the_calendar_does_not_learn_eagerly(no_network, tmp_path):
    """ساختِ تقویم باید ارزان باشد؛ یادگیری تا اولین پرسش عقب می‌افتد.

    این تست **تلاشِ خواندن** را می‌شمارد، نه استثنا را. دلیلش مهم است:
    `learn_from_client` عمداً هر خطایی را می‌بلعد (قطعی TSETMC نباید پاس
    رصد را بخواباند)، پس اگر منتظر استثنای شبکه بمانیم این تست هیچ‌وقت
    fail نمی‌شود. واقعاً امتحانش کردم — با یادگیریِ حریص هم پاس می‌شد،
    یعنی گاردِ بی‌اثر.
    """
    from bootstrap import build_market_data, build_trading_calendar

    settings = _offline_settings(tmp_path)
    reads: list[str] = []

    market_data = build_market_data(settings)
    original = type(market_data).get_history

    def _spy(self, symbol, days=90):
        reads.append(symbol)
        return original(self, symbol, days=days)

    monkeypatch_target = type(market_data)
    monkeypatch_target.get_history = _spy  # type: ignore[method-assign]
    try:
        build_trading_calendar(settings, market_data)
    finally:
        monkeypatch_target.get_history = original  # type: ignore[method-assign]

    assert reads == [], (
        f"ساختِ تقویم تاریخچه خواند ({reads}). یادگیری باید تا اولین "
        "پرسشِ واقعی عقب بیفتد."
    )


def test_weekend_is_answered_without_any_data(no_network, tmp_path):
    """آخرهفته قطعی است — نباید هیچ داده‌ای لازم داشته باشد."""
    from bootstrap import build_market_data, build_trading_calendar

    settings = _offline_settings(tmp_path)
    calendar = build_trading_calendar(settings, build_market_data(settings))

    assert calendar.is_holiday(date(2026, 9, 4))  # جمعه
    assert not calendar.is_open(datetime(2026, 9, 4, 10, 0))


def test_a_full_scan_pass_stays_offline(no_network, tmp_path):
    """پاس رصد کامل روی فیکسچر — همان مسیری که کاربر اجرا می‌کند."""
    from bootstrap import create_app
    from main import run_cycle

    context = create_app(_offline_settings(tmp_path), dry_run=True)
    try:
        run_cycle(context)
    finally:
        context.close()


def test_learning_failure_is_not_retried_forever(no_network, tmp_path):
    """اگر یادگیری شکست بخورد، هر پرسشِ بعدی نباید دوباره تلاش کند.

    وگرنه در نبودِ شبکه یک پاس رصد ساده دقیقه‌ها طول می‌کشید — دقیقاً
    همان چیزی که سوئیت را به ۱۰ دقیقه رساند.
    """
    from market.trading_calendar import TradingCalendar

    attempts = {"n": 0}

    def _failing(_calendar):
        attempts["n"] += 1
        raise RuntimeError("شبکه نیست")

    calendar = TradingCalendar()
    calendar.defer_learning(_failing)

    with pytest.raises(RuntimeError):
        calendar.is_holiday(date(2026, 9, 6))
    # تلاش دوم نباید اتفاق بیفتد
    calendar.is_holiday(date(2026, 9, 7))
    assert attempts["n"] == 1


# ======================================================================
# داشبورد
# ======================================================================
def test_dashboard_status_stays_offline(no_network, tmp_path, monkeypatch):
    """`/api/status` تقویم را می‌پرسد؛ نباید به شبکه برود."""
    pytest.importorskip("fastapi")
    yaml = pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from web import api as web_api

    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        yaml.safe_dump(_offline_settings(tmp_path), allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_api, "SETTINGS_PATH", settings_path)

    with TestClient(web_api.app) as client:
        body = client.get("/api/status").json()
    assert "market_open" in body


def test_regime_analysis_stays_offline(no_network, tmp_path, monkeypatch):
    """تحلیل وضعیت هم باید روی نمونه‌ی ضبط‌شده کار کند.

    این مسیر دو درخواستِ شبکه‌ی تازه آورد (تاریخچه‌ی شاخص و رویدادهای
    شرکتی). هر دو باید از تنظیمات قابل خاموش‌کردن باشند، وگرنه همان
    کندیِ بی‌صدای قبلی برمی‌گردد.
    """
    pytest.importorskip("fastapi")
    yaml = pytest.importorskip("yaml")
    from fastapi.testclient import TestClient

    from web import api as web_api

    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        yaml.safe_dump(_offline_settings(tmp_path), allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(web_api, "SETTINGS_PATH", settings_path)

    with TestClient(web_api.app) as client:
        body = client.get("/api/regime?underlyings=خودرو").json()

    assert body["enabled"] is True
    assert body["market"]["sessions_used"] > 0, "شاخص از نمونه خوانده شد"
    assert body["underlyings"]["خودرو"]["adjustment"] == "unknown"

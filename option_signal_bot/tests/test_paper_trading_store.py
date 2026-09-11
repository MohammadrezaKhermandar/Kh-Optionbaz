"""تست‌های لایه ماندگاری معاملات کاغذی."""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from storage.paper_trading_store import PaperTradingStore


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "paper_trading.db"
    with PaperTradingStore(db) as s:
        yield s


def _order(order_id="o1", symbol="ضخود7001", side="buy", qty=1, **overrides):
    row = {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "filled_quantity": qty,
        "avg_fill_price": 1000.0,
        "status": "filled",
        "fee_paid": 0.0,
        "signal_id": None,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "metadata": None,
    }
    row.update(overrides)
    return row


def test_account_does_not_exist_before_init(store):
    assert store.get_account() is None


def test_init_account_creates_it_once(store):
    first = store.init_account(1_000.0)
    assert first["cash"] == 1_000.0
    assert first["initial_balance"] == 1_000.0

    # فراخوانی دوم نباید موجودی را دوباره صفر کند
    second = store.init_account(9_999.0)
    assert second["cash"] == 1_000.0


def test_update_cash_persists(store):
    store.init_account(1_000.0)
    store.update_cash(750.0)
    assert store.get_account()["cash"] == 750.0


def test_save_and_get_order_roundtrip(store):
    store.save_order(_order())
    row = store.get_order("o1")
    assert row is not None
    assert row["symbol"] == "ضخود7001"
    assert row["status"] == "filled"


def test_save_order_is_idempotent_on_order_id(store):
    store.save_order(_order(status="pending"))
    store.save_order(_order(status="filled"))
    assert store.get_order("o1")["status"] == "filled"
    assert len(store.list_orders()) == 1


def test_list_orders_is_newest_first(store):
    store.save_order(_order("o1", created_at="2026-01-01T00:00:00"))
    store.save_order(_order("o2", created_at="2026-01-02T00:00:00"))
    ids = [row["order_id"] for row in store.list_orders()]
    assert ids == ["o2", "o1"]


def test_list_orders_respects_limit(store):
    for i in range(3):
        store.save_order(_order(f"o{i}", created_at=f"2026-01-0{i + 1}T00:00:00"))
    assert len(store.list_orders(limit=2)) == 2


def test_get_position_missing_returns_none(store):
    assert store.get_position("ضخود7001") is None


def test_upsert_position_creates_and_updates(store):
    store.upsert_position("ضخود7001", 2, 1000.0, "2026-01-01T00:00:00")
    row = store.get_position("ضخود7001")
    assert row["quantity"] == 2
    assert row["average_price"] == 1000.0

    store.upsert_position("ضخود7001", 5, 1200.0, "2026-01-01T00:00:00")
    row = store.get_position("ضخود7001")
    assert row["quantity"] == 5
    assert row["average_price"] == 1200.0


def test_upsert_position_with_non_positive_quantity_deletes_it(store):
    store.upsert_position("ضخود7001", 2, 1000.0, "2026-01-01T00:00:00")
    store.upsert_position("ضخود7001", 0, 0.0, "2026-01-01T00:00:00")
    assert store.get_position("ضخود7001") is None


def test_list_positions_is_sorted_by_symbol(store):
    store.upsert_position("ب", 1, 100.0, "2026-01-01T00:00:00")
    store.upsert_position("آ", 1, 100.0, "2026-01-01T00:00:00")
    symbols = [row["symbol"] for row in store.list_positions()]
    assert symbols == ["آ", "ب"]


def test_record_and_list_trades(store):
    store.record_trade(
        {
            "trade_id": "t1",
            "symbol": "ضخود7001",
            "quantity": 1,
            "entry_price": 1000.0,
            "exit_price": 1100.0,
            "fee_paid": 0.0,
            "pnl_absolute": 100.0,
            "pnl_pct": 10.0,
            "opened_at": "2026-01-01T00:00:00",
            "closed_at": "2026-01-02T00:00:00",
            "signal_id": None,
            "close_reason": "manual",
        }
    )
    trades = store.list_trades()
    assert len(trades) == 1
    assert trades[0]["pnl_pct"] == 10.0


def test_reset_clears_everything_and_restores_balance(store):
    store.init_account(1_000.0)
    store.save_order(_order())
    store.upsert_position("ضخود7001", 1, 1000.0, "2026-01-01T00:00:00")
    store.record_trade(
        {
            "trade_id": "t1",
            "symbol": "ضخود7001",
            "quantity": 1,
            "entry_price": 1000.0,
            "exit_price": 900.0,
            "fee_paid": 0.0,
            "pnl_absolute": -100.0,
            "pnl_pct": -10.0,
            "opened_at": "2026-01-01T00:00:00",
            "closed_at": "2026-01-02T00:00:00",
            "signal_id": None,
            "close_reason": "manual",
        }
    )

    account = store.reset(5_000.0)

    assert account["cash"] == 5_000.0
    assert account["initial_balance"] == 5_000.0
    assert account["reset_at"] is not None
    assert store.list_orders() == []
    assert store.list_positions() == []
    assert store.list_trades() == []


# ----------------------------------------------------------------------
# مهاجرت اسکیما — نسخه ۱ به ۲، بدون از دست رفتن داده
# ----------------------------------------------------------------------
_V1_SCHEMA = """
CREATE TABLE paper_account (
    id INTEGER PRIMARY KEY CHECK (id = 1), cash REAL NOT NULL,
    initial_balance REAL NOT NULL, created_at TEXT NOT NULL, reset_at TEXT
);
CREATE TABLE paper_orders (
    order_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
    quantity INTEGER NOT NULL, filled_quantity INTEGER NOT NULL DEFAULT 0,
    avg_fill_price REAL, status TEXT NOT NULL, fee_paid REAL NOT NULL DEFAULT 0,
    signal_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT
);
CREATE TABLE paper_positions (
    symbol TEXT PRIMARY KEY, quantity INTEGER NOT NULL, average_price REAL NOT NULL,
    opened_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE paper_trades (
    trade_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, quantity INTEGER NOT NULL,
    entry_price REAL NOT NULL, exit_price REAL NOT NULL, fee_paid REAL NOT NULL DEFAULT 0,
    pnl_absolute REAL NOT NULL, pnl_pct REAL NOT NULL, opened_at TEXT NOT NULL,
    closed_at TEXT NOT NULL, signal_id TEXT, close_reason TEXT NOT NULL
);
"""


def _legacy_db(path):
    """یک پایگاه نسخه‌ی ۱ با داده‌ی واقعیِ همان نسخه."""
    connection = sqlite3.connect(str(path))
    connection.executescript(_V1_SCHEMA)
    connection.execute(
        "INSERT INTO paper_account VALUES (1, 900.0, 1000.0, '2026-01-01T00:00:00', NULL)"
    )
    connection.execute(
        "INSERT INTO paper_positions VALUES ('ضخود7001', 3, 500.0,"
        " '2026-01-01T00:00:00', '2026-01-01T00:00:00')"
    )
    # نسخه‌ی ۱: `pnl_absolute` = ناخالص منهای کارمزد خروج، و `fee_paid`
    # فقط کارمزد خروج بود.
    connection.execute(
        "INSERT INTO paper_trades VALUES ('t1', 'ضخود7001', 2, 100.0, 150.0, 30.0,"
        " 70.0, 50.0, '2026-01-01T00:00:00', '2026-01-02T00:00:00', NULL, 'manual')"
    )
    connection.commit()
    connection.close()
    return path


def test_migration_keeps_existing_rows(tmp_path):
    """ارتقا نباید هیچ ردیفی را حذف یا بازنویسی کند."""
    path = _legacy_db(tmp_path / "legacy.db")

    store = PaperTradingStore(path)

    assert store.schema_version == 2
    assert store.get_account()["cash"] == 900.0
    assert store.get_account()["initial_balance"] == 1000.0
    position = store.get_position("ضخود7001")
    assert position["quantity"] == 3
    assert position["average_price"] == 500.0
    trades = store.list_trades()
    assert len(trades) == 1
    assert trades[0]["pnl_absolute"] == 70.0
    store.close()


def test_migration_rebuilds_what_is_derivable_and_leaves_the_rest_unknown(tmp_path):
    """ناخالص و کارمزد خروج بازسازی‌شدنی‌اند؛ کارمزد ورود نیست.

    `NULL` ماندنِ `entry_fee` عمدی است: صفر گذاشتنش یعنی ادعای اینکه
    هزینه‌ی ورودی نبوده، که دانسته نیست.
    """
    path = _legacy_db(tmp_path / "legacy.db")

    store = PaperTradingStore(path)
    trade = store.list_trades()[0]

    assert trade["exit_fee"] == 30.0, "کارمزد خروج همان fee_paid قدیمی است"
    assert trade["gross_pnl"] == 100.0, "۷۰ + ۳۰ = ناخالص"
    assert trade["entry_fee"] is None, "دانسته نیست، پس صفر هم نمی‌شود"
    assert store.get_position("ضخود7001")["entry_fees"] == 0.0
    store.close()


def test_migration_is_idempotent(tmp_path):
    """باز کردن دوباره‌ی همان فایل نباید چیزی را عوض کند."""
    path = _legacy_db(tmp_path / "legacy.db")
    PaperTradingStore(path).close()

    store = PaperTradingStore(path)
    assert store.schema_version == 2
    assert len(store.list_trades()) == 1
    assert store.get_account()["cash"] == 900.0
    store.close()


def test_a_fresh_database_starts_at_the_current_version(tmp_path):
    store = PaperTradingStore(tmp_path / "fresh.db")
    assert store.schema_version == 2
    store.close()


# ----------------------------------------------------------------------
# تراکنش
# ----------------------------------------------------------------------
def test_transaction_rolls_back_everything_on_failure(store):
    """شکست وسط عملیات نباید حسابِ نیمه‌تغییرکرده بگذارد."""
    store.init_account(1000.0)

    with pytest.raises(RuntimeError, match="بوم"), store.transaction():
        store.update_cash(500.0)
        store.upsert_position("ضخود7001", 1, 100.0, "2026-01-01T00:00:00")
        raise RuntimeError("بوم")

    assert store.get_account()["cash"] == 1000.0
    assert store.list_positions() == []


def test_transaction_commits_everything_on_success(store):
    store.init_account(1000.0)

    with store.transaction():
        store.update_cash(500.0)
        store.upsert_position("ضخود7001", 1, 100.0, "2026-01-01T00:00:00")

    assert store.get_account()["cash"] == 500.0
    assert len(store.list_positions()) == 1


def test_nested_transactions_commit_once_with_the_outer_block(store):
    """تراکنش تودرتو نباید زودتر از بلوک بیرونی commit کند."""
    store.init_account(1000.0)

    with pytest.raises(RuntimeError), store.transaction():
        with store.transaction():
            store.update_cash(500.0)
        raise RuntimeError("بیرونی شکست")

    assert store.get_account()["cash"] == 1000.0, "بلوک داخلی نباید جدا commit شده باشد"

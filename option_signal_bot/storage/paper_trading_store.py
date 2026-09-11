"""ذخیره‌ی حساب، سفارش، پوزیشن و معامله‌ی معاملات کاغذی در SQLite.

هدف: وضعیت حساب شبیه‌سازی‌شده (`PaperBroker`) بین اجراهای مختلف داشبورد
پایدار بماند. مثل `storage/signal_log.py`، بدون ORM — یک اسکیمای دستی و
یک کلاس نگه‌دارنده‌ی اتصال.

**تراکنش** (`transaction()`): یک عملیات مالی چند نوشتن دارد — نقد،
موقعیت، معامله. اگر وسطش چیزی بشکند و هر نوشتن جداگانه commit شده باشد،
حسابِ نیمه‌تغییرکرده می‌ماند: پول کم شده ولی موقعیتی ثبت نشده. با این
context manager همه‌ی نوشتن‌ها یک‌جا commit یا یک‌جا rollback می‌شوند.

**نسخه‌ی اسکیما ۲** — ستون‌های تازه‌ای اضافه شده تا هزینه‌ی ورود و خروج
از هم جدا بماند. مهاجرت **حافظِ داده** است: ستون‌ها با `ALTER TABLE`
اضافه می‌شوند و ردیف‌های موجود می‌مانند. چیزی که دانسته نیست (سهم کارمزد
ورودِ معاملات قدیمی) `NULL` می‌ماند و صفر **فرض نمی‌شود** — وگرنه
خالصِ آن معاملات بی‌سروصدا خوش‌بینانه می‌شد.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: نسخه‌ی جاری اسکیما. با هر تغییر ساختاری یکی بالا می‌رود.
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_account (
    id              INTEGER PRIMARY KEY CHECK (id = 1),
    cash            REAL NOT NULL,
    initial_balance REAL NOT NULL,
    created_at      TEXT NOT NULL,
    reset_at        TEXT
);

CREATE TABLE IF NOT EXISTS paper_orders (
    order_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    avg_fill_price  REAL,
    status          TEXT NOT NULL,
    fee_paid        REAL NOT NULL DEFAULT 0,
    signal_id       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol ON paper_orders(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_orders_status ON paper_orders(status);

CREATE TABLE IF NOT EXISTS paper_positions (
    symbol          TEXT PRIMARY KEY,
    quantity        INTEGER NOT NULL,
    average_price   REAL NOT NULL,
    opened_at       TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    entry_fees      REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS paper_trades (
    trade_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    entry_price     REAL NOT NULL,
    exit_price      REAL NOT NULL,
    fee_paid        REAL NOT NULL DEFAULT 0,
    pnl_absolute    REAL NOT NULL,
    pnl_pct         REAL NOT NULL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT NOT NULL,
    signal_id       TEXT,
    close_reason    TEXT NOT NULL,
    gross_pnl       REAL,
    entry_fee       REAL,
    exit_fee        REAL,
    return_on_cost_pct REAL,
    contract_size   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_closed_at ON paper_trades(closed_at);
"""

#: ستون‌هایی که در نسخه‌ی ۲ اضافه شده‌اند: (جدول، ستون، تعریف).
#: ترتیب مهم نیست؛ هرکدام که نباشد اضافه می‌شود.
_V2_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("paper_positions", "entry_fees", "REAL NOT NULL DEFAULT 0"),
    ("paper_trades", "gross_pnl", "REAL"),
    ("paper_trades", "entry_fee", "REAL"),
    ("paper_trades", "exit_fee", "REAL"),
    ("paper_trades", "return_on_cost_pct", "REAL"),
    ("paper_trades", "contract_size", "INTEGER"),
)


class PaperTradingStore:
    """لایه‌ی ماندگاری معاملات کاغذی، روی یک فایل SQLite مستقل.

    Args:
        db_path: مسیر فایل SQLite (پوشه‌اش در صورت نبود ساخته می‌شود)
    """

    def __init__(self, db_path: str | Path = "var/paper_trading.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.row_factory = sqlite3.Row
        self._depth = 0
        self._connection.executescript(_SCHEMA)
        self._migrate()
        self._connection.commit()

    # -- تراکنش ------------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[PaperTradingStore]:
        """همه‌ی نوشتن‌های داخل بلوک، یک‌جا commit یا یک‌جا rollback.

        تودرتو بودن پشتیبانی می‌شود و تراکنشِ بیرونی برنده است — SQLite
        تراکنش تودرتوی واقعی ندارد، پس commit زودهنگامِ بلوک داخلی
        تضمینِ بلوک بیرونی را می‌شکست.
        """
        if self._depth:
            self._depth += 1
            try:
                yield self
            finally:
                self._depth -= 1
            return

        self._depth = 1
        try:
            yield self
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()
        finally:
            self._depth = 0

    def _commit(self) -> None:
        """commit فقط وقتی داخل تراکنشِ صریح نیستیم."""
        if not self._depth:
            self._connection.commit()

    # -- مهاجرت ------------------------------------------------------------
    def _migrate(self) -> None:
        """ارتقای اسکیمای موجود **بدون از دست رفتن داده**."""
        version = self._schema_version()
        if version >= SCHEMA_VERSION:
            self._set_meta("schema_version", str(SCHEMA_VERSION))
            return

        added = 0
        for table, column, definition in _V2_COLUMNS:
            if column in self._columns(table):
                continue
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            added += 1

        # ردیف‌های قدیمی: `pnl_absolute` برابر «ناخالص منهای کارمزد خروج»
        # بود، پس ناخالص و کارمزد خروجشان بازسازی‌شدنی است. سهمِ کارمزد
        # ورود اما هرگز ثبت نشده بود و `NULL` می‌ماند — حدس زده نمی‌شود.
        self._connection.execute(
            """
            UPDATE paper_trades
               SET exit_fee  = COALESCE(exit_fee, fee_paid),
                   gross_pnl = COALESCE(gross_pnl, pnl_absolute + fee_paid)
             WHERE exit_fee IS NULL OR gross_pnl IS NULL
            """
        )
        self._set_meta("schema_version", str(SCHEMA_VERSION))
        if added or version:
            logger.info(
                "اسکیمای معاملات کاغذی از نسخه %s به %s ارتقا یافت (%s ستون تازه)؛ "
                "داده‌ی موجود دست نخورد.",
                version or 1, SCHEMA_VERSION, added,
            )

    def _columns(self, table: str) -> set[str]:
        return {row[1] for row in self._connection.execute(f"PRAGMA table_info({table})")}

    def _schema_version(self) -> int:
        """نسخه‌ی اسکیمای فایل موجود. صفر یعنی پایگاهِ تازه."""
        row = self._connection.execute(
            "SELECT value FROM paper_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is not None:
            return int(row[0])
        # پایگاهِ نسخه‌ی ۱ جدول meta نداشت؛ وجود داشتنِ داده یعنی قدیمی است.
        has_rows = self._connection.execute(
            "SELECT 1 FROM paper_account LIMIT 1"
        ).fetchone()
        return 1 if has_rows else 0

    def _set_meta(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO paper_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    @property
    def schema_version(self) -> int:
        return self._schema_version()

    # -- حساب --------------------------------------------------------------
    def get_account(self) -> dict[str, Any] | None:
        """وضعیت فعلی حساب کاغذی؛ `None` اگر هنوز مقداردهی اولیه نشده."""
        row = self._connection.execute(
            "SELECT cash, initial_balance, created_at, reset_at FROM paper_account WHERE id = 1"
        ).fetchone()
        return dict(row) if row else None

    def init_account(self, initial_balance: float) -> dict[str, Any]:
        """مقداردهی اولیه‌ی حساب، فقط اگر هنوز وجود نداشته باشد (idempotent)."""
        existing = self.get_account()
        if existing is not None:
            return existing
        now = datetime.now().isoformat(timespec="seconds")
        self._connection.execute(
            """
            INSERT INTO paper_account (id, cash, initial_balance, created_at, reset_at)
            VALUES (1, ?, ?, ?, NULL)
            """,
            (initial_balance, initial_balance, now),
        )
        self._commit()
        return self.get_account()  # type: ignore[return-value]

    def update_cash(self, cash: float) -> None:
        self._connection.execute("UPDATE paper_account SET cash = ? WHERE id = 1", (cash,))
        self._commit()

    # -- سفارش‌ها ------------------------------------------------------------
    def save_order(self, order: dict[str, Any]) -> None:
        """ثبت یک سفارش جدید (idempotent روی order_id)."""
        self._connection.execute(
            """
            INSERT OR REPLACE INTO paper_orders (
                order_id, symbol, side, quantity, filled_quantity, avg_fill_price,
                status, fee_paid, signal_id, created_at, updated_at, metadata
            ) VALUES (
                :order_id, :symbol, :side, :quantity, :filled_quantity, :avg_fill_price,
                :status, :fee_paid, :signal_id, :created_at, :updated_at, :metadata
            )
            """,
            order,
        )
        self._commit()

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM paper_orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_orders(self, limit: int | None = None) -> list[dict[str, Any]]:
        """سفارش‌ها از جدید به قدیم."""
        query = "SELECT * FROM paper_orders ORDER BY created_at DESC"
        if limit:
            query += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._connection.execute(query)]

    # -- پوزیشن‌ها -----------------------------------------------------------
    def get_position(self, symbol: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM paper_positions WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_position(
        self,
        symbol: str,
        quantity: int,
        average_price: float,
        opened_at: str,
        entry_fees: float = 0.0,
    ) -> None:
        """ثبت یا به‌روزرسانی پوزیشن. `quantity <= 0` یعنی صاف‌شده — حذف می‌شود.

        `entry_fees` کارمزدِ ورودِ **پرداخت‌شده‌ی** همین تعدادِ باقی‌مانده
        است. با هر خروج جزئی باید به نسبت کم شود، وگرنه هزینه‌ای که
        سهمش به معامله‌ی بسته‌شده رفته، دوباره روی موقعیت هم می‌ماند.
        """
        if quantity <= 0:
            self.delete_position(symbol)
            return
        now = datetime.now().isoformat(timespec="seconds")
        self._connection.execute(
            """
            INSERT INTO paper_positions (
                symbol, quantity, average_price, opened_at, updated_at, entry_fees
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                quantity = excluded.quantity,
                average_price = excluded.average_price,
                updated_at = excluded.updated_at,
                entry_fees = excluded.entry_fees
            """,
            (symbol, quantity, average_price, opened_at, now, entry_fees),
        )
        self._commit()

    def delete_position(self, symbol: str) -> None:
        self._connection.execute("DELETE FROM paper_positions WHERE symbol = ?", (symbol,))
        self._commit()

    def list_positions(self) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT * FROM paper_positions ORDER BY symbol"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- معاملات بسته‌شده -----------------------------------------------------
    def record_trade(self, trade: dict[str, Any]) -> None:
        """ثبت یک معامله‌ی بسته‌شده.

        کلیدهای نسخه‌ی ۲ (`gross_pnl`، `entry_fee`، `exit_fee`،
        `return_on_cost_pct`، `contract_size`) اختیاری‌اند تا فراخوان‌های
        قدیمی نشکنند؛ نبودشان `NULL` ثبت می‌شود، نه صفر.
        """
        row = {
            "gross_pnl": None,
            "entry_fee": None,
            "exit_fee": None,
            "return_on_cost_pct": None,
            "contract_size": None,
            **trade,
        }
        self._connection.execute(
            """
            INSERT INTO paper_trades (
                trade_id, symbol, quantity, entry_price, exit_price, fee_paid,
                pnl_absolute, pnl_pct, opened_at, closed_at, signal_id, close_reason,
                gross_pnl, entry_fee, exit_fee, return_on_cost_pct, contract_size
            ) VALUES (
                :trade_id, :symbol, :quantity, :entry_price, :exit_price, :fee_paid,
                :pnl_absolute, :pnl_pct, :opened_at, :closed_at, :signal_id, :close_reason,
                :gross_pnl, :entry_fee, :exit_fee, :return_on_cost_pct, :contract_size
            )
            """,
            row,
        )
        self._commit()

    def list_trades(self, days: int | None = None) -> list[dict[str, Any]]:
        """معاملات بسته‌شده، قدیم به جدید (ترتیب لازم برای منحنی تجمعی)."""
        query = "SELECT * FROM paper_trades"
        params: tuple[Any, ...] = ()
        if days is not None:
            since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
            query += " WHERE closed_at >= ?"
            params = (since,)
        query += " ORDER BY closed_at ASC"
        return [dict(r) for r in self._connection.execute(query, params)]

    # -- ریست ---------------------------------------------------------------
    def reset(self, initial_balance: float) -> dict[str, Any]:
        """پاک‌کردن کامل سفارش/پوزیشن/معامله و بازگرداندن موجودی به مقدار اولیه."""
        now = datetime.now().isoformat(timespec="seconds")
        with self.transaction():
            self._connection.execute("DELETE FROM paper_orders")
            self._connection.execute("DELETE FROM paper_positions")
            self._connection.execute("DELETE FROM paper_trades")
            self._connection.execute(
                """
                INSERT INTO paper_account (id, cash, initial_balance, created_at, reset_at)
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    cash = excluded.cash,
                    initial_balance = excluded.initial_balance,
                    reset_at = excluded.reset_at
                """,
                (initial_balance, initial_balance, now, now),
            )
        return self.get_account()  # type: ignore[return-value]

    # -- چرخه‌ی عمر -----------------------------------------------------------
    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> PaperTradingStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

"""ضبط پاسخ **واقعی** TSETMC برای تست آفلاین.

این پروژه داده‌ی ساختگی ندارد. تست‌ها روی همان پاسخی اجرا می‌شوند که
بازار واقعاً داده — فقط یک بار ضبط شده تا CI به شبکه و ساعت بازار
وابسته نباشد.

اجرا (ترجیحاً در ساعت بازار، ولی بعدش هم کار می‌کند):

    .venv\\Scripts\\python.exe scripts/record_fixtures.py
    .venv\\Scripts\\python.exe scripts/record_fixtures.py --symbols خودرو شستا
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import force_utf8_stdio
from data.tsetmc_http import fetch_json
from data.tsetmc_index_client import (
    INDEX_HISTORY_URL,
    INDEX_KEY,
    TSE_ALL_SHARE_INS_CODE,
    TSE_ALL_SHARE_LABEL,
)
from data.tsetmc_market_data_client import DAILY_HISTORY_URL
from data.tsetmc_option_chain_client import (
    HttpPayloadSource,
    TsetmcOptionChainClient,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures"
HISTORY_DIR = FIXTURE_DIR / "history"
INDEX_DIR = FIXTURE_DIR / "index"

#: تاریخچه‌ی شاخص از ۲۰۰۸ شروع می‌شود (هزاران ردیف) و کلش برای تست لازم
#: نیست. همان اندازه‌ای نگه داشته می‌شود که TSETMC برای سهم می‌دهد.
INDEX_SESSIONS = 400

#: نمادهایی که تست‌ها استفاده می‌کنند؛ نقدشونده و همیشه آپشن دارند
DEFAULT_SYMBOLS = ("خودرو", "شستا", "اهرم")


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()

    parser = argparse.ArgumentParser(description="ضبط پاسخ واقعی TSETMC برای تست")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    parser.add_argument("--market", type=int, default=0)
    args = parser.parse_args(argv)

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    # ۱) دیده‌بان کامل بازار آپشن — یک درخواست، همه‌ی نمادها
    print("دریافت دیده‌بان بازار آپشن...")
    source = HttpPayloadSource(market=args.market)
    payload = source.fetch()
    chain_path = FIXTURE_DIR / "tsetmc_option_market_watch.json"
    chain_path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    rows = payload.get("instrumentOptMarketWatch") or []
    print(f"  ذخیره شد: {chain_path.name}  ({len(rows)} ردیف استرایک)")

    # ۲) تاریخچه‌ی هر نماد پایه
    client = TsetmcOptionChainClient(source)
    from data.tsetmc_market_data_client import TsetmcMarketDataClient

    market_data = TsetmcMarketDataClient(source)

    print("\nدریافت تاریخچه نمادهای پایه...")
    saved = 0
    for symbol in args.symbols:
        try:
            ins_code = market_data.resolve_ins_code(symbol)
        except Exception as exc:
            print(f"  {symbol}: رد شد ({exc})")
            continue

        history = fetch_json(
            DAILY_HISTORY_URL.format(ins_code=ins_code),
            label=f"تاریخچه {symbol}",
        )
        out = HISTORY_DIR / f"{ins_code}.json"
        out.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
        count = len(history.get("closingPriceDaily") or [])
        print(f"  {symbol:10} → {out.name}  ({count} کندل)")
        saved += 1

    # ۳) تاریخچه‌ی شاخص کل — «بازار» در تحلیل وضعیت
    print("\nدریافت تاریخچه شاخص کل...")
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    try:
        index_payload = fetch_json(
            INDEX_HISTORY_URL.format(ins_code=TSE_ALL_SHARE_INS_CODE),
            label=f"تاریخچه {TSE_ALL_SHARE_LABEL}",
        )
        rows = index_payload.get(INDEX_KEY) or []
        trimmed = {INDEX_KEY: rows[-INDEX_SESSIONS:]}
        index_path = INDEX_DIR / f"{TSE_ALL_SHARE_INS_CODE}.json"
        index_path.write_text(
            json.dumps(trimmed, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"  {TSE_ALL_SHARE_LABEL} → {index_path.name} "
            f"({len(trimmed[INDEX_KEY])} جلسه از {len(rows)})"
        )
    except Exception as exc:  # نبودِ شاخص نباید ضبطِ بقیه را باطل کند
        print(f"  شاخص کل ضبط نشد: {exc}")

    print(f"\n{saved} نماد ضبط شد. حالا تست‌ها روی داده‌ی واقعی اجرا می‌شوند.")
    print(f"نمادهای موجود در بازار: {len(client.available_underlyings())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

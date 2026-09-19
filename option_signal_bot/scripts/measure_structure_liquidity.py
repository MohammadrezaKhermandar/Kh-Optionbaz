"""اندازه‌گیریِ اجراپذیریِ **ساختارها** روی زنجیره‌ی اختیار.

**سؤالی که جواب می‌دهد**

«چند درصد از فرصت‌های هر خانواده‌ی استراتژی اصلاً قابل اجرا هستند؟»
ساختاری که دو یا سه پایه دارد، وقتی قابل اجراست که **همه‌ی پایه‌هایش
هم‌زمان** اجراپذیر باشند — و چون باید بشود از آن **خارج** هم شد، هر
پایه در هر دو سمت بررسی می‌شود.

این عدد پایه‌ی تصمیمِ `docs/strategy-selection.md` است: کدام خانواده در
پیشنهادهای فعال بماند و کدام کنار برود.

اجرا:

    python scripts/measure_structure_liquidity.py                 # زنده
    python scripts/measure_structure_liquidity.py tests/fixtures/tsetmc_option_market_watch.json

⚠️ خروجی **عکسِ یک لحظه** است. در ساعت بازار و در روزِ معاملاتی اجرا
کنید، وگرنه چیزی که می‌بینید ماندهٔ جلسه‌ی قبل است.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import force_utf8_stdio
from data.tsetmc_option_chain_client import (
    FilePayloadSource,
    HttpPayloadSource,
    TsetmcOptionChainClient,
)

#: اندازه‌های سفارشی که گزارش می‌شوند (تعداد قرارداد).
SIZES = (1, 5, 10, 50)


def _executable(contract, side: str, size: int) -> bool:
    """آیا سطح اولِ این سمت برای این اندازه جا دارد؟

    عمداً فقط سطح اول: این یک **کفِ** محافظه‌کارانه است، نه برآوردِ
    کاملِ عمق. دفترِ چندسطحی فقط برای نمادِ تک‌تک در دسترس است و
    کشیدنش برای ۱۴۰۰ قرارداد یعنی ۱۴۰۰ درخواست.
    """
    price = contract.ask if side == "buy" else contract.bid
    quantity = contract.ask_quantity if side == "buy" else contract.bid_quantity
    return bool(price) and quantity is not None and quantity >= size


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = list(argv if argv is not None else sys.argv[1:])
    source = FilePayloadSource(Path(args[0])) if args else HttpPayloadSource()
    client = TsetmcOptionChainClient(source)

    contracts = []
    for symbol in client.available_underlyings():
        try:
            contracts.extend(client.get_chain(symbol).contracts)
        except Exception as exc:  # یک نماد خراب نباید کلِ اندازه‌گیری را ببندد
            print(f"  {symbol}: رد شد ({exc})")
    if not contracts:
        print("هیچ قراردادی خوانده نشد.")
        return 1

    two_sided = [c for c in contracts if c.bid and c.ask]
    print(f"قراردادها: {len(contracts)} · هر دو مظنه: {len(two_sided)} "
          f"({len(two_sided) / len(contracts):.0%})")

    spreads = sorted(
        (c.ask - c.bid) / ((c.ask + c.bid) / 2) * 100 for c in two_sided
    )
    if spreads:
        def at(fraction: float) -> float:
            return spreads[min(int(len(spreads) * fraction), len(spreads) - 1)]

        print(f"اسپرد نسبی — چارک اول {at(0.25):.1f}٪ · میانه {at(0.5):.1f}٪ "
              f"· چارک سوم {at(0.75):.1f}٪ · صدک ۹۰ {at(0.9):.1f}٪")
        print("  (هر عدد نسبت به میانه‌ی مظنه‌ی **همان قرارداد** است. "
              "درصدِ پایه‌ها با هم جمع نمی‌شود: هزینه‌ی یک ساختار یعنی "
              "مجموع هزینه‌ی ریالیِ پایه‌ها ÷ مجموع پرمیومِ پرداختیِ آن‌ها.)")

    # جفت‌های کال/پوتِ هم‌استرایک (برای استردل) و استرایک‌های مجاور
    # (برای اسپرد عمودی).
    by_strike: dict[tuple, dict] = defaultdict(dict)
    for contract in contracts:
        by_strike[(contract.underlying, contract.expiry, contract.strike)][
            contract.option_type
        ] = contract
    by_expiry: dict[tuple, list] = defaultdict(list)
    for (underlying, expiry, strike), legs in by_strike.items():
        by_expiry[(underlying, expiry)].append((strike, legs))

    adjacent = sum(
        max(len([1 for s, legs in strikes if "call" in legs]) - 1, 0)
        for strikes in by_expiry.values()
    )
    # ⚠️ مخرجِ استردل فقط استرایک‌هایی است که **هر دو پایه** را دارند.
    # شمردنِ همه‌ی استرایک‌ها (حتی آن‌هایی که فقط کال یا فقط پوت دارند)
    # مخرج را باد می‌کند و نسبت را کوچک‌تر از واقع نشان می‌دهد.
    straddle_candidates = sum(
        1 for legs in by_strike.values() if "call" in legs and "put" in legs
    )
    print(f"استرایک‌ها: {len(by_strike)} · با هر دو پایه (نامزدِ استردل): "
          f"{straddle_candidates} · جفتِ عمودیِ مجاور: {adjacent}")

    print("\nاندازه | خریدِ تک‌پایه | ورود+خروجِ تک‌پایه | استردل (۲ پایه) | اسپرد عمودی")
    for size in SIZES:
        entry = sum(1 for c in contracts if _executable(c, "buy", size))
        round_trip = sum(
            1 for c in contracts
            if _executable(c, "buy", size) and _executable(c, "sell", size)
        )
        straddle = sum(
            1 for legs in by_strike.values()
            if "call" in legs and "put" in legs
            and all(
                _executable(legs[kind], side, size)
                for kind in ("call", "put")
                for side in ("buy", "sell")
            )
        )
        vertical = 0
        for strikes in by_expiry.values():
            calls = sorted(
                (strike, legs["call"]) for strike, legs in strikes if "call" in legs
            )
            for i in range(len(calls) - 1):
                low, high = calls[i][1], calls[i + 1][1]
                if all(
                    _executable(leg, side, size)
                    for leg in (low, high)
                    for side in ("buy", "sell")
                ):
                    vertical += 1

        total = len(contracts)
        pairs = max(straddle_candidates, 1)
        print(
            f"{size:>5} | {entry:>5} ({entry / total:>4.0%}) | "
            f"{round_trip:>6} ({round_trip / total:>4.0%}) | "
            f"{straddle:>6} ({straddle / pairs:>4.0%}) | "
            f"{vertical:>5} ({vertical / max(adjacent, 1):>4.0%})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

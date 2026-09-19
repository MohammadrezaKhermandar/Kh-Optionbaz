"""تاریخچه‌ی روزانه‌ی **شاخص** از TSETMC — برای سنجشِ وضعیتِ کلِ بازار.

**چرا جدا از `tsetmc_market_data_client`**

شاخص در دیده‌بانِ بازار آپشن نیست، پس `resolve_ins_code` پیدایش نمی‌کند؛
و endpointاش هم فرق دارد:

    GET https://cdn.tsetmc.com/api/Index/GetIndexB2History/{insCode}

پاسخ: `indexB2[]` با `dEven` (تاریخ شمسی‌شده‌ی میلادی به شکل YYYYMMDD)،
`xNivInuClMresIbs` (پایانی)، `xNivInuPbMresIbs` (کف) و
`xNivInuPhMresIbs` (سقف). **بازِ روز را نمی‌دهد** — پس اینجا هم ساخته
نمی‌شود.

**تعدیل قیمت اینجا موضوع نیست.** شاخص خودش سریِ سطح است و افزایش
سرمایه‌ی تک‌سهم‌ها در ساختِ خودش لحاظ شده؛ چیزی برای تعدیل‌کردن نداریم.
برای **سهم** داستان فرق می‌کند و `market/corporate_actions.py` همان را
انجام می‌دهد.

مثل بقیه‌ی منابع این پروژه: بدون احراز هویت، و با `history_dir` می‌شود
همین پاسخ را از فایلِ ضبط‌شده خواند تا تست به شبکه گره نخورد.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from data.tsetmc_http import DEFAULT_USER_AGENT, fetch_json
from data.tsetmc_market_data_client import parse_tsetmc_date

logger = logging.getLogger(__name__)

INDEX_HISTORY_URL = "https://cdn.tsetmc.com/api/Index/GetIndexB2History/{ins_code}"
INDEX_KEY = "indexB2"

#: شاخص کل بورس تهران. پیش‌فرضِ «بازار» در این پروژه.
TSE_ALL_SHARE_INS_CODE = "32097828799138957"
TSE_ALL_SHARE_LABEL = "شاخص کل بورس تهران"


@dataclass(frozen=True)
class IndexPoint:
    """یک روزِ شاخص. `open` عمداً نیست چون منبع نمی‌دهد."""

    date: date
    close: float
    low: float | None = None
    high: float | None = None


class TsetmcIndexClient:
    """تاریخچه‌ی روزانه‌ی شاخص، از شبکه یا از فایلِ ضبط‌شده."""

    def __init__(
        self,
        timeout: float = 20.0,
        retries: int = 2,
        history_dir: str | Path | None = None,
        ttl_seconds: float = 900.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.history_dir = Path(history_dir) if history_dir else None
        self.ttl_seconds = ttl_seconds
        self.user_agent = user_agent
        self._cache: dict[str, tuple[float, list[IndexPoint]]] = {}

    # ------------------------------------------------------------------
    def get_history(
        self,
        ins_code: str = TSE_ALL_SHARE_INS_CODE,
        days: int = 120,
        label: str = TSE_ALL_SHARE_LABEL,
    ) -> list[IndexPoint]:
        """آخرین `days` روزِ شاخص، مرتب از قدیم به جدید.

        پاسخِ منبع از قدیم به جدید است و کلِ تاریخ شاخص را می‌دهد
        (هزاران ردیف)، پس برش داده و کش می‌شود.
        """
        now = time.monotonic()
        cached = self._cache.get(ins_code)
        if cached and now - cached[0] < self.ttl_seconds and len(cached[1]) >= days:
            return cached[1][-days:]

        payload = self._fetch(ins_code, label)
        rows = payload.get(INDEX_KEY)
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"تاریخچه‌ای برای «{label}» برنگشت.")

        points = [p for p in (self._to_point(r) for r in rows) if p is not None]
        points.sort(key=lambda p: p.date)
        self._cache[ins_code] = (now, points)
        logger.info("تاریخچه %s دریافت شد: %s روز.", label, len(points))
        return points[-days:]

    # ------------------------------------------------------------------
    def _fetch(self, ins_code: str, label: str) -> dict[str, Any]:
        """پاسخ خام — از شبکه، یا از فایلِ ضبط‌شده اگر مسیرش داده شده باشد."""
        if self.history_dir is not None:
            path = self.history_dir / f"{ins_code}.json"
            if not path.exists():
                raise FileNotFoundError(
                    f"تاریخچه‌ی ضبط‌شده‌ی {label} (کد {ins_code}) نیست: {path}\n"
                    "با scripts/record_fixtures.py ضبطش کنید."
                )
            return json.loads(path.read_text(encoding="utf-8"))

        return fetch_json(
            INDEX_HISTORY_URL.format(ins_code=ins_code),
            timeout=self.timeout,
            retries=self.retries,
            user_agent=self.user_agent,
            label=f"تاریخچه {label}",
        )

    @staticmethod
    def _to_point(row: dict[str, Any]) -> IndexPoint | None:
        """یک ردیف را به نقطه تبدیل می‌کند؛ ردیفِ ناقص را رد می‌کند."""
        try:
            close = float(row["xNivInuClMresIbs"])
            if close <= 0:
                return None
            low = row.get("xNivInuPbMresIbs")
            high = row.get("xNivInuPhMresIbs")
            return IndexPoint(
                date=parse_tsetmc_date(row["dEven"]),
                close=close,
                low=float(low) if low else None,
                high=float(high) if high else None,
            )
        except (KeyError, TypeError, ValueError):
            return None

"""کلاینت واقعی زنجیره آپشن بورس تهران از API عمومی TSETMC.

endpoint (تأییدشده، بدون احراز هویت و بدون کوکی):
    https://cdn.tsetmc.com/api/Instrument/GetInstrumentOptionMarketWatch/{market}
    market: 0 = همه، 1 = بورس، 2 = فرابورس

هر ردیف پاسخ = یک استرایک با جفت نماد کال و پوت، به‌علاوه قیمت لحظه‌ای نماد پایه.
یعنی کل زنجیره بازار با **یک درخواست** می‌آید، نه یکی به‌ازای هر نماد.

نگاشت فیلدها (پسوند `_C` کال، `_P` پوت، `_UA` نماد پایه):

    strikePrice      → قیمت اعمال          endDate        → سررسید (YYYYMMDD میلادی)
    lVal18AFC_C/P    → نماد آپشن           lval30_UA      → نماد پایه
    pMeDem_C/P       → بهترین مظنه خرید    pMeOf_C/P      → بهترین مظنه فروش
    pDrCotVal_C/P    → آخرین معامله        pClosing_C/P   → قیمت پایانی
    oP_C/P           → موقعیت‌های باز      qTotTran5J_C/P → حجم معاملات
    contractSize     → اندازه قرارداد      pDrCotVal_UA   → قیمت لحظه‌ای پایه
    remainedDay      → روز تا سررسید       insCode_C/P    → کد یکتای نماد

همه قیمت‌ها ریال هستند. مقدار صفر در فیلد مظنه یعنی «مظنه‌ای وجود ندارد»، نه قیمت صفر.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from data.option_chain_client import (
    DEFAULT_CONTRACT_SIZE,
    OptionChain,
    OptionChainClient,
    OptionContract,
)
from data.tsetmc_http import DEFAULT_USER_AGENT, fetch_json

logger = logging.getLogger(__name__)

OPTION_WATCH_URL = (
    "https://cdn.tsetmc.com/api/Instrument/GetInstrumentOptionMarketWatch/{market}"
)
PAYLOAD_KEY = "instrumentOptMarketWatch"


# ----------------------------------------------------------------------
# قواعد کیفیت داده
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class DataQualityRules:
    """گیت‌هایی که قراردادهای غیرقابل‌معامله را قبل از رسیدن به استراتژی حذف می‌کنند.

    بازار واقعی پر از نمادهای مرده است (بدون مظنه، بدون موقعیت باز، اسپرد ۱۰۰٪).
    بدون این گیت‌ها استراتژی روی قیمت‌های بی‌معنا سیگنال صادر می‌کند.
    """

    #: حداقل یک سمت مظنه یا یک قیمت معامله لازم است
    require_quote: bool = True
    #: حداکثر اسپرد نسبی مجاز ((ask-bid)/mid)؛ None = بدون محدودیت
    max_relative_spread: float | None = 0.5
    #: حذف نمادهای بدون موقعیت باز
    drop_zero_open_interest: bool = False
    #: حذف سررسیدهای نزدیک‌تر از این تعداد روز
    min_days_to_expiry: int = 0


@dataclass
class ChainQualityReport:
    """شمارش این‌که چند قرارداد و به چه دلیل حذف شد — هیچ حذفی بی‌صدا نیست."""

    total: int = 0
    accepted: int = 0
    dropped_no_quote: int = 0
    dropped_wide_spread: int = 0
    dropped_zero_open_interest: int = 0
    dropped_near_expiry: int = 0

    @property
    def dropped(self) -> int:
        return self.total - self.accepted

    def summary(self) -> str:
        return (
            f"{self.accepted}/{self.total} قرارداد پذیرفته شد "
            f"(بدون مظنه: {self.dropped_no_quote}، اسپرد پهن: {self.dropped_wide_spread}، "
            f"بدون موقعیت باز: {self.dropped_zero_open_interest}، "
            f"سررسید نزدیک: {self.dropped_near_expiry})"
        )


# ----------------------------------------------------------------------
# منبع پاسخ خام (HTTP یا فایل ضبط‌شده)
# ----------------------------------------------------------------------
class PayloadSource(ABC):
    """منبع پاسخ خام TSETMC. تزریق‌پذیر است تا تست‌ها بدون شبکه اجرا شوند."""

    source_name: str = "unknown"

    @abstractmethod
    def fetch(self) -> dict[str, Any]:
        """پاسخ خام (دیکشنری JSON) را برمی‌گرداند."""


class HttpPayloadSource(PayloadSource):
    """دریافت زنده از cdn.tsetmc.com با تلاش مجدد و backoff."""

    source_name = "tsetmc"

    def __init__(
        self,
        market: int = 0,
        timeout: int = 15,
        retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.market = market
        self.timeout = timeout
        self.retries = max(retries, 1)
        self.user_agent = user_agent

    def fetch(self) -> dict[str, Any]:
        return fetch_json(
            OPTION_WATCH_URL.format(market=self.market),
            timeout=self.timeout,
            retries=self.retries,
            user_agent=self.user_agent,
            label="زنجیره آپشن TSETMC",
        )


class FilePayloadSource(PayloadSource):
    """پخش مجدد یک پاسخ ضبط‌شده — برای تست آفلاین و بازتولید یک روز خاص."""

    source_name = "fixture"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self) -> dict[str, Any]:
        if not self.path.exists():
            raise FileNotFoundError(f"فایل نمونه زنجیره آپشن پیدا نشد: {self.path}")
        return json.loads(self.path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------
# کمکی‌ها
# ----------------------------------------------------------------------
def parse_tsetmc_date(value: str | int) -> date:
    """تبدیل تاریخ میلادی فشرده TSETMC (مثل `20260819`) به `date`."""
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise ValueError(f"تاریخ TSETMC نامعتبر است: {value!r}")
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def _optional_int(value: Any) -> int | None:
    """عدد صحیح، یا `None` وقتی منبع اصلاً چیزی نداده.

    برخلاف `_positive`، صفر اینجا **مقدارِ معتبر** است: «امروز هیچ
    معامله‌ای نشد» با «نمی‌دانیم چند معامله شد» یکی نیست، و غربالگر
    روی همین تفاوت تصمیم می‌گیرد.
    """
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive(value: Any) -> float | None:
    """صفر یا None در فیلد مظنه یعنی «مظنه‌ای نیست»، نه قیمت صفر."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# ----------------------------------------------------------------------
# کلاینت
# ----------------------------------------------------------------------
class TsetmcOptionChainClient(OptionChainClient):
    """زنجیره واقعی آپشن از TSETMC، با کش کوتاه و گیت‌های کیفیت داده.

    Args:
        source: منبع پاسخ خام (`HttpPayloadSource` یا `FilePayloadSource`)
        cache_ttl_seconds: تا این مدت پاسخ قبلی استفاده می‌شود. چون یک درخواست
            کل بازار را می‌دهد، همه نمادهای یک پاس از همین کش تغذیه می‌شوند.
        quality: گیت‌های حذف قرارداد بی‌کیفیت
    """

    def __init__(
        self,
        source: PayloadSource,
        cache_ttl_seconds: float = 20.0,
        quality: DataQualityRules | None = None,
    ) -> None:
        self.source = source
        self.cache_ttl_seconds = cache_ttl_seconds
        self.quality = quality or DataQualityRules()
        self._rows: list[dict[str, Any]] = []
        self._fetched_at: float | None = None
        self._last_report = ChainQualityReport()

    @property
    def source_name(self) -> str:
        return self.source.source_name

    @property
    def last_quality_report(self) -> ChainQualityReport:
        return self._last_report

    # ------------------------------------------------------------------
    def _fresh_rows(self) -> list[dict[str, Any]]:
        """ردیف‌های زنجیره، با احترام به TTL کش."""
        now = time.monotonic()
        if (
            self._rows
            and self._fetched_at is not None
            and now - self._fetched_at < self.cache_ttl_seconds
        ):
            return self._rows

        payload = self.source.fetch()
        rows = payload.get(PAYLOAD_KEY)
        if not isinstance(rows, list):
            raise ValueError(
                f"ساختار پاسخ TSETMC غیرمنتظره است؛ کلید «{PAYLOAD_KEY}» پیدا نشد."
            )
        self._rows = rows
        self._fetched_at = now
        logger.info("زنجیره آپشن دریافت شد: %s ردیف استرایک.", len(rows))
        return rows

    def refresh(self) -> None:
        """بی‌اعتبار کردن کش (اجبار به دریافت مجدد در فراخوان بعدی)."""
        self._fetched_at = None

    def available_underlyings(self) -> list[str]:
        """نمادهای پایه‌ای که واقعاً آپشن دارند."""
        names = {str(row.get("lval30_UA", "")).strip() for row in self._fresh_rows()}
        return sorted(names - {""})

    # ------------------------------------------------------------------
    def get_chain(self, underlying: str) -> OptionChain:
        target = underlying.strip()
        rows = [
            row
            for row in self._fresh_rows()
            if str(row.get("lval30_UA", "")).strip() == target
        ]
        if not rows:
            raise ValueError(
                f"نماد پایه «{underlying}» در بازار آپشن پیدا نشد. "
                f"نمادهای دارای آپشن: {', '.join(self.available_underlyings())}"
            )

        spot = (
            _positive(rows[0].get("pDrCotVal_UA"))
            or _positive(rows[0].get("pClosing_UA"))
            or 0.0
        )
        today = date.today()
        report = ChainQualityReport()
        contracts: list[OptionContract] = []

        for row in rows:
            for side in ("C", "P"):
                report.total += 1
                try:
                    contract = self._build_contract(row, side)
                except (KeyError, ValueError) as exc:
                    logger.debug("ردیف نامعتبر در زنجیره %s رد شد: %s", underlying, exc)
                    continue
                if self._accept(contract, report, today):
                    report.accepted += 1
                    contracts.append(contract)

        self._last_report = report
        logger.info("زنجیره %s: %s", underlying, report.summary())
        return OptionChain(
            underlying=target,
            spot_price=spot,
            as_of=datetime.now(),
            contracts=tuple(contracts),
        )

    def get_contract(self, option_symbol: str) -> OptionContract | None:
        target = option_symbol.strip()
        for row in self._fresh_rows():
            for side in ("C", "P"):
                if str(row.get(f"lVal18AFC_{side}", "")).strip() == target:
                    return self._build_contract(row, side)
        return None

    # ------------------------------------------------------------------
    def _build_contract(self, row: dict[str, Any], side: str) -> OptionContract:
        """ساخت `OptionContract` از یک ردیف خام و یک سمت (کال یا پوت)."""
        symbol = str(row[f"lVal18AFC_{side}"]).strip()
        if not symbol:
            raise ValueError("نماد آپشن خالی است.")
        return OptionContract(
            symbol=symbol,
            underlying=str(row.get("lval30_UA", "")).strip(),
            option_type="call" if side == "C" else "put",
            strike=float(row["strikePrice"]),
            expiry=parse_tsetmc_date(row["endDate"]),
            bid=_positive(row.get(f"pMeDem_{side}")),
            ask=_positive(row.get(f"pMeOf_{side}")),
            last_price=_positive(row.get(f"pDrCotVal_{side}"))
            or _positive(row.get(f"pClosing_{side}")),
            open_interest=int(row.get(f"oP_{side}") or 0),
            volume=int(row.get(f"qTotTran5J_{side}") or 0),
            contract_size=int(row.get("contractSize") or DEFAULT_CONTRACT_SIZE),
            ins_code=str(row.get(f"insCode_{side}") or "").strip(),
            # سه سنجه‌ای که در همین پاسخ هست و تا امروز نگاشت نمی‌شد.
            # `_optional_int` نبودِ کلید را `None` نگه می‌دارد تا از صفرِ
            # واقعی جدا بماند — غربالگر روی همین تمایز تصمیم می‌گیرد.
            trade_count=_optional_int(row.get(f"zTotTran_{side}")),
            bid_quantity=_optional_int(row.get(f"qTitMeDem_{side}")),
            ask_quantity=_optional_int(row.get(f"qTitMeOf_{side}")),
        )

    def _accept(
        self, contract: OptionContract, report: ChainQualityReport, today: date
    ) -> bool:
        """اعمال گیت‌های کیفیت؛ دلیل هر حذف در گزارش شمرده می‌شود."""
        rules = self.quality

        if contract.days_to_expiry(today) < rules.min_days_to_expiry:
            report.dropped_near_expiry += 1
            return False

        if rules.require_quote and not (
            contract.bid or contract.ask or contract.last_price
        ):
            report.dropped_no_quote += 1
            return False

        if rules.drop_zero_open_interest and contract.open_interest <= 0:
            report.dropped_zero_open_interest += 1
            return False

        if rules.max_relative_spread is not None and contract.bid and contract.ask:
            mid = (contract.bid + contract.ask) / 2.0
            if mid > 0 and (contract.ask - contract.bid) / mid > rules.max_relative_spread:
                report.dropped_wide_spread += 1
                return False

        return True

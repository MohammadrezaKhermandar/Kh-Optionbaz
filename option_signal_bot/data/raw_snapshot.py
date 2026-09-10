"""استخراج **بدون فیلتر** پاسخ خام دیده‌بان بازار آپشن TSETMC.

**چرا جدا از `tsetmc_option_chain_client`:** آن کلاینت عمداً فیلتر
می‌کند. `DataQualityRules` قرارداد بدون مظنه، با اسپرد پهن یا موقعیت باز
صفر را حذف می‌کند تا استراتژی روی قیمت بی‌معنا سیگنال ندهد — که برای
تولید سیگنال درست است و برای **ثبت تاریخچه غلط**.

قراردادی که امروز بی‌مظنه است، فردا ممکن است نقدشونده شود؛ اگر ثبتش
نکنیم، آن تاریخچه برای همیشه رفته. پس این ماژول **هر ردیفی که منبع داده**
را برمی‌گرداند، و قضاوت کیفیت را به مصرف‌کننده واگذار می‌کند.

**قرارداد `None`:** مقدار ناموجود `None` می‌ماند و هرگز صفر نمی‌شود.
صفرِ خامِ منبع هم دست‌نخورده عبور می‌کند. این دو در بازار آپشن تهران
معنای متفاوتی دارند: «مظنه‌ای نیست» با «مظنه صفر است» یکی نیست، و
`_positive` در کلاینت زنجیره هر دو را به `None` تبدیل می‌کند — رفتاری
که برای استراتژی درست است و برای ثبت خام، اتلاف اطلاعات.

**واحدها:** همه‌ی قیمت‌ها **ریال** و همه‌ی حجم‌ها **تعداد قرارداد**
هستند (`SCHEMA_VERSION` این قرارداد را نسخه‌بندی می‌کند). فیلدی که
واحدش معلوم نیست نگاشت نمی‌شود و در payload خام باقی می‌ماند.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: نسخه‌ی نگاشت این ماژول. با هر تغییر در معنا یا واحد فیلدها بالا
#: می‌رود تا ردیف‌های قدیمی قابل تفکیک بمانند.
SCHEMA_VERSION = 1

#: کلید آرایه‌ی ردیف‌ها در پاسخ دیده‌بان.
PAYLOAD_KEY = "instrumentOptMarketWatch"

#: واحد همه‌ی فیلدهای قیمتی این ماژول.
CURRENCY = "IRR"

#: ⚠️ پاسخ دیده‌بان **هیچ مهر زمانی منبع ندارد** — تنها کلید سطح‌بالا
#: `instrumentOptMarketWatch` است و در ردیف‌ها فقط `beginDate`/`endDate`
#: هست که صفتِ قرارداد است، نه زمان مشاهده. پس `source_time` برای این
#: endpoint همیشه `None` می‌ماند و با زمان محلی جایگزین **نمی‌شود**.
HAS_SOURCE_TIME = False


class SnapshotPayloadError(ValueError):
    """پاسخ، ساختار مورد انتظار دیده‌بان را ندارد."""


@dataclass(frozen=True)
class ContractSpec:
    """مشخصات یک قرارداد، **همان‌طور که در این مشاهده دیده شد**.

    عمداً `frozen` است: مشخصات یک مشاهده نباید بعداً ویرایش شود. اگر
    منبع فردا استرایک دیگری بدهد، یک `ContractSpec` **تازه** ساخته
    می‌شود و مشاهده‌ی قبلی دست‌نخورده می‌ماند.
    """

    ins_code: str
    symbol: str
    option_type: str  # "call" | "put"
    underlying: str | None
    underlying_ins_code: str | None
    strike: float | None
    expiry: str | None  # YYYYMMDD خام منبع
    contract_size: int | None
    begin_date: str | None  # YYYYMMDD خام منبع
    full_name: str | None

    def identity(self) -> tuple:
        """مقادیری که «نسخه»ی مشخصات را تعریف می‌کنند.

        `ins_code` تنها نیست: اگر منبع برای همان کد، استرایک یا سررسید
        دیگری بدهد، آن یک **نسخه‌ی جدید** است نه به‌روزرسانی همان ردیف.
        """
        return (
            self.ins_code,
            self.symbol,
            self.option_type,
            self.underlying,
            self.underlying_ins_code,
            self.strike,
            self.expiry,
            self.contract_size,
            self.begin_date,
            self.full_name,
        )


@dataclass(frozen=True)
class ContractQuote:
    """مشاهده‌ی قیمتی یک قرارداد در یک نوبت دریافت.

    هر فیلد می‌تواند `None` باشد؛ `None` یعنی «منبع نداد»، و صفر یعنی
    «منبع صفر داد». این دو هرگز یکی نمی‌شوند.
    """

    spec: ContractSpec
    bid: float | None
    bid_qty: int | None
    ask: float | None
    ask_qty: int | None
    last_price: float | None
    close_price: float | None
    previous_close: float | None
    volume: int | None
    value: float | None
    trade_count: int | None
    open_interest: int | None
    previous_open_interest: int | None
    notional_value: float | None
    remained_day: int | None


@dataclass(frozen=True)
class UnderlyingQuote:
    """آنچه از نماد **پایه** در همین payload آمده.

    ⚠️ این تمام چیزی است که دیده‌بان درباره‌ی پایه می‌دهد: نام، کد و سه
    قیمت. حجم، موقعیت باز، عمق مظنه و هیچ داده‌ی دیگری از پایه در این
    endpoint نیست و برای گرفتنشان درخواست جداگانه لازم است — که در این
    مرحله عمداً اضافه نشده.
    """

    symbol: str
    ins_code: str | None
    last_price: float | None
    close_price: float | None
    previous_close: float | None


@dataclass
class ExtractionResult:
    """نتیجه‌ی استخراج یک payload کامل."""

    quotes: list[ContractQuote] = field(default_factory=list)
    underlyings: list[UnderlyingQuote] = field(default_factory=list)
    #: ردیف‌هایی که استخراج نشدند، با دلیل. **بی‌صدا حذف نمی‌شوند** —
    #: در پایگاه ثبت می‌شوند تا بعداً از payload خام قابل بازیابی باشند.
    rejected: list[RejectedRow] = field(default_factory=list)
    row_count: int = 0

    @property
    def contract_count(self) -> int:
        return len(self.quotes)


@dataclass(frozen=True)
class RejectedRow:
    """یک ردیف/سمت که استخراج نشد، به‌همراه علت."""

    row_index: int
    side: str
    reason: str
    ins_code: str | None = None


def _text(value: Any) -> str | None:
    """رشته‌ی تمیز، یا `None` اگر خالی باشد."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | None:
    """عدد اعشاری، یا `None`. صفر **حفظ می‌شود** و به `None` تبدیل نمی‌شود."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    """عدد صحیح، یا `None`. صفر **حفظ می‌شود**."""
    number = _number(value)
    return None if number is None else int(number)


def extract(payload: dict[str, Any]) -> ExtractionResult:
    """همه‌ی قراردادهای پاسخ خام را استخراج می‌کند — بدون هیچ فیلتر کیفیت.

    Args:
        payload: پاسخ خام دیده‌بان بازار آپشن.

    Returns:
        `ExtractionResult` شامل مشاهده‌ها، نمادهای پایه و ردیف‌های ردشده.

    Raises:
        SnapshotPayloadError: اگر ساختار پاسخ آن چیزی نباشد که انتظار
            می‌رود. عمداً خطا می‌دهد و «صفر قرارداد» برنمی‌گرداند:
            پاسخ خراب نباید به‌عنوان «بازار خالی بود» ثبت شود.
    """
    if not isinstance(payload, dict):
        raise SnapshotPayloadError(
            f"پاسخ باید دیکشنری باشد، نه {type(payload).__name__}."
        )
    rows = payload.get(PAYLOAD_KEY)
    if not isinstance(rows, list):
        raise SnapshotPayloadError(
            f"ساختار پاسخ غیرمنتظره است؛ کلید «{PAYLOAD_KEY}» آرایه نیست."
        )

    result = ExtractionResult(row_count=len(rows))
    seen: dict[str, tuple] = {}

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            result.rejected.append(
                RejectedRow(index, "-", f"ردیف دیکشنری نیست: {type(row).__name__}")
            )
            continue

        underlying = _extract_underlying(row)
        if underlying is not None:
            result.underlyings.append(underlying)

        for side in ("C", "P"):
            quote, rejection = _extract_side(row, side, index)
            if rejection is not None:
                result.rejected.append(rejection)
                continue

            # شناسه‌ی تکراری با مشخصات **متعارض** نباید بی‌صدا
            # overwrite شود؛ هر دو نگه داشته و تعارض ثبت می‌شود.
            identity = quote.spec.identity()
            previous = seen.get(quote.spec.ins_code)
            if previous is not None and previous != identity:
                result.rejected.append(
                    RejectedRow(
                        index,
                        side,
                        "کد یکتای تکراری با مشخصات متعارض در همین پاسخ",
                        quote.spec.ins_code,
                    )
                )
            else:
                seen[quote.spec.ins_code] = identity
            result.quotes.append(quote)

    return result


def _extract_underlying(row: dict[str, Any]) -> UnderlyingQuote | None:
    symbol = _text(row.get("lval30_UA"))
    if symbol is None:
        return None
    return UnderlyingQuote(
        symbol=symbol,
        ins_code=_text(row.get("uaInsCode")),
        last_price=_number(row.get("pDrCotVal_UA")),
        close_price=_number(row.get("pClosing_UA")),
        previous_close=_number(row.get("priceYesterday_UA")),
    )


def _extract_side(
    row: dict[str, Any], side: str, index: int
) -> tuple[ContractQuote | None, RejectedRow | None]:
    """یک سمت (کال یا پوت) از یک ردیف.

    فقط دو چیز اجباری‌اند: کد یکتا و نماد. بدون کد یکتا مشاهده به هیچ
    قراردادی قابل نسبت‌دادن نیست. بقیه‌ی فیلدها می‌توانند `None` باشند.
    """
    ins_code = _text(row.get(f"insCode_{side}"))
    symbol = _text(row.get(f"lVal18AFC_{side}"))
    if ins_code is None:
        return None, RejectedRow(index, side, "کد یکتا (insCode) خالی است")
    if symbol is None:
        return None, RejectedRow(index, side, "نماد قرارداد خالی است", ins_code)

    spec = ContractSpec(
        ins_code=ins_code,
        symbol=symbol,
        option_type="call" if side == "C" else "put",
        underlying=_text(row.get("lval30_UA")),
        underlying_ins_code=_text(row.get("uaInsCode")),
        strike=_number(row.get("strikePrice")),
        expiry=_text(row.get("endDate")),
        contract_size=_integer(row.get("contractSize")),
        begin_date=_text(row.get("beginDate")),
        full_name=_text(row.get(f"lVal30_{side}")),
    )
    quote = ContractQuote(
        spec=spec,
        bid=_number(row.get(f"pMeDem_{side}")),
        bid_qty=_integer(row.get(f"qTitMeDem_{side}")),
        ask=_number(row.get(f"pMeOf_{side}")),
        ask_qty=_integer(row.get(f"qTitMeOf_{side}")),
        last_price=_number(row.get(f"pDrCotVal_{side}")),
        close_price=_number(row.get(f"pClosing_{side}")),
        previous_close=_number(row.get(f"priceYesterday_{side}")),
        volume=_integer(row.get(f"qTotTran5J_{side}")),
        value=_number(row.get(f"qTotCap_{side}")),
        trade_count=_integer(row.get(f"zTotTran_{side}")),
        open_interest=_integer(row.get(f"oP_{side}")),
        previous_open_interest=_integer(row.get(f"yesterdayOP_{side}")),
        notional_value=_number(row.get(f"notionalValue_{side}")),
        remained_day=_integer(row.get("remainedDay")),
    )
    return quote, None

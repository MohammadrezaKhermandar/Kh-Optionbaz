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

import math
from dataclasses import dataclass, field, replace
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
    #: فیلدهایی که منبع داد ولی معتبر نبودند (NaN، بی‌نهایت، کسر برای
    #: فیلد صحیح، …). مقدارشان `None` شده ولی **علتشان گم نمی‌شود**.
    invalid_fields: tuple[str, ...] = ()


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
    """نتیجه‌ی استخراج یک payload کامل.

    `quotes` فقط شامل مشاهده‌هایی است که **واقعاً ثبت می‌شوند**، پس
    `contract_count` با تعداد ردیف‌های نوشته‌شده در پایگاه یکی است.
    مشاهده‌ی متعارض از این فهرست بیرون می‌ماند و در `conflicts` می‌نشیند.
    """

    quotes: list[ContractQuote] = field(default_factory=list)
    underlyings: list[UnderlyingQuote] = field(default_factory=list)
    #: ردیف‌هایی که استخراج نشدند، با دلیل. **بی‌صدا حذف نمی‌شوند** —
    #: در پایگاه ثبت می‌شوند تا بعداً از payload خام قابل بازیابی باشند.
    rejected: list[RejectedRow] = field(default_factory=list)
    #: تعارض‌ها: یک شناسه با **مقادیر متفاوت** در همان پاسخ. هیچ‌کدام
    #: ثبت نمی‌شوند تا انتخابِ بی‌صدا رخ ندهد.
    conflicts: list[ConflictingRow] = field(default_factory=list)
    #: تکرارِ **کاملاً یکسان**: بار دوم چیزی به داده اضافه نمی‌کند، پس
    #: کنار گذاشته می‌شود و فقط شمرده می‌شود. این تعارض نیست.
    duplicate_count: int = 0
    row_count: int = 0

    @property
    def contract_count(self) -> int:
        """تعداد مشاهده‌ای که ثبت می‌شود — نه تعداد ردیف دیده‌شده."""
        return len(self.quotes)

    @property
    def has_conflicts(self) -> bool:
        return bool(self.conflicts)

    @property
    def is_clean(self) -> bool:
        """آیا استخراج بدون تعارض و بدون ردیف ردشده تمام شد؟"""
        return not self.conflicts and not self.rejected


@dataclass(frozen=True)
class RejectedRow:
    """یک ردیف/سمت که استخراج نشد، به‌همراه علت."""

    row_index: int
    side: str
    reason: str
    ins_code: str | None = None


@dataclass(frozen=True)
class ConflictingRow:
    """یک شناسه که در همان پاسخ با **مقادیر متفاوت** تکرار شده.

    فرق مهمش با `RejectedRow`: ردیف ردشده قابل استخراج نبود؛ اینجا هر
    دو نسخه قابل استخراج بودند ولی با هم نمی‌خوانند و **معلوم نیست
    کدام درست است**. پس هیچ‌کدام ثبت نمی‌شوند.
    """

    kind: str  # "contract" | "underlying"
    key: str  # ins_code یا نماد پایه
    row_index: int
    side: str
    reason: str


def _text(value: Any) -> str | None:
    """رشته‌ی تمیز، یا `None` اگر خالی باشد."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


#: نتیجه‌ی تبدیل یک فیلد عددی: (مقدار، علتِ نامعتبربودن).
#: هر دو `None` یعنی «منبع این فیلد را نداد» — که با «صفر داد» و با
#: «چیزی داد که عدد نبود» سه حالت **متفاوت**اند.
FieldResult = tuple[float | int | None, str | None]


def _number(value: Any, field: str) -> FieldResult:
    """عدد اعشاری.

    سه حالت جدا:

    * `(None, None)` — منبع این فیلد را نداد.
    * `(0.0, None)` — منبع **صفر** داد. صفر حفظ می‌شود.
    * `(None, "...")` — منبع چیزی داد که عدد معتبر نیست. مقدار وارد
      ستون نمی‌شود ولی **علتش ثبت می‌گردد**، نه اینکه بی‌صدا `None` شود.

    `NaN` و `Infinity` نامعتبرند: SQLite `Infinity` را می‌پذیرد و
    `NaN` را بی‌صدا به `NULL` تبدیل می‌کند، پس هر دو باید همین‌جا
    گرفته شوند وگرنه یا ستون آلوده می‌شود یا اطلاعات بی‌صدا می‌رود.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{field}: مقدار بولی برای فیلد عددی ({value!r})"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{field}: عدد نیست ({value!r})"
    if math.isnan(number):
        return None, f"{field}: NaN"
    if math.isinf(number):
        return None, f"{field}: بی‌نهایت ({value!r})"
    return number, None


def _integer(value: Any, field: str) -> FieldResult:
    """عدد صحیح. کسر **بی‌صدا بریده نمی‌شود**.

    اگر منبع برای فیلدی که باید صحیح باشد عدد کسری بدهد، آن یک تغییر
    معنادار در داده است: `truncate` کردنش یعنی پنهان‌کردن همان تغییر.
    """
    number, reason = _number(value, field)
    if reason is not None or number is None:
        return None, reason
    if float(number) != int(number):
        return None, f"{field}: عدد صحیح نیست ({value!r})"
    return int(number), None


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
    #: ins_code → (فهرستِ ایندکس در result.quotes، امضای کامل مشاهده)
    seen: dict[str, tuple[int, tuple]] = {}
    conflicted: set[str] = set()
    seen_underlying: dict[str, tuple] = {}
    conflicted_underlying: set[str] = set()

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            result.rejected.append(
                RejectedRow(index, "-", f"ردیف دیکشنری نیست: {type(row).__name__}")
            )
            continue

        _collect_underlying(
            row, index, result, seen_underlying, conflicted_underlying
        )
        for side in ("C", "P"):
            _collect_side(row, side, index, result, seen, conflicted)

    # مشاهده‌های متعارض از فهرست ثبت بیرون می‌روند تا `contract_count`
    # با تعداد ردیف‌های واقعاً نوشته‌شده یکی بماند.
    if conflicted:
        result.quotes = [q for q in result.quotes if q.spec.ins_code not in conflicted]
    if conflicted_underlying:
        result.underlyings = [
            u for u in result.underlyings if u.symbol not in conflicted_underlying
        ]
    return result


def _collect_side(
    row: dict[str, Any],
    side: str,
    index: int,
    result: ExtractionResult,
    seen: dict[str, tuple[int, tuple]],
    conflicted: set[str],
) -> None:
    """یک سمت را استخراج و در برابر تکرار/تعارض بررسی می‌کند."""
    quote, rejection = _extract_side(row, side, index)
    if rejection is not None:
        result.rejected.append(rejection)
        return
    assert quote is not None

    key = quote.spec.ins_code
    signature = _signature(quote)
    previous = seen.get(key)

    if previous is None:
        seen[key] = (index, signature)
        result.quotes.append(quote)
        return

    if previous[1] == signature:
        # تکرارِ کاملاً یکسان: بار دوم هیچ اطلاعاتی اضافه نمی‌کند.
        # این تعارض نیست و snapshot را «مشکوک» نمی‌کند.
        result.duplicate_count += 1
        return

    # تعارض: همان شناسه با مقادیر متفاوت. کدام درست است؟ نمی‌دانیم.
    # پس **هیچ‌کدام** ثبت نمی‌شود و هر دو رخداد گزارش می‌شوند.
    if key not in conflicted:
        conflicted.add(key)
        result.conflicts.append(
            ConflictingRow(
                "contract", key, previous[0], side,
                "همین شناسه پیش‌تر در این پاسخ با مقادیر دیگری آمده بود",
            )
        )
    result.conflicts.append(
        ConflictingRow(
            "contract", key, index, side,
            f"شناسه‌ی تکراری با مقادیر متفاوت: {_first_difference(previous[1], signature)}",
        )
    )


def _collect_underlying(
    row: dict[str, Any],
    index: int,
    result: ExtractionResult,
    seen: dict[str, tuple],
    conflicted: set[str],
) -> None:
    """نماد پایه.

    ⚠️ تکرارِ **یکسان** نماد پایه بین ردیف‌های استرایک کاملاً طبیعی است
    (هر استرایک همان پایه را حمل می‌کند) و تعارض شمرده نمی‌شود. فقط
    وقتی همان نماد با **قیمت متفاوت** بیاید، یعنی پاسخ با خودش
    نمی‌خواند.
    """
    underlying = _extract_underlying(row)
    if underlying is None:
        return
    signature = (
        underlying.ins_code,
        underlying.last_price,
        underlying.close_price,
        underlying.previous_close,
    )
    previous = seen.get(underlying.symbol)
    if previous is None:
        seen[underlying.symbol] = signature
        result.underlyings.append(underlying)
        return
    if previous == signature:
        return  # طبیعی: همان پایه در استرایک دیگر
    if underlying.symbol not in conflicted:
        conflicted.add(underlying.symbol)
        result.conflicts.append(
            ConflictingRow(
                "underlying", underlying.symbol, index, "UA",
                f"نماد پایه با مقادیر متفاوت در همین پاسخ: "
                f"{_first_difference(previous, signature)}",
            )
        )


def _signature(quote: ContractQuote) -> tuple:
    """امضای کامل یک مشاهده: مشخصات **و** مقادیر قیمتی.

    تعارض فقط اختلاف مشخصات نیست؛ همان قرارداد با قیمت یا حجم متفاوت
    در یک پاسخ هم یعنی معلوم نیست کدام درست است.
    """
    return (
        *quote.spec.identity(),
        quote.bid, quote.bid_qty, quote.ask, quote.ask_qty,
        quote.last_price, quote.close_price, quote.previous_close,
        quote.volume, quote.value, quote.trade_count,
        quote.open_interest, quote.previous_open_interest,
        quote.notional_value, quote.remained_day,
    )


def _first_difference(left: tuple, right: tuple) -> str:
    """اولین موقعیتی که دو امضا فرق دارند — برای اینکه علت خوانا بماند."""
    for position, (a, b) in enumerate(zip(left, right, strict=False)):
        if a != b:
            return f"موقعیت {position}: {a!r} در برابر {b!r}"
    return "طول امضا متفاوت است"


def _extract_underlying(row: dict[str, Any]) -> UnderlyingQuote | None:
    symbol = _text(row.get("lval30_UA"))
    if symbol is None:
        return None
    return UnderlyingQuote(
        symbol=symbol,
        ins_code=_text(row.get("uaInsCode")),
        last_price=_number(row.get("pDrCotVal_UA"), "pDrCotVal_UA")[0],
        close_price=_number(row.get("pClosing_UA"), "pClosing_UA")[0],
        previous_close=_number(row.get("priceYesterday_UA"), "priceYesterday_UA")[0],
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

    #: علتِ هر فیلدی که منبع داد ولی معتبر نبود. مقدارش وارد ستون
    #: نمی‌شود، ولی **کل ردیف هم دور ریخته نمی‌شود**: بقیه‌ی فیلدهای
    #: همان مشاهده هنوز ارزش دارند.
    problems: list[str] = []

    def num(key: str) -> float | None:
        value, reason = _number(row.get(key), key)
        if reason:
            problems.append(reason)
        return value

    def integer(key: str) -> int | None:
        value, reason = _integer(row.get(key), key)
        if reason:
            problems.append(reason)
        return value

    spec = ContractSpec(
        ins_code=ins_code,
        symbol=symbol,
        option_type="call" if side == "C" else "put",
        underlying=_text(row.get("lval30_UA")),
        underlying_ins_code=_text(row.get("uaInsCode")),
        strike=num("strikePrice"),
        expiry=_text(row.get("endDate")),
        contract_size=integer("contractSize"),
        begin_date=_text(row.get("beginDate")),
        full_name=_text(row.get(f"lVal30_{side}")),
    )
    quote = ContractQuote(
        spec=spec,
        bid=num(f"pMeDem_{side}"),
        bid_qty=integer(f"qTitMeDem_{side}"),
        ask=num(f"pMeOf_{side}"),
        ask_qty=integer(f"qTitMeOf_{side}"),
        last_price=num(f"pDrCotVal_{side}"),
        close_price=num(f"pClosing_{side}"),
        previous_close=num(f"priceYesterday_{side}"),
        volume=integer(f"qTotTran5J_{side}"),
        value=num(f"qTotCap_{side}"),
        trade_count=integer(f"zTotTran_{side}"),
        open_interest=integer(f"oP_{side}"),
        previous_open_interest=integer(f"yesterdayOP_{side}"),
        notional_value=num(f"notionalValue_{side}"),
        remained_day=integer("remainedDay"),
    )
    return replace(quote, invalid_fields=tuple(problems)), None

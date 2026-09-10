"""تست‌های هدفمند ثبت داده‌ی خام بازار.

عمداً کم و مشخص‌اند — هر کدام یک **خرابیِ داده** را می‌گیرد که بدون
تست دیر و بی‌صدا پیدا می‌شد:

۱. فیلتر کیفیت به مسیر ثبت نشت کند و قرارداد گم شود؛ و `NULL` با صفر
   یکی گرفته شود.
۲. خرابی وسط ثبت، snapshot ناقص را «کامل» جا بزند.
۳. retry ذخیره‌سازی ردیف تکراری بسازد، یا دو مشاهده‌ی مستقلِ هم‌قیمت
   یکی شمرده شوند.
۴. داده‌ی fixture و زنده در یک پایگاه مخلوط شوند.
۵. مشاهده به نسخه‌ی اشتباهِ مشخصات قرارداد بچسبد.

پوشش هدف نیست؛ تست تکراری اضافه نشده.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from data.raw_snapshot import (
    SnapshotPayloadError,
    _integer,
    _number,
    extract,
)
from data.tsetmc_option_chain_client import (
    DataQualityRules,
    FilePayloadSource,
    TsetmcOptionChainClient,
)
from storage.market_recorder import (
    KIND_LIVE,
    KIND_TEST,
    STATUS_COMPLETE,
    STATUS_COMPLETE_WITH_ISSUES,
    STATUS_FAILED,
    STATUS_PARTIAL,
    DatabaseKindMismatch,
    MarketRecorder,
    SnapshotConflict,
)

TEHRAN = timezone(timedelta(hours=3, minutes=30))

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "tsetmc_option_market_watch.json"
)


def _moment(hour: int = 10, minute: int = 0) -> datetime:
    return datetime(2026, 9, 10, hour, minute, tzinfo=TEHRAN)


def _row(ins_code: str = "111", **overrides) -> dict:
    """یک ردیف استرایک با شکل واقعی پاسخ TSETMC."""
    row = {
        "insCode_C": ins_code,
        "lVal18AFC_C": "ضخود1001",
        "lVal30_C": "اختيارخ خودرو-5000-1405/07/29",
        "insCode_P": f"{ins_code}9",
        "lVal18AFC_P": "طخود1001",
        "lVal30_P": "اختيارف خودرو-5000-1405/07/29",
        "lval30_UA": "خودرو",
        "uaInsCode": "65883838195688438",
        "strikePrice": 5000.0,
        "endDate": "20261021",
        "beginDate": "20260607",
        "contractSize": 1000,
        "remainedDay": 41,
        "pMeDem_C": 0, "qTitMeDem_C": 0, "pMeOf_C": 0, "qTitMeOf_C": 0,
        "pDrCotVal_C": 0, "pClosing_C": 0, "priceYesterday_C": 0,
        "qTotTran5J_C": 0, "qTotCap_C": 0, "zTotTran_C": 0,
        "oP_C": 0, "yesterdayOP_C": 0, "notionalValue_C": 0,
        "pMeDem_P": 0, "qTitMeDem_P": 0, "pMeOf_P": 0, "qTitMeOf_P": 0,
        "pDrCotVal_P": 0, "pClosing_P": 0, "priceYesterday_P": 0,
        "qTotTran5J_P": 0, "qTotCap_P": 0, "zTotTran_P": 0,
        "oP_P": 0, "yesterdayOP_P": 0, "notionalValue_P": 0,
        "pDrCotVal_UA": 55000.0, "pClosing_UA": 54900.0, "priceYesterday_UA": 54000.0,
    }
    row.update(overrides)
    return row


def _payload(*rows: dict) -> dict:
    return {"instrumentOptMarketWatch": list(rows)}


def _record(recorder: MarketRecorder, payload: dict, snapshot_id: str, **kw) -> int:
    defaults = {
        "source": "fixture",
        "is_live": False,
        "requested_at": _moment(),
        "received_at": _moment(10, 1),
    }
    defaults.update(kw)
    return recorder.record(snapshot_id, payload, extract(payload), **defaults)


@pytest.fixture
def recorder(tmp_path) -> MarketRecorder:
    return MarketRecorder(tmp_path / "test.db", kind=KIND_TEST)


# ----------------------------------------------------------------------
# ۱. ثبت پیش از فیلتر، و تفاوت NULL با صفر
# ----------------------------------------------------------------------
def test_records_contracts_the_quality_filter_would_drop(recorder, payload_source):
    """گیت‌های کیفیت نباید به مسیر ثبت نشت کنند.

    انتظار مستقل: تعداد ثبت‌شده باید برابر تعداد قرارداد در **پاسخ خام**
    باشد، و اکیداً بیشتر از چیزی که کلاینت زنجیره تحویل می‌دهد.
    """
    payload = payload_source.fetch()
    rows = payload["instrumentOptMarketWatch"]

    written = _record(recorder, payload, recorder.new_snapshot_id())

    # هر ردیف استرایک یک کال و یک پوت دارد — شمارش مستقل از پیاده‌سازی.
    assert written == len(rows) * 2

    strict = TsetmcOptionChainClient(
        FilePayloadSource(payload_source.path),
        quality=DataQualityRules(
            require_quote=True, drop_zero_open_interest=True, max_relative_spread=0.35
        ),
    )
    underlying = strict.available_underlyings()[0]
    filtered = len(strict.get_chain(underlying).contracts)
    assert filtered < written, "فیلتر کیفیت باید کمتر از ثبت خام بدهد"


def test_missing_value_stays_null_and_real_zero_stays_zero(recorder):
    """`None` یعنی «منبع نداد»؛ صفر یعنی «منبع صفر داد». یکی نیستند."""
    row = _row()
    row["pMeDem_C"] = 0        # صفرِ خام منبع
    row.pop("pMeOf_C")         # اصلاً نیامده
    row["oP_C"] = 0

    _record(recorder, _payload(row), "snap-null")

    stored = recorder._connection.execute(
        "SELECT bid, ask, open_interest FROM quotes WHERE ins_code = '111'"
    ).fetchone()
    assert stored["bid"] == 0.0, "صفرِ خام نباید به NULL تبدیل شود"
    assert stored["ask"] is None, "فیلد نیامده نباید صفر شود"
    assert stored["open_interest"] == 0


def test_invalid_row_is_recorded_not_silently_dropped(recorder):
    """ردیف بی‌کدیکتا حذف می‌شود ولی **علتش ثبت** می‌شود."""
    bad = _row(ins_code="222")
    bad["insCode_C"] = ""

    _record(recorder, _payload(bad), "snap-bad")

    rejected = recorder._connection.execute(
        "SELECT side, reason FROM rejected_rows WHERE snapshot_id = 'snap-bad'"
    ).fetchall()
    assert [r["side"] for r in rejected] == ["C"]
    assert "insCode" in rejected[0]["reason"]
    # و payload خام هنوز کامل است
    blob = recorder._connection.execute(
        "SELECT raw_payload FROM snapshots WHERE snapshot_id = 'snap-bad'"
    ).fetchone()["raw_payload"]
    restored = json.loads(gzip.decompress(blob).decode("utf-8"))
    assert restored["instrumentOptMarketWatch"][0]["insCode_C"] == ""


def test_conflicting_duplicate_is_not_written_and_counts_agree(recorder):
    """شناسه‌ی تکراری با مشخصات متعارض: **هیچ‌کدام** ثبت نمی‌شود.

    فقط وجود متن خطا کافی نیست — تعداد واقعی ردیف SQL، شمارش گزارش و
    وضعیت snapshot باید با هم بخوانند.
    """
    first = _row(ins_code="333")
    second = _row(ins_code="333", strikePrice=7000.0)
    second["insCode_P"] = "3339b"
    payload = _payload(first, second)

    extraction = extract(payload)
    written = _record(recorder, payload, "snap-dup")

    conn = recorder._connection
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM quotes WHERE snapshot_id = 'snap-dup'"
    ).fetchone()["n"]

    # ۱) کال متعارض اصلاً ثبت نشده — دو پوت مانده‌اند و بس.
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM quotes WHERE ins_code = '333'"
    ).fetchone()["n"] == 0
    # ۲) سه عدد با هم می‌خوانند: SQL، گزارش، مقدار بازگشتی.
    assert rows == extraction.contract_count == written
    # ۳) شمارش ذخیره‌شده هم همان است.
    stored = conn.execute(
        "SELECT contract_count, conflict_count, status FROM snapshots"
        " WHERE snapshot_id = 'snap-dup'"
    ).fetchone()
    assert stored["contract_count"] == rows
    assert stored["conflict_count"] == 2  # هر دو رخداد گزارش می‌شوند
    # ۴) و این snapshot «معمولی» معرفی نمی‌شود.
    assert stored["status"] == STATUS_COMPLETE_WITH_ISSUES

    reasons = [
        r["reason"]
        for r in conn.execute(
            "SELECT reason FROM conflicts WHERE snapshot_id = 'snap-dup'"
        ).fetchall()
    ]
    assert any("مقادیر متفاوت" in r for r in reasons)


def test_identical_duplicate_is_not_a_conflict(recorder):
    """تکرارِ کاملاً یکسان اطلاعاتی اضافه نمی‌کند و تعارض هم نیست."""
    row = _row(ins_code="334")
    payload = _payload(row, dict(row))  # همان ردیف، دو بار

    extraction = extract(payload)
    written = _record(recorder, payload, "snap-same")

    assert extraction.duplicate_count == 2  # کال و پوت، هرکدام یک‌بار
    assert extraction.conflicts == []
    assert written == extraction.contract_count == 2
    stored = recorder._connection.execute(
        "SELECT status, duplicate_count FROM snapshots WHERE snapshot_id = 'snap-same'"
    ).fetchone()
    assert stored["status"] == STATUS_COMPLETE  # تکرار یکسان مشکل نیست
    assert stored["duplicate_count"] == 2


def test_same_contract_with_different_prices_is_a_conflict(recorder):
    """تعارض فقط اختلاف مشخصات نیست؛ اختلاف قیمت هم هست."""
    first = _row(ins_code="335")
    second = _row(ins_code="335")
    second["pMeDem_C"] = 999  # همان قرارداد، قیمت دیگر

    payload = _payload(first, second)
    extraction = extract(payload)
    _record(recorder, payload, "snap-price")

    assert extraction.has_conflicts
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM quotes WHERE ins_code = '335'"
    ).fetchone()["n"] == 0


def test_identical_underlying_across_strikes_is_normal(recorder):
    """تکرار یکسان نماد پایه بین استرایک‌ها طبیعی است، نه تعارض."""
    payload = _payload(
        _row(ins_code="336", strikePrice=5000.0),
        _row(ins_code="337", strikePrice=6000.0),
    )
    extraction = extract(payload)

    assert extraction.conflicts == []
    assert len(extraction.underlyings) == 1  # یک پایه، نه دو تا
    _record(recorder, payload, "snap-ua")
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM underlying_quotes WHERE snapshot_id = 'snap-ua'"
    ).fetchone()["n"] == 1


def test_underlying_with_different_prices_is_a_conflict(recorder):
    """ولی همان پایه با قیمت متفاوت یعنی پاسخ با خودش نمی‌خواند."""
    second = _row(ins_code="338", strikePrice=6000.0)
    second["pDrCotVal_UA"] = 99999.0
    payload = _payload(_row(ins_code="339"), second)

    extraction = extract(payload)
    _record(recorder, payload, "snap-ua2")

    assert any(c.kind == "underlying" for c in extraction.conflicts)
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM underlying_quotes WHERE snapshot_id = 'snap-ua2'"
    ).fetchone()["n"] == 0
    assert recorder._connection.execute(
        "SELECT status FROM snapshots WHERE snapshot_id = 'snap-ua2'"
    ).fetchone()["status"] == STATUS_COMPLETE_WITH_ISSUES


# ----------------------------------------------------------------------
# ۲. خرابی وسط ثبت
# ----------------------------------------------------------------------
def test_failure_midway_leaves_no_complete_snapshot(tmp_path, monkeypatch):
    """اگر نوشتن وسط کار بشکند، هیچ ردیفی نباید `complete` بماند.

    تراکنش باید کامل برگردد: نه snapshot، نه quote.
    """
    recorder = MarketRecorder(tmp_path / "t.db", kind=KIND_TEST)
    payload = _payload(_row("444"), _row("555"))

    original = MarketRecorder._write_rows
    calls = {"n": 0}

    def explode(self, snapshot_id, extraction, observed_at):
        calls["n"] += 1
        original(self, snapshot_id, extraction, observed_at)
        raise sqlite3.OperationalError("قطع شبیه‌سازی‌شده وسط نوشتن")

    monkeypatch.setattr(MarketRecorder, "_write_rows", explode)
    with pytest.raises(sqlite3.OperationalError):
        _record(recorder, payload, "snap-crash")

    assert calls["n"] == 1
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM snapshots"
    ).fetchone()["n"] == 0
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM quotes"
    ).fetchone()["n"] == 0


def test_partial_status_is_never_the_end_state(recorder):
    """گذار به `complete` فقط پس از نوشتن کامل رخ می‌دهد."""
    _record(recorder, _payload(_row("666")), "snap-ok")
    status = recorder._connection.execute(
        "SELECT status FROM snapshots WHERE snapshot_id = 'snap-ok'"
    ).fetchone()["status"]
    assert status == STATUS_COMPLETE
    assert status != STATUS_PARTIAL


def test_failed_fetch_is_visible_in_status(recorder):
    """نبودِ داده هم باید در تاریخچه دیده شود، نه اینکه ناپدید بماند."""
    recorder.record_failure(
        "snap-fail",
        source="fixture",
        is_live=False,
        requested_at=_moment(11),
        error="دریافت ناموفق: timeout",
    )
    status = recorder.status()
    assert status.last_attempt_status == STATUS_FAILED
    assert "timeout" in status.last_error
    assert status.last_complete_at is None


# ----------------------------------------------------------------------
# ۳. retry همان نوبت در برابر مشاهده‌ی مستقل
# ----------------------------------------------------------------------
def test_storage_retry_of_same_snapshot_does_not_duplicate(recorder):
    """retry ذخیره‌سازی همان نوبت با همان محتوا = بی‌اثر.

    این مسیر جدا از retry شبکه است: اینجا خودِ نوشتن دوباره اجرا می‌شود.
    """
    payload = _payload(_row("777"))
    first = _record(recorder, payload, "snap-retry")
    second = _record(recorder, payload, "snap-retry")  # همان شناسه، همان محتوا

    assert first == second == 2  # یک کال و یک پوت
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM snapshots"
    ).fetchone()["n"] == 1
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM quotes"
    ).fetchone()["n"] == 2


def test_same_snapshot_id_with_different_content_is_refused(recorder):
    """همان شناسه با محتوای متفاوت: خطای صریح، و نسخه‌ی قبلی سالم می‌ماند."""
    _record(recorder, _payload(_row("778", strikePrice=5000.0)), "snap-imm")

    with pytest.raises(SnapshotConflict):
        _record(recorder, _payload(_row("778", strikePrice=9000.0)), "snap-imm")

    # نسخه‌ی قبلی دست‌نخورده
    stored = recorder._connection.execute(
        """
        SELECT s.strike FROM quotes q JOIN contract_specs s ON s.spec_id = q.spec_id
        WHERE q.ins_code = '778'
        """
    ).fetchone()
    assert stored["strike"] == 5000.0
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM snapshots"
    ).fetchone()["n"] == 1


def test_record_failure_does_not_erase_a_successful_snapshot(recorder):
    """شکستِ بعدی نباید داده‌ی سالمِ قبلیِ همان شناسه را پاک کند."""
    _record(recorder, _payload(_row("779")), "snap-keep")

    with pytest.raises(SnapshotConflict):
        recorder.record_failure(
            "snap-keep",
            source="fixture",
            is_live=False,
            requested_at=_moment(12),
            error="خطای بعدی",
        )

    row = recorder._connection.execute(
        "SELECT status FROM snapshots WHERE snapshot_id = 'snap-keep'"
    ).fetchone()
    assert row["status"] == STATUS_COMPLETE
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM quotes WHERE snapshot_id = 'snap-keep'"
    ).fetchone()["n"] == 2


def test_two_independent_observations_stay_two_even_if_identical(recorder):
    """قیمت یکسان در دو زمان = دو مشاهده. حذف دومی یعنی گم‌کردن واقعیت."""
    payload = _payload(_row("888"))
    _record(recorder, payload, "snap-a", received_at=_moment(10, 0))
    _record(recorder, payload, "snap-b", received_at=_moment(10, 5))

    rows = recorder._connection.execute(
        "SELECT snapshot_id FROM quotes WHERE ins_code = '888' ORDER BY snapshot_id"
    ).fetchall()
    assert [r["snapshot_id"] for r in rows] == ["snap-a", "snap-b"]


# ----------------------------------------------------------------------
# ۴. تفکیک آزمایشی و زنده
# ----------------------------------------------------------------------
def test_fixture_cannot_enter_a_live_database(tmp_path):
    """پایگاه زنده داده‌ی آزمایشی نمی‌پذیرد — قید در لایه‌ی ذخیره‌سازی است."""
    live = MarketRecorder(tmp_path / "live.db", kind=KIND_LIVE)
    with pytest.raises(DatabaseKindMismatch):
        _record(live, _payload(_row("999")), "snap-x", is_live=False)
    assert live._connection.execute(
        "SELECT COUNT(*) AS n FROM snapshots"
    ).fetchone()["n"] == 0


def test_live_data_cannot_enter_a_test_database(recorder):
    """و برعکس: پایگاه آزمایشی هم داده‌ی زنده نمی‌پذیرد."""
    with pytest.raises(DatabaseKindMismatch):
        _record(recorder, _payload(_row("1010")), "snap-y", is_live=True)


def test_database_kind_is_enforced_on_reopen(tmp_path):
    """نوع پایگاه هنگام ساخت مهر می‌شود و در بازکردن بعدی کنترل می‌گردد."""
    path = tmp_path / "stamped.db"
    MarketRecorder(path, kind=KIND_TEST).close()
    with pytest.raises(DatabaseKindMismatch):
        MarketRecorder(path, kind=KIND_LIVE)


# ----------------------------------------------------------------------
# ۵. اتصال مشاهده به نسخه‌ی درست مشخصات
# ----------------------------------------------------------------------
def test_spec_change_creates_a_new_version_and_keeps_the_old_observation(recorder):
    """تغییر استرایک نباید مشاهده‌ی قبلی را بازنویسی کند.

    انتظار مستقل: دو نسخه‌ی مشخصات، و هر مشاهده به استرایکِ زمان خودش.
    """
    _record(recorder, _payload(_row("1111", strikePrice=5000.0)), "snap-1")
    _record(recorder, _payload(_row("1111", strikePrice=6000.0)), "snap-2")

    linked = recorder._connection.execute(
        """
        SELECT q.snapshot_id, s.strike
        FROM quotes q JOIN contract_specs s ON s.spec_id = q.spec_id
        WHERE q.ins_code = '1111' ORDER BY q.snapshot_id
        """
    ).fetchall()
    assert [(r["snapshot_id"], r["strike"]) for r in linked] == [
        ("snap-1", 5000.0),
        ("snap-2", 6000.0),
    ]


def test_identical_spec_is_reused_not_duplicated(recorder):
    """مشخصات بدون تغییر نباید نسخه‌ی تکراری بسازد."""
    payload = _payload(_row("1212"))
    _record(recorder, payload, "snap-1")
    _record(recorder, payload, "snap-2")

    versions = recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM contract_specs WHERE ins_code = '1212'"
    ).fetchone()["n"]
    assert versions == 1


# ----------------------------------------------------------------------
# ساختار پاسخ و زمان
# ----------------------------------------------------------------------
def test_unexpected_payload_raises_instead_of_reporting_zero_contracts():
    """پاسخ خراب نباید «بازار خالی بود» تفسیر شود."""
    with pytest.raises(SnapshotPayloadError):
        extract({"somethingElse": []})
    with pytest.raises(SnapshotPayloadError):
        extract({"instrumentOptMarketWatch": "نه یک آرایه"})


def test_naive_timestamp_is_rejected(recorder):
    """زمان بدون منطقه ثبت نمی‌شود؛ بعداً با هیچ منبعی هم‌تراز نمی‌شد."""
    with pytest.raises(ValueError, match="timezone-aware"):
        _record(
            recorder,
            _payload(_row("1313")),
            "snap-naive",
            requested_at=datetime(2026, 9, 10, 10, 0),  # بدون منطقه — عمدی
        )


def test_source_time_is_null_when_the_source_gives_none(recorder):
    """دیده‌بان آپشن مهر زمانی ندارد؛ زمان محلی جایش گذاشته نمی‌شود."""
    _record(recorder, _payload(_row("1414")), "snap-t")
    row = recorder._connection.execute(
        "SELECT source_time, requested_at, received_at FROM snapshots"
        " WHERE snapshot_id = 'snap-t'"
    ).fetchone()
    assert row["source_time"] is None
    assert row["requested_at"].endswith("+03:30")
    assert row["received_at"] != row["requested_at"]


# ----------------------------------------------------------------------
# ۶. مقادیر نامعتبر: ناموجود، صفر واقعی، و نامعتبر سه چیز جدا هستند
# ----------------------------------------------------------------------
def test_three_states_of_a_numeric_field_are_distinguishable():
    """ناموجود، صفرِ واقعی، و نامعتبر نباید یکی شوند."""
    assert _number(None, "f") == (None, None)          # منبع نداد
    assert _number(0, "f") == (0.0, None)              # صفرِ واقعی
    value, reason = _number("نه‌عدد", "f")
    assert value is None and "عدد نیست" in reason      # نامعتبر، با علت


def test_nan_and_infinity_never_reach_a_numeric_column(recorder):
    """`NaN` و بی‌نهایت وارد ستون نمی‌شوند و برنامه را هم نمی‌خوابانند."""
    row = _row(ins_code="1515")
    row["pMeDem_C"] = float("nan")
    row["pMeOf_C"] = float("inf")

    _record(recorder, _payload(row), "snap-nan")

    stored = recorder._connection.execute(
        "SELECT bid, ask, last_price FROM quotes WHERE ins_code = '1515'"
    ).fetchone()
    assert stored["bid"] is None
    assert stored["ask"] is None
    # بقیه‌ی فیلدهای همان مشاهده هنوز ثبت شده‌اند — کل ردیف دور نرفته
    assert stored["last_price"] == 0.0

    reasons = [
        r["reason"]
        for r in recorder._connection.execute(
            "SELECT reason FROM invalid_fields WHERE ins_code = '1515'"
        ).fetchall()
    ]
    assert any("NaN" in r for r in reasons)
    assert any("بی‌نهایت" in r for r in reasons)
    # پاسخ مشکل‌دار «معمولی» معرفی نمی‌شود
    assert recorder._connection.execute(
        "SELECT status FROM snapshots WHERE snapshot_id = 'snap-nan'"
    ).fetchone()["status"] == STATUS_COMPLETE


def test_fractional_value_for_an_integer_field_is_not_truncated(recorder):
    """`۱۰۰۰.۵` برای اندازه‌ی قرارداد بی‌صدا به ۱۰۰۰ تبدیل نمی‌شود."""
    value, reason = _integer(1000.5, "contractSize")
    assert value is None
    assert "صحیح نیست" in reason

    row = _row(ins_code="1616")
    row["contractSize"] = 1000.5
    _record(recorder, _payload(row), "snap-frac")

    spec = recorder._connection.execute(
        "SELECT contract_size FROM contract_specs WHERE ins_code = '1616'"
    ).fetchone()
    assert spec["contract_size"] is None  # نه ۱۰۰۰


def test_a_valid_payload_is_not_discarded_because_one_field_is_bad(recorder):
    """یک فیلد خراب نباید کل پاسخ معتبر را دور بریزد."""
    bad = _row(ins_code="1717")
    bad["oP_C"] = "نامعتبر"
    payload = _payload(bad, _row(ins_code="1818"))

    written = _record(recorder, payload, "snap-partial-bad")

    assert written == 4  # هر دو ردیف، هر دو سمت
    assert recorder._connection.execute(
        "SELECT open_interest FROM quotes WHERE ins_code = '1717'"
    ).fetchone()["open_interest"] is None


# ----------------------------------------------------------------------
# ۷. زمانِ اولین مشاهده
# ----------------------------------------------------------------------
def test_first_seen_at_comes_from_the_observation_not_the_clock(recorder):
    """`first_seen_at` زمان دریافت مشاهده است، نه ساعت اجرای ذخیره‌سازی."""
    observed = _moment(9, 15)
    _record(recorder, _payload(_row("1919")), "snap-seen", received_at=observed)

    stored = recorder._connection.execute(
        "SELECT first_seen_at FROM contract_specs WHERE ins_code = '1919'"
    ).fetchone()["first_seen_at"]
    assert stored == observed.isoformat()


# ----------------------------------------------------------------------
# ۸. قیدهای CLI: پیش از شبکه و پیش از ساخت پایگاه
# ----------------------------------------------------------------------
def test_cli_refuses_test_kind_without_fixture_before_touching_anything(tmp_path):
    """`--kind test` بدون fixture یعنی داده‌ی زنده در پایگاه آزمایشی.

    باید **پیش از** هر درخواست شبکه و پیش از ساخت پایگاه رد شود.
    """
    from scripts import record_market

    db = tmp_path / "never.db"
    code = record_market.main(["--once", "--kind", "test", "--db", str(db)])

    assert code == record_market.EXIT_MISUSE
    assert not db.exists(), "پایگاه نباید ساخته شده باشد"


def test_cli_refuses_fixture_into_a_live_database(tmp_path):
    from scripts import record_market

    db = tmp_path / "live.db"
    code = record_market.main(
        ["--once", "--fixture", str(FIXTURE), "--db", str(db)]
    )
    assert code == record_market.EXIT_MISUSE
    assert not db.exists()


def test_cli_test_kind_uses_the_test_path_without_an_explicit_db(tmp_path, monkeypatch):
    """`--kind test` بدون `--db` باید مسیر آزمایشیِ تنظیمات را بردارد."""
    from scripts import record_market

    monkeypatch.chdir(tmp_path)
    args = record_market.build_parser().parse_args(
        ["--once", "--kind", "test", "--fixture", str(FIXTURE)]
    )
    options = record_market.resolve_options(args)
    assert "test" in Path(options["db"]).name

    live_args = record_market.build_parser().parse_args(["--once"])
    assert Path(record_market.resolve_options(live_args)["db"]) != Path(options["db"])


def test_cli_explicit_db_wins_over_settings(tmp_path):
    from scripts import record_market

    chosen = tmp_path / "chosen.db"
    args = record_market.build_parser().parse_args(
        ["--status", "--db", str(chosen)]
    )
    assert Path(record_market.resolve_options(args)["db"]) == chosen


def test_cli_status_needs_no_network_and_no_database(tmp_path, capsys):
    from scripts import record_market

    code = record_market.main(["--status", "--db", str(tmp_path / "absent.db")])
    assert code == record_market.EXIT_OK
    assert "هنوز چیزی ثبت نشده" in capsys.readouterr().out

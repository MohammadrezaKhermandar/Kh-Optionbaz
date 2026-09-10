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

import pytest

from data.raw_snapshot import SnapshotPayloadError, extract
from data.tsetmc_option_chain_client import (
    DataQualityRules,
    FilePayloadSource,
    TsetmcOptionChainClient,
)
from storage.market_recorder import (
    KIND_LIVE,
    KIND_TEST,
    STATUS_COMPLETE,
    STATUS_FAILED,
    STATUS_PARTIAL,
    DatabaseKindMismatch,
    MarketRecorder,
)

TEHRAN = timezone(timedelta(hours=3, minutes=30))


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


def test_conflicting_duplicate_ins_code_is_flagged(recorder):
    """کد یکتای تکراری با مشخصات متعارض نباید بی‌صدا overwrite شود."""
    first = _row(ins_code="333")
    second = _row(ins_code="333", strikePrice=7000.0)
    second["insCode_P"] = "3339b"

    _record(recorder, _payload(first, second), "snap-dup")

    reasons = recorder._connection.execute(
        "SELECT reason FROM rejected_rows WHERE snapshot_id = 'snap-dup'"
    ).fetchall()
    assert any("متعارض" in r["reason"] for r in reasons)


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

    def explode(self, snapshot_id, extraction):
        calls["n"] += 1
        original(self, snapshot_id, extraction)
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
    """retry ذخیره‌سازی همان نوبت = همان ردیف‌ها، نه ردیف تازه.

    این مسیر جدا از retry شبکه است: اینجا خودِ نوشتن دوباره اجرا می‌شود.
    """
    payload = _payload(_row("777"))
    _record(recorder, payload, "snap-retry")
    _record(recorder, payload, "snap-retry")  # همان شناسه

    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM snapshots"
    ).fetchone()["n"] == 1
    assert recorder._connection.execute(
        "SELECT COUNT(*) AS n FROM quotes"
    ).fetchone()["n"] == 2  # یک کال و یک پوت


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

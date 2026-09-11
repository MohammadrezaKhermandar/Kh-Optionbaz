"""تست‌های زمان‌بندی ثبت — با ساعت تزریقی، بدون انتظار واقعی.

محدود به همان خطرهایی است که بدون تست دیر و بی‌صدا پیدا می‌شدند:
هم‌پوشانی نوبت‌ها، جبران انبوه پس از خواب سیستم، ثبت بیرون ساعت بازار،
ادامه‌نیافتن پس از خطای موقت، و اجرای دو نمونه روی یک پایگاه.

هیچ تستی منتظر گذشت زمان واقعی نمی‌ماند: `next_tick`، `skipped_ticks`
و `decide` توابع خالص‌اند و `now` را ورودی می‌گیرند.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from market.schedule import (
    DEFAULT_TIMEZONE,
    RecordingWindow,
    decide,
    next_tick,
    resolve_timezone,
    skipped_ticks,
)
from storage.process_lock import LockUnavailable, ProcessLock

TEHRAN = timezone(timedelta(hours=3, minutes=30))
FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "tsetmc_option_market_watch.json"
)
INTERVAL = 300


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, 12, hour, minute, second, tzinfo=TEHRAN)


# ----------------------------------------------------------------------
# تیکِ مطلق: بدون هم‌پوشانی، بدون جبران انبوه
# ----------------------------------------------------------------------
def test_ticks_land_on_aligned_wall_clock_slots():
    """با فاصله‌ی ۳۰۰ ثانیه، نوبت‌ها روی دقیقه‌های گرد می‌نشینند."""
    assert next_tick(_at(9, 0, 0), INTERVAL) == _at(9, 5)
    assert next_tick(_at(9, 0, 1), INTERVAL) == _at(9, 5)
    assert next_tick(_at(9, 4, 59), INTERVAL) == _at(9, 5)
    # دقیقاً روی مرز: نوبت **بعدی**، نه همین لحظه — وگرنه حلقه دوبار
    # روی یک اسلات اجرا می‌شد.
    assert next_tick(_at(9, 5, 0), INTERVAL) == _at(9, 10)


def test_a_long_run_swallows_the_next_slot_instead_of_queueing():
    """نوبتی که از فاصله بیشتر طول بکشد، نباید نوبت بعدی را صف کند.

    این همان «عدم هم‌پوشانی» است: چون زمان بعدی از **الان** حساب
    می‌شود، اسلاتی که در حین کار گذشته دیگر اجرا نمی‌شود.
    """
    started = _at(9, 5)
    finished = started + timedelta(seconds=7 * 60)  # ۷ دقیقه طول کشید

    following = next_tick(finished, INTERVAL)

    assert following == _at(9, 15)
    assert following > finished, "نوبت بعدی باید در آینده باشد، نه همین حالا"


def test_a_long_sleep_produces_one_run_not_a_backlog():
    """پس از دو ساعت خوابِ ویندوز، فقط یک نوبت اجرا می‌شود."""
    expected = _at(9, 5)
    woke = _at(11, 5)  # ۲۴ اسلات جا ماند

    assert skipped_ticks(expected, woke, INTERVAL) == 24
    # ولی زمان‌بندی فقط یک اسلات بعدی می‌دهد — نه ۲۴ تا.
    assert next_tick(woke, INTERVAL) == _at(11, 10)


def test_waking_on_time_reports_no_skips():
    assert skipped_ticks(_at(9, 5), _at(9, 5, 1), INTERVAL) == 0
    # ساعتی که عقب رفته هم نباید عدد منفی بدهد
    assert skipped_ticks(_at(9, 5), _at(9, 0), INTERVAL) == 0


# ----------------------------------------------------------------------
# بازه‌ی فعالیت
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (_at(8, 59), False),   # پیش از گشایش
        (_at(9, 0), True),     # لحظه‌ی گشایش
        (_at(11, 30), True),   # میانه‌ی جلسه
        (_at(12, 30), True),   # لحظه‌ی پایان
        (_at(12, 34), True),   # داخل مهلت پایانی
        (_at(12, 36), False),  # پس از مهلت
        (_at(20, 0), False),   # شب
    ],
)
def test_recording_only_inside_the_window(moment, expected):
    verdict = decide(moment, RecordingWindow(), is_trading_day=True)
    assert verdict.should_record is expected


def test_no_recording_on_a_non_trading_day():
    verdict = decide(_at(11, 0), RecordingWindow(), is_trading_day=False)
    assert verdict.is_waiting
    assert "معاملاتی" in verdict.reason


def test_after_close_is_recorded_but_marked_as_such():
    """snapshot پس از پایان جلسه ثبت می‌شود، ولی «قیمت پایانی» نیست."""
    window = RecordingWindow()
    assert window.is_after_close(_at(12, 33))
    assert not window.is_after_close(_at(12, 0))
    assert decide(_at(12, 33), window, is_trading_day=True).should_record


def test_an_invalid_window_is_refused_up_front():
    with pytest.raises(ValueError, match="پیش از پایان"):
        RecordingWindow(start=time(13, 0), end=time(9, 0))


def test_zero_interval_is_refused():
    with pytest.raises(ValueError, match="مثبت"):
        next_tick(_at(9, 0), 0)


# ----------------------------------------------------------------------
# منطقه‌ی زمانی
# ----------------------------------------------------------------------
def test_tehran_resolves_and_has_the_right_offset():
    zone = resolve_timezone(DEFAULT_TIMEZONE)
    assert _at(11, 0).astimezone(zone).utcoffset() == timedelta(hours=3, minutes=30)


def test_an_unknown_timezone_fails_loudly_instead_of_guessing():
    """بازگشت به آفست ثابت فقط برای تهران امن است، نه برای هر نامی."""
    with pytest.raises(ValueError, match="در دسترس نیست"):
        resolve_timezone("Mars/Olympus")


# ----------------------------------------------------------------------
# قفل تک‌نمونه‌ای
# ----------------------------------------------------------------------
def test_a_second_instance_cannot_take_the_same_lock(tmp_path):
    """دو حلقه روی یک پایگاه نباید هم‌زمان ثبت کنند."""
    path = tmp_path / "market.db.lock"
    held = ProcessLock(path)
    held.__enter__()
    try:
        with pytest.raises(LockUnavailable):
            ProcessLock(path).__enter__()
    finally:
        held.__exit__(None, None, None)


def test_the_lock_is_released_for_the_next_run(tmp_path):
    path = tmp_path / "market.db.lock"
    with ProcessLock(path):
        pass
    with ProcessLock(path):  # نباید خطا بدهد
        pass


# ----------------------------------------------------------------------
# رفتار حلقه در برابر خطا — با ساعت و منبع ساختگی
# ----------------------------------------------------------------------
def test_loop_stops_before_starting_on_an_incompatible_database(tmp_path):
    """خطای غیرقابل ادامه باید **پیش از** ورود به حلقه متوقف کند."""
    from scripts import record_market
    from tests.legacy_schemas import build_legacy_db

    db = build_legacy_db(tmp_path / "old.db", 2)
    code = record_market.main(
        ["--loop", "--kind", "test", "--fixture", str(FIXTURE), "--db", str(db)]
    )
    assert code == record_market.EXIT_MISUSE


def test_loop_refuses_live_data_into_a_test_database(tmp_path):
    from scripts import record_market

    db = tmp_path / "never.db"
    code = record_market.main(["--loop", "--kind", "test", "--db", str(db)])
    assert code == record_market.EXIT_MISUSE
    assert not db.exists()


def test_a_transient_failure_does_not_stop_the_loop(tmp_path, monkeypatch):
    """یک نوبت ناموفق، حلقه را نمی‌خواباند؛ نوبت بعدی اجرا می‌شود.

    حلقه پس از سه نوبت با `KeyboardInterrupt` بسته می‌شود تا تست
    منتظر زمان واقعی نماند.
    """
    from scripts import record_market

    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return record_market.EXIT_FAILED  # خطای موقت
        if calls["n"] >= 3:
            raise KeyboardInterrupt
        return record_market.EXIT_OK

    monkeypatch.setattr(record_market, "_record_once", flaky)
    monkeypatch.setattr(record_market, "_sleep_until", lambda *a, **k: None)
    monkeypatch.setattr(record_market, "_trading_calendar", lambda *a, **k: None)

    code = record_market.main(
        [
            "--loop", "--kind", "test", "--fixture", str(FIXTURE),
            "--db", str(tmp_path / "loop.db"), "--interval", "1",
        ]
    )
    assert code == record_market.EXIT_OK      # Ctrl+C خروج تمیز است
    assert calls["n"] == 3, "پس از نوبت ناموفق باید ادامه داده باشد"


def test_waiting_outside_the_window_writes_nothing(tmp_path, monkeypatch):
    """انتظار خارج ساعت بازار نباید با خرابی دریافت اشتباه شود.

    نه snapshot ثبت می‌شود نه failure — وگرنه تاریخچه پر می‌شد از
    شکست‌های ساختگی.
    """
    from scripts import record_market

    ticks = {"n": 0}

    def never_records(*args, **kwargs):
        raise AssertionError("بیرون بازه نباید دریافتی انجام شود")

    def stop_after_two(*args, **kwargs):
        ticks["n"] += 1
        if ticks["n"] > 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(record_market, "_record_once", never_records)
    monkeypatch.setattr(record_market, "_sleep_until", stop_after_two)
    monkeypatch.setattr(record_market, "_trading_calendar", lambda *a, **k: None)
    # پنجره‌ای که «الان» قطعاً بیرونش است
    monkeypatch.setattr(
        record_market,
        "RecordingWindow",
        lambda **kw: RecordingWindow(
            start=time(0, 1), end=time(0, 2), closing_grace_seconds=0
        ),
    )

    db = tmp_path / "idle.db"
    code = record_market.main(
        ["--loop", "--kind", "test", "--fixture", str(FIXTURE), "--db", str(db)]
    )
    assert code == record_market.EXIT_OK

    connection = sqlite3.connect(str(db))
    try:
        assert connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    finally:
        connection.close()

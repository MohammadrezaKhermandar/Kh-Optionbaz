"""قفل تک‌نمونه‌ای بین‌پردازه‌ای — کتابخانه‌ی استاندارد، بدون وابستگی.

**مسئله:** دو حلقه‌ی recorder روی یک پایگاه یعنی دو دریافت هم‌زمان و
دو snapshot موازی از یک لحظه. `asyncio.Lock` داشبورد
(`web/api.py`) فقط داخل یک پردازه کار می‌کند و این را نمی‌گیرد.

**چرا قفلِ خودِ سیستم‌عامل و نه فایلِ PID:** فایل PID پس از crash
می‌ماند و نمونه‌ی بعدی را برای همیشه مسدود می‌کند، مگر اینکه منطق
«آیا این PID هنوز زنده است؟» اضافه شود — که خودش روی ویندوز و لینوکس
فرق دارد و باگ‌خیز است. قفلِ سیستم‌عامل با مرگ پردازه **خودکار** آزاد
می‌شود، حتی با kill یا قطع برق.

روی ویندوز `msvcrt.locking` و روی POSIX `fcntl.flock` — هر دو در
کتابخانه‌ی استاندارد.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import TracebackType
from typing import IO

logger = logging.getLogger(__name__)


class LockUnavailable(RuntimeError):
    """پردازه‌ی دیگری همین قفل را در اختیار دارد."""


def _acquire(handle: IO[str]) -> bool:
    """تلاش **بدون انتظار** برای گرفتن قفل. `False` یعنی کس دیگری دارد."""
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _release(handle: IO[str]) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:  # آزادسازی که شکست بخورد، با بستن فایل حل می‌شود
        logger.debug("آزادکردن قفل خطا داد (بی‌اهمیت): %s", exc)


class ProcessLock:
    """قفل انحصاری روی یک فایل، به‌صورت context manager.

    Args:
        path: مسیر فایل قفل. معمولاً کنار پایگاه، با پسوند `.lock`.
            خودِ فایل چیزی نگه نمی‌دارد؛ فقط تکیه‌گاه قفل است.

    Raises:
        LockUnavailable: اگر پردازه‌ی دیگری قفل را داشته باشد.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._handle: IO[str] | None = None

    def __enter__(self) -> ProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # حالت a+ فایل را می‌سازد و محتوایش را پاک نمی‌کند.
        handle = self.path.open("a+")
        # `msvcrt.locking` از موقعیت جاری قفل می‌کند، پس باید صفر باشد.
        handle.seek(0)
        if not _acquire(handle):
            handle.close()
            raise LockUnavailable(
                f"نمونه‌ی دیگری از recorder روی «{self.path.name}» در حال "
                f"اجراست. دو حلقه روی یک پایگاه، snapshotهای موازی از یک "
                f"لحظه می‌سازند؛ اول آن یکی را متوقف کنید."
            )
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._handle is None:
            return
        _release(self._handle)
        self._handle.close()
        self._handle = None
        # فایل عمداً پاک نمی‌شود: حذفش با قفلِ پردازه‌ی دیگر مسابقه
        # می‌سازد. یک فایل صفر-بایتی هزینه‌ای ندارد.

"""Shanghai business time, with a fixed UTC+8 fallback for modern dates."""

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    SHANGHAI = ZoneInfo("Asia/Shanghai")
except ZoneInfoNotFoundError:
    # Windows Python installations may omit IANA data. Modern Shanghai dates use UTC+8.
    SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")

import datetime
import math

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


try:
    KST = ZoneInfo("Asia/Seoul") if ZoneInfo else None
except Exception:
    KST = None


def now_kst():
    if KST:
        return datetime.datetime.now(KST).replace(tzinfo=None)
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)


def today_kst():
    return now_kst().date()


def get_exit_time(entry_dt):
    return entry_dt + datetime.timedelta(hours=5)


def remaining_minutes(exit_dt, now=None):
    now = now or now_kst()
    return math.ceil((exit_dt - now).total_seconds() / 60)


def format_remaining(exit_dt, now=None):
    minutes = remaining_minutes(exit_dt, now)
    if minutes <= 0:
        overdue = abs(minutes)
        if overdue >= 60:
            hours, mins = divmod(overdue, 60)
            return f"출차시간 경과 {hours}시간 {mins}분"
        return f"출차시간 경과 {overdue}분"

    if minutes >= 60:
        hours, mins = divmod(minutes, 60)
        return f"{hours}시간 {mins}분"
    return f"{minutes}분"

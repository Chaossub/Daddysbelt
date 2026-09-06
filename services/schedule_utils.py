from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SUPPORTED_REPEATS = {"once", "daily", "weekly", "monthly"}


def validate_timezone(value: str) -> str:
    timezone_name = value.strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ValueError(
            "Use a valid timezone such as America/Los_Angeles."
        ) from error
    return timezone_name


def parse_local_datetime(
    value: str,
    timezone_name: str,
) -> datetime:
    try:
        naive = datetime.strptime(
            value.strip(),
            "%Y-%m-%d %H:%M",
        )
    except ValueError as error:
        raise ValueError(
            "Use this format: YYYY-MM-DD HH:MM"
        ) from error

    zone = ZoneInfo(validate_timezone(timezone_name))
    local = naive.replace(tzinfo=zone)
    return local.astimezone(timezone.utc)


def normalize_repeat(value: str) -> str:
    repeat = value.strip().lower()
    if repeat not in SUPPORTED_REPEATS:
        raise ValueError(
            "Repeat must be once, daily, weekly, or monthly."
        )
    return repeat


def next_occurrence(
    current_utc: datetime,
    repeat: str,
    timezone_name: str,
) -> datetime | None:
    repeat = normalize_repeat(repeat)

    if repeat == "once":
        return None

    zone = ZoneInfo(validate_timezone(timezone_name))
    current_local = current_utc.astimezone(zone)

    if repeat == "daily":
        next_local = current_local + timedelta(days=1)
    elif repeat == "weekly":
        next_local = current_local + timedelta(weeks=1)
    else:
        year = current_local.year
        month = current_local.month + 1

        if month == 13:
            year += 1
            month = 1

        final_day = calendar.monthrange(year, month)[1]
        day = min(current_local.day, final_day)

        next_local = current_local.replace(
            year=year,
            month=month,
            day=day,
        )

    return next_local.astimezone(timezone.utc)


def format_schedule_time(
    utc_value: datetime,
    timezone_name: str,
) -> str:
    zone = ZoneInfo(validate_timezone(timezone_name))
    local = utc_value.astimezone(zone)
    return local.strftime("%Y-%m-%d %I:%M %p")

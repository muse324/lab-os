import json
import os
import re
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from db import INDIVIDUAL_MEETING_SINCE_DATE, upsert_student_meeting


GOOGLE_CALENDAR_EVENTS_URL = (
    "https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
)
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
MEETING_TITLE_RE = re.compile(
    r"個別M\s*[（(]\s*(?P<duration>\d+)\s*分\s*[）)]\s*"
    r"[（(]\s*(?P<student_name>.+?)\s*[）)]"
)
RESERVER_RE = re.compile(r"予約者:\s*(?P<student_name>.+?)(?:\s+[\w.+-]+@|$)")


def _config_value(name, default=None):
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def google_calendar_id():
    return _config_value("GOOGLE_CALENDAR_ID", "primary")


def google_calendar_timezone_name():
    return _config_value("GOOGLE_CALENDAR_TIMEZONE", "Asia/Tokyo")


def _load_json_or_text(value):
    value = (value or "").strip()
    if not value:
        return None

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _read_token_file(path):
    with open(path, encoding="utf-8") as f:
        return _load_json_or_text(f.read())


def _extract_access_token(data):
    if isinstance(data, str):
        return data.strip() or None

    if not isinstance(data, dict):
        return None

    for key in ("access_token", "token"):
        token = data.get(key)
        if token:
            return str(token).strip()

    credentials = data.get("credentials")
    if isinstance(credentials, dict):
        return _extract_access_token(credentials)

    return None


def _refresh_access_token(data):
    if not isinstance(data, dict):
        return None

    refresh_token = data.get("refresh_token")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    token_uri = data.get("token_uri") or GOOGLE_OAUTH_TOKEN_URL
    if not (refresh_token and client_id and client_secret):
        return None

    response = requests.post(
        token_uri,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=float(_config_value("GOOGLE_CALENDAR_TIMEOUT", "10")),
    )
    response.raise_for_status()
    return response.json().get("access_token")


def _token_from_data(data):
    refreshed_token = _refresh_access_token(data)
    if refreshed_token:
        return refreshed_token
    return _extract_access_token(data)


def get_google_calendar_access_token():
    direct_token = _config_value("GOOGLE_CALENDAR_ACCESS_TOKEN")
    if direct_token:
        return direct_token

    token_json = _config_value("GOOGLE_CALENDAR_TOKEN_JSON")
    token_from_json = _token_from_data(_load_json_or_text(token_json))
    if token_from_json:
        return token_from_json

    for env_name in ("GOOGLE_CALENDAR_TOKEN_PATH", "GOOGLE_CALENDAR_CREDENTIALS_PATH"):
        token_path = _config_value(env_name)
        if not token_path:
            continue
        token_from_file = _token_from_data(_read_token_file(token_path))
        if token_from_file:
            return token_from_file

    return None


def parse_meeting_title(title):
    match = MEETING_TITLE_RE.search(title or "")
    if not match:
        return None

    return {
        "duration_minutes": int(match.group("duration")),
        "student_name": match.group("student_name").strip(),
    }


def parse_reserver_name(description):
    match = RESERVER_RE.search(description or "")
    if not match:
        return None
    return match.group("student_name").strip()


def _normalize_name(value):
    return " ".join((value or "").strip().split())


def match_student_id(students, student_name):
    normalized_name = _normalize_name(student_name)
    if not normalized_name:
        return None

    for student in students or []:
        if _normalize_name(student.get("name")) != normalized_name:
            continue

        student_id = student.get("student_id")
        try:
            return int(student_id)
        except (TypeError, ValueError):
            return None

    return None


def _parse_datetime(value, timezone):
    if not value:
        return None

    if "T" not in value:
        return datetime.fromisoformat(value).replace(tzinfo=timezone)

    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone)
    return dt.astimezone(timezone)


def _event_datetime(event_part, timezone):
    event_part = event_part or {}
    return _parse_datetime(event_part.get("dateTime") or event_part.get("date"), timezone)


def _duration_from_datetimes(start_dt, end_dt):
    if not start_dt or not end_dt:
        return None

    minutes = int((end_dt - start_dt).total_seconds() // 60)
    if minutes < 0:
        return None
    return minutes


def _fallback_calendar_event_url(event_id):
    if not event_id:
        return None
    return f"https://calendar.google.com/calendar/event?eid={quote(event_id, safe='')}"


def build_meeting_from_event(event, students, timezone_name=None):
    timezone = ZoneInfo(timezone_name or google_calendar_timezone_name())
    title = (event.get("summary") or "").strip()
    description = (event.get("description") or "").strip()
    title_info = parse_meeting_title(title) or {}
    student_name = title_info.get("student_name") or parse_reserver_name(description)

    start_dt = _event_datetime(event.get("start"), timezone)
    end_dt = _event_datetime(event.get("end"), timezone)
    if not start_dt or not end_dt:
        return None, "start/end datetime is missing"

    duration_minutes = title_info.get("duration_minutes") or _duration_from_datetimes(
        start_dt, end_dt
    )
    student_id = match_student_id(students, student_name)

    warning = None
    if student_name and student_id is None:
        warning = f"student not matched: {student_name}"
    elif not student_name:
        warning = f"student name not found: event_id={event.get('id')}"

    meeting = {
        "student_id": student_id,
        "meeting_date": start_dt.date().isoformat(),
        "start_time": start_dt.strftime("%H:%M"),
        "end_time": end_dt.strftime("%H:%M"),
        "title": title,
        "summary": description,
        "topics": json.dumps([], ensure_ascii=False),
        "google_event_id": event.get("id"),
        "duration_minutes": duration_minutes,
        "calendar_source": "google_calendar",
        "source": "google_calendar",
        "calendar_event_url": event.get("htmlLink")
        or _fallback_calendar_event_url(event.get("id")),
    }
    return meeting, warning


def fetch_gcal_individual_meetings(
    since_date=INDIVIDUAL_MEETING_SINCE_DATE,
    calendar_id=None,
):
    access_token = get_google_calendar_access_token()
    if not access_token:
        raise RuntimeError(
            "Google Calendar token is not configured. Set GOOGLE_CALENDAR_ACCESS_TOKEN "
            "or GOOGLE_CALENDAR_TOKEN_PATH."
        )

    calendar_id = calendar_id or google_calendar_id()
    timezone_name = google_calendar_timezone_name()
    url = GOOGLE_CALENDAR_EVENTS_URL.format(calendar_id=quote(calendar_id, safe=""))
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "timeMin": f"{since_date}T00:00:00+09:00",
        "singleEvents": "true",
        "orderBy": "startTime",
        "q": "個別M",
        "timeZone": timezone_name,
        "maxResults": int(_config_value("GOOGLE_CALENDAR_MAX_RESULTS", "2500")),
        "fields": (
            "nextPageToken,"
            "items(id,summary,description,start,end,htmlLink,status,updated)"
        ),
    }
    timeout = float(_config_value("GOOGLE_CALENDAR_TIMEOUT", "10"))
    events = []

    while True:
        response = requests.get(url, headers=headers, params=params, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        events.extend(payload.get("items", []))

        next_page_token = payload.get("nextPageToken")
        if not next_page_token:
            break
        params["pageToken"] = next_page_token

    return [
        event
        for event in events
        if "個別M" in (event.get("summary") or "")
        and event.get("status") != "cancelled"
    ]


def sync_student_meetings_from_gcal(
    cursor,
    students,
    since_date=INDIVIDUAL_MEETING_SINCE_DATE,
    calendar_id=None,
):
    stats = {
        "since_date": since_date,
        "calendar_id": calendar_id or google_calendar_id(),
        "fetched": 0,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "warnings": [],
        "errors": [],
    }

    try:
        events = fetch_gcal_individual_meetings(since_date, calendar_id)
    except Exception as e:
        stats["errors"].append(str(e))
        print("google calendar sync failed:", e)
        return stats

    stats["fetched"] = len(events)
    timezone_name = google_calendar_timezone_name()

    for event in events:
        meeting, warning = build_meeting_from_event(event, students, timezone_name)
        if warning:
            stats["warnings"].append(warning)
            print("google calendar sync warning:", warning)

        if not meeting:
            stats["skipped"] += 1
            continue

        result = upsert_student_meeting(cursor, meeting)
        stats[result] += 1

    return stats

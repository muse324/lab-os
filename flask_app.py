from io import StringIO
from contextlib import contextmanager
from flask import Flask, render_template, request, redirect, jsonify, url_for
import json
import os
import requests
import csv
from datetime import datetime
from urllib.parse import quote
from dateutil.relativedelta import relativedelta

from db import (
    build_all_snapshots,
    build_snapshot,
    fetch_all_projects,
    fetch_export_tasks_as_memo,
    fetch_export_tasks_for_chatgpt,
    fetch_home_tasks,
    fetch_inbox_project,
    fetch_inbox_tasks,
    fetch_project_detail_rows,
    fetch_task_for_note,
    fetch_student_log_rows,
    fetch_student_meetings,
    fetch_student_research_theme_overrides,
    fetch_student_summary_rows,
    get_db,
    get_today_str,
    init_db,
    insert_imported_task,
    insert_note,
    insert_project,
    insert_task,
    mark_task_done,
    merge_project_tasks,
    move_task_to_project,
    move_tasks_to_project,
    sync_student_meetings_from_tasks,
    TASK_STAGE_DEFINITIONS,
    TASK_STAGE_LABELS,
    update_task_deadline_and_priority,
    update_task_title as update_task_title_row,
    upsert_student_research_theme,
)
from sync import (
    apply_sync_diff,
    build_gpt_memory_delta,
    build_sync_diff,
    build_sync_preview_response,
    fetch_recent_sync_history,
    normalize_imported_tasks,
    parse_sync_payload,
    update_snapshot,
    upsert_imported_task_note,
)
from services.google_calendar_sync import sync_student_meetings_from_gcal
from task_parser import (
    build_quick_task_payload,
    guess_student_id,
    local_generate_sync_tasks,
    normalize_quotes,
)

# =========================================================
# Initialization / App Setup
# =========================================================


app = Flask(__name__)  # ← これが最重要

MUSELAB_PROJECT = os.getenv("MUSELAB_PROJECT", "muselab")
MUSESTUDIO_PROJECT = os.getenv("MUSESTUDIO_PROJECT", "musestudio")
SCRAPBOX_PROJECT = os.getenv("SCRAPBOX_PROJECT", MUSESTUDIO_PROJECT)
DEADLINE_SOON_DAYS = 3


# Scrapboxはprivate運用前提のため、OS側では本文/API取得を行わずURL導線だけを扱う。
def scrapbox_project_page_url(project, title):
    page_name = (title or "").strip()
    if not page_name:
        return None
    return f"https://scrapbox.io/{project}/{quote(page_name, safe='')}"


def scrapbox_page_url(title):
    return scrapbox_project_page_url(SCRAPBOX_PROJECT, title)


def student_log_url(student_id, name):
    return url_for("student_log", student_id=student_id, name=name)


def student_links(student_id, name, muselab_page_title):
    links = {
        "os": {
            "label": "OSノート",
            "url": student_log_url(student_id, name),
            "external": False,
        },
        "musestudio": {
            "label": "musestudio",
            "url": scrapbox_project_page_url(MUSESTUDIO_PROJECT, name),
            "external": True,
        },
    }

    muselab_url = scrapbox_project_page_url(MUSELAB_PROJECT, muselab_page_title)
    if muselab_url:
        links["muselab"] = {
            "label": "muselab",
            "url": muselab_url,
            "external": True,
        }

    return links


@app.context_processor
def inject_scrapbox_helpers():
    return {
        "scrapbox_page_url": scrapbox_page_url,
        "scrapbox_project_page_url": scrapbox_project_page_url,
        "scrapbox_base_url": f"https://scrapbox.io/{SCRAPBOX_PROJECT}/",
    }


@app.context_processor
def inject_task_stage_helpers():
    return {
        "task_stages": TASK_STAGE_DEFINITIONS,
        "task_stage_labels": TASK_STAGE_LABELS,
    }


@contextmanager
def db_cursor(commit=False):
    conn = get_db()
    try:
        yield conn.cursor()
        if commit:
            conn.commit()
    finally:
        conn.close()


def next_url(default="/"):
    return request.form.get("next") or default


def resolve_student_request(student_id_arg, requested_student_name=""):
    student_name = requested_student_name
    student_id = normalize_student_id_value(student_id_arg)
    if student_id is None and not student_name:
        return None

    student = find_student(student_id, student_name)
    if not student and student_id is not None:
        student = {
            "name": student_name or f"student_id:{student_id}",
            "student_id": student_id,
        }
    if not student and student_name:
        student = {"name": student_name, "student_id": student_id}
    if not student:
        return None

    student_id = normalize_student_id_value(student["student_id"])
    student_name = student.get("name") or student_name or f"student_id:{student_id}"
    student_aliases = student_aliases_for_id(student_id, student_name)
    if requested_student_name and requested_student_name not in student_aliases:
        student_aliases.append(requested_student_name)

    return {
        "student": student,
        "student_id": student_id,
        "student_name": student_name,
        "student_aliases": student_aliases,
    }


# =========================================================
# Student Data Loading
# =========================================================
def load_students():
    url = "https://docs.google.com/spreadsheets/d/14syTM70RCLc7UQZcK9btrU8PgsaPVKTDYhGhDvIkkao/export?format=csv"

    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        res.encoding = "utf-8"

        f = StringIO(res.text)
        reader = csv.DictReader(f)

        students = []

        for row in reader:
            name = row.get("氏名")
            nickname = row.get("呼び名")
            student_id = row.get("学籍番号")
            grade = row.get("学年") or ""
            research_theme = row.get("研究テーマ") or ""

            if name and name != "橋田光代":
                students.append(
                    {
                        "name": name,
                        "student_id": int(student_id) if student_id else None,
                        "grade": grade,
                        "research_theme": research_theme,
                    }
                )

            if nickname:
                students.append(
                    {
                        "name": nickname,
                        "student_id": int(student_id) if student_id else None,
                        "grade": grade,
                        "research_theme": research_theme,
                    }
                )

        return students

    except Exception as e:
        print("学生名簿取得失敗:", e)
        return []


# グローバルで読み込み
STUDENTS_DATA = load_students()


def normalize_student_id_value(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def find_student(student_id=None, student_name=""):
    normalized_id = normalize_student_id_value(student_id)

    if normalized_id is not None:
        for s in STUDENTS_DATA:
            if normalize_student_id_value(s.get("student_id")) == normalized_id:
                return s

    if student_name:
        for s in STUDENTS_DATA:
            if s.get("name") == student_name and s.get("student_id"):
                return s

    return None


def student_aliases_for_id(student_id, primary_name=""):
    normalized_id = normalize_student_id_value(student_id)
    aliases = []

    if primary_name:
        aliases.append(primary_name)

    for s in STUDENTS_DATA:
        if normalize_student_id_value(s.get("student_id")) != normalized_id:
            continue
        name = s.get("name")
        if name and name not in aliases:
            aliases.append(name)

    return aliases


@app.route("/export_snapshot/<project_name>")
def export_snapshot(project_name):
    conn = get_db()
    c = conn.cursor()

    snapshot = build_snapshot(project_name, c)

    conn.close()
    return jsonify(snapshot)


# =========================================================
# Snapshot Formatting
# =========================================================
def format_snapshot_as_scrapbox(snapshot):
    lines = []

    lines.append(f"[{snapshot['project']}]")
    lines.append("")
    lines.append("[現在状態]")
    for item in snapshot["current_state"]:
        lines.append(f" {item}")

    lines.append("")
    lines.append("[未完了]")
    for item in snapshot["todos"]:
        lines.append(f" {item}")

    lines.append("")
    lines.append("#snapshot")

    return "\n".join(lines)


@app.route("/export_snapshot_scrapbox/<project_name>")
def export_snapshot_scrapbox(project_name):
    conn = get_db()
    c = conn.cursor()

    snapshot = build_snapshot(project_name, c)
    text = format_snapshot_as_scrapbox(snapshot)

    conn.close()

    return text, 200, {"Content-Type": "text/plain; charset=utf-8"}


def format_all_snapshots_as_scrapbox(snapshots):
    lines = []
    lines.append("Lab OS Active")
    lines.append("")
    lines.append("[プロジェクト横断 snapshot]")
    lines.append("")

    for snapshot in snapshots:
        lines.append(f"[** {snapshot['project']}]")

        if snapshot["current_state"]:
            lines.append("[現在状態]")
            for item in snapshot["current_state"]:
                lines.append(f" {item}")

        if snapshot["todos"]:
            lines.append("[未完了]")
            for item in snapshot["todos"]:
                lines.append(f" {item}")

        lines.append("")

    lines.append("#snapshot")
    lines.append("#active")

    return "\n".join(lines).strip() + "\n"


@app.route("/export_snapshot_scrapbox_all")
def export_snapshot_scrapbox_all():
    with db_cursor() as c:
        snapshots = build_all_snapshots(c)
    text = format_all_snapshots_as_scrapbox(snapshots)

    return text, 200, {"Content-Type": "text/plain; charset=utf-8"}


# =========================================================
# Formatting Utilities
# =========================================================
def format_date_jp(date_str):
    if not date_str:
        return None
    from datetime import datetime

    weekdays_jp = ["月", "火", "水", "木", "金", "土", "日"]
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        return date_str

    today = datetime.now()
    fiscal_year = today.year if today.month < 4 else today.year + 1

    # 年度基準：4/1〜翌3/31
    start = datetime(fiscal_year - 1, 4, 1)
    end = datetime(fiscal_year, 3, 31)

    w = weekdays_jp[dt.weekday()]

    if start <= dt <= end:
        return dt.strftime(f"%m/%d({w})")
    else:
        return dt.strftime(f"%Y/%m/%d({w})")


def format_history_rows(rows):
    result = []
    for r in rows:
        d = dict(r)
        if "deadline" in d and d["deadline"]:
            d["deadline_display"] = format_date_jp(d["deadline"])
        else:
            d["deadline_display"] = None

        if "old_deadline" in d and d["old_deadline"]:
            d["old_deadline_display"] = format_date_jp(d["old_deadline"])
        else:
            d["old_deadline_display"] = None

        if "new_deadline" in d and d["new_deadline"]:
            d["new_deadline_display"] = format_date_jp(d["new_deadline"])
        else:
            d["new_deadline_display"] = None

        result.append(d)
    return result


def format_student_meeting_rows(rows):
    result = []
    for row in rows:
        meeting = dict(row)
        meeting["meeting_date_display"] = format_date_jp(meeting.get("meeting_date"))
        meeting["duration_minutes"] = meeting.get("duration_minutes")
        meeting["source_display"] = (
            meeting.get("calendar_source") or meeting.get("source") or "unknown"
        )
        if meeting.get("google_event_id") and not meeting.get("calendar_event_url"):
            meeting["calendar_event_url"] = (
                "https://calendar.google.com/calendar/event?eid="
                + quote(str(meeting["google_event_id"]), safe="")
            )
        topics = []
        raw_topics = meeting.get("topics")

        if raw_topics:
            try:
                parsed_topics = json.loads(raw_topics)
                if isinstance(parsed_topics, list):
                    topics = [str(topic) for topic in parsed_topics if topic]
            except (TypeError, ValueError):
                topics = [
                    topic.strip()
                    for topic in str(raw_topics).split("/")
                    if topic.strip()
                ]

        meeting["topics_list"] = topics
        result.append(meeting)
    return result


# --- Apply deadline/formatting for tasks ---
def is_deadline_soon(deadline, today=None):
    if not deadline:
        return False

    try:
        deadline_date = datetime.strptime(deadline, "%Y-%m-%d").date()
    except ValueError:
        return False

    today = today or datetime.now().date()
    days_left = (deadline_date - today).days
    return 0 <= days_left <= DEADLINE_SOON_DAYS


def apply_format(rows):
    result = []
    today = datetime.now().date()
    for r in rows:
        d = dict(r)

        if "deadline" in d and d["deadline"]:
            d["deadline_display"] = format_date_jp(d["deadline"])
        else:
            d["deadline_display"] = None

        if "original_deadline" in d and d["original_deadline"]:
            d["original_deadline_display"] = format_date_jp(d["original_deadline"])
        else:
            d["original_deadline_display"] = None

        d["deadline_soon"] = (
            d.get("status") != "done"
            and int(d.get("archived") or 0) == 0
            and is_deadline_soon(d.get("deadline"), today)
        )
        task_stage = d.get("task_stage") or "idea"
        d["task_stage"] = task_stage
        d["task_stage_label"] = TASK_STAGE_LABELS.get(task_stage, task_stage)

        result.append(d)

    return result


# =========================================================
# Task Classification
# =========================================================
def classify_tasks(rows, today_str):
    overdue_tasks = [
        t
        for t in rows
        if t["archived"] == 0
        and t["status"] != "done"
        and t["deadline"]
        and t["deadline"] < today_str
    ]
    today_tasks = [
        t
        for t in rows
        if t["archived"] == 0 and t["status"] != "done" and t["deadline"] == today_str
    ]
    future_tasks = [
        t
        for t in rows
        if t["archived"] == 0
        and t["status"] != "done"
        and t["deadline"]
        and t["deadline"] > today_str
    ]
    anytime_tasks = [
        t
        for t in rows
        if t["archived"] == 0 and t["status"] != "done" and not t["deadline"]
    ]
    anytime_tasks = sorted(
        anytime_tasks,
        key=lambda t: (
            t.get("source_updated_at") or "",
            t.get("task_id") or 0,
        ),
        reverse=True,
    )
    done_tasks = [t for t in rows if t["archived"] == 0 and t["status"] == "done"]
    archived_tasks = [t for t in rows if t["archived"] == 1]

    return {
        "overdue_tasks": overdue_tasks,
        "today_tasks": today_tasks,
        "future_tasks": future_tasks,
        "anytime_tasks": anytime_tasks,
        "done_tasks": done_tasks,
        "archived_tasks": archived_tasks,
    }


def filter_future_tasks_by_months(tasks_todo, today_str, months):
    today_date = datetime.strptime(today_str, "%Y-%m-%d").date()

    if months == 0:
        # 月末まで（今月フィルタ）
        next_month = today_date + relativedelta(months=1, day=1)
        limit_date = next_month - relativedelta(days=1)
    else:
        # Nヶ月先（従来通り）
        limit_date = today_date + relativedelta(months=months)

    filtered_tasks_todo = []
    for t in tasks_todo:
        d = t.get("deadline")
        if not d:
            continue
        if isinstance(d, str):
            try:
                # Try full ISO format first (e.g., 2026-04-23T09:00:00)
                d = datetime.fromisoformat(d).date()
            except ValueError:
                # Fallback to date-only format
                d = datetime.strptime(d.split("T")[0], "%Y-%m-%d").date()
        if today_date < d <= limit_date:
            filtered_tasks_todo.append(t)

    return filtered_tasks_todo


def build_home_context(months):
    with db_cursor() as c:
        today_str = get_today_str(c)
        all_tasks = apply_format(fetch_home_tasks(c))
        classified = classify_tasks(all_tasks, today_str)

        return {
            "today": classified["today_tasks"],
            "overdue": classified["overdue_tasks"],
            "tasks_todo": filter_future_tasks_by_months(
                classified["future_tasks"], today_str, months
            ),
            "projects": fetch_all_projects(c),
            "tasks_done": classified["done_tasks"],
            "tasks_todo_anytime": classified["anytime_tasks"],
        }


def attach_related_notes(tasks, notes_by_task_id):
    return [
        {**dict(t), "related_notes": notes_by_task_id.get(dict(t)["task_id"], [])}
        for t in tasks
    ]


def build_archived_tasks_with_reason(archived_rows):
    archived_tasks = []
    for t in archived_rows:
        d = dict(t)
        if d.get("source_type") == "manual_merge":
            d["archive_reason"] = "タスク統合でアーカイブ"
        elif d.get("source_type") in ["chatgpt_memory", "manual_json"] and d.get(
            "sync_key"
        ):
            d["archive_reason"] = "同期差分でアーカイブ"
        elif d.get("source_type"):
            d["archive_reason"] = f"{d['source_type']}由来のアーカイブ"
        else:
            d["archive_reason"] = "手動または不明"
        archived_tasks.append(d)
    return archived_tasks


def build_project_detail_context(project_id, task_id_filter):
    with db_cursor() as c:
        today_str = get_today_str(c)
        project, all_tasks, history, notes = fetch_project_detail_rows(
            c, project_id, task_id_filter
        )

        all_tasks = apply_format(all_tasks)
        classified = classify_tasks(all_tasks, today_str)
        archived_tasks = build_archived_tasks_with_reason(classified["archived_tasks"])

        notes_by_task_id = {}
        unlinked_notes = []
        for n in notes:
            nd = dict(n)
            task_id = nd.get("task_id")
            if task_id:
                notes_by_task_id.setdefault(task_id, []).append(nd)
            else:
                unlinked_notes.append(nd)

        return {
            "project": project,
            "projects": fetch_all_projects(c),
            "tasks": attach_related_notes(all_tasks, notes_by_task_id),
            "overdue_tasks": attach_related_notes(
                classified["overdue_tasks"], notes_by_task_id
            ),
            "today_tasks": attach_related_notes(
                classified["today_tasks"], notes_by_task_id
            ),
            "future_tasks": attach_related_notes(
                classified["future_tasks"], notes_by_task_id
            ),
            "anytime_tasks": attach_related_notes(
                classified["anytime_tasks"], notes_by_task_id
            ),
            "done_tasks": attach_related_notes(
                classified["done_tasks"], notes_by_task_id
            ),
            "archived_tasks": attach_related_notes(archived_tasks, notes_by_task_id),
            "notes": notes,
            "unlinked_notes": unlinked_notes,
            "history": history,
        }


# =========================================================
# Routes: Main Views
# =========================================================
@app.route("/")
def home():
    months = max(0, min(request.args.get("months", 0, type=int), 12))
    context = build_home_context(months)

    return render_template(
        "index.html",
        **context,
        is_production=os.getenv("IS_PRODUCTION") == "1",
        students=STUDENTS_DATA,
        months=months,
    )


@app.route("/inbox")
def inbox_view():
    conn = get_db()
    c = conn.cursor()

    inbox = fetch_inbox_project(c)
    if not inbox:
        conn.close()
        return "Inbox not found"

    tasks = apply_format(fetch_inbox_tasks(c, inbox["id"]))
    projects = fetch_all_projects(c)
    conn.close()

    return render_template("inbox_review.html", tasks=tasks, projects=projects)


@app.route("/move_task", methods=["POST"])
def move_task():
    task_id = request.form["task_id"]
    new_project_id = request.form["project_id"]

    with db_cursor(commit=True) as c:
        move_task_to_project(c, task_id, new_project_id)

    return redirect(next_url("/inbox"))


@app.route("/move_tasks", methods=["POST"])
def move_tasks():
    task_ids = request.form.getlist("task_ids")
    new_project_id = request.form.get("project_id")

    if not new_project_id:
        return "project_id is required", 400

    if not task_ids:
        return redirect(next_url())

    with db_cursor(commit=True) as c:
        move_tasks_to_project(c, task_ids, new_project_id)

    return redirect(next_url())


@app.route("/update_task", methods=["POST"])
def update_task():
    task_id = request.form["task_id"]
    new_deadline = request.form.get("deadline")
    make_high = request.form.get("priority")
    priority_submitted = request.form.get("priority_present") == "1"
    task_stage = request.form.get("task_stage")
    task_stage_submitted = request.form.get("task_stage_present") == "1"

    with db_cursor(commit=True) as c:
        update_task_deadline_and_priority(
            c,
            task_id,
            new_deadline,
            make_high,
            priority_submitted,
            task_stage,
            task_stage_submitted,
        )

    return redirect(next_url())


# =========================================================
# Routes: Student Views
# =========================================================
@app.route("/students")
def students_index():
    students = [s for s in STUDENTS_DATA if s.get("student_id")]

    # 学籍番号昇順（上級生→下級生）
    students.sort(key=lambda x: x["student_id"])

    with db_cursor() as c:
        theme_overrides = fetch_student_research_theme_overrides(c)

    student_rows = []
    seen_student_ids = set()
    for s in students:
        student_id = normalize_student_id_value(s.get("student_id"))
        if student_id in seen_student_ids:
            continue
        seen_student_ids.add(student_id)

        theme_override = theme_overrides.get(student_id)
        research_theme = (s.get("research_theme") or "").strip()
        theme_source = "sheet"
        muselab_page_title = research_theme
        if theme_override:
            research_theme = theme_override["research_theme"]
            muselab_page_title = theme_override["muselab_page_title"] or research_theme
            theme_source = "local"

        student_rows.append(
            {
                "name": s["name"],
                "student_id": student_id,
                "grade": s.get("grade", ""),
                "research_theme": research_theme,
                "theme_source": theme_source,
                "links": student_links(student_id, s["name"], muselab_page_title),
            }
        )

    return render_template(
        "students.html",
        students=student_rows,
        reload_status=request.args.get("reload"),
        theme_status=request.args.get("theme"),
    )


@app.route("/students/reload", methods=["POST"])
def reload_students():
    global STUDENTS_DATA

    students = load_students()
    if not students:
        return redirect("/students?reload=failed")

    STUDENTS_DATA = students
    return redirect("/students?reload=ok")


@app.route("/students/research_theme", methods=["POST"])
def update_student_research_theme():
    student_id = normalize_student_id_value(request.form.get("student_id"))
    research_theme = request.form.get("research_theme", "").strip()

    if student_id is None:
        return redirect("/students?theme=failed")

    muselab_page_title = research_theme

    with db_cursor(commit=True) as c:
        upsert_student_research_theme(
            c,
            student_id,
            research_theme,
            muselab_page_title,
        )

    return redirect(f"/students?theme=ok#student-{student_id}")


@app.route("/student_log")
def student_log():
    student_id_arg = request.args.get("student_id")
    requested_student_name = request.args.get("name", "")
    resolved = resolve_student_request(student_id_arg, requested_student_name)
    if not resolved:
        return redirect("/students")

    student = resolved["student"]
    student_id = resolved["student_id"]
    student_name = resolved["student_name"]
    student_aliases = resolved["student_aliases"]

    with db_cursor() as c:
        theme_overrides = fetch_student_research_theme_overrides(c)
        tasks, notes, history = fetch_student_log_rows(c, student_id, student_aliases)
        meetings = fetch_student_meetings(c, student_id)

    research_theme = (student.get("research_theme") or "").strip()
    muselab_page_title = research_theme
    theme_override = theme_overrides.get(student_id)
    if theme_override:
        research_theme = theme_override["research_theme"]
        muselab_page_title = theme_override["muselab_page_title"] or research_theme

    tasks = format_history_rows(tasks)
    history = format_history_rows(history)
    meetings = format_student_meeting_rows(meetings)
    notes = [dict(n) for n in notes]
    links = student_links(student_id, student_name, muselab_page_title)
    scrapbox_links = {
        link_id: link for link_id, link in links.items() if link.get("external")
    }

    return render_template(
        "student_log.html",
        student_name=student_name,
        student_id=student_id,
        links=links,
        scrapbox_links=scrapbox_links,
        tasks=tasks,
        meetings=meetings,
        notes=notes,
        history=history,
    )


# === student_summary API ===
@app.route("/student_summary")
def student_summary():
    student_id_arg = request.args.get("student_id")
    requested_student_name = request.args.get("name", "")
    if student_id_arg is None and not requested_student_name:
        return jsonify({"error": "name or student_id is required"}), 400

    resolved = resolve_student_request(student_id_arg, requested_student_name)
    if not resolved:
        return jsonify({"error": "student not found"}), 404

    student_id = resolved["student_id"]
    student_name = resolved["student_name"]
    display_student_name = requested_student_name or student_name
    student_aliases = resolved["student_aliases"]

    with db_cursor() as c:
        todo_count, done_count, overdue_count, next_tasks = fetch_student_summary_rows(
            c, student_id, student_aliases
        )

    return jsonify(
        {
            "name": display_student_name,
            "student_id": student_id,
            "todo_count": todo_count,
            "done_count": done_count,
            "overdue_count": overdue_count,
            "next_tasks": [
                {"title": row["title"], "deadline": row["deadline"]}
                for row in next_tasks
            ],
        }
    )


# =========================================================
# Routes: Project Views
# =========================================================
@app.route("/projects")
def projects():
    with db_cursor() as c:
        projects = fetch_all_projects(c)

    return render_template("projects.html", projects=projects)


@app.route("/add_project", methods=["POST"])
def add_project():
    name = request.form["name"]
    type_ = request.form["type"]

    with db_cursor(commit=True) as c:
        insert_project(c, name, type_)

    return redirect("/projects")


# =========================================================
# Routes: Task Operations
# =========================================================
@app.route("/add_task", methods=["POST"])
def add_task():
    title = request.form["title"]
    project_id = request.form["project_id"]
    deadline = request.form.get("deadline") or None
    priority = request.form.get("priority", "medium")
    student_id = normalize_student_id_value(request.form.get("student_id"))
    if student_id is None:
        student_id = guess_student_id(title, STUDENTS_DATA)

    with db_cursor(commit=True) as c:
        insert_task(c, title, project_id, deadline, priority, student_id)

    return redirect(next_url())


@app.route("/done/<int:task_id>", methods=["POST"])
def done_task(task_id):
    with db_cursor(commit=True) as c:
        mark_task_done(c, task_id)
    return redirect(next_url())


@app.route("/project/<int:project_id>")
def project_detail(project_id):
    task_id_filter = request.args.get("task_id", type=int)
    context = build_project_detail_context(project_id, task_id_filter)

    return render_template(
        "project_detail.html",
        **context,
        students=STUDENTS_DATA,
    )


@app.route("/merge_tasks", methods=["POST"])
def merge_tasks():
    project_id = request.form.get("project_id", type=int)
    keep_task_id = request.form.get("keep_task_id", type=int)
    merge_task_ids = request.form.getlist("merge_task_ids")
    merged_title = request.form.get("merged_title")
    merged_deadline = request.form.get("merged_deadline")
    merged_priority = "high" if request.form.get("merged_priority") == "high" else None
    next_url = request.form.get("next") or (
        f"/project/{project_id}" if project_id else "/"
    )

    if not project_id or not keep_task_id:
        return "project_id and keep_task_id are required", 400

    with db_cursor(commit=True) as c:
        merge_project_tasks(
            c,
            project_id,
            keep_task_id,
            merge_task_ids,
            merged_title,
            merged_deadline,
            merged_priority,
        )

    return redirect(next_url)


@app.route("/add_note", methods=["POST"])
def add_note():
    title = request.form.get("title", "").strip()
    content = request.form.get("content", "").strip()
    project_id = request.form["project_id"]
    student_id = normalize_student_id_value(request.form.get("student_id"))
    task_id = request.form.get("task_id") or None
    scrapbox_url = request.form.get("scrapbox_url", "").strip()

    with db_cursor(commit=True) as c:
        task = fetch_task_for_note(c, task_id) if task_id else None
        if task:
            title = title or task["title"]
            project_id = task["project_id"] or project_id
            student_id = student_id if student_id is not None else task["student_id"]
            scrapbox_url = scrapbox_url or scrapbox_page_url(task["title"])
        else:
            task_id = None
            scrapbox_url = scrapbox_url or scrapbox_page_url(title)

        if not title:
            return "title is required", 400

        insert_note(c, title, content, project_id, student_id, task_id, scrapbox_url)

    return redirect(next_url(f"/project/{project_id}"))


@app.route("/quick_add", methods=["POST"])
def quick_add():
    text = request.form["text"]

    with db_cursor(commit=True) as c:
        payload = build_quick_task_payload(text, c, STUDENTS_DATA)
        insert_task(
            c,
            payload["title"],
            payload["project_id"],
            payload["deadline"] or None,
            payload["priority"],
            payload["student_id"],
        )

    return redirect("/")


# ChatGPTからのタスク一括インポート（簡易版）
@app.route("/import_tasks", methods=["POST"])
def import_tasks():
    import json

    raw = request.form.get("json")
    raw = normalize_quotes(raw)
    data = json.loads(raw)

    with db_cursor(commit=True) as c:
        normalized_items, _ = normalize_imported_tasks(data, c, STUDENTS_DATA)

        for t in normalized_items:
            task_id = insert_imported_task(c, t)
            upsert_imported_task_note(c, task_id, t)

    return redirect("/")


# ChatGPTを使わないローカルタスク抽出器
@app.route("/generate_sync_json", methods=["POST"])
def generate_sync_json():
    memo_text = request.form.get("memo", "")
    memo_text = normalize_quotes(memo_text).strip()
    if not memo_text:
        return jsonify({"error": "memo is empty"}), 400

    try:
        tasks = local_generate_sync_tasks(memo_text, STUDENTS_DATA)
        return jsonify({"tasks": tasks, "mode": "local"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/sync_preview", methods=["POST"])
def sync_preview():
    raw_json = request.form.get("json", "")
    data, error = parse_sync_payload(raw_json)
    if error:
        return jsonify(error), 400

    with db_cursor() as c:
        response = build_sync_preview_response(data, c, STUDENTS_DATA)

    return jsonify(response)


@app.route("/sync_apply", methods=["POST"])
def sync_apply():
    raw_json = request.form.get("json", "")
    data, error = parse_sync_payload(raw_json)
    if error:
        return jsonify(error), 400

    with db_cursor(commit=True) as c:
        diff = build_sync_diff(data, c, STUDENTS_DATA)
        created, updated, archived, updated_task_ids = apply_sync_diff(c, diff)
        if created or updated or archived:
            update_snapshot(c)
        sync_history = fetch_recent_sync_history(c)

    return jsonify(
        {
            "created": created,
            "updated": updated,
            "updated_task_ids": updated_task_ids,
            "archived": archived,
            "unchanged": len(diff["unchanged"]),
            "errors": diff["errors"],
            "sync_history": sync_history,
        }
    )


# --- 新規: 選択同期 ---
@app.route("/sync_apply_selected", methods=["POST"])
def sync_apply_selected():
    import json

    raw_json = request.form.get("json", "")
    raw_selected = request.form.get("selected", "{}")

    data, error = parse_sync_payload(raw_json)
    if error:
        return jsonify(error), 400

    try:
        selected = json.loads(raw_selected)
    except Exception:
        return jsonify({"error": "selected is invalid JSON"}), 400

    selected_create = set(selected.get("create", []))
    selected_update = set(selected.get("update", []))
    selected_archive = set(selected.get("archive", []))

    def get_key(item):
        if isinstance(item, dict):
            return item.get("sync_key")
        return item

    with db_cursor(commit=True) as c:
        diff = build_sync_diff(data, c, STUDENTS_DATA)

        filtered_diff = dict(diff)
        filtered_diff["create"] = [
            item for item in diff.get("create", []) if get_key(item) in selected_create
        ]
        filtered_diff["update"] = [
            item for item in diff.get("update", []) if get_key(item) in selected_update
        ]
        filtered_diff["archive"] = [
            item
            for item in diff.get("archive", [])
            if get_key(item) in selected_archive
        ]
        filtered_diff["unchanged"] = []

        created, updated, archived, updated_task_ids = apply_sync_diff(c, filtered_diff)
        if created or updated or archived:
            update_snapshot(c)
        sync_history = fetch_recent_sync_history(c)

    return jsonify(
        {
            "created": created,
            "updated": updated,
            "updated_task_ids": updated_task_ids,
            "archived": archived,
            "unchanged": len(diff.get("unchanged", [])),
            "errors": diff.get("errors", []),
            "sync_history": sync_history,
            "selected": selected,
        }
    )


@app.route("/deploy", methods=["POST"])
def deploy():
    import subprocess  # ←これだけ残す

    # 本番環境以外では実行禁止
    if os.getenv("IS_PRODUCTION") != "1":
        return (
            jsonify(
                {"status": "error", "message": "deployは本番環境のみ許可されています"}
            ),
            403,
        )

    try:
        result = subprocess.run(
            ["bash", os.path.expanduser("~/deploy.sh")],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            print("DEPLOY STDOUT:", result.stdout)
            return jsonify(
                {"status": "success", "message": "deployed!", "log": result.stdout}
            )
        else:
            print("DEPLOY STDERR:", result.stderr)
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": result.stderr or "Unknown error",
                    }
                ),
                500,
            )

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ChatGPT向けタスクエクスポート
@app.route("/export_tasks_for_chatgpt")
def export_tasks_for_chatgpt():
    with db_cursor() as c:
        rows = fetch_export_tasks_for_chatgpt(c)

    tasks = []
    for r in rows:
        tasks.append(
            {
                "title": r["title"],
                "deadline": r["deadline"],
                "priority": r["priority"],
                "status": r["status"],
                "project": r["project_name"],
                "student_id": r["student_id"],
            }
        )

    return jsonify({"tasks": tasks})


# 人間向けタスクエクスポート（メモ形式）
@app.route("/export_tasks_as_memo")
def export_tasks_as_memo():
    conn = get_db()
    c = conn.cursor()

    rows = fetch_export_tasks_as_memo(c)
    conn.close()

    lines = []

    for r in rows:
        line = r["title"]

        if r["deadline"]:
            line += f"（{r['deadline']}まで）"

        if r["priority"] == "high":
            line += "（重要）"

        if r["project_name"]:
            line += f" @{r['project_name']}"

        lines.append(line)

    return jsonify({"memo": "\n".join(lines)})


@app.route("/export_delta_for_gpt")
def export_delta_for_gpt():
    conn = get_db()
    c = conn.cursor()

    payload = build_gpt_memory_delta(c)

    conn.close()

    return jsonify(payload)


@app.route("/mark_gpt_delta_exported", methods=["POST"])
def mark_gpt_delta_exported():
    conn = get_db()
    c = conn.cursor()

    try:
        update_snapshot(c)
        conn.commit()
        snapshot_items = c.execute("SELECT COUNT(*) FROM sync_snapshot").fetchone()[0]
    finally:
        conn.close()

    return jsonify({"status": "ok", "snapshot_items": snapshot_items})


@app.route("/student_meetings/rebuild", methods=["POST"])
def rebuild_student_meetings():
    conn = get_db()
    c = conn.cursor()

    try:
        stats = sync_student_meetings_from_tasks(c)
        conn.commit()
        print("student_meetings rebuild:", stats)
    finally:
        conn.close()

    return jsonify({"status": "ok", **stats})


@app.route("/student_meetings/sync_google_calendar", methods=["POST"])
def sync_google_calendar_student_meetings():
    conn = get_db()
    c = conn.cursor()
    stats = {}

    try:
        stats = sync_student_meetings_from_gcal(c, STUDENTS_DATA)
        conn.commit()
        print("google calendar student_meetings sync:", stats)
    except Exception as e:
        conn.rollback()
        stats = {"errors": [str(e)]}
        print("google calendar student_meetings sync failed:", e)
    finally:
        conn.close()

    status = "error" if stats.get("errors") else "ok"
    return jsonify({"status": status, **stats})


@app.route("/edit_task_title", methods=["POST"])
def edit_task_title():
    task_id = request.form["task_id"]
    new_title = request.form["title"]

    if not new_title:
        return redirect("/")

    with db_cursor(commit=True) as c:
        update_task_title_row(c, task_id, new_title)

    return redirect(next_url())


@app.route("/gpt_memory_sync", methods=["POST"])
def gpt_memory_sync():
    data = request.get_json(force=True, silent=True) or {}
    raw_text = data.get("text", "")

    if not raw_text:
        return jsonify({"error": "text is required"}), 400

    try:
        tasks = extract_tasks_from_gpt_memory(raw_text)
    except Exception as e:
        return jsonify({"error": f"extract failed: {str(e)}"}), 500

    normalized_tasks = []
    for t in tasks:
        try:
            nt = normalize_task(t)

            # デフォルト補完
            nt.setdefault("priority", "medium")
            nt.setdefault("status", "active")
            nt.setdefault("notes", "")
            nt.setdefault("project", nt.get("project") or "General")

            normalized_tasks.append(nt)

        except Exception as e:
            print("normalize error:", t, e)

    with db_cursor(commit=True) as c:
        diff = build_sync_diff(normalized_tasks, c, STUDENTS_DATA)
        created, updated, archived, updated_task_ids = apply_sync_diff(c, diff)
        if created or updated or archived:
            update_snapshot(c)

    # --- ログ出力 ---
    print("=== GPT MEMORY SYNC RESULT ===")
    print(f"created: {created}")
    print(f"updated: {updated}")
    print(f"archived: {archived}")
    print(f"task_count: {len(normalized_tasks)}")

    for t in normalized_tasks:
        print(f"[TASK] {t.get('title')} | {t.get('project')} | {t.get('deadline')}")

    return jsonify(
        {
            "status": "ok",
            "created": created,
            "updated": updated,
            "updated_task_ids": updated_task_ids,
            "archived": archived,
            "task_count": len(normalized_tasks),
            "tasks": normalized_tasks,  # ← UI確認用
        }
    )


def normalize_project_name(text):
    aliases = {
        "AR楽譜インタフェース": "XR音楽理論インタフェース",
        "AR音楽理論インタフェース": "XR音楽理論インタフェース",
        "XR音楽理論インタフェース": "XR音楽理論インタフェース",
    }

    for old, new in aliases.items():
        if old in text:
            return new, old

    # 上位概念は project にしない
    if "音楽理論ビジュアライゼーション" in text:
        return None, None

    return None, None


def normalize_task(task):
    joined = " ".join(
        [
            str(task.get("title", "")),
            str(task.get("project", "")),
            str(task.get("notes", "")),
        ]
    )

    project, original = normalize_project_name(joined)

    if project:
        task["project"] = project

    if original:
        note = task.get("notes") or ""
        if "元表記" not in note:
            task["notes"] = (note + f" / 元表記: {original}").strip()

    task["sync_key"] = make_sync_key(task)

    return task


import re
import hashlib


def extract_tasks_from_gpt_memory(text):
    tasks = local_generate_sync_tasks(text, STUDENTS_DATA)

    # 空タイトル除去
    return [t for t in tasks if t.get("title")]


def make_sync_key(task):
    base = "|".join(
        [task.get("project") or "", task.get("title") or "", task.get("deadline") or ""]
    )

    normalized = re.sub(r"\s+", "", base.lower())
    digest = hashlib.md5(normalized.encode("utf-8")).hexdigest()[:10]

    return f"gpt_{digest}"


@app.route("/gpt_memory_sync_test")
def gpt_memory_sync_test():
    return """
    <html>
    <body>
    <h2>GPT Memory Sync Test</h2>
    <textarea id="input" style="width:100%;height:200px;"></textarea><br>
    <button onclick="send()">Sync</button>
    <pre id="output"></pre>

    <script>
    async function send() {
        const text = document.getElementById('input').value;
        const res = await fetch('/gpt_memory_sync', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text})
        });

        const data = await res.json();
        document.getElementById('output').textContent = JSON.stringify(data, null, 2);
    }
    </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001)

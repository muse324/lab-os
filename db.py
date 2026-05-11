import json
import os
import re
import sqlite3
from datetime import date, timedelta


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")

RESEARCH_NOTE_TITLE_RE = re.compile(
    r"^第(?P<index>\d+)回(?P<space>\s*)研究のおと（note\.com）を執筆する$"
)
PRACTICE_TITLE_RE = re.compile(r"^.+を練習する$")
INDIVIDUAL_MEETING_TITLE_PREFIX = "個別M:"
INDIVIDUAL_MEETING_SINCE_DATE = "2026-04-01"
MEETING_TIME_RE = re.compile(
    r"(?P<start>[0-2]?\d[:：][0-5]\d)\s*[-ー〜~–－−—]\s*(?P<end>[0-2]?\d[:：][0-5]\d)"
)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    # ロック対策
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")

    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            type TEXT,
            status TEXT
        )
        """
    )

    # tasksテーブルの作成
    # sync_key: 同一タスク判定用の一意キー
    # source_type: タスクの発生元（例: chatgpt_memory, manual_json, sheet_importなど）
    # source_updated_at: タスクが発生元で最後に更新された日時
    # archived: タスクがアーカイブされているか（論理削除用。メモから消えたが、すぐ削除しないためのフラグ）
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            status TEXT,
            deadline TEXT,
            original_deadline TEXT,
            project_id INTEGER,
            student_id INTEGER,
            priority TEXT DEFAULT 'medium',
            sync_key TEXT,
            source_type TEXT,
            source_updated_at TEXT,
            source_url TEXT,
            scrapbox_url TEXT,
            archived INTEGER DEFAULT 0
        )
        """
    )

    c.execute("PRAGMA table_info(tasks)")
    columns = [col[1] for col in c.fetchall()]
    if "original_deadline" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN original_deadline TEXT")
    if "student_id" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN student_id INTEGER")
    if "sync_key" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN sync_key TEXT")
    if "source_type" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN source_type TEXT")
    if "source_updated_at" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN source_updated_at TEXT")
    if "source_url" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN source_url TEXT")
    if "scrapbox_url" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN scrapbox_url TEXT")
    if "archived" not in columns:
        c.execute("ALTER TABLE tasks ADD COLUMN archived INTEGER DEFAULT 0")

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            project_id INTEGER,
            student_id INTEGER,
            task_id INTEGER,
            scrapbox_url TEXT,
            created_at TEXT
        )
        """
    )

    # 既存DBに student_id カラムが無い場合に追加
    c.execute("PRAGMA table_info(notes)")
    note_columns = [col[1] for col in c.fetchall()]
    if "student_id" not in note_columns:
        c.execute("ALTER TABLE notes ADD COLUMN student_id INTEGER")
    if "task_id" not in note_columns:
        c.execute("ALTER TABLE notes ADD COLUMN task_id INTEGER")

    ensure_student_meetings_table(c)

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS task_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        old_deadline TEXT,
        new_deadline TEXT,
        changed_at TEXT
    )
    """
    )

    c.execute(
        """
    CREATE TABLE IF NOT EXISTS sync_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        sync_key TEXT,
        field_name TEXT,
        old_value TEXT,
        new_value TEXT,
        changed_at TEXT,
        source_type TEXT
    )
    """
    )

    # 初期データ投入
    c.execute("SELECT COUNT(*) FROM projects")
    if c.fetchone()[0] == 0:
        c.execute(
            "INSERT INTO projects (name, type, status) VALUES ('Inbox', 'admin', 'active')"
        )
        c.execute(
            "INSERT INTO projects (name, type, status) VALUES ('研究室運営OS', 'research', 'active')"
        )

    # スナップショットテーブルの作成
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_snapshot (
            sync_key TEXT PRIMARY KEY,
            title TEXT,
            deadline TEXT,
            project_id INTEGER,
            student_id INTEGER,
            priority TEXT,
            status TEXT,
            updated_at TEXT
        )
        """
    )

    ensure_student_research_themes_table(c)

    # ===== deadline正規化（空文字→NULL） =====
    c.execute("UPDATE tasks SET deadline=NULL WHERE deadline=''")
    c.execute("UPDATE tasks SET original_deadline=NULL WHERE original_deadline=''")
    c.execute("UPDATE tasks SET student_id=NULL WHERE student_id=''")
    c.execute("UPDATE notes SET student_id=NULL WHERE student_id=''")
    c.execute("UPDATE notes SET task_id=NULL WHERE task_id=''")
    c.execute(
        """
        UPDATE student_meetings
        SET calendar_source=source
        WHERE (calendar_source IS NULL OR calendar_source='')
          AND source IS NOT NULL
          AND source != ''
        """
    )

    conn.commit()
    conn.close()


def ensure_student_research_themes_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_research_themes (
            student_id INTEGER PRIMARY KEY,
            research_theme TEXT NOT NULL,
            muselab_page_title TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _ensure_columns(cursor, table_name, column_definitions):
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {col[1] for col in cursor.fetchall()}

    for column_name, column_definition in column_definitions:
        if column_name not in existing_columns:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def ensure_student_meetings_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS student_meetings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            meeting_date TEXT,
            start_time TEXT,
            end_time TEXT,
            title TEXT,
            summary TEXT,
            topics TEXT,
            task_id INTEGER,
            source TEXT,
            google_event_id TEXT,
            duration_minutes INTEGER,
            calendar_source TEXT,
            calendar_event_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _ensure_columns(
        cursor,
        "student_meetings",
        [
            ("student_id", "student_id INTEGER"),
            ("meeting_date", "meeting_date TEXT"),
            ("start_time", "start_time TEXT"),
            ("end_time", "end_time TEXT"),
            ("title", "title TEXT"),
            ("summary", "summary TEXT"),
            ("topics", "topics TEXT"),
            ("task_id", "task_id INTEGER"),
            ("source", "source TEXT"),
            ("google_event_id", "google_event_id TEXT"),
            ("duration_minutes", "duration_minutes INTEGER"),
            ("calendar_source", "calendar_source TEXT"),
            ("calendar_event_url", "calendar_event_url TEXT"),
            ("created_at", "created_at TEXT"),
            ("updated_at", "updated_at TEXT"),
        ],
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_student_meetings_student_date
        ON student_meetings(student_id, meeting_date)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_student_meetings_task_id
        ON student_meetings(task_id)
        """
    )
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_student_meetings_google_event_id
        ON student_meetings(google_event_id)
        """
    )


def _normalize_meeting_time(value):
    value = (value or "").strip().replace("：", ":")
    if not value:
        return None

    try:
        hour_text, minute_text = value.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError:
        return None

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None

    return f"{hour:02d}:{minute:02d}"


def extract_meeting_time_range(text):
    match = MEETING_TIME_RE.search(text or "")
    if not match:
        return None, None

    return (
        _normalize_meeting_time(match.group("start")),
        _normalize_meeting_time(match.group("end")),
    )


def meeting_duration_minutes(start_time, end_time):
    start_time = _normalize_meeting_time(start_time)
    end_time = _normalize_meeting_time(end_time)
    if not start_time or not end_time:
        return None

    start_hour, start_minute = [int(value) for value in start_time.split(":")]
    end_hour, end_minute = [int(value) for value in end_time.split(":")]
    start_total = start_hour * 60 + start_minute
    end_total = end_hour * 60 + end_minute

    if end_total < start_total:
        return None

    return end_total - start_total


def _strip_meeting_time_range(text):
    text = MEETING_TIME_RE.sub("", text or "", count=1)
    return text.strip(" \t\n/-ー〜~|｜／")


def _compact_meeting_text(text):
    lines = [
        line.strip(" \t/-ー〜~|｜／")
        for line in (text or "").splitlines()
        if line.strip()
    ]
    return "\n".join(lines).strip()


def _meeting_title_summary(title):
    title = (title or "").strip()
    if title.startswith(INDIVIDUAL_MEETING_TITLE_PREFIX):
        title = title[len(INDIVIDUAL_MEETING_TITLE_PREFIX) :].strip()
    return title


def _meeting_topic_candidates(summary, title):
    summary = _strip_meeting_time_range(summary or "")
    first_summary_line = ""
    for line in summary.splitlines():
        if line.strip():
            first_summary_line = line.strip()
            break

    if first_summary_line:
        yield first_summary_line

    for match in re.findall(r"[（(]([^）)]+)[）)]", title or ""):
        if match.strip():
            yield match.strip()

    fallback = _meeting_title_summary(title)
    if fallback:
        yield fallback


def extract_meeting_topics(summary, title):
    for candidate in _meeting_topic_candidates(summary, title):
        candidate = _strip_meeting_time_range(candidate)
        chunks = re.split(r"\s*(?:/|／|、|,|;|；|\||｜|・)\s*", candidate)
        topics = []

        for chunk in chunks:
            topic = chunk.strip(" \t\n-ー〜~")
            if not topic:
                continue
            if MEETING_TIME_RE.fullmatch(topic):
                continue
            if topic not in topics:
                topics.append(topic)

        if topics:
            return topics

    return []


def _build_student_meeting_record(task, note_contents):
    note_body = _compact_meeting_text("\n".join(note_contents))
    source_text = note_body or task["title"]
    start_time, end_time = extract_meeting_time_range(source_text)
    summary = _strip_meeting_time_range(note_body) if note_body else ""
    summary = _compact_meeting_text(summary) or _meeting_title_summary(task["title"])
    topics = extract_meeting_topics(summary, task["title"])

    return {
        "student_id": task["student_id"],
        "meeting_date": (task["deadline"] or "")[:10],
        "start_time": start_time,
        "end_time": end_time,
        "title": task["title"],
        "summary": summary,
        "topics": json.dumps(topics, ensure_ascii=False),
        "task_id": task["task_id"],
        "source": "task_import",
        "calendar_source": "task_import",
        "duration_minutes": meeting_duration_minutes(start_time, end_time),
    }


def _fetch_individual_meeting_sources(cursor, since_date):
    rows = cursor.execute(
        """
        SELECT
            tasks.id AS task_id,
            tasks.student_id AS student_id,
            tasks.deadline AS deadline,
            tasks.title AS title,
            notes.id AS note_id,
            notes.content AS note_content
        FROM tasks
        LEFT JOIN notes
          ON notes.task_id = tasks.id
          OR (
            COALESCE(CAST(notes.task_id AS TEXT), '') = ''
            AND notes.student_id = tasks.student_id
            AND notes.title = tasks.title
          )
        WHERE tasks.title LIKE ?
          AND tasks.student_id IS NOT NULL
          AND CAST(tasks.student_id AS TEXT) != ''
          AND tasks.deadline IS NOT NULL
          AND tasks.deadline != ''
          AND substr(tasks.deadline, 1, 10) >= ?
        ORDER BY substr(tasks.deadline, 1, 10), tasks.id, notes.created_at, notes.id
        """,
        (f"{INDIVIDUAL_MEETING_TITLE_PREFIX}%", since_date),
    ).fetchall()

    sources = {}
    note_ids_by_task = {}

    for row in rows:
        task_id = row["task_id"]
        if task_id not in sources:
            sources[task_id] = {
                "task": {
                    "task_id": row["task_id"],
                    "student_id": row["student_id"],
                    "deadline": row["deadline"],
                    "title": row["title"],
                },
                "note_contents": [],
            }
            note_ids_by_task[task_id] = set()

        note_id = row["note_id"]
        note_content = row["note_content"]
        if note_id and note_id not in note_ids_by_task[task_id] and note_content:
            sources[task_id]["note_contents"].append(note_content)
            note_ids_by_task[task_id].add(note_id)

    return list(sources.values())


def _row_value_for_compare(value):
    if value is None:
        return ""
    return str(value)


def _find_existing_student_meeting(cursor, meeting):
    google_event_id = meeting.get("google_event_id")
    if google_event_id:
        existing = cursor.execute(
            """
            SELECT *
            FROM student_meetings
            WHERE google_event_id=?
            ORDER BY id
            LIMIT 1
            """,
            (google_event_id,),
        ).fetchone()
        if existing:
            return existing

    task_id = meeting.get("task_id")
    if task_id:
        existing = cursor.execute(
            """
            SELECT *
            FROM student_meetings
            WHERE task_id=?
            ORDER BY id
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if existing:
            return existing

    student_id = meeting.get("student_id")
    meeting_date = meeting.get("meeting_date")
    start_time = meeting.get("start_time")
    if student_id not in (None, "") and meeting_date and start_time:
        existing = cursor.execute(
            """
            SELECT *
            FROM student_meetings
            WHERE student_id=?
              AND meeting_date=?
              AND start_time=?
            ORDER BY google_event_id IS NULL, task_id IS NULL, id
            LIMIT 1
            """,
            (student_id, meeting_date, start_time),
        ).fetchone()
        if existing:
            return existing

    return None


def upsert_student_meeting(cursor, meeting):
    ensure_student_meetings_table(cursor)
    meeting = dict(meeting)
    if "calendar_source" not in meeting and meeting.get("source"):
        meeting["calendar_source"] = meeting["source"]
    if "source" not in meeting and meeting.get("calendar_source"):
        meeting["source"] = meeting["calendar_source"]

    fields = [
        "student_id",
        "meeting_date",
        "start_time",
        "end_time",
        "title",
        "summary",
        "topics",
        "task_id",
        "source",
        "google_event_id",
        "duration_minutes",
        "calendar_source",
        "calendar_event_url",
    ]
    existing = _find_existing_student_meeting(cursor, meeting)

    if existing:
        if (
            meeting.get("calendar_source") == "task_import"
            and existing["google_event_id"]
        ):
            meeting.pop("source", None)
            meeting.pop("calendar_source", None)

        update_fields = [field for field in fields if field in meeting]
        if not update_fields:
            return "unchanged"

        changed = any(
            _row_value_for_compare(existing[field])
            != _row_value_for_compare(meeting.get(field))
            for field in update_fields
        )
        if not changed:
            return "unchanged"

        set_clause = ", ".join([f"{field}=?" for field in update_fields])
        cursor.execute(
            f"""
            UPDATE student_meetings
            SET {set_clause}, updated_at=datetime('now')
            WHERE id=?
            """,
            tuple(meeting.get(field) for field in update_fields) + (existing["id"],),
        )
        return "updated"

    cursor.execute(
        f"""
        INSERT INTO student_meetings (
            {", ".join(fields)}, created_at, updated_at
        )
        VALUES ({", ".join(["?"] * len(fields))}, datetime('now'), datetime('now'))
        """,
        tuple(meeting.get(field) for field in fields),
    )
    return "created"


def sync_student_meetings_from_tasks(
    cursor, since_date=INDIVIDUAL_MEETING_SINCE_DATE
):
    ensure_student_meetings_table(cursor)
    sources = _fetch_individual_meeting_sources(cursor, since_date)
    stats = {
        "since_date": since_date,
        "scanned": len(sources),
        "created": 0,
        "updated": 0,
        "unchanged": 0,
    }

    for source in sources:
        meeting = _build_student_meeting_record(
            source["task"], source["note_contents"]
        )
        result = upsert_student_meeting(cursor, meeting)
        stats[result] += 1

    return stats


def fetch_student_meetings(cursor, student_id, descending=True):
    ensure_student_meetings_table(cursor)
    order = "DESC" if descending else "ASC"
    return cursor.execute(
        f"""
        SELECT
            student_meetings.*,
            tasks.project_id AS project_id,
            projects.name AS project_name
        FROM student_meetings
        LEFT JOIN tasks ON student_meetings.task_id = tasks.id
        LEFT JOIN projects ON tasks.project_id = projects.id
        WHERE student_meetings.student_id=?
        ORDER BY
            student_meetings.meeting_date {order},
            student_meetings.start_time {order},
            student_meetings.id {order}
        """,
        (student_id,),
    ).fetchall()


def fetch_student_research_theme_overrides(cursor):
    ensure_student_research_themes_table(cursor)
    rows = cursor.execute(
        """
        SELECT student_id, research_theme, muselab_page_title
        FROM student_research_themes
        """
    ).fetchall()
    return {
        row["student_id"]: {
            "research_theme": row["research_theme"],
            "muselab_page_title": row["muselab_page_title"],
        }
        for row in rows
    }


def upsert_student_research_theme(
    cursor, student_id, research_theme, muselab_page_title
):
    ensure_student_research_themes_table(cursor)

    research_theme = (research_theme or "").strip()
    muselab_page_title = (muselab_page_title or "").strip()

    if not research_theme:
        cursor.execute(
            "DELETE FROM student_research_themes WHERE student_id = ?",
            (student_id,),
        )
        return

    cursor.execute(
        """
        INSERT INTO student_research_themes (
            student_id, research_theme, muselab_page_title, updated_at
        )
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(student_id) DO UPDATE SET
            research_theme = excluded.research_theme,
            muselab_page_title = excluded.muselab_page_title,
            updated_at = excluded.updated_at
        """,
        (student_id, research_theme, muselab_page_title),
    )


def get_today_str(cursor):
    return cursor.execute("SELECT date('now')").fetchone()[0]


def fetch_home_tasks(cursor):
    return cursor.execute(
        """
        SELECT
            tasks.id AS task_id,
            tasks.title AS title,
            tasks.status AS status,
            tasks.deadline AS deadline,
            tasks.priority AS priority,
            tasks.original_deadline AS original_deadline,
            tasks.archived AS archived,
            tasks.student_id AS student_id,
            tasks.source_url AS source_url,
            tasks.scrapbox_url AS scrapbox_url,
            projects.id AS project_id,
            projects.name AS project_name
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        ORDER BY tasks.deadline IS NULL, tasks.deadline
        """
    ).fetchall()


def fetch_home_task_rows(
    cursor, where_clause, params=(), order_by="tasks.deadline IS NULL, tasks.deadline"
):
    query = f"""
        SELECT
            tasks.id AS task_id,
            tasks.title AS title,
            projects.id AS project_id,
            projects.name AS project_name,
            tasks.deadline AS deadline,
            tasks.priority AS priority,
            tasks.original_deadline AS original_deadline
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        WHERE tasks.status != 'done'
          AND tasks.archived = 0
          AND ({where_clause})
        ORDER BY {order_by}
    """
    return cursor.execute(query, params).fetchall()


def fetch_done_task_rows(cursor):
    return cursor.execute(
        """
        SELECT tasks.id, tasks.title AS title, projects.name AS name, tasks.status
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        WHERE tasks.status = 'done' AND tasks.archived = 0
        ORDER BY tasks.id DESC
        """
    ).fetchall()


def fetch_all_projects(cursor):
    return cursor.execute("SELECT * FROM projects").fetchall()


def fetch_inbox_project(cursor):
    return cursor.execute("SELECT id FROM projects WHERE name='Inbox' LIMIT 1").fetchone()


def fetch_inbox_project_id(cursor):
    inbox = fetch_inbox_project(cursor)
    return inbox["id"] if inbox else None


def fetch_inbox_tasks(cursor, inbox_id):
    return cursor.execute(
        """
        SELECT tasks.id, tasks.title, tasks.deadline, tasks.priority
        FROM tasks
        WHERE project_id=? AND tasks.status != 'done'
        ORDER BY tasks.deadline IS NULL, tasks.deadline
    """,
        (inbox_id,),
    ).fetchall()


def fetch_task_for_note(cursor, task_id):
    return cursor.execute(
        """
        SELECT id, title, project_id, student_id
        FROM tasks
        WHERE id=?
        """,
        (task_id,),
    ).fetchone()


def move_task_to_project(cursor, task_id, new_project_id):
    move_tasks_to_project(cursor, [task_id], new_project_id)


def move_tasks_to_project(cursor, task_ids, new_project_id):
    normalized_task_ids = []
    for task_id in task_ids:
        try:
            normalized_task_ids.append(int(task_id))
        except (TypeError, ValueError):
            continue

    if not normalized_task_ids:
        return 0

    placeholders = ",".join(["?"] * len(normalized_task_ids))
    params = [new_project_id] + normalized_task_ids
    cursor.execute(
        f"UPDATE tasks SET project_id=? WHERE id IN ({placeholders})",
        params,
    )
    cursor.execute(
        f"UPDATE notes SET project_id=? WHERE task_id IN ({placeholders})",
        params,
    )
    return len(normalized_task_ids)


def update_task_deadline_and_priority(
    cursor, task_id, new_deadline, make_high, priority_submitted=False
):
    current = cursor.execute(
        "SELECT deadline FROM tasks WHERE id=?", (task_id,)
    ).fetchone()
    old_deadline = current["deadline"] if current else None

    if new_deadline:
        cursor.execute(
            "UPDATE tasks SET deadline=? WHERE id=?",
            (new_deadline, task_id),
        )

        cursor.execute(
            "INSERT INTO task_history (task_id, old_deadline, new_deadline, changed_at) VALUES (?, ?, ?, datetime('now'))",
            (task_id, old_deadline, new_deadline),
        )

    if priority_submitted:
        cursor.execute(
            "UPDATE tasks SET priority=? WHERE id=?",
            ("high" if make_high == "high" else "medium", task_id),
        )


def _student_text_match_clause(table_alias, text_columns, student_names):
    clauses = []
    params = []

    for name in student_names or []:
        if not name:
            continue
        text_clause = " OR ".join([f"{column} LIKE ?" for column in text_columns])
        clauses.append(
            f"(({table_alias}.student_id IS NULL OR {table_alias}.student_id = '') "
            f"AND ({text_clause}))"
        )
        params.extend([f"%{name}%"] * len(text_columns))

    return clauses, params


def fetch_student_log_rows(cursor, student_id, student_names=None):
    task_clauses = ["tasks.student_id = ?"]
    task_params = [student_id]
    fallback_clauses, fallback_params = _student_text_match_clause(
        "tasks", ["tasks.title"], student_names
    )
    task_clauses.extend(fallback_clauses)
    task_params.extend(fallback_params)
    task_where = " OR ".join(task_clauses)

    tasks = cursor.execute(
        f"""
        SELECT tasks.id AS task_id, tasks.title AS title, tasks.status AS status,
            tasks.deadline AS deadline, tasks.priority AS priority,
            tasks.original_deadline AS original_deadline,
            tasks.archived AS archived,
            tasks.source_url AS source_url,
            tasks.scrapbox_url AS scrapbox_url,
            projects.id AS project_id, projects.name AS project_name,
            tasks.student_id AS student_id
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        WHERE ({task_where})
        ORDER BY tasks.deadline IS NULL, tasks.deadline
        """,
        tuple(task_params),
    ).fetchall()

    note_clauses = ["notes.student_id = ?"]
    note_params = [student_id]
    fallback_clauses, fallback_params = _student_text_match_clause(
        "notes", ["notes.title", "notes.content"], student_names
    )
    note_clauses.extend(fallback_clauses)
    note_params.extend(fallback_params)
    note_where = " OR ".join(note_clauses)

    notes = cursor.execute(
        f"""
        SELECT notes.id AS note_id, notes.title AS title, notes.content AS content,
            notes.created_at AS created_at, notes.scrapbox_url AS scrapbox_url,
            notes.task_id AS task_id,
            projects.id AS project_id, projects.name AS project_name,
            notes.student_id AS student_id
        FROM notes
        LEFT JOIN projects ON notes.project_id = projects.id
        WHERE ({note_where})
        ORDER BY notes.created_at DESC
        """,
        tuple(note_params),
    ).fetchall()

    history_clauses = ["t.student_id = ?"]
    history_params = [student_id]
    fallback_clauses, fallback_params = _student_text_match_clause(
        "t", ["t.title"], student_names
    )
    history_clauses.extend(fallback_clauses)
    history_params.extend(fallback_params)
    history_where = " OR ".join(history_clauses)

    history = cursor.execute(
        f"""
        SELECT th.task_id AS task_id, t.title AS task_title,
            th.old_deadline AS old_deadline, th.new_deadline AS new_deadline,
            th.changed_at AS changed_at,
            projects.id AS project_id, projects.name AS project_name,
            t.student_id AS student_id
        FROM task_history th
        LEFT JOIN tasks t ON th.task_id = t.id
        LEFT JOIN projects ON t.project_id = projects.id
        WHERE ({history_where})
        ORDER BY th.changed_at DESC
        """,
        tuple(history_params),
    ).fetchall()

    return tasks, notes, history


def fetch_student_summary_rows(cursor, student_id, student_names=None):
    task_clauses = ["tasks.student_id = ?"]
    task_params = [student_id]
    fallback_clauses, fallback_params = _student_text_match_clause(
        "tasks", ["tasks.title"], student_names
    )
    task_clauses.extend(fallback_clauses)
    task_params.extend(fallback_params)
    task_where = " OR ".join(task_clauses)

    todo_count = cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM tasks
        WHERE ({task_where}) AND archived = 0 AND status != 'done'
        """,
        tuple(task_params),
    ).fetchone()[0]

    done_count = cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM tasks
        WHERE ({task_where}) AND archived = 0 AND status = 'done'
        """,
        tuple(task_params),
    ).fetchone()[0]

    overdue_count = cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM tasks
        WHERE ({task_where}) AND archived = 0 AND status != 'done'
          AND deadline IS NOT NULL AND deadline != '' AND deadline < date('now')
        """,
        tuple(task_params),
    ).fetchone()[0]

    next_tasks = cursor.execute(
        f"""
        SELECT title, deadline
        FROM tasks
        WHERE ({task_where}) AND archived = 0 AND status != 'done'
        ORDER BY deadline IS NULL, deadline
        LIMIT 3
        """,
        tuple(task_params),
    ).fetchall()

    return todo_count, done_count, overdue_count, next_tasks


def insert_project(cursor, name, type_):
    cursor.execute(
        "INSERT INTO projects (name, type, status) VALUES (?, ?, ?)",
        (name, type_, "active"),
    )


def insert_task(cursor, title, project_id, deadline, priority, student_id, status="todo"):
    student_id = student_id or None
    cursor.execute(
        "INSERT INTO tasks (title, status, project_id, deadline, original_deadline, student_id, priority) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, status, project_id, deadline, deadline, student_id, priority),
    )


def build_next_research_note_title(title):
    match = RESEARCH_NOTE_TITLE_RE.match(title or "")
    if not match:
        return None

    next_index = int(match.group("index")) + 1
    return f"第{next_index}回{match.group('space')}研究のおと（note.com）を執筆する"


def next_week_wednesday_from(completed_on):
    next_monday = completed_on + timedelta(days=7 - completed_on.weekday())
    return next_monday + timedelta(days=2)


def next_weekday_from(completed_on):
    next_day = completed_on + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return next_day


def _active_task_exists(cursor, title, project_id, student_id):
    return cursor.execute(
        """
        SELECT id
        FROM tasks
        WHERE title=?
          AND COALESCE(project_id, -1)=COALESCE(?, -1)
          AND COALESCE(student_id, -1)=COALESCE(?, -1)
          AND archived=0
          AND status != 'done'
        LIMIT 1
        """,
        (title, project_id, student_id),
    ).fetchone()


def generate_next_research_note_task(cursor, task):
    next_title = build_next_research_note_title(task["title"])
    if not next_title:
        return

    if _active_task_exists(cursor, next_title, task["project_id"], task["student_id"]):
        return

    insert_task(
        cursor,
        next_title,
        task["project_id"],
        next_week_wednesday_from(date.today()).isoformat(),
        task["priority"],
        task["student_id"],
        status="future",
    )


def generate_next_practice_task(cursor, task):
    title = task["title"]
    if not PRACTICE_TITLE_RE.match(title or ""):
        return

    if _active_task_exists(cursor, title, task["project_id"], task["student_id"]):
        return

    insert_task(
        cursor,
        title,
        task["project_id"],
        next_weekday_from(date.today()).isoformat(),
        task["priority"],
        task["student_id"],
        status="future",
    )


def generate_completion_triggered_tasks(cursor, task):
    generate_next_research_note_task(cursor, task)
    generate_next_practice_task(cursor, task)


def mark_task_done(cursor, task_id):
    task = cursor.execute(
        """
        SELECT title, status, project_id, priority, student_id
        FROM tasks
        WHERE id=?
        """,
        (task_id,),
    ).fetchone()

    if not task:
        return

    cursor.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
    if task["status"] != "done":
        generate_completion_triggered_tasks(cursor, task)


def _first_nonempty(*values):
    for value in values:
        if value not in (None, "", "None", "none", "NULL", "null"):
            return value
    return None


def merge_project_tasks(
    cursor,
    project_id,
    keep_task_id,
    merge_task_ids,
    merged_title=None,
    merged_deadline=None,
    merged_priority=None,
):
    keep_task_id = int(keep_task_id)
    merge_task_ids = [int(task_id) for task_id in merge_task_ids if int(task_id) != keep_task_id]
    if not merge_task_ids:
        raise ValueError("統合対象タスクが選択されていません")

    all_task_ids = [keep_task_id] + merge_task_ids
    placeholders = ",".join(["?"] * len(all_task_ids))
    rows = cursor.execute(
        f"""
        SELECT *
        FROM tasks
        WHERE id IN ({placeholders})
          AND project_id=?
        """,
        tuple(all_task_ids + [int(project_id)]),
    ).fetchall()

    rows_by_id = {row["id"]: row for row in rows}
    if set(rows_by_id) != set(all_task_ids):
        raise ValueError("統合対象に別プロジェクトのタスク、または存在しないタスクが含まれています")

    keep = rows_by_id[keep_task_id]
    merged_rows = [rows_by_id[task_id] for task_id in merge_task_ids]

    title = (merged_title or "").strip() or keep["title"]
    deadline = merged_deadline if merged_deadline not in ("", None) else keep["deadline"]
    priority = merged_priority or keep["priority"]
    if priority not in ("high", "medium", "low"):
        priority = "medium"

    source_url = _first_nonempty(
        keep["source_url"], *[row["source_url"] for row in merged_rows]
    )
    scrapbox_url = _first_nonempty(
        keep["scrapbox_url"], *[row["scrapbox_url"] for row in merged_rows]
    )
    student_id = _first_nonempty(
        keep["student_id"], *[row["student_id"] for row in merged_rows]
    )
    original_deadline = _first_nonempty(
        keep["original_deadline"], *[row["original_deadline"] for row in merged_rows]
    )

    old_deadline = keep["deadline"]
    cursor.execute(
        """
        UPDATE tasks
        SET title=?, deadline=?, priority=?, source_url=?, scrapbox_url=?,
            student_id=?, original_deadline=?
        WHERE id=?
        """,
        (
            title,
            deadline,
            priority,
            source_url,
            scrapbox_url,
            student_id,
            original_deadline,
            keep_task_id,
        ),
    )

    if deadline != old_deadline:
        cursor.execute(
            """
            INSERT INTO task_history (task_id, old_deadline, new_deadline, changed_at)
            VALUES (?, ?, ?, datetime('now'))
            """,
            (keep_task_id, old_deadline, deadline),
        )

    merge_placeholders = ",".join(["?"] * len(merge_task_ids))
    cursor.execute(
        f"UPDATE notes SET task_id=? WHERE task_id IN ({merge_placeholders})",
        tuple([keep_task_id] + merge_task_ids),
    )
    cursor.execute(
        f"UPDATE task_history SET task_id=? WHERE task_id IN ({merge_placeholders})",
        tuple([keep_task_id] + merge_task_ids),
    )
    cursor.execute(
        f"UPDATE sync_history SET task_id=? WHERE task_id IN ({merge_placeholders})",
        tuple([keep_task_id] + merge_task_ids),
    )

    cursor.execute(
        f"UPDATE tasks SET archived=1, source_type='manual_merge' WHERE id IN ({merge_placeholders})",
        tuple(merge_task_ids),
    )

    for row in merged_rows:
        cursor.execute(
            """
            INSERT INTO sync_history (
                task_id, sync_key, field_name, old_value, new_value, changed_at, source_type
            )
            VALUES (?, ?, 'merged_into_task', ?, ?, datetime('now'), 'manual_merge')
            """,
            (row["id"], row["sync_key"], row["title"], str(keep_task_id)),
        )

    return {
        "keep_task_id": keep_task_id,
        "merged_task_ids": merge_task_ids,
        "title": title,
        "deadline": deadline,
        "priority": priority,
    }


def fetch_project_detail_rows(cursor, project_id, task_id_filter=None):
    project = cursor.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    tasks = cursor.execute(
        """
        SELECT id AS task_id, title, status, deadline, priority, archived,
               project_id,
               original_deadline, sync_key, source_type, source_updated_at,
               source_url, scrapbox_url, student_id
        FROM tasks
        WHERE project_id=?
        ORDER BY deadline IS NULL, deadline
        """,
        (project_id,),
    ).fetchall()

    if task_id_filter:
        history = cursor.execute(
            """
        SELECT th.task_id, t.title AS task_title, th.old_deadline, th.new_deadline, th.changed_at
        FROM task_history th
        LEFT JOIN tasks t ON th.task_id = t.id
        WHERE th.task_id=?
        ORDER BY th.changed_at DESC
        """,
            (task_id_filter,),
        ).fetchall()
    else:
        history = cursor.execute(
            """
            SELECT th.task_id, t.title AS task_title, th.old_deadline, th.new_deadline, th.changed_at
            FROM task_history th
            LEFT JOIN tasks t ON th.task_id = t.id
            WHERE th.task_id IN (
                SELECT id FROM tasks WHERE project_id=?
            )
            ORDER BY th.changed_at DESC
        """,
            (project_id,),
        ).fetchall()

    notes = cursor.execute(
        """
        SELECT id, title, content, scrapbox_url, created_at, task_id
        FROM notes
        WHERE project_id=?
        ORDER BY created_at DESC
    """,
        (project_id,),
    ).fetchall()

    return project, tasks, history, notes


def insert_note(cursor, title, content, project_id, student_id, task_id, scrapbox_url):
    student_id = student_id or None
    cursor.execute(
        """
            INSERT INTO notes (title, content, project_id, student_id, task_id, scrapbox_url, created_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (title, content, project_id, student_id, task_id, scrapbox_url),
    )


def insert_imported_task(cursor, task):
    cursor.execute(
        """
            INSERT INTO tasks (
                title, status, project_id, deadline, original_deadline,
                student_id, priority, sync_key, source_type, source_updated_at,
                source_url, scrapbox_url, archived
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task.get("title"),
            task.get("status", "todo"),
            task.get("project_id"),
            task.get("deadline") or None,
            task.get("deadline") or None,
            task.get("student_id"),
            task.get("priority", "medium"),
            task.get("sync_key"),
            task.get("source_type", "manual_json"),
            task.get("source_updated_at"),
            task.get("source_url"),
            task.get("scrapbox_url"),
            int(task.get("archived", 0)),
        ),
    )
    return cursor.lastrowid


def fetch_export_tasks_for_chatgpt(cursor):
    return cursor.execute(
        """
        SELECT t.title, t.deadline, t.priority, t.status,
               p.name as project_name, t.student_id
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.archived = 0
    """
    ).fetchall()


def fetch_export_tasks_as_memo(cursor):
    return cursor.execute(
        """
        SELECT t.title, t.deadline, t.priority,
               p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.status != 'done' AND t.archived = 0
        ORDER BY t.deadline IS NULL, t.deadline
    """
    ).fetchall()


def update_task_title(cursor, task_id, new_title):
    cursor.execute(
        "UPDATE tasks SET title=? WHERE id=?",
        (new_title, task_id),
    )


def build_snapshot(project_name, cursor):
    rows = cursor.execute(
        """
        SELECT t.title, t.status, t.deadline, t.priority,
               p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE p.name=? AND t.archived=0
    """,
        (project_name,),
    ).fetchall()

    current = []
    todos = []

    for r in rows:
        if r["status"] == "done":
            continue
        elif r["status"] == "in_progress":
            current.append(r["title"])
        else:
            todos.append(r["title"])

    return {"project": project_name, "current_state": current, "todos": todos}


def build_all_snapshots(cursor):
    projects = cursor.execute(
        """
        SELECT DISTINCT projects.name AS project_name
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        WHERE tasks.archived = 0
          AND tasks.status != 'done'
          AND projects.name IS NOT NULL
        ORDER BY projects.name
        """
    ).fetchall()

    snapshots = []
    for row in projects:
        project_name = row["project_name"]
        snapshot = build_snapshot(project_name, cursor)
        if snapshot["current_state"] or snapshot["todos"]:
            snapshots.append(snapshot)

    return snapshots

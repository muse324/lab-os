import os
import sqlite3


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")


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

    # ===== deadline正規化（空文字→NULL） =====
    c.execute("UPDATE tasks SET deadline=NULL WHERE deadline=''")
    c.execute("UPDATE tasks SET original_deadline=NULL WHERE original_deadline=''")
    c.execute("UPDATE tasks SET student_id=NULL WHERE student_id=''")
    c.execute("UPDATE notes SET student_id=NULL WHERE student_id=''")

    conn.commit()
    conn.close()


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


def move_task_to_project(cursor, task_id, new_project_id):
    cursor.execute(
        "UPDATE tasks SET project_id=? WHERE id=?",
        (new_project_id, task_id),
    )


def update_task_deadline_and_priority(cursor, task_id, new_deadline, make_high):
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

    if make_high == "high":
        cursor.execute(
            "UPDATE tasks SET priority='high' WHERE id=?",
            (task_id,),
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


def mark_task_done(cursor, task_id):
    cursor.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))


def fetch_project_detail_rows(cursor, project_id, task_id_filter=None):
    project = cursor.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    tasks = cursor.execute(
        """
        SELECT id AS task_id, title, status, deadline, priority, archived,
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

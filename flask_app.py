from io import StringIO
from flask import Flask, render_template, request, redirect, jsonify
import sqlite3
import os
import re
import requests
import csv


# ===== 全角→半角クォート正規化 =====
def normalize_quotes(text):
    if not text:
        return text
    return (
        text.replace("“", '"')
        .replace("”", '"')
        .replace("＂", '"')
        .replace("‘", "'")
        .replace("’", "'")
    )


# OpenAIは任意（未インストールでもOK）
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# OpenAIは任意（APIキーがない環境でも動作させる）
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if (api_key and OpenAI) else None

app = Flask(__name__)  # ← これが最重要


# ===== 学生名簿取得（Google Sheets） =====
def load_students():
    url = "https://docs.google.com/spreadsheets/d/14syTM70RCLc7UQZcK9btrU8PgsaPVKTDYhGhDvIkkao/export?format=csv"

    try:
        res = requests.get(url)
        res.encoding = "utf-8"

        f = StringIO(res.text)
        reader = csv.DictReader(f)

        students = []

        for row in reader:
            name = row.get("氏名")
            nickname = row.get("呼び名")
            student_id = row.get("学籍番号")

            if name and name != "橋田光代":
                students.append(
                    {
                        "name": name,
                        "student_id": int(student_id) if student_id else None,
                    }
                )

            if nickname:
                students.append(
                    {
                        "name": nickname,
                        "student_id": int(student_id) if student_id else None,
                    }
                )

        return students

    except Exception as e:
        print("学生名簿取得失敗:", e)
        return []


# グローバルで読み込み
STUDENTS_DATA = load_students()
STUDENTS = [s["name"] for s in STUDENTS_DATA]

PROJECT_RULES = {
    "授業（メディア情報学）": [
        "メディア情報学",
        "メディア",
        "授業スライド",
        "グループワーク",
        "スライド",
        "講義資料",
    ],
    "授業（音情報処理）": ["音情報処理", "音情", "試験", "成績入力", "採点"],
    "研究（論文投稿）": [
        "論文",
        "査読",
        "イントロ",
        "introduction",
        "phrase structure",
        "フレーズ構造",
        "投稿",
        "レビュー",
        "英文",
        "revision",
    ],
    "研究（音楽理論ビジュアライゼーション）": [
        "音楽理論ビジュアライゼーション",
        "ビジュアライゼーション",
        "研究史",
        "ツール20選",
        "可視化",
    ],
    "研究（AR楽譜インタフェース）": [
        "AR楽譜",
        "XR",
        "VITURE",
        "リアルタイム注釈",
        "楽譜インタフェース",
        "AR",
    ],
    "研究室運営OS": [
        "研究室運営OS",
        "研究室OS",
        "OS設計",
        "Scrapbox",
        "WikiCFP",
        "カレンダー連携",
        "Todayダッシュボード",
        "タスク管理",
        "OS",
    ],
    "プロジェクト（かやのおと）": [
        "かやのおと",
        "かや",
        "蚊帳",
        "音響処理",
        "投影",
        "インスタレーション",
        "イベント",
        "展示",
        "いこてん",
    ],
    "プロジェクト（JRビエラ展示）": [
        "JRビエラ",
        "ビエラ",
        "展示",
        "オープニング",
        "イベント",
        "ブース",
    ],
    "大学業務（委員会・評価）": [
        "教授会",
        "委員会",
        "評価",
        "FUJIN",
        "業務実績報告",
        "中期計画",
        "出張講義ナビ",
        "教務",
        "学長",
        "報告書",
    ],
    "学生指導": [
        "卒論",
        "修論",
        "学生",
        "研究テーマ",
        "指導",
        "コメント",
        "レビュー",
        "山口",
        "川添",
        "白波瀬",
        "松永",
        "和田",
        "矢野",
    ],
    "研究室配属": ["研究室配属", "配属", "見学対応", "希望調査", "定員調査"],
    "UBM活動": ["UBM", "月報", "合宿", "売上", "活動"],
    "備忘録（助成・制度）": [
        "助成",
        "公募",
        "募集要項",
        "制度",
        "Adobe",
        "補助金",
        "財団",
    ],
}


def get_db():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    conn = sqlite3.connect(
        os.path.join(BASE_DIR, "db.sqlite3"), timeout=5, check_same_thread=False
    )
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

    conn.commit()
    conn.close()


def build_delta(cursor):
    current_rows = cursor.execute(
        """
        SELECT sync_key, title, deadline, project_id, student_id, priority, status
        FROM tasks
        WHERE archived=0 AND sync_key IS NOT NULL
    """
    ).fetchall()

    current = {row["sync_key"]: row for row in current_rows}
    snapshot = {
        row["sync_key"]: row
        for row in cursor.execute("SELECT * FROM sync_snapshot").fetchall()
    }

    delta = {"added": [], "updated": [], "deleted": []}
    tracked_fields = [
        "title",
        "deadline",
        "project_id",
        "student_id",
        "priority",
        "status",
    ]

    for sync_key, row in current.items():
        row_dict = dict(row)

        if sync_key not in snapshot:
            delta["added"].append(row_dict)
            continue

        old = snapshot[sync_key]
        changes = []
        for field in tracked_fields:
            old_value = old[field]
            new_value = row[field]
            if old_value != new_value:
                changes.append({"field": field, "old": old_value, "new": new_value})

        if changes:
            row_dict["changes"] = changes
            delta["updated"].append(row_dict)

    for sync_key, old in snapshot.items():
        if sync_key not in current:
            delta["deleted"].append(
                {
                    "sync_key": sync_key,
                    "title": old["title"],
                    "deadline": old["deadline"],
                    "project_id": old["project_id"],
                    "student_id": old["student_id"],
                    "priority": old["priority"],
                    "status": old["status"],
                }
            )

    return delta


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


# --- Apply deadline/formatting for tasks ---
def apply_format(rows):
    result = []
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

        result.append(d)

    return result


# --- Shared helper for classifying tasks ---
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


# --- Helper functions for fetching tasks/projects ---
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


@app.route("/")
def home():
    conn = get_db()
    c = conn.cursor()

    today_str = c.execute("SELECT date('now')").fetchone()[0]
    all_tasks = c.execute(
        """
        SELECT
            tasks.id AS task_id,
            tasks.title AS title,
            tasks.status AS status,
            tasks.deadline AS deadline,
            tasks.priority AS priority,
            tasks.original_deadline AS original_deadline,
            tasks.archived AS archived,
            projects.id AS project_id,
            projects.name AS project_name
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        ORDER BY tasks.deadline IS NULL, tasks.deadline
        """
    ).fetchall()

    all_tasks = apply_format(all_tasks)

    classified = classify_tasks(all_tasks, today_str)

    overdue = classified["overdue_tasks"]
    today = classified["today_tasks"]
    tasks_todo = classified["future_tasks"]
    tasks_todo_anytime = classified["anytime_tasks"]
    tasks_done = classified["done_tasks"]

    projects = fetch_all_projects(c)
    conn.close()

    return render_template(
        "index.html",
        today=today,
        overdue=overdue,
        tasks_todo=tasks_todo,
        projects=projects,
        tasks_done=tasks_done,
        tasks_todo_anytime=tasks_todo_anytime,
        is_production=os.getenv("IS_PRODUCTION") == "1",
        students=STUDENTS_DATA,
    )


@app.route("/inbox")
def inbox_view():
    conn = get_db()
    c = conn.cursor()

    inbox = c.execute("SELECT id FROM projects WHERE name='Inbox' LIMIT 1").fetchone()
    if not inbox:
        conn.close()
        return "Inbox not found"

    tasks = c.execute(
        """
        SELECT tasks.id, tasks.title, tasks.deadline, tasks.priority
        FROM tasks
        WHERE project_id=? AND tasks.status != 'done'
        ORDER BY tasks.deadline IS NULL, tasks.deadline
    """,
        (inbox["id"],),
    ).fetchall()

    projects = fetch_all_projects(c)
    conn.close()

    return render_template("inbox_review.html", tasks=tasks, projects=projects)


@app.route("/move_task", methods=["POST"])
def move_task():
    task_id = request.form["task_id"]
    new_project_id = request.form["project_id"]

    conn = get_db()
    c = conn.cursor()

    c.execute(
        "UPDATE tasks SET project_id=? WHERE id=?",
        (new_project_id, task_id),
    )

    conn.commit()
    conn.close()

    return redirect("/inbox")


@app.route("/update_task", methods=["POST"])
def update_task():
    task_id = request.form["task_id"]
    new_deadline = request.form.get("deadline")
    make_high = request.form.get("priority")

    conn = get_db()
    c = conn.cursor()

    current = c.execute("SELECT deadline FROM tasks WHERE id=?", (task_id,)).fetchone()
    old_deadline = current["deadline"] if current else None

    if new_deadline:
        c.execute(
            "UPDATE tasks SET deadline=? WHERE id=?",
            (new_deadline, task_id),
        )

        c.execute(
            "INSERT INTO task_history (task_id, old_deadline, new_deadline, changed_at) VALUES (?, ?, ?, datetime('now'))",
            (task_id, old_deadline, new_deadline),
        )

    if make_high == "high":
        c.execute(
            "UPDATE tasks SET priority='high' WHERE id=?",
            (task_id,),
        )

    conn.commit()
    conn.close()

    return redirect("/")


@app.route("/students")
def students_index():
    students = [s for s in STUDENTS_DATA if s.get("student_id")]

    # 学籍番号昇順（上級生→下級生）
    students.sort(key=lambda x: x["student_id"])

    names = [s["name"] for s in students]

    return render_template("students.html", students=names)


@app.route("/student_log")
def student_log():
    student_name = request.args.get("name", "")
    if not student_name:
        return redirect("/students")

    student = next(
        (
            s
            for s in STUDENTS_DATA
            if s.get("name") == student_name and s.get("student_id")
        ),
        None,
    )
    if not student:
        return redirect("/students")

    student_id = student["student_id"]

    conn = get_db()
    c = conn.cursor()

    tasks = c.execute(
        """
        SELECT tasks.id AS task_id, tasks.title AS title, tasks.status AS status,
            tasks.deadline AS deadline, tasks.priority AS priority,
            projects.id AS project_id, projects.name AS project_name,
            tasks.student_id AS student_id
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        WHERE tasks.student_id = ?
        ORDER BY tasks.deadline IS NULL, tasks.deadline
        """,
        (student_id,),
    ).fetchall()

    notes = c.execute(
        """
        SELECT notes.id AS note_id, notes.title AS title, notes.content AS content,
            notes.created_at AS created_at, notes.scrapbox_url AS scrapbox_url,
            projects.id AS project_id, projects.name AS project_name,
            notes.student_id AS student_id
        FROM notes
        LEFT JOIN projects ON notes.project_id = projects.id
        WHERE notes.student_id = ?
        ORDER BY notes.created_at DESC
        """,
        (student_id,),
    ).fetchall()

    history = c.execute(
        """
        SELECT th.task_id AS task_id, t.title AS task_title,
            th.old_deadline AS old_deadline, th.new_deadline AS new_deadline,
            th.changed_at AS changed_at,
            projects.id AS project_id, projects.name AS project_name,
            t.student_id AS student_id
        FROM task_history th
        LEFT JOIN tasks t ON th.task_id = t.id
        LEFT JOIN projects ON t.project_id = projects.id
        WHERE t.student_id = ?
        ORDER BY th.changed_at DESC
        """,
        (student_id,),
    ).fetchall()

    conn.close()

    tasks = format_history_rows(tasks)
    history = format_history_rows(history)
    notes = [dict(n) for n in notes]

    return render_template(
        "student_log.html",
        student_name=student_name,
        tasks=tasks,
        notes=notes,
        history=history,
    )


# === student_summary API ===
@app.route("/student_summary")
def student_summary():
    student_name = request.args.get("name", "")
    if not student_name:
        return jsonify({"error": "name is required"}), 400

    matched_students = [
        s
        for s in STUDENTS_DATA
        if s.get("name") == student_name and s.get("student_id")
    ]
    if not matched_students:
        return jsonify({"error": "student not found"}), 404

    student_id = matched_students[0]["student_id"]

    conn = get_db()
    c = conn.cursor()

    todo_count = c.execute(
        """
        SELECT COUNT(*)
        FROM tasks
        WHERE student_id = ? AND archived = 0 AND status != 'done'
        """,
        (student_id,),
    ).fetchone()[0]

    done_count = c.execute(
        """
        SELECT COUNT(*)
        FROM tasks
        WHERE student_id = ? AND archived = 0 AND status = 'done'
        """,
        (student_id,),
    ).fetchone()[0]

    overdue_count = c.execute(
        """
        SELECT COUNT(*)
        FROM tasks
        WHERE student_id = ? AND archived = 0 AND status != 'done'
          AND deadline IS NOT NULL AND deadline != '' AND deadline < date('now')
        """,
        (student_id,),
    ).fetchone()[0]

    next_tasks = c.execute(
        """
        SELECT title, deadline
        FROM tasks
        WHERE student_id = ? AND archived = 0 AND status != 'done'
        ORDER BY deadline IS NULL, deadline
        LIMIT 3
        """,
        (student_id,),
    ).fetchall()

    conn.close()

    return jsonify(
        {
            "name": student_name,
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


@app.route("/projects")
def projects():
    conn = get_db()
    c = conn.cursor()
    projects = fetch_all_projects(c)
    conn.close()

    return render_template("projects.html", projects=projects)


@app.route("/add_project", methods=["POST"])
def add_project():
    name = request.form["name"]
    type_ = request.form["type"]

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO projects (name, type, status) VALUES (?, ?, ?)",
        (name, type_, "active"),
    )
    conn.commit()
    conn.close()

    return redirect("/projects")


@app.route("/add_task", methods=["POST"])
def add_task():
    title = request.form["title"]
    project_id = request.form["project_id"]
    deadline = request.form.get("deadline") or None
    priority = request.form.get("priority", "medium")
    student_id = request.form.get("student_id")

    # ★追加
    next_page = request.form.get("next")

    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO tasks (title, status, project_id, deadline, original_deadline, student_id, priority) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (title, "todo", project_id, deadline, deadline, student_id, priority),
    )
    conn.commit()
    conn.close()

    # ★ここが分岐
    if next_page:
        return redirect(next_page)
    else:
        return redirect("/")


@app.route("/done/<int:task_id>", methods=["POST"])
def done_task(task_id):
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))
        conn.commit()
    finally:
        conn.close()
    next_url = request.form.get("next")
    return redirect(next_url or "/")


@app.route("/project/<int:project_id>")
def project_detail(project_id):
    task_id_filter = request.args.get("task_id", type=int)
    conn = get_db()
    c = conn.cursor()

    today_str = c.execute("SELECT date('now')").fetchone()[0]

    # Project取得
    project = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    # ★ タスク取得
    all_tasks = c.execute(
        """
        SELECT id AS task_id, title, status, deadline, priority, archived,
               original_deadline, sync_key, source_type, source_updated_at
        FROM tasks
        WHERE project_id=?
        ORDER BY deadline IS NULL, deadline
        """,
        (project_id,),
    ).fetchall()

    all_tasks = apply_format(all_tasks)

    classified = classify_tasks(all_tasks, today_str)
    overdue_tasks = classified["overdue_tasks"]
    today_tasks = classified["today_tasks"]
    future_tasks = classified["future_tasks"]
    anytime_tasks = classified["anytime_tasks"]
    done_tasks = classified["done_tasks"]

    archived_tasks = []
    for t in classified["archived_tasks"]:
        d = dict(t)
        if d.get("source_type") in ["chatgpt_memory", "manual_json"] and d.get(
            "sync_key"
        ):
            d["archive_reason"] = "同期差分でアーカイブ"
        elif d.get("source_type"):
            d["archive_reason"] = f"{d['source_type']}由来のアーカイブ"
        else:
            d["archive_reason"] = "手動または不明"
        archived_tasks.append(d)

    # ★ 履歴取得（taskごとにまとめる）
    if task_id_filter:
        history = c.execute(
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
        history = c.execute(
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

    # ★ ノート取得
    notes = c.execute(
        """
        SELECT id, title, content, scrapbox_url, created_at
        FROM notes
        WHERE project_id=?
        ORDER BY created_at DESC
    """,
        (project_id,),
    ).fetchall()

    conn.close()

    return render_template(
        "project_detail.html",
        project=project,
        tasks=all_tasks,
        overdue_tasks=overdue_tasks,
        today_tasks=today_tasks,
        future_tasks=future_tasks,
        anytime_tasks=anytime_tasks,
        done_tasks=done_tasks,
        archived_tasks=archived_tasks,
        notes=notes,
        history=history,
        students=STUDENTS_DATA,
    )


@app.route("/add_note", methods=["POST"])
def add_note():
    title = request.form["title"]
    content = request.form["content"]
    project_id = request.form["project_id"]
    student_id = request.form.get("student_id")
    scrapbox_url = request.form["scrapbox_url"]

    conn = get_db()
    c = conn.cursor()

    c.execute(
        """
            INSERT INTO notes (title, content, project_id, student_id, scrapbox_url, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
        """,
        (title, content, project_id, student_id, scrapbox_url),
    )

    conn.commit()
    conn.close()

    return redirect("/project/" + project_id)


def guess_student_id(text):
    for s in STUDENTS_DATA:
        name = s.get("name")
        student_id = s.get("student_id")
        if name and student_id and name in text:
            return student_id
    return None


# ===== タスクタイトル正規化 =====
def normalize_task_title(text):
    title = text or ""

    leading_patterns = [
        r"^今日中に",
        r"^今日までに",
        r"^明日までに",
        r"^明後日までに",
        r"^来週までに",
        r"^今週中に",
        r"^至急",
        r"^急ぎで",
        r"^急ぎ",
        r"^重要",
    ]
    for pattern in leading_patterns:
        title = re.sub(pattern, "", title)

    trailing_patterns = [
        r"（重要）$",
        r"\(重要\)$",
        r"【重要】$",
        r"\[重要\]$",
        r"（急ぎ）$",
        r"\(急ぎ\)$",
        r"【急ぎ】$",
        r"\[急ぎ\]$",
        r"（至急）$",
        r"\(至急\)$",
        r"【至急】$",
        r"\[至急\]$",
    ]
    for pattern in trailing_patterns:
        title = re.sub(pattern, "", title)

    title = re.sub(r"\s+", " ", title).strip()
    return title or (text or "").strip()


# ===== プロジェクト解決（統合スコアリング） =====
def resolve_project_id_from_text(text, projects, default_project_id=None):
    text = text or ""

    # 1. 学生優先
    for name in STUDENTS:
        if name and name in text:
            for p in projects:
                if p["name"] == "学生指導":
                    return p["id"]

    # 2. スコアリング
    scores = {}

    for project_name, keywords in PROJECT_RULES.items():
        score = 0
        for kw in keywords:
            if kw in text:
                score += 2  # ルール一致は強め

        # プロジェクト名そのもの一致はさらに強く
        if project_name in text:
            score += 3

        if score > 0:
            scores[project_name] = score

    # 3. エイリアス補正
    aliases = {
        "メディア": "授業（メディア情報学）",
        "音情": "授業（音情報処理）",
        "研究室": "研究室運営OS",
        "OS": "研究室運営OS",
    }

    for key, val in aliases.items():
        if key in text:
            scores[val] = scores.get(val, 0) + 2

    # 4. スコア最大を選択
    if scores:
        best_project_name = max(scores, key=scores.get)
        for p in projects:
            if p["name"] == best_project_name:
                return p["id"]

    # 5. フォールバック
    return default_project_id


# ===== ChatGPTフォールバック解析 =====
def parse_task_with_chatgpt(text, default_title, default_deadline, default_priority):
    import json

    result_data = {
        "title": default_title,
        "deadline": default_deadline,
        "priority": default_priority,
        "project_hint": "",
    }

    if not client:
        return result_data

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "ユーザーのタスク入力を解析してJSONで返してください。                        フィールド: title, deadline(YYYY-MM-DD or null), priority(high/medium), project_hint",
                },
                {"role": "user", "content": text},
            ],
        )

        parsed = json.loads(response.choices[0].message.content)
        result_data["title"] = parsed.get("title") or default_title
        result_data["deadline"] = parsed.get("deadline") or default_deadline
        result_data["priority"] = parsed.get("priority") or default_priority
        result_data["project_hint"] = parsed.get("project_hint", "")
    except Exception as e:
        print("ChatGPT解析失敗:", e)

    return result_data


# sync_key自動生成
def generate_sync_key(item):
    source_type = item.get("source_type", "manual_json")
    project = item.get("project") or "no-project"
    student_id = item.get("student_id") or "no-student"
    deadline = item.get("deadline") or "no-deadline"
    title = item.get("title") or "untitled"

    # 英数字以外はハイフン化して簡易スラッグを作る
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    if not slug:
        slug = "task"

    return f"{source_type}:{project}:{student_id}:{deadline}:{slug}"


# 学生名簿をプロンプトへ渡すための補助関数
def build_student_catalog_text():
    rows = []
    seen = set()
    for s in STUDENTS_DATA:
        name = s.get("name")
        student_id = s.get("student_id")
        key = (name, student_id)
        if not name or key in seen:
            continue
        seen.add(key)
        if student_id:
            rows.append(f"- {name}: {student_id}")
    return "\n".join(rows)


# ===== ローカルタスク抽出器 =====
def clean_task_title(text):
    title = (text or "").strip()

    # 箇条書き記号や番号付きリスト（例: "1. "）のみ除去し、日付の数字は残す
    title = re.sub(r"^[\-・●○◯▪︎■□☑︎✓✔\s]+", "", title)
    title = re.sub(r"^\d+\.\s*", "", title)
    title = re.sub(r"^(完了済み[:：]?|完了[:：]?|済[:：]?)", "", title).strip()
    # 「TODO:」「ToDo:」「メモ:」のようなラベルだけ除去する。
    # 「メモ整理する」は実タスクなので消さない。
    title = re.sub(r"^(TODO[:：]?|ToDo[:：]?|メモ[:：])", "", title).strip()

    # 行末ノイズ除去
    title = re.sub(r"[。．]+$", "", title)
    title = re.sub(r"[ 　]+$", "", title)

    # 期限や優先度の定型句だけ軽く除去
    leading_patterns = [
        r"^今日中に",
        r"^今日までに",
        r"^明日までに",
        r"^明後日までに",
        r"^来週までに",
        r"^今週中に",
        r"^至急",
        r"^急ぎで",
        r"^急ぎ",
        r"^重要",
    ]
    for pattern in leading_patterns:
        title = re.sub(pattern, "", title)

    trailing_patterns = [
        r"（重要）$",
        r"\(重要\)$",
        r"【重要】$",
        r"\[重要\]$",
        r"（急ぎ）$",
        r"\(急ぎ\)$",
        r"【急ぎ】$",
        r"\[急ぎ\]$",
        r"（至急）$",
        r"\(至急\)$",
        r"【至急】$",
        r"\[至急\]$",
    ]
    for pattern in trailing_patterns:
        title = re.sub(pattern, "", title)

    title = re.sub(r"\s+", " ", title).strip()
    return title


def local_extract_priority(text):
    text = text or ""
    if any(word in text for word in ["重要", "急ぎ", "至急", "早め", "優先"]):
        return "high"
    return "medium"


def local_extract_status(text):
    text = text or ""
    done_patterns = [
        "完了",
        "済み",
        "済",
        "提出済",
        "出席した",
        "対応した",
        "終了",
        "終わった",
    ]
    if any(word in text for word in done_patterns):
        return "done"
    return "todo"


def local_extract_deadline(text):
    from datetime import datetime, timedelta

    text = text or ""
    today = datetime.now()
    deadline = None

    # 1) YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 2) M/D
    m = re.search(r"(\d{1,2})/(\d{1,2})", text)
    if m:
        y = today.year
        mm, dd = int(m.group(1)), int(m.group(2))
        try:
            dt = datetime(y, mm, dd)
            if dt.date() < today.date():
                dt = datetime(y + 1, mm, dd)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass

    # 3) 相対日
    if "今日" in text:
        deadline = today.strftime("%Y-%m-%d")
    elif "明日" in text:
        deadline = (today + timedelta(days=1)).strftime("%Y-%m-%d")
    elif "明後日" in text:
        deadline = (today + timedelta(days=2)).strftime("%Y-%m-%d")

    # 4) 曜日
    weekdays = {"月": 0, "火": 1, "水": 2, "木": 3, "金": 4, "土": 5, "日": 6}
    if not deadline:
        for k, v in weekdays.items():
            if k + "曜" in text:
                if "来週" in text:
                    ref = today + timedelta(days=7)
                else:
                    ref = today
                days_ahead = (v - ref.weekday() + 7) % 7
                if days_ahead == 0:
                    days_ahead = 7
                deadline = (ref + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                break

    # 5) 月末系
    if not deadline:
        if "今月末" in text or ("月末" in text and "来月" not in text):
            next_month = datetime(
                today.year + (today.month // 12), (today.month % 12) + 1, 1
            )
            last_day = next_month - timedelta(days=1)
            deadline = last_day.strftime("%Y-%m-%d")
        elif "来月末" in text:
            nm = datetime(
                today.year + ((today.month + 1) // 12), ((today.month + 1) % 12) + 1, 1
            )
            last_day = nm - timedelta(days=1)
            deadline = last_day.strftime("%Y-%m-%d")

    # 6) 週中/月中
    if not deadline:
        if "今週中" in text:
            target_weekday = 4
            days_ahead = (target_weekday - today.weekday() + 7) % 7
            deadline = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        elif "来週中" in text:
            ref = today + timedelta(days=7)
            target_weekday = 4
            days_ahead = (target_weekday - ref.weekday() + 7) % 7
            deadline = (ref + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        elif "今月中" in text:
            next_month = datetime(
                today.year + (today.month // 12), (today.month % 12) + 1, 1
            )
            last_day = next_month - timedelta(days=1)
            if last_day.weekday() == 5:
                last_day = last_day - timedelta(days=1)
            elif last_day.weekday() == 6:
                last_day = last_day - timedelta(days=2)
            deadline = last_day.strftime("%Y-%m-%d")

    # 7) ◯日 / ◯日まで / ◯日頃
    if not deadline:
        m = re.search(r"(\d{1,2})日(?:まで|頃)?", text)
        if m:
            d = int(m.group(1))
            try:
                dt = datetime(today.year, today.month, d)
                if dt.date() < today.date():
                    y = today.year + (today.month // 12)
                    mth = (today.month % 12) + 1
                    dt = datetime(y, mth, d)
                deadline = dt.strftime("%Y-%m-%d")
            except Exception:
                pass

    # 8) ASAP / 週末 / 年度末
    if not deadline:
        if "ASAP" in text or "asap" in text:
            deadline = today.strftime("%Y-%m-%d")
        elif "週末" in text:
            target = 5
            days_ahead = (target - today.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            deadline = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        elif "年度末" in text:
            year = today.year
            if today.month >= 4:
                year += 1
            march_end = datetime(year, 3, 31)
            if march_end.weekday() == 5:
                march_end = march_end - timedelta(days=1)
            elif march_end.weekday() == 6:
                march_end = march_end - timedelta(days=2)
            deadline = march_end.strftime("%Y-%m-%d")

    return deadline


def split_memo_into_task_lines(memo_text):
    raw_lines = []
    for line in (memo_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^[\-・●○◯▪︎■□☑︎✓✔]+", "", line).strip()
        if not line:
            continue
        raw_lines.append(line)

    candidates = []
    for line in raw_lines:
        parts = re.split(r"[。．]\s*", line)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            candidates.append(part)

    return candidates


def local_generate_sync_tasks(memo_text):
    tasks = []
    seen = set()

    for line in split_memo_into_task_lines(memo_text):
        title = clean_task_title(line)
        if not title:
            continue

        # あまりに短い断片を除外
        if len(title) <= 2:
            continue

        # project / student 推定
        student_id = guess_student_id(line)
        project_name = None

        if student_id:
            project_name = "学生指導"
        else:
            scores = {}
            for project_name_candidate, keywords in PROJECT_RULES.items():
                score = sum(1 for kw in keywords if kw in line)
                if score > 0:
                    scores[project_name_candidate] = score
            if scores:
                project_name = max(scores, key=scores.get)
            else:
                project_name = None  # Inboxにフォールバック

        task = {
            "title": title,
            "deadline": local_extract_deadline(line),
            "project": project_name,
            "student_id": student_id,
            "priority": local_extract_priority(line),
            "status": local_extract_status(line),
            "source_type": "chatgpt_memory",
        }

        dedupe_key = (
            task["title"],
            task["deadline"],
            task["project"],
            task["student_id"],
            task["status"],
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        tasks.append(task)

    return tasks


def resolve_project_id(cursor, project_name):
    projects = fetch_all_projects(cursor)

    # 明示指定があれば優先
    if project_name:
        row = cursor.execute(
            "SELECT id FROM projects WHERE name=? LIMIT 1",
            (project_name,),
        ).fetchone()
        if row:
            return row["id"]

        # 明示指定でも見つからない場合はテキストとして解釈
        resolved = resolve_project_id_from_text(project_name, projects, None)
        if resolved:
            return resolved

    # project_nameが無い場合もテキスト推定は行わない（sync用途のため）

    # 見つからない場合は Inbox にフォールバック
    inbox = cursor.execute(
        "SELECT id FROM projects WHERE name='Inbox' LIMIT 1"
    ).fetchone()

    return inbox["id"] if inbox else None


SYNC_FIELDS = [
    "title",
    "deadline",
    "project_id",
    "student_id",
    "priority",
    "status",
    "archived",
    "source_type",
    "source_updated_at",
]


def normalize_sync_item(item, cursor):
    project_name = item.get("project")
    project_id = resolve_project_id(cursor, project_name)
    sync_key = item.get("sync_key") or generate_sync_key(item)
    return {
        "sync_key": sync_key,
        "title": item.get("title"),
        "deadline": item.get("deadline") or None,
        "project_id": project_id,
        "project_name": project_name,
        "student_id": item.get("student_id"),
        "priority": item.get("priority", "medium"),
        "status": item.get("status", "todo"),
        "archived": int(item.get("archived", 0)),
        "source_type": item.get("source_type", "manual_json"),
        "source_updated_at": item.get("source_updated_at"),
    }


# === Merge and normalization helpers for imported tasks ===
def merge_normalized_task(existing, incoming):
    merged = dict(existing)

    for field in [
        "title",
        "deadline",
        "project_id",
        "project_name",
        "student_id",
        "priority",
        "status",
        "archived",
        "source_type",
        "source_updated_at",
    ]:
        new_value = incoming.get(field)
        old_value = merged.get(field)

        if field == "priority":
            priority_rank = {"low": 0, "medium": 1, "high": 2}
            if priority_rank.get(new_value, -1) > priority_rank.get(old_value, -1):
                merged[field] = new_value
            continue

        if field == "status":
            if old_value != "done" and new_value == "done":
                merged[field] = new_value
            continue

        if field == "source_updated_at":
            if new_value and (not old_value or new_value >= old_value):
                merged[field] = new_value
            continue

        if new_value not in [None, ""]:
            merged[field] = new_value

    return merged


def normalize_imported_tasks(imported_items, cursor):
    normalized_by_key = {}
    errors = []

    for item in imported_items:
        if not isinstance(item, dict):
            errors.append({"error": "invalid_item_type", "value": str(item)})
            continue

        normalized = normalize_sync_item(item, cursor)
        sync_key = normalized.get("sync_key")
        if not sync_key:
            errors.append({"error": "missing_sync_key", "value": str(item)})
            continue

        if sync_key in normalized_by_key:
            normalized_by_key[sync_key] = merge_normalized_task(
                normalized_by_key[sync_key], normalized
            )
        else:
            normalized_by_key[sync_key] = normalized

    return list(normalized_by_key.values()), errors


def diff_task(existing, normalized):
    changes = []
    for field in SYNC_FIELDS:
        old = existing[field]
        new = normalized[field]
        if old != new:
            changes.append({"field": field, "old": old, "new": new})
    return changes


def create_task_from_sync(cursor, normalized):
    cursor.execute(
        """
        INSERT INTO tasks (
            title, status, deadline, original_deadline, project_id, student_id,
            priority, sync_key, source_type, source_updated_at, archived
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            normalized["title"],
            normalized["status"],
            normalized["deadline"] or None,
            normalized["deadline"] or None,
            normalized["project_id"],
            normalized["student_id"],
            normalized["priority"],
            normalized["sync_key"],
            normalized["source_type"],
            normalized["source_updated_at"],
            normalized["archived"],
        ),
    )
    return cursor.lastrowid


def record_sync_changes(cursor, task_id, sync_key, source_type, changes):
    for change in changes:
        cursor.execute(
            """
            INSERT INTO sync_history (
                task_id, sync_key, field_name, old_value, new_value, changed_at, source_type
            )
            VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
            """,
            (
                task_id,
                sync_key,
                change["field"],
                None if change["old"] is None else str(change["old"]),
                None if change["new"] is None else str(change["new"]),
                source_type,
            ),
        )

        if change["field"] == "deadline":
            cursor.execute(
                """
                INSERT INTO task_history (task_id, old_deadline, new_deadline, changed_at)
                VALUES (?, ?, ?, datetime('now'))
                """,
                (task_id, change["old"], change["new"]),
            )


def apply_task_update(cursor, task_id, normalized, changes):
    if not changes:
        return

    update_fields = [f"{change['field']}=?" for change in changes]
    values = [change["new"] for change in changes]
    values.append(task_id)
    cursor.execute(
        f"UPDATE tasks SET {', '.join(update_fields)} WHERE id=?",
        values,
    )


def build_sync_diff(imported_items, cursor):
    results = {
        "create": [],
        "update": [],
        "archive": [],
        "unchanged": [],
        "errors": [],
    }

    normalized_items, normalize_errors = normalize_imported_tasks(
        imported_items, cursor
    )
    results["errors"].extend(normalize_errors)

    imported_sync_keys = set()
    source_types = set()

    for normalized in normalized_items:
        sync_key = normalized["sync_key"]
        imported_sync_keys.add(sync_key)
        source_type = normalized.get("source_type") or "manual_json"
        source_types.add(source_type)

        existing = cursor.execute(
            "SELECT * FROM tasks WHERE sync_key=? LIMIT 1",
            (sync_key,),
        ).fetchone()

        if not existing:
            results["create"].append(normalized)
            continue

        changes = diff_task(existing, normalized)
        if changes:
            results["update"].append(
                {
                    "task_id": existing["id"],
                    "sync_key": sync_key,
                    "source_type": source_type,
                    "normalized": normalized,
                    "changes": changes,
                }
            )
        else:
            results["unchanged"].append(
                {"task_id": existing["id"], "sync_key": sync_key}
            )

    for source_type in source_types:
        if source_type == "chatgpt_memory":
            continue
        
        existing_rows = cursor.execute(
            """
            SELECT id, sync_key
            FROM tasks
            WHERE source_type=?
              AND sync_key IS NOT NULL
              AND archived=0
            """,
            (source_type,),
        ).fetchall()

        for row in existing_rows:
            if row["sync_key"] not in imported_sync_keys:
                task_row = cursor.execute(
                    """
                    SELECT id, title, sync_key, source_type, archived
                    FROM tasks
                    WHERE id=?
                    LIMIT 1
                    """,
                    (row["id"],),
                ).fetchone()

                snapshot_row = cursor.execute(
                    """
                    SELECT sync_key, title, deadline, project_id, student_id, priority, status, updated_at
                    FROM sync_snapshot
                    WHERE sync_key=?
                    LIMIT 1
                    """,
                    (row["sync_key"],),
                ).fetchone()

                results["archive"].append(
                    {
                        "task_id": row["id"],
                        "title": task_row["title"] if task_row else None,
                        "sync_key": row["sync_key"],
                        "source_type": source_type,
                        "is_soft_delete": True,
                        "comparison_basis": "前回同期スナップショット vs 現在インポートJSON",
                        "snapshot_found": bool(snapshot_row),
                        "snapshot_title": (
                            snapshot_row["title"] if snapshot_row else None
                        ),
                        "snapshot_updated_at": (
                            snapshot_row["updated_at"] if snapshot_row else None
                        ),
                        "revivable": True,
                    }
                )

    return results


# ===== クイックタスクペイロード構築 =====
def build_quick_task_payload(text, cursor):
    normalized_text = normalize_quotes(text)
    inbox = cursor.execute(
        "SELECT id FROM projects WHERE name='Inbox' LIMIT 1"
    ).fetchone()
    default_project_id = inbox["id"] if inbox else None

    projects = fetch_all_projects(cursor)
    title = normalize_task_title(normalized_text)
    project_id = resolve_project_id_from_text(
        normalized_text, projects, default_project_id
    )
    student_id = guess_student_id(normalized_text)
    deadline = local_extract_deadline(normalized_text)
    priority = local_extract_priority(normalized_text)

    parsed = parse_task_with_chatgpt(normalized_text, title, deadline, priority)
    title = parsed["title"]
    deadline = parsed["deadline"]
    priority = parsed["priority"]
    project_hint = parsed["project_hint"]

    if project_hint:
        resolved = resolve_project_id_from_text(project_hint, projects, project_id)
        if resolved:
            project_id = resolved

    return {
        "title": title,
        "project_id": project_id,
        "student_id": student_id,
        "deadline": deadline,
        "priority": priority,
    }


@app.route("/quick_add", methods=["POST"])
def quick_add():
    text = request.form["text"]

    conn = get_db()
    c = conn.cursor()
    payload = build_quick_task_payload(text, c)

    c.execute(
        "INSERT INTO tasks (title, status, project_id, deadline, original_deadline, student_id, priority) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            payload["title"],
            "todo",
            payload["project_id"],
            payload["deadline"] or None,
            payload["deadline"] or None,
            payload["student_id"],
            payload["priority"],
        ),
    )
    conn.commit()
    conn.close()

    return redirect("/")


# ChatGPTからのタスク一括インポート（簡易版）
@app.route("/import_tasks", methods=["POST"])
def import_tasks():
    import json

    raw = request.form.get("json")
    raw = normalize_quotes(raw)
    data = json.loads(raw)

    conn = get_db()
    c = conn.cursor()

    normalized_items, _ = normalize_imported_tasks(data, c)

    for t in normalized_items:
        c.execute(
            """
            INSERT INTO tasks (
                title, status, project_id, deadline, original_deadline,
                student_id, priority, sync_key, source_type, source_updated_at, archived
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                t.get("title"),
                t.get("status", "todo"),
                t.get("project_id"),
                t.get("deadline") or None,
                t.get("deadline") or None,
                t.get("student_id"),
                t.get("priority", "medium"),
                t.get("sync_key"),
                t.get("source_type", "manual_json"),
                t.get("source_updated_at"),
                int(t.get("archived", 0)),
            ),
        )

    conn.commit()
    conn.close()

    return redirect("/")


# ChatGPTを使わないローカルタスク抽出器
@app.route("/generate_sync_json", methods=["POST"])
def generate_sync_json():
    memo_text = request.form.get("memo", "")
    memo_text = normalize_quotes(memo_text).strip()
    if not memo_text:
        return jsonify({"error": "memo is empty"}), 400

    try:
        tasks = local_generate_sync_tasks(memo_text)
        return jsonify({"tasks": tasks, "mode": "local"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/sync_preview", methods=["POST"])
def sync_preview():
    import json

    raw_json = request.form.get("json", "")
    raw_json = normalize_quotes(raw_json)

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        # シングルクォート対応（暫定フォールバック）
        try:
            fixed = raw_json.replace("'", '"')
            data = json.loads(fixed)
        except Exception as e:
            return jsonify({"error": f"Invalid JSON: {str(e)}", "raw": raw_json}), 400

    # {"tasks": [...]} 形式対応
    if isinstance(data, dict) and "tasks" in data:
        data = data["tasks"]

    conn = get_db()
    c = conn.cursor()
    diff = build_sync_diff(data, c)
    conn.close()

    return jsonify(
        {
            "create": len(diff["create"]),
            "update": len(diff["update"]),
            "archive": len(diff["archive"]),
            "unchanged": len(diff["unchanged"]),
            "errors": diff["errors"],
            "details": {
                "create": diff["create"],
                "update": [
                    {
                        "task_id": item["task_id"],
                        "sync_key": item["sync_key"],
                        "title": item["normalized"].get("title"),
                        "changes": item["changes"],
                    }
                    for item in diff["update"]
                ],
                "archive": diff["archive"],
            },
        }
    )


@app.route("/sync_apply", methods=["POST"])
def sync_apply():
    import json

    raw_json = request.form.get("json", "")
    raw_json = normalize_quotes(raw_json)

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        try:
            fixed = raw_json.replace("'", '"')
            data = json.loads(fixed)
        except Exception as e:
            return jsonify({"error": f"Invalid JSON: {str(e)}", "raw": raw_json}), 400

    # {"tasks": [...]} 形式対応
    if isinstance(data, dict) and "tasks" in data:
        data = data["tasks"]

    conn = get_db()
    c = conn.cursor()
    diff = build_sync_diff(data, c)

    created = 0
    updated = 0
    archived = 0
    updated_task_ids = []

    for normalized in diff["create"]:
        create_task_from_sync(c, normalized)
        created += 1

    for item in diff["update"]:
        apply_task_update(c, item["task_id"], item["normalized"], item["changes"])
        record_sync_changes(
            c,
            item["task_id"],
            item["sync_key"],
            item["source_type"],
            item["changes"],
        )
        updated_task_ids.append(item["task_id"])
        updated += 1

    for item in diff["archive"]:
        c.execute("UPDATE tasks SET archived=1 WHERE id=?", (item["task_id"],))
        c.execute(
            """
            INSERT INTO sync_history (
                task_id, sync_key, field_name, old_value, new_value, changed_at, source_type
            )
            VALUES (?, ?, 'archived', '0', '1', datetime('now'), ?)
            """,
            (item["task_id"], item["sync_key"], item["source_type"]),
        )
        archived += 1

    try:
        conn.commit()
        if created or updated or archived:
            update_snapshot(c)
            conn.commit()
        # --- Fetch recent sync_history records ---
        history_rows = c.execute(
            """
            SELECT task_id, sync_key, field_name, old_value, new_value, changed_at, source_type
            FROM sync_history
            ORDER BY changed_at DESC
            LIMIT 50
            """
        ).fetchall()
        sync_history = [dict(row) for row in history_rows]
    finally:
        conn.close()

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


def update_snapshot(cursor):
    cursor.execute("DELETE FROM sync_snapshot")

    cursor.execute(
        """
        INSERT INTO sync_snapshot
        SELECT sync_key, title, deadline, project_id, student_id, priority, status, datetime('now')
        FROM tasks
        WHERE archived=0
    """
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
    conn = get_db()
    c = conn.cursor()

    rows = c.execute(
        """
        SELECT t.title, t.deadline, t.priority, t.status,
               p.name as project_name, t.student_id
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.archived = 0
    """
    ).fetchall()

    conn.close()

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

    rows = c.execute(
        """
        SELECT t.title, t.deadline, t.priority,
               p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.status != 'done' AND t.archived = 0
        ORDER BY t.deadline IS NULL, t.deadline
    """
    ).fetchall()

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

    delta = build_delta(c)

    conn.close()

    return jsonify({"delta": delta})


@app.route("/edit_task_title", methods=["POST"])
def edit_task_title():
    task_id = request.form["task_id"]
    new_title = request.form["title"]

    if not new_title:
        return redirect("/")

    conn = get_db()
    c = conn.cursor()

    try:
        c.execute(
            "UPDATE tasks SET title=? WHERE id=?",
            (new_title, task_id),
        )
        conn.commit()
    finally:
        conn.close()

    next_url = request.form.get("next")
    return redirect(next_url or "/")

if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001)

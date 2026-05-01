import json
import re

from db import fetch_all_projects, fetch_inbox_project_id
from task_parser import normalize_quotes, resolve_project_id_from_text


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
    "source_url",
]


def parse_sync_payload(raw_json):
    raw_json = normalize_quotes(raw_json)

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        # シングルクォート対応（暫定フォールバック）
        try:
            fixed = raw_json.replace("'", '"')
            data = json.loads(fixed)
        except Exception as e:
            return None, {"error": f"Invalid JSON: {str(e)}", "raw": raw_json}

    # {"tasks": [...]} 形式対応
    if isinstance(data, dict) and "tasks" in data:
        data = data["tasks"]

    return data, None


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


def resolve_project_id(cursor, project_name, students_data=None):
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
        resolved = resolve_project_id_from_text(
            project_name, projects, None, students_data
        )
        if resolved:
            return resolved

    # project_nameが無い場合もテキスト推定は行わない（sync用途のため）

    # 見つからない場合は Inbox にフォールバック
    return fetch_inbox_project_id(cursor)


def normalize_sync_item(item, cursor, students_data=None):
    project_name = item.get("project")
    project_id = resolve_project_id(cursor, project_name, students_data)
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
        "source_url": item.get("source_url"),
    }


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


def normalize_imported_tasks(imported_items, cursor, students_data=None):
    normalized_by_key = {}
    errors = []

    for item in imported_items:
        if not isinstance(item, dict):
            errors.append({"error": "invalid_item_type", "value": str(item)})
            continue

        normalized = normalize_sync_item(item, cursor, students_data)
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
            priority, sync_key, source_type, source_updated_at, source_url, archived
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            normalized.get("source_url"),
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


def build_sync_diff(imported_items, cursor, students_data=None):
    results = {
        "create": [],
        "update": [],
        "archive": [],
        "unchanged": [],
        "errors": [],
    }

    normalized_items, normalize_errors = normalize_imported_tasks(
        imported_items, cursor, students_data
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


def apply_sync_diff(cursor, diff):
    created = 0
    updated = 0
    archived = 0
    updated_task_ids = []

    for normalized in diff["create"]:
        create_task_from_sync(cursor, normalized)
        created += 1

    for item in diff["update"]:
        apply_task_update(cursor, item["task_id"], item["normalized"], item["changes"])
        record_sync_changes(
            cursor,
            item["task_id"],
            item["sync_key"],
            item["source_type"],
            item["changes"],
        )
        updated_task_ids.append(item["task_id"])
        updated += 1

    for item in diff["archive"]:
        cursor.execute("UPDATE tasks SET archived=1 WHERE id=?", (item["task_id"],))
        cursor.execute(
            """
            INSERT INTO sync_history (
                task_id, sync_key, field_name, old_value, new_value, changed_at, source_type
            )
            VALUES (?, ?, 'archived', '0', '1', datetime('now'), ?)
            """,
            (item["task_id"], item["sync_key"], item["source_type"]),
        )
        archived += 1

    return created, updated, archived, updated_task_ids


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


def fetch_recent_sync_history(cursor, limit=50):
    rows = cursor.execute(
        """
        SELECT task_id, sync_key, field_name, old_value, new_value, changed_at, source_type
        FROM sync_history
        ORDER BY changed_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def build_sync_preview_response(data, cursor, students_data=None):
    diff = build_sync_diff(data, cursor, students_data)
    return {
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

import json
import os
import re
from urllib.parse import quote

from db import (
    fetch_all_projects,
    fetch_inbox_project_id,
    generate_completion_triggered_tasks,
    insert_note,
)
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

GPT_MEMORY_POLICY = {
    "purpose": "GPTメモリを意思決定に必要な最小構造のみに圧縮する",
    "gpt_keeps": [
        "戦略（practice + articulation）",
        "優先ピラー",
        "研究室運営OSの構造",
        "学生マネジメント方針",
        "年度業務構造",
    ],
    "externalized_to_scrapbox": [
        "知識蓄積",
        "思考ログ",
        "研究メモ",
        "参照情報",
        "完了業務ログ",
    ],
    "externalized_to_os": [
        "タスク管理",
        "期限管理",
        "担当管理",
        "状態管理",
        "学生個別ログ",
    ],
}

LAB_OS_TERMS = [
    "研究室運営os",
    "lab os",
    "dashboard",
    "ダッシュボード",
    "同期",
    "ui",
    "タスク統合",
    "構造",
]

STRATEGY_TERMS = [
    "戦略",
    "方針",
    "構造",
    "設計",
    "優先",
    "ピラー",
    "practice",
    "articulation",
    "役割分担",
    "再構成",
    "シナリオ",
    "論点整理",
    "テーマ整理",
    "申請枠",
    "研究テーマ",
]

STUDENT_MANAGEMENT_TERMS = [
    "学生マネジメント",
    "学生指導",
    "研究室配属",
    "指導方針",
    "育成",
    "面談設計",
    "指導設計",
]

ANNUAL_WORK_TERMS = [
    "年度",
    "授業",
    "委員会",
    "評価",
    "ac審査",
    "科研費",
    "kakenhi",
    "報告書",
    "申請",
    "審査",
    "業務構造",
]

PRIORITY_PILLAR_TERMS = [
    "研究",
    "論文",
    "投稿",
    "科研",
    "審査",
    "申請",
    "評価",
    "年度",
    "運営",
    "授業",
]

LAB_OS_STRUCTURE_TERMS = [
    "構造",
    "設計",
    "方針",
    "同期",
    "統合",
    "dashboard",
    "ダッシュボード",
    "ui",
    "学生ログ",
    "プロジェクト詳細",
]

STUDENT_POLICY_TERMS = [
    "方針",
    "体制",
    "設計",
    "管理",
    "マネジメント",
    "限定",
    "優先",
    "計画",
]


SCRAPBOX_PROJECT = os.getenv("SCRAPBOX_PROJECT", "musestudio")


def build_scrapbox_page_url(title):
    page_name = (title or "").strip()
    if not page_name:
        return None
    return f"https://scrapbox.io/{SCRAPBOX_PROJECT}/{quote(page_name, safe='')}"


def _note_value_to_text(value):
    if value in (None, "", "None", "none", "NULL", "null"):
        return []

    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []

    if isinstance(value, list):
        parts = []
        for item in value:
            parts.extend(_note_value_to_text(item))
        return parts

    if isinstance(value, dict):
        for key in ("content", "text", "body", "note", "notes"):
            if key in value:
                parts = _note_value_to_text(value.get(key))
                if parts:
                    return parts
        return [json.dumps(value, ensure_ascii=False, sort_keys=True)]

    text = str(value).strip()
    return [text] if text else []


def extract_imported_note_content(item):
    parts = []
    for key in ("note", "notes"):
        for text in _note_value_to_text(item.get(key)):
            if text and text not in parts:
                parts.append(text)
    return "\n\n".join(parts) if parts else None


def _imported_note_identity(task_id, normalized):
    content = (normalized.get("note") or "").strip()
    if not content:
        return None

    title = normalized.get("note_title") or normalized.get("title") or f"task-{task_id}"
    scrapbox_url = (
        normalized.get("note_scrapbox_url")
        or normalized.get("scrapbox_url")
        or build_scrapbox_page_url(normalized.get("title"))
    )
    return {"title": title, "content": content, "scrapbox_url": scrapbox_url}


def imported_task_note_exists(cursor, task_id, normalized):
    note = _imported_note_identity(task_id, normalized)
    if not note:
        return False

    existing = cursor.execute(
        """
        SELECT id
        FROM notes
        WHERE task_id=?
          AND title=?
          AND content=?
        LIMIT 1
        """,
        (task_id, note["title"], note["content"]),
    ).fetchone()
    return bool(existing)


def build_note_change(cursor, task_id, normalized):
    note = _imported_note_identity(task_id, normalized)
    if not note or imported_task_note_exists(cursor, task_id, normalized):
        return None

    return {
        "field": "note",
        "old": None,
        "new": note["content"],
        "note_title": note["title"],
    }


def upsert_imported_task_note(cursor, task_id, normalized):
    note = _imported_note_identity(task_id, normalized)
    if not note:
        return False

    if imported_task_note_exists(cursor, task_id, normalized):
        return False

    insert_note(
        cursor,
        note["title"],
        note["content"],
        normalized.get("project_id"),
        normalized.get("student_id"),
        task_id,
        note["scrapbox_url"],
    )
    return True


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


def _project_lookup(cursor):
    rows = cursor.execute("SELECT id, name, type FROM projects").fetchall()
    return {row["id"]: {"name": row["name"], "type": row["type"]} for row in rows}


def _enrich_project_fields(item, projects):
    project_id = item.get("project_id")
    project = projects.get(project_id)
    if not project:
        return item

    item.setdefault("project_name", project["name"])
    item.setdefault("project_type", project["type"])
    return item


def _match_text(*values):
    return " ".join(str(value or "") for value in values).lower()


def _contains_any(text, terms):
    return any(term.lower() in text for term in terms)


def _is_completed(item):
    return str(item.get("status") or "").lower() in {"done", "completed", "complete"}


def _changed_to_done(item):
    for change in item.get("changes", []) or []:
        if (
            change.get("field") == "status"
            and str(change.get("new") or "").lower() == "done"
        ):
            return True
    return False


def _classify_gpt_memory_item(kind, item):
    title_text = _match_text(item.get("title"))
    project_text = _match_text(item.get("project_name"), item.get("project_type"))
    text = _match_text(title_text, project_text)
    completion_delta = _changed_to_done(item) or kind == "deleted"
    completed = _is_completed(item) or completion_delta

    if _is_completed(item) and not completion_delta:
        return {
            "include": False,
            "omit_bucket": "scrapbox_archive",
            "reason": "完了済みはScrapbox退避後に削除",
        }

    category = None
    reason = None

    if _contains_any(project_text, LAB_OS_TERMS) and _contains_any(
        title_text, LAB_OS_STRUCTURE_TERMS
    ):
        category = "lab_os_structure"
        reason = "研究室運営OSの構造に関わる変更"
    elif _contains_any(text, STUDENT_MANAGEMENT_TERMS) and _contains_any(
        title_text, STUDENT_POLICY_TERMS
    ):
        category = "student_management_policy"
        reason = "学生マネジメント方針に関わる変更"
    elif _contains_any(title_text, STRATEGY_TERMS):
        category = "strategy"
        reason = "戦略・設計・構造に関わる変更"
    elif _contains_any(title_text, ANNUAL_WORK_TERMS):
        category = "annual_work_structure"
        reason = "年度業務構造に関わる変更"
    elif item.get("priority") == "high" and _contains_any(
        title_text, PRIORITY_PILLAR_TERMS
    ):
        category = "priority_pillar"
        reason = "優先順位判断に影響する変更"

    if not category:
        omit_bucket = "scrapbox_archive" if completed else "os_only"
        return {
            "include": False,
            "omit_bucket": omit_bucket,
            "reason": (
                "完了済み・ログ系はScrapbox退避後に削除"
                if completed
                else "期限・担当・状態は研究室運営OSに保持"
            ),
        }

    return {
        "include": True,
        "category": category,
        "reason": reason,
        "action": "remove_from_gpt_memory" if completed else "upsert_gpt_memory",
    }


def _memory_change(change, item):
    field = change.get("field")
    old = change.get("old")
    new = change.get("new")

    if field == "project_id":
        return {
            "field": field,
            "old": old,
            "new": new,
            "new_project": item.get("project_name"),
        }

    return {"field": field, "old": old, "new": new}


def _minimal_gpt_memory_item(kind, item, classification):
    minimal = {
        "sync_key": item.get("sync_key"),
        "title": item.get("title"),
        "project": item.get("project_name"),
        "project_type": item.get("project_type"),
        "status": item.get("status"),
        "priority": item.get("priority"),
        "category": classification["category"],
        "memory_action": classification["action"],
        "reason": classification["reason"],
    }

    if item.get("deadline"):
        minimal["deadline"] = item.get("deadline")

    if item.get("changes"):
        minimal["changes"] = [
            _memory_change(change, item)
            for change in item.get("changes", [])
            if change.get("field")
            in {"title", "project_id", "priority", "status", "deadline"}
        ]

    return minimal


def build_delta(cursor):
    current_rows = cursor.execute(
        """
        SELECT
            tasks.sync_key AS sync_key,
            tasks.title AS title,
            tasks.deadline AS deadline,
            tasks.project_id AS project_id,
            tasks.student_id AS student_id,
            tasks.priority AS priority,
            tasks.status AS status,
            projects.name AS project_name,
            projects.type AS project_type
        FROM tasks
        LEFT JOIN projects ON tasks.project_id = projects.id
        WHERE tasks.archived=0 AND tasks.sync_key IS NOT NULL
        """
    ).fetchall()

    projects = _project_lookup(cursor)

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
        _enrich_project_fields(row_dict, projects)

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
            item = {
                "sync_key": sync_key,
                "title": old["title"],
                "deadline": old["deadline"],
                "project_id": old["project_id"],
                "student_id": old["student_id"],
                "priority": old["priority"],
                "status": old["status"],
            }
            _enrich_project_fields(item, projects)
            delta["deleted"].append(item)

    return delta


def build_gpt_memory_delta(cursor):
    raw_delta = build_delta(cursor)
    filtered = {"added": [], "updated": [], "deleted": []}
    omitted_counts = {"os_only": 0, "scrapbox_archive": 0}

    for kind in ("added", "updated", "deleted"):
        for item in raw_delta.get(kind, []):
            classification = _classify_gpt_memory_item(kind, item)
            if classification["include"]:
                filtered[kind].append(
                    _minimal_gpt_memory_item(kind, item, classification)
                )
            else:
                omitted_counts[classification["omit_bucket"]] += 1

    source_counts = {
        "added": len(raw_delta.get("added", [])),
        "updated": len(raw_delta.get("updated", [])),
        "deleted": len(raw_delta.get("deleted", [])),
    }
    included_counts = {
        "added": len(filtered["added"]),
        "updated": len(filtered["updated"]),
        "deleted": len(filtered["deleted"]),
    }
    snapshot_count = cursor.execute("SELECT COUNT(*) FROM sync_snapshot").fetchone()[0]

    return {
        "mode": "gpt_memory_minimal_delta",
        "policy": GPT_MEMORY_POLICY,
        "delta": filtered,
        "source_counts": source_counts,
        "included_counts": included_counts,
        "omitted_summary": {
            "os_only": {
                "count": omitted_counts["os_only"],
                "reason": "期限・担当・状態・学生個別ログは研究室運営OSに保持",
            },
            "scrapbox_archive": {
                "count": omitted_counts["scrapbox_archive"],
                "reason": "完了済み・ログ・参照はScrapbox退避後にGPTメモリから削除",
            },
        },
        "baseline": {
            "has_snapshot": snapshot_count > 0,
            "snapshot_items": snapshot_count,
        },
    }


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
    note_content = extract_imported_note_content(item)
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
        "scrapbox_url": item.get("scrapbox_url"),
        "note": note_content,
        "note_title": item.get("note_title"),
        "note_scrapbox_url": item.get("note_scrapbox_url"),
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
        "source_url",
        "scrapbox_url",
        "note_title",
        "note_scrapbox_url",
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

    incoming_note = incoming.get("note")
    if incoming_note:
        existing_note = merged.get("note")
        if existing_note and incoming_note not in existing_note:
            merged["note"] = f"{existing_note}\n\n{incoming_note}"
        else:
            merged["note"] = incoming_note

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
            priority, sync_key, source_type, source_updated_at, source_url,
            scrapbox_url, archived
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            normalized.get("scrapbox_url"),
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
    task_changes = [change for change in changes if change["field"] in SYNC_FIELDS]
    if not task_changes:
        return

    update_fields = [f"{change['field']}=?" for change in task_changes]
    values = [change["new"] for change in task_changes]
    values.append(task_id)
    cursor.execute(
        f"UPDATE tasks SET {', '.join(update_fields)} WHERE id=?",
        values,
    )
    if any(
        change["field"] == "status"
        and str(change["new"] or "").lower() == "done"
        for change in task_changes
    ):
        generate_completion_triggered_tasks(cursor, normalized)


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
        note_change = build_note_change(cursor, existing["id"], normalized)
        if note_change:
            changes.append(note_change)

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
                {
                    "task_id": existing["id"],
                    "sync_key": sync_key,
                    "source_type": source_type,
                    "normalized": normalized,
                }
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
        task_id = create_task_from_sync(cursor, normalized)
        upsert_imported_task_note(cursor, task_id, normalized)
        created += 1

    for item in diff["update"]:
        apply_task_update(cursor, item["task_id"], item["normalized"], item["changes"])
        upsert_imported_task_note(cursor, item["task_id"], item["normalized"])
        record_sync_changes(
            cursor,
            item["task_id"],
            item["sync_key"],
            item["source_type"],
            item["changes"],
        )
        updated_task_ids.append(item["task_id"])
        updated += 1

    for item in diff.get("unchanged", []):
        upsert_imported_task_note(cursor, item["task_id"], item.get("normalized", {}))

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
        WHERE archived=0 AND sync_key IS NOT NULL
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
                    "project_id": item["normalized"].get("project_id"),
                    "project_name": item["normalized"].get("project_name"),
                    "changes": item["changes"],
                }
                for item in diff["update"]
            ],
            "archive": diff["archive"],
        },
    }

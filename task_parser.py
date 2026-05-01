import json
import os
import re
from datetime import datetime, timedelta

from db import fetch_all_projects, fetch_inbox_project_id


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


# OpenAIは任意（未インストールでもOK）
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


# OpenAIは任意（APIキーがない環境でも動作させる）
api_key = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=api_key) if (api_key and OpenAI) else None


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


def student_names(students_data):
    return [s["name"] for s in students_data]


def guess_student_id(text, students_data):
    for s in students_data:
        name = s.get("name")
        student_id = s.get("student_id")
        if name and student_id and name in text:
            return student_id
    return None


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


def resolve_project_id_from_text(
    text, projects, default_project_id=None, students_data=None
):
    text = text or ""

    # 1. 学生優先
    for name in student_names(students_data or []):
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


def parse_task_with_chatgpt(text, default_title, default_deadline, default_priority):
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


def build_student_catalog_text(students_data):
    rows = []
    seen = set()
    for s in students_data:
        name = s.get("name")
        student_id = s.get("student_id")
        key = (name, student_id)
        if not name or key in seen:
            continue
        seen.add(key)
        if student_id:
            rows.append(f"- {name}: {student_id}")
    return "\n".join(rows)


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


def local_generate_sync_tasks(memo_text, students_data):
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
        student_id = guess_student_id(line, students_data)
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


def build_quick_task_payload(text, cursor, students_data):
    normalized_text = normalize_quotes(text)
    default_project_id = fetch_inbox_project_id(cursor)

    projects = fetch_all_projects(cursor)
    title = normalize_task_title(normalized_text)
    project_id = resolve_project_id_from_text(
        normalized_text, projects, default_project_id, students_data
    )
    student_id = guess_student_id(normalized_text, students_data)
    deadline = local_extract_deadline(normalized_text)
    priority = local_extract_priority(normalized_text)

    parsed = parse_task_with_chatgpt(normalized_text, title, deadline, priority)
    title = parsed["title"]
    deadline = parsed["deadline"]
    priority = parsed["priority"]
    project_hint = parsed["project_hint"]

    if project_hint:
        resolved = resolve_project_id_from_text(
            project_hint, projects, project_id, students_data
        )
        if resolved:
            project_id = resolved

    return {
        "title": title,
        "project_id": project_id,
        "student_id": student_id,
        "deadline": deadline,
        "priority": priority,
    }

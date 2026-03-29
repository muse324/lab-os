import sqlite3

conn = sqlite3.connect("db.sqlite3")
conn.row_factory = sqlite3.Row
c = conn.cursor()

# プロジェクト名 → id の辞書を作る
projects = c.execute("SELECT id, name FROM projects").fetchall()
project_map = {p["name"]: p["id"] for p in projects}

# タスク定義（プロジェクト名付き）
tasks = [
    # 🔴 締切あり
    (
        "業務実績報告・中期計画評価を提出する",
        "2026-04-10",
        "high",
        "大学業務（委員会・評価）",
    ),
    (
        "出張講義ナビの内容を確認する",
        "2026-04-06",
        "medium",
        "大学業務（委員会・評価）",
    ),
    (
        "JRビエラ展示のプロトタイプを作る",
        "2026-04-18",
        "high",
        "プロジェクト（JRビエラ展示）",
    ),
    # 🟠 直近
    ("研究科委員会に出席する", "2026-03-25", "high", "大学業務（委員会・評価）"),
    ("研究室配属対応を行う", "2026-03-27", "high", "研究室配属"),
    # 🟡 OS
    ("研究室運営OSを構築する", None, "high", "研究室運営OS"),
    ("Scrapbox運用設計を整理する", None, "medium", "研究室運営OS"),
    ("WikiCFP連携を設計する", None, "medium", "研究室運営OS"),
    # 🟡 研究
    ("論文イントロを修正する", None, "high", "研究（論文投稿）"),
    ("査読コメントに対応する", None, "high", "研究（論文投稿）"),
    ("phrase structure説明を整理する", None, "medium", "研究（論文投稿）"),
    # 🟢 研究テーマ
    (
        "音楽理論ビジュアライゼーション整理を進める",
        None,
        "medium",
        "研究（音楽理論ビジュアライゼーション）",
    ),
    (
        "AR楽譜インタフェース研究ロードマップを整理する",
        None,
        "medium",
        "研究（AR楽譜インタフェース）",
    ),
    # 🟢 プロジェクト
    ("かやのおとシステム設計を進める", None, "high", "プロジェクト（かやのおと）"),
    ("かやのおと音響処理を実装する", None, "medium", "プロジェクト（かやのおと）"),
    # 🟢 学生
    ("山口くんの研究テーマを整理する", None, "medium", "学生指導"),
    ("川添くんの研究準備を支援する", None, "medium", "学生指導"),
    # 🟢 その他
    ("UBM月報を作成する", None, "medium", "UBM活動"),
    ("助成情報を整理する", None, "low", "備忘録（助成・制度）"),
]

# タスク投入
for title, deadline, priority, project_name in tasks:
    project_id = project_map.get(project_name)

    if not project_id:
        print(f"⚠ プロジェクト未発見: {project_name}")
        continue

    c.execute(
        """
        INSERT INTO tasks
        (title, status, project_id, deadline, original_deadline, priority)
        VALUES (?, 'todo', ?, ?, ?, ?)
        """,
        (title, project_id, deadline, deadline, priority),
    )

conn.commit()
conn.close()

print("✅ タスク一括登録完了")

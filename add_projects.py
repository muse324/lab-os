import sqlite3

conn = sqlite3.connect("db.sqlite3")
c = conn.cursor()

projects = [
    ("授業（メディア情報学）", "lecture"),
    ("授業（音情報処理）", "lecture"),
    ("研究（論文投稿）", "research"),
    ("研究（音楽理論ビジュアライゼーション）", "research"),
    ("研究（AR楽譜インタフェース）", "research"),
    ("研究室運営OS", "research"),
    ("プロジェクト（かやのおと）", "project"),
    ("プロジェクト（JRビエラ展示）", "project"),
    ("大学業務（委員会・評価）", "admin"),
    ("学生指導", "education"),
    ("研究室配属", "education"),
    ("UBM活動", "activity"),
    ("備忘録（助成・制度）", "memo"),
]

for name, type_ in projects:
    c.execute(
        "INSERT INTO projects (name, type, status) VALUES (?, ?, 'active')",
        (name, type_),
    )

conn.commit()
conn.close()

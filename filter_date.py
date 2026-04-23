months = max(0, min(request.args.get("months", 3, type=int), 12))

today = date.today()
limit_date = today + relativedelta(months=months)

def normalize_deadline(t):
    d = t.get("deadline")
    if isinstance(d, str):
        return datetime.strptime(d, "%Y-%m-%d").date()
    return d

filtered = []
for t in tasks_todo:
    d = normalize_deadline(t)
    if d and today < d <= limit_date:
        t["deadline"] = d
        filtered.append(t)

tasks_todo = filtered
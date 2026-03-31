// Lab-OS Frontend Script (Modularized)

// ================================
// Student Name Linking Module
// ================================
const StudentLinkModule = (() => {
    function normalizeName(str) {
        return (str || "")
            .replace(/[ 　]/g, "")
            .replace(/さん|くん|君|氏/g, "")
            .toLowerCase();
    }

    function linkStudentNames(container) {
        const students = window.students || [];
        if (!students || students.length === 0 || !container) return;

        const nameMap = new Map();
        students.forEach((s) => {
            if (!s || !s.name || !s.student_id) return;
            const key = normalizeName(s.name);
            if (!nameMap.has(key)) {
                nameMap.set(key, { rawName: s.name, student_id: s.student_id });
            }
        });

        const escapedNames = Array.from(nameMap.values())
            .map((s) => s.rawName)
            .filter(Boolean)
            .sort((a, b) => b.length - a.length)
            .map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));

        if (escapedNames.length === 0) return;

        const combinedPattern = new RegExp(
            `(${escapedNames.join("|")})(?:さん|くん|君|氏)?`,
            "g"
        );

        function walk(node) {
            if (node.nodeType === Node.TEXT_NODE) {
                const text = node.nodeValue;
                if (!text || !combinedPattern.test(text)) {
                    combinedPattern.lastIndex = 0;
                    return;
                }
                combinedPattern.lastIndex = 0;

                const parent = node.parentNode;
                if (!parent) return;

                let lastIndex = 0;
                const fragments = [];
                let match;

                while ((match = combinedPattern.exec(text)) !== null) {
                    const matchedText = match[0];
                    const normalizedMatched = normalizeName(matchedText);
                    const matchedStudent = nameMap.get(normalizedMatched);
                    if (!matchedStudent) continue;

                    if (match.index > lastIndex) {
                        fragments.push(document.createTextNode(text.slice(lastIndex, match.index)));
                    }

                    const a = document.createElement("a");
                    a.href = `/student_log?name=${encodeURIComponent(matchedStudent.rawName)}`;
                    a.style.color = "inherit";
                    a.style.textDecoration = "underline";
                    a.textContent = matchedText;
                    a.className = "student-link";
                    a.dataset.studentName = matchedStudent.rawName;
                    a.dataset.studentId = String(matchedStudent.student_id);
                    fragments.push(a);

                    lastIndex = match.index + matchedText.length;
                }

                if (fragments.length === 0) {
                    combinedPattern.lastIndex = 0;
                    return;
                }

                if (lastIndex < text.length) {
                    fragments.push(document.createTextNode(text.slice(lastIndex)));
                }

                fragments.forEach((fragment) => parent.insertBefore(fragment, node));
                parent.removeChild(node);
                combinedPattern.lastIndex = 0;
                return;
            }

            if (node.nodeType === Node.ELEMENT_NODE) {
                const tagName = node.tagName ? node.tagName.toLowerCase() : "";
                if (["a", "button", "textarea", "input", "select", "option", "script", "style"].includes(tagName)) {
                    return;
                }
                Array.from(node.childNodes).forEach(walk);
            }
        }

        walk(container);
    }

    return { linkStudentNames };
})();


// ================================
// Student Summary Tooltip Module
// ================================
const StudentTooltipModule = (() => {
    let tooltip;
    let abortController = null;
    const cache = new Map();

    function ensureTooltip() {
        if (tooltip) return tooltip;
        tooltip = document.createElement("div");
        tooltip.id = "studentSummaryTooltip";
        tooltip.style.position = "fixed";
        tooltip.style.display = "none";
        tooltip.style.maxWidth = "320px";
        tooltip.style.padding = "8px 10px";
        tooltip.style.background = "#fffbe6";
        tooltip.style.border = "1px solid #cc9a00";
        tooltip.style.borderRadius = "8px";
        tooltip.style.boxShadow = "0 4px 12px rgba(0,0,0,0.15)";
        tooltip.style.fontSize = "0.9em";
        tooltip.style.lineHeight = "1.4";
        tooltip.style.zIndex = "9999";
        tooltip.style.pointerEvents = "none";
        document.body.appendChild(tooltip);
        return tooltip;
    }

    function positionTooltip(event) {
        const tip = ensureTooltip();
        tip.style.left = `${event.clientX + 14}px`;
        tip.style.top = `${event.clientY + 14}px`;
    }

    function render(summary) {
        const nextTasks = (summary.next_tasks || [])
            .map((task) => {
                const deadline = task.deadline ? ` [${task.deadline}]` : "";
                return `・${task.title}${deadline}`;
            })
            .join("<br>");

        return [
            `<strong>${summary.name}</strong>`,
            `未完了: ${summary.todo_count}件 / 完了: ${summary.done_count}件 / 期限切れ: ${summary.overdue_count}件`,
            nextTasks ? `直近タスク:<br>${nextTasks}` : "直近タスク: なし",
        ].join("<br>");
    }

    async function show(link, event) {
        const tip = ensureTooltip();
        const name = link.dataset.studentName;
        if (!name) return;

        positionTooltip(event);
        tip.style.display = "block";
        tip.innerHTML = "読込中...";

        if (cache.has(name)) {
            tip.innerHTML = render(cache.get(name));
            return;
        }

        if (abortController) {
            abortController.abort();
        }
        abortController = new AbortController();

        try {
            const res = await fetch(`/student_summary?name=${encodeURIComponent(name)}`, {
                signal: abortController.signal,
            });
            const data = await res.json();
            cache.set(name, data);
            tip.innerHTML = render(data);
        } catch {
            tip.innerHTML = "サマリ取得失敗";
        }
    }

    function hide() {
        const tip = ensureTooltip();
        tip.style.display = "none";
    }

    function bind(root = document) {
        root.querySelectorAll("a.student-link").forEach((link) => {
            if (link.dataset.hoverBound === "1") return;
            link.dataset.hoverBound = "1";

            link.addEventListener("mouseenter", (e) => show(link, e));
            link.addEventListener("mousemove", positionTooltip);
            link.addEventListener("mouseleave", hide);
            link.addEventListener("blur", hide);
        });
    }

    return { bind };
})();
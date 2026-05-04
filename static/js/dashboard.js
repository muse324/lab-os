(() => {
  const SyncModule = {};
  const ExportModule = {};
  const RenderModule = {};
  const ApiModule = {};
  const UtilsModule = {};

  window.lastSyncPreview = null;

  ApiModule.get = async function (url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(await res.text());
    return res;
  };

  ApiModule.postForm = async function (url, data) {
    const body = Object.entries(data)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join("&");
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!res.ok) throw new Error(await res.text());
    return res;
  };

  UtilsModule.fieldLabel = function (field) {
    const labels = {
      title: "タイトル (title)",
      deadline: "締切 (deadline)",
      project_id: "プロジェクトID (project_id)",
      student_id: "学生ID (student_id)",
      priority: "優先度 (priority)",
      status: "状態 (status)",
      archived: "アーカイブ (archived)",
      source_type: "同期元 (source_type)",
      source_updated_at: "元データ更新時刻 (source_updated_at)",
    };
    return labels[field] || field;
  };

  UtilsModule.formatValue = function (value) {
    if (value === null || value === undefined || value === "") {
      return "未設定";
    }
    if (value === 0 || value === "0") return "いいえ";
    if (value === 1 || value === "1") return "はい";
    if (value === "todo") return "未完了";
    if (value === "done") return "完了";
    if (value === "high") return "重要";
    if (value === "medium") return "通常";
    return String(value);
  };

  UtilsModule.escapeHTML = function (value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };

  ExportModule.copySnapshot = async function () {
    const project = document.getElementById("snapshotProject").value;
    if (!project) return;

    const url =
      project === "__all__"
        ? "/export_snapshot_scrapbox_all"
        : `/export_snapshot_scrapbox/${encodeURIComponent(project)}`;

    try {
      const res = await ApiModule.get(url);
      if (!res.ok) {
        throw new Error(await res.text());
      }
      const text = await res.text();

      await navigator.clipboard.writeText(text);

      alert("Snapshotをコピーしました");
    } catch (e) {
      alert("コピー失敗: " + e);
    }
  };

  function restoreScrollPosition() {
    const y = localStorage.getItem("scrollY");
    if (y !== null) {
      window.scrollTo(0, parseInt(y, 10));
      localStorage.removeItem("scrollY");
    }
  }

  function initStudentsData() {
    const el = document.getElementById("studentsData");
    if (!el) return;

    try {
      window.students = JSON.parse(el.textContent || "[]");
    } catch {
      window.students = [];
    }
  }

  function initStudentLinks() {
    if (!window.students || window.students.length === 0) {
      initStudentsData();
    }

    document
      .querySelectorAll(
        "[data-task-id], .task-title-scope, .note-title-scope, .history-title-scope",
      )
      .forEach((el) => {
        StudentLinkModule.linkStudentNames(el);
      });

    if (typeof StudentTooltipModule !== "undefined") {
      StudentTooltipModule.bind(document);
    }
  }

  function initMonthFilter() {
    const container = document.getElementById("monthFilter");
    if (!container) return;

    const now = new Date();
    const currentMonth = now.getMonth() + 1;
    const params = new URLSearchParams(window.location.search);
    const fallback = container.dataset.selectedMonths || "3";
    const selected = parseInt(params.get("months") ?? fallback, 10);
    const links = container.querySelectorAll("a[data-months]");

    links.forEach((link) => {
      const m = parseInt(link.dataset.months, 10);
      const displayMonth = ((currentMonth - 1 + m) % 12) + 1;

      let label;
      if (m === 0) label = `今月(${displayMonth}月)`;
      else if (m === 1) label = `来月(${displayMonth}月)`;
      else label = `〜${displayMonth}月`;

      if (m === selected) {
        const span = document.createElement("span");
        span.textContent = label;
        span.style.fontWeight = "bold";
        span.style.background = "#eee";
        span.style.padding = "2px 6px";
        span.style.borderRadius = "4px";
        link.replaceWith(span);
      } else {
        link.textContent = label;
      }
    });
  }

  function buildSummary(data, mode) {
    const lines = [];
    if (mode === "preview") {
      lines.push("差分確認結果");
      lines.push("------------------------------");
      lines.push(`新規追加: ${data.create}件`);
      lines.push(`更新: ${data.update}件`);
      lines.push(`アーカイブ: ${data.archive}件`);
      lines.push(`変更なし: ${data.unchanged}件`);
    } else {
      lines.push("同期実行結果");
      lines.push("------------------------------");
      lines.push(`新規追加: ${data.created}件`);
      lines.push(`更新: ${data.updated}件`);
      lines.push(`アーカイブ: ${data.archived}件`);
      lines.push(`変更なし: ${data.unchanged}件`);
    }
    return lines;
  }

  function buildScrapboxLink(t) {
    if (t.scrapbox_url) {
      return ` [<a href="${t.scrapbox_url}" target="_blank" rel="noopener noreferrer" style="font-size:0.85em;">Scrapbox</a>]`;
    }

    if (t.scrapbox_page) {
      const url =
        "https://scrapbox.io/musestudio/" + encodeURIComponent(t.scrapbox_page);
      return ` [<a href="${url}" target="_blank" rel="noopener noreferrer" style="font-size:0.85em;">Scrapbox</a>]`;
    }

    if (t.project || t.project_name) {
      const raw = t.project || t.project_name;
      const normalized = raw.replace(/\s+/g, "");
      const url =
        "https://scrapbox.io/musestudio/" + encodeURIComponent(normalized);
      return ` [<a href="${url}" target="_blank" rel="noopener noreferrer" style="font-size:0.85em;">Scrapbox</a>]`;
    }

    return "";
  }

  function getDiffItemKey(t) {
    return t.sync_key || t.task_id || t.id || t.title || "";
  }

  function buildDiffCheckbox(op, t, checkedDefault) {
    const key = getDiffItemKey(t);
    if (!key) return "";
    const checked = checkedDefault ? "checked" : "";
    return `<input type="checkbox" class="diff-select" data-op="${op}" data-key="${String(
      key,
    ).replace(/"/g, "&quot;")}" ${checked}> `;
  }

  function toggleAll(op, checked) {
    document.querySelectorAll(".diff-select").forEach((el) => {
      if (!op || el.dataset.op === op) {
        el.checked = checked;
      }
    });
  }

  function buildDiffTaskLine(t) {
    const title = UtilsModule.escapeHTML(t.title || "（無題）");
    const project = t.project || t.project_name || "";
    const deadline = t.deadline ? ` / ${t.deadline}` : "";
    const scrapboxLink = buildScrapboxLink(t);
    const projectLabel = UtilsModule.escapeHTML(project);
    const deadlineLabel = UtilsModule.escapeHTML(deadline);

    let studentLabel = "";
    if (t.student_name) {
      studentLabel = ` <span style="color:#0066cc;font-size:0.85em;">👤${UtilsModule.escapeHTML(t.student_name)}</span>`;
    } else if (t.student_id) {
      studentLabel = ` <span style="color:#0066cc;font-size:0.85em;">👤ID:${UtilsModule.escapeHTML(t.student_id)}</span>`;
    }

    let meta = "";
    if (project || deadline) {
      let linkedProject = projectLabel;
      if (project && t.project_id) {
        linkedProject = `<a href="/project/${encodeURIComponent(t.project_id)}" style="color:#666;">${projectLabel}</a>`;
      }
      meta = ` <span style="color:#666;font-size:0.85em;">${linkedProject}${deadlineLabel}</span>`;
    }

    return `${title}${meta}${studentLabel}${scrapboxLink}`;
  }

  function buildSyncResultHTML(data, mode) {
    let html = "";

    const summary = buildSummary(data, mode);
    html += "<div><strong>" + summary[0] + "</strong></div>";

    html += "<ul>";
    for (let i = 2; i < summary.length; i++) {
      html += "<li>" + summary[i] + "</li>";
    }
    html += "</ul>";

    if (mode === "preview" && data.details) {
      html += "<h4>差分詳細</h4>";
      html += `
                <div style="margin-bottom:8px;">
                    <button type="button" onclick="toggleAll('create', true)">新規 全選択</button>
                    <button type="button" onclick="toggleAll('update', true)">更新 全選択</button>
                    <button type="button" onclick="toggleAll('archive', true)">アーカイブ 全選択</button>
                    <button type="button" onclick="toggleAll(null, false)">全解除</button>
                </div>
            `;

      if (data.details.create?.length) {
        html +=
          "<div style='background:#e8f5e9;padding:8px;border-left:4px solid #4caf50;'>";
        html += "<strong>新規追加</strong><ul>";
        data.details.create.forEach((t) => {
          html += `<li>${buildDiffCheckbox(
            "create",
            t,
            true,
          )}${buildDiffTaskLine(t)}</li>`;
        });
        html += "</ul></div>";
      }

      if (data.details.update?.length) {
        html +=
          "<div style='background:#e3f2fd;padding:8px;border-left:4px solid #2196f3;margin-top:8px;'>";
        html += "<strong>更新</strong><ul>";
        data.details.update.forEach((t) => {
          let line = `${buildDiffCheckbox(
            "update",
            t,
            true,
          )}${buildDiffTaskLine(t)}`;

          if (t.changes && t.changes.length) {
            line += "<ul style='margin-top:4px;'>";
            t.changes.forEach((ch) => {
              const oldVal = UtilsModule.formatValue(ch.old);
              const newVal = UtilsModule.formatValue(ch.new);
              const label = UtilsModule.fieldLabel(ch.field);
              line += `<li style='font-size:0.85em;color:#333;'>${label}: ${oldVal} → <strong>${newVal}</strong></li>`;
            });
            line += "</ul>";
          }

          html += `<li>${line}</li>`;
        });
        html += "</ul></div>";
      }

      if (data.details.archive?.length) {
        html +=
          "<div style='background:#ffebee;padding:8px;border-left:4px solid #f44336;margin-top:8px;'>";
        html += "<strong>アーカイブ</strong><ul>";
        data.details.archive.forEach((t) => {
          let line = `${buildDiffCheckbox(
            "archive",
            t,
            false,
          )}${buildDiffTaskLine(t)}`;

          if (t.archive_reason) {
            line += `<div style='font-size:0.85em;color:#666;margin-top:2px;'>理由: ${t.archive_reason}</div>`;
          } else if (t.source_type) {
            line += `<div style='font-size:0.85em;color:#666;margin-top:2px;'>理由: ${t.source_type}由来</div>`;
          } else {
            line +=
              "<div style='font-size:0.85em;color:#666;margin-top:2px;'>理由: GPTメモリから消えた可能性</div>";
          }

          html += `<li>${line}</li>`;
        });
        html += "</ul></div>";
      }
    }

    if (data.errors?.length) {
      html += "<div style='color:red'><strong>エラー</strong><ul>";
      data.errors.forEach((err) => {
        html += "<li>" + err.reason + "</li>";
      });
      html += "</ul></div>";
    }

    return html;
  }

  RenderModule.renderSyncResult = function (data, mode) {
    const html = buildSyncResultHTML(data, mode);
    const el = document.getElementById("syncResult");
    el.innerHTML = html;
  };

  SyncModule.generateSyncJson = async function () {
    const memo = document.getElementById("memoInput").value;
    const res = await ApiModule.postForm("/generate_sync_json", { memo });
    const data = await res.json();

    if (data.error) {
      document.getElementById("syncResult").textContent =
        "JSON生成エラー: " + data.error;
      return;
    }

    document.getElementById("jsonInput").value = JSON.stringify(
      data.tasks,
      null,
      2,
    );
    document.getElementById("syncResult").textContent =
      `JSON生成完了: ${data.tasks.length}件（ローカル解析）`;
  };

  SyncModule.previewSync = async function () {
    const json = document.getElementById("jsonInput").value;
    const res = await ApiModule.postForm("/sync_preview", { json });
    const data = await res.json();
    window.lastSyncPreview = data;
    RenderModule.renderSyncResult(data, "preview");
  };

  SyncModule.applySelectedSync = async function () {
    const json = document.getElementById("jsonInput").value;
    const selected = { create: [], update: [], archive: [] };

    document.querySelectorAll(".diff-select:checked").forEach((el) => {
      const op = el.dataset.op;
      const key = el.dataset.key;
      if (selected[op]) selected[op].push(key);
    });

    const total =
      selected.create.length + selected.update.length + selected.archive.length;
    if (total === 0) {
      alert("同期する差分が選択されていません");
      return;
    }

    const res = await ApiModule.postForm("/sync_apply_selected", {
      json,
      selected: JSON.stringify(selected),
    });
    const data = await res.json();
    RenderModule.renderSyncResult(data, "apply");
    await RenderModule.refreshUpdatedTasks(data.updated_task_ids || []);
  };

  RenderModule.refreshUpdatedTasks = async function (taskIds) {
    if (!taskIds || taskIds.length === 0) {
      return;
    }

    const res = await ApiModule.get("/");
    const html = await res.text();
    const parser = new DOMParser();
    const doc = parser.parseFromString(html, "text/html");

    taskIds.forEach((taskId) => {
      const current = document.querySelector(`[data-task-id="${taskId}"]`);
      const fresh = doc.querySelector(`[data-task-id="${taskId}"]`);

      if (current && fresh) {
        current.replaceWith(fresh);
        StudentLinkModule.linkStudentNames(fresh);
        StudentTooltipModule.bind(fresh);
        RenderModule.markTaskUpdated(taskId, "update");
      }
    });
  };

  RenderModule.markTaskUpdated = function (taskId, type = "update") {
    const li = document.querySelector(`[data-task-id="${taskId}"]`);
    if (!li) return;

    let bg = "#fff7cc";
    let border = "#cc9a00";
    let label = "更新";

    if (type === "create") {
      bg = "#e8f5e9";
      border = "#2e7d32";
      label = "新規";
    } else if (type === "archive") {
      bg = "#ffebee";
      border = "#c62828";
      label = "アーカイブ";
    }

    li.style.backgroundColor = bg;
    li.style.transition = "background-color 0.3s ease";

    const oldBadge = li.querySelector(".updated-badge");
    if (oldBadge) {
      oldBadge.remove();
    }

    const badge = document.createElement("span");
    badge.className = "updated-badge";
    badge.textContent = label;
    badge.style.marginLeft = "8px";
    badge.style.padding = "2px 6px";
    badge.style.fontSize = "0.8em";
    badge.style.border = `1px solid ${border}`;
    badge.style.borderRadius = "8px";
    badge.style.backgroundColor = bg;

    li.appendChild(badge);
  };

  RenderModule.markCreatedTasks = function (taskIds) {
    taskIds.forEach((id) => RenderModule.markTaskUpdated(id, "create"));
  };

  RenderModule.markArchivedTasks = function (taskIds) {
    taskIds.forEach((id) => RenderModule.markTaskUpdated(id, "archive"));
  };

  SyncModule.applySync = async function () {
    const json = document.getElementById("jsonInput").value;
    const res = await ApiModule.postForm("/sync_apply", { json });
    const data = await res.json();
    RenderModule.renderSyncResult(data, "apply");
    await RenderModule.refreshUpdatedTasks(data.updated_task_ids || []);
  };

  SyncModule.importTasks = async function () {
    const json = document.getElementById("jsonInput").value;
    const form = document.createElement("form");
    form.method = "POST";
    form.action = "/import_tasks";

    const input = document.createElement("input");
    input.type = "hidden";
    input.name = "json";
    input.value = json;

    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
  };

  async function runDeploy(event) {
    event.preventDefault();

    const resultEl = document.getElementById("deployResult");
    const button = event.target.querySelector("button");

    button.disabled = true;
    resultEl.textContent = "実行中...";

    const start = Date.now();

    try {
      const res = await ApiModule.postForm("/deploy", {});
      const text = await res.text();

      let data;
      try {
        data = JSON.parse(text);
      } catch (e) {
        throw new Error("サーバエラー: " + text.slice(0, 100));
      }

      const elapsed = ((Date.now() - start) / 1000).toFixed(1);

      if (data.status === "success") {
        resultEl.textContent = `deployed! (${elapsed}s)\n${data.log || ""}`;
        resultEl.style.color = "green";
      } else {
        resultEl.textContent = `エラー (${elapsed}s): ${data.message}`;
        resultEl.style.color = "red";
      }
    } catch (e) {
      const elapsed = ((Date.now() - start) / 1000).toFixed(1);
      resultEl.textContent = `通信エラー (${elapsed}s): ${e}`;
      resultEl.style.color = "red";
    } finally {
      button.disabled = false;
    }
  }

  ExportModule.exportMemo = async function () {
    const res = await ApiModule.get("/export_tasks_as_memo");
    const data = await res.json();
    const prompt = buildFullMemoPrompt();

    document.getElementById("memoInput").value = prompt + "\n\n" + data.memo;
  };

  ExportModule.exportDelta = async function () {
    const res = await ApiModule.get("/export_delta_for_gpt");
    const data = await res.json();
    const prompt = buildDeltaPrompt();
    const body = formatDeltaForGPT(data.delta);

    document.getElementById("memoInput").value = prompt + "\n\n" + body;
  };

  ExportModule.exportDeltaJson = async function () {
    const res = await ApiModule.get("/export_delta_for_gpt");
    const data = await res.json();
    const prompt = buildDeltaJsonPrompt();
    const body = JSON.stringify(data.delta, null, 2);

    document.getElementById("memoInput").value = prompt + "\n\n" + body;
  };

  function buildFullMemoPrompt() {
    return [
      "以下は現在のタスク一覧です。【ChatGPTにはこのまま投げないように!】",
      "",
      "【目的】",
      "- タスクを分かりやすく整理する",
      "- 重複や曖昧な表現を必要に応じて整理する",
    ].join("\n");
  }

  function buildDeltaPrompt() {
    return [
      "あなたはタスク管理アシスタントです。",
      "以下の差分情報をもとに、既存のタスク一覧を更新してください。",
      "",
      "【ルール】",
      "- [追加] は新規タスクとして追加する",
      "- [更新] は既存タスクを更新する（タイトルが一致するもの）",
      "- [削除] は該当タスクを削除またはアーカイブ扱いにする",
      "",
      "出力は、更新後のタスク一覧を簡潔に整理した形式で提示してください。",
    ].join("\n");
  }

  function buildDeltaJsonPrompt() {
    return [
      "あなたはタスク管理アシスタントです。",
      "以下は変更差分のJSONです。",
      "sync_key を同一タスク識別子として扱い、既存タスク一覧を更新してください。",
      "",
      "【ルール】",
      "- added は新規追加",
      "- updated は既存タスク更新",
      "- deleted は削除またはアーカイブ扱い",
      "- updated.changes の field / old / new を優先して反映する",
      "- タイトルではなく sync_key を優先して照合する",
      "",
      "出力は、更新後のタスク一覧を簡潔に整理した形式で提示してください。",
    ].join("\n");
  }

  function formatDeltaForGPT(delta) {
    const lines = [];
    const added = delta.added || delta.create || [];
    const updated = delta.updated || delta.update || [];
    const deleted = delta.deleted || delta.archive || [];

    if (added.length) {
      lines.push("[追加]");
      added.forEach((t) => {
        const title = t.title || "（無題）";
        lines.push(`- ${title} [sync_key: ${t.sync_key || "unknown"}]`);
        lines.push(
          `    ${UtilsModule.fieldLabel(
            "deadline",
          )}: ${UtilsModule.formatValue(t.deadline)}`,
        );
        lines.push(
          `    ${UtilsModule.fieldLabel(
            "priority",
          )}: ${UtilsModule.formatValue(t.priority)}`,
        );
        lines.push(
          `    ${UtilsModule.fieldLabel(
            "status",
          )}: ${UtilsModule.formatValue(t.status)}`,
        );
        lines.push(
          `    ${UtilsModule.fieldLabel(
            "project_id",
          )}: ${UtilsModule.formatValue(t.project_id)}`,
        );
        lines.push(
          `    ${UtilsModule.fieldLabel(
            "student_id",
          )}: ${UtilsModule.formatValue(t.student_id)}`,
        );
      });
    }

    if (updated.length) {
      lines.push("\n[更新]");
      updated.forEach((t) => {
        const title = t.title || "（無題）";
        lines.push(`- ${title} [sync_key: ${t.sync_key || "unknown"}]`);

        if (t.changes && t.changes.length) {
          t.changes.forEach((change) => {
            lines.push(
              `    ${UtilsModule.fieldLabel(
                change.field,
              )}: ${UtilsModule.formatValue(
                change.old,
              )} → ${UtilsModule.formatValue(change.new)}`,
            );
          });
        }
      });
    }

    if (deleted.length) {
      lines.push("\n[削除]");
      deleted.forEach((t) => {
        const title = typeof t === "string" ? t : t.title || "（無題）";
        const syncKey = typeof t === "string" ? t : t.sync_key || "unknown";
        lines.push(`- ${title} [sync_key: ${syncKey}]`);
        if (typeof t !== "string") {
          lines.push(
            `    ${UtilsModule.fieldLabel(
              "deadline",
            )}: ${UtilsModule.formatValue(t.deadline)}`,
          );
          lines.push(
            `    ${UtilsModule.fieldLabel(
              "priority",
            )}: ${UtilsModule.formatValue(t.priority)}`,
          );
          lines.push(
            `    ${UtilsModule.fieldLabel(
              "status",
            )}: ${UtilsModule.formatValue(t.status)}`,
          );
          lines.push(
            `    ${UtilsModule.fieldLabel(
              "project_id",
            )}: ${UtilsModule.formatValue(t.project_id)}`,
          );
          lines.push(
            `    ${UtilsModule.fieldLabel(
              "student_id",
            )}: ${UtilsModule.formatValue(t.student_id)}`,
          );
        }
      });
    }

    return lines.join("\n");
  }

  function toggleInlineEdit(button) {
    const form = button.closest(".inline-edit-form");
    if (!form) return;
    const titleSpan = form.querySelector(".inline-edit-title");
    const titleInput = form.querySelector(".inline-edit-input");
    if (!titleInput || !titleSpan) return;
    const isEditing = titleInput.style.display !== "none";
    if (!isEditing) {
      titleSpan.style.display = "none";
      titleInput.style.display = "inline-block";
      button.textContent = "OK";
      const strike = form.querySelector("s");
      if (strike) {
        strike.style.display = "none";
      }
      titleInput.focus();
      titleInput.select();
    } else {
      form.submit();
    }
  }

  window.addEventListener("beforeunload", () => {
    localStorage.setItem("scrollY", window.scrollY);
  });

  window.addEventListener("load", () => {
    restoreScrollPosition();
    initStudentLinks();
  });

  document.addEventListener("DOMContentLoaded", () => {
    initStudentsData();
    initMonthFilter();
  });

  window.copySnapshot = ExportModule.copySnapshot;
  window.generateSyncJson = SyncModule.generateSyncJson;
  window.previewSync = SyncModule.previewSync;
  window.applySync = SyncModule.applySync;
  window.applySelectedSync = SyncModule.applySelectedSync;
  window.importTasks = SyncModule.importTasks;
  window.exportMemo = ExportModule.exportMemo;
  window.exportDelta = ExportModule.exportDelta;
  window.exportDeltaJson = ExportModule.exportDeltaJson;
  window.runDeploy = runDeploy;
  window.toggleAll = toggleAll;
  window.toggleInlineEdit = toggleInlineEdit;
})();

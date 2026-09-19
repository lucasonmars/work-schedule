const COLORS = ["#c2410c", "#b45309", "#4d7c0f", "#0f766e", "#1d4ed8", "#7c3aed", "#be185d", "#44403c"];
const STATUS_CYCLE = ["empty", "doing", "done", "blocked"];
const STATUS_LABEL = { empty: "未填", doing: "进行中", done: "已完成", blocked: "受阻" };

const REMEMBER_KEY = "jianjin_remember";

const state = {
  user: null,
  registerMode: false,
  registerModeType: "create",
  view: "board",
  board: null,
  summary: null,
  inbox: null,
  selectedWeek: null,
  visibleMonth: null,
  modal: null,
  agentApiKey: null,
};

function isAdmin() {
  return !!state.user?.is_admin && !isSuperAdmin();
}

function isSuperAdmin() {
  return !!state.user?.is_superadmin;
}

const $ = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "请求失败");
    err.code = data.code;
    err.payload = data;
    throw err;
  }
  return data;
}

function parseWeek(weekKey) {
  const [year, week] = weekKey.split("-W").map(Number);
  return { year, week };
}

function formatWeekKey({ year, week }) {
  return `${year}-W${String(week).padStart(2, "0")}`;
}

function shiftWeek(weekKey, delta) {
  const monday = mondayOf(weekKey);
  monday.setDate(monday.getDate() + delta * 7);
  return formatWeekKey(getISOWeek(monday));
}

function mondayOf(weekKey) {
  const { year, week } = parseWeek(weekKey);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const day = jan4.getUTCDay() || 7;
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - (day - 1) + (week - 1) * 7);
  return new Date(monday.getUTCFullYear(), monday.getUTCMonth(), monday.getUTCDate());
}

function getISOWeek(date) {
  const tmp = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const day = tmp.getUTCDay() || 7;
  tmp.setUTCDate(tmp.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((tmp - yearStart) / 86400000 + 1) / 7);
  return { year: tmp.getUTCFullYear(), week };
}

function weekLabel(weekKey) {
  const monday = mondayOf(weekKey);
  const sunday = new Date(monday);
  sunday.setDate(monday.getDate() + 6);
  const fmt = (d) => `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}`;
  const { week } = parseWeek(weekKey);
  return { title: `第 ${week} 周`, span: `${fmt(monday)} – ${fmt(sunday)}`, short: `W${String(week).padStart(2, "0")}` };
}

function weeksOfYear(year) {
  const weeks = [];
  for (let w = 1; w <= 53; w++) {
    const key = formatWeekKey({ year, week: w });
    const monday = mondayOf(key);
    const thursday = new Date(monday);
    thursday.setDate(monday.getDate() + 3);
    if (thursday.getFullYear() !== year) {
      if (w === 1) weeks.push(key);
      else break;
    } else {
      weeks.push(key);
    }
  }
  return weeks;
}

function boardWeeks(selectedWeek, currentWeek) {
  const nowYear = parseWeek(currentWeek).year;
  const selYear = parseWeek(selectedWeek).year;
  const startYear = Math.min(selYear, nowYear);
  const endYear = Math.max(nowYear + 1, selYear);
  const weeks = [];
  const seen = new Set();
  for (let y = startYear; y <= endYear; y++) {
    for (const w of weeksOfYear(y)) {
      if (!seen.has(w)) {
        seen.add(w);
        weeks.push(w);
      }
    }
  }
  return weeks;
}

function monthGroups(weeks) {
  const groups = [];
  for (const w of weeks) {
    const monday = mondayOf(w);
    const key = `${monday.getFullYear()}-${monday.getMonth()}`;
    const label = `${monday.getFullYear()}年${monday.getMonth() + 1}月`;
    if (!groups.length || groups[groups.length - 1].key !== key) groups.push({ key, label, weeks: [w] });
    else groups[groups.length - 1].weeks.push(w);
  }
  return groups;
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function shortTime(iso) {
  if (!iso) return "";
  const d = new Date(iso.replace(" ", "T"));
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function userByTag(tag) {
  const t = String(tag || "")
    .replace(/^@/, "")
    .toLowerCase();
  return (state.board?.users || []).find((u) => u.username.toLowerCase() === t || u.display_name.toLowerCase() === t);
}

function monthKeyOfWeek(weekKey) {
  const d = mondayOf(weekKey);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(monthKey) {
  if (!monthKey) return "";
  const [y, m] = monthKey.split("-");
  return `${y}年${Number(m)}月`;
}

function mentionChips(assignees, taskId) {
  return (assignees || [])
    .map((a) => {
      const u = userByTag(a);
      const name = u ? u.display_name : String(a).replace(/^@/, "");
      const taskAttr = taskId ? ` data-act="insert-at" data-task="${taskId}" data-name="${escapeHtml(name)}"` : "";
      return `<button type="button" class="at at-btn"${taskAttr}>@${escapeHtml(name)}</button>`;
    })
    .join("");
}

function peopleFromWork(task) {
  const names = [];
  const seen = new Set();
  for (const p of (state.board?.progress || []).filter((x) => x.task_id === task.id)) {
    for (const m of (p.requirement || "").match(/@([^\s@,，;；]+)/g) || []) {
      const tag = m.slice(1);
      const key = tag.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      names.push(tag);
    }
  }
  return names;
}

function memberTags() {
  const users = state.board?.users || [];
  if (!users.length) return `<p class="muted">还没有成员。管理员可在「管理」里开通帐号。</p>`;
  return `<div class="name-tags">${users
    .map((u) => `<button type="button" class="at at-btn" data-insert-at="${escapeHtml(u.display_name)}">@${escapeHtml(u.display_name)}</button>`)
    .join("")}</div>`;
}

function insertAtCursor(field, text) {
  if (!field) return;
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? field.value.length;
  const before = field.value.slice(0, start);
  const after = field.value.slice(end);
  const needSpace = before && !/\s$/.test(before);
  const token = `${needSpace ? " " : ""}${text}`.replace(/\s+$/, " ");
  field.value = before + token + after;
  const pos = (before + token).length;
  field.focus();
  field.setSelectionRange(pos, pos);
}

function insertMention(displayName) {
  const token = `@${displayName} `;
  const req = $("prog-req");
  const active = document.activeElement;
  if (active && (active.id === "prog-content" || active.id === "task-name" || active.id === "task-desc")) {
    insertAtCursor(active, token);
    return;
  }
  insertAtCursor(req || $("task-name") || $("task-desc"), token);
}

function withAuthorPrefix(text, displayName, previous = "") {
  const tag = `@${displayName}`;
  const t = (text || "").trim();
  if (!t || t === tag) return "";
  if (t.startsWith(tag)) return t;
  const prev = (previous || "").trim();
  if (prev && t.startsWith(prev)) {
    const added = t.slice(prev.length).trim();
    if (!added) return text;
    if (added.startsWith(tag)) return t;
    return `${prev}\n${tag} ${added}`;
  }
  return `${tag} ${t}`;
}

function bindMentionClicks() {
  $("modal-body")?.querySelectorAll("[data-insert-at]").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      insertMention(btn.dataset.insertAt);
      const cb = btn.parentElement?.querySelector("input[type=checkbox]");
      if (cb) cb.checked = true;
    });
  });
}

function showLogin() {
  $("login-view").classList.remove("hidden");
  $("app-view").classList.add("hidden");
}

function clearSuperAdminUi() {
  state.superadmin = null;
  const stats = $("sa-stats");
  const orgs = $("sa-orgs");
  const members = $("sa-members");
  const msg = $("sa-msg");
  const section = $("sa-members-section");
  if (stats) stats.innerHTML = "";
  if (orgs) orgs.innerHTML = "";
  if (members) members.innerHTML = "";
  if (msg) msg.textContent = "";
  if (section) section.classList.add("hidden");
  $("superadmin-view")?.classList.add("hidden");
  $("superadmin-tab")?.classList.add("hidden");
}

function resetAppViews() {
  state.view = "board";
  state.selectedWeek = null;
  state.board = null;
  state.summary = null;
  state.inbox = null;
  clearSuperAdminUi();
  $("board-view")?.classList.remove("hidden");
  $("summary-view")?.classList.add("hidden");
  $("sales-view")?.classList.add("hidden");
  $("admin-view")?.classList.add("hidden");
  $("agent-view")?.classList.add("hidden");
  $("week-nav")?.classList.remove("hidden");
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("active", t.dataset.view === "board");
  });
  $("admin-tab")?.classList.add("hidden");
  $("sales-tab")?.classList.add("hidden");
  $("agent-tab")?.classList.add("hidden");
  document.querySelectorAll(".tab[data-view='board'], .tab[data-view='summary']").forEach((tab) => {
    tab.classList.remove("hidden");
  });
}

function showApp() {
  $("login-view").classList.add("hidden");
  $("app-view").classList.remove("hidden");
  const org = state.user.organization_name ? ` · ${state.user.organization_name}` : "";
  $("whoami").textContent = isSuperAdmin() ? `${state.user.display_name} · 超管` : `${state.user.display_name}${org}`;
  $("admin-tab").classList.toggle("hidden", !isAdmin());
  $("sales-tab").classList.toggle("hidden", !isAdmin());
  $("agent-tab").classList.toggle("hidden", isSuperAdmin());
  $("superadmin-tab").classList.toggle("hidden", !isSuperAdmin());
  document.querySelectorAll(".tab[data-view='board'], .tab[data-view='summary']").forEach((tab) => {
    tab.classList.toggle("hidden", isSuperAdmin());
  });
  if (!isSuperAdmin()) {
    clearSuperAdminUi();
  }
}

function loadRememberedCredentials() {
  try {
    const raw = localStorage.getItem(REMEMBER_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (data.username) $("username").value = data.username;
    if (data.password) $("password").value = data.password;
    if (data.remember) $("remember-me").checked = true;
  } catch {
    /* ignore */
  }
}

function saveRememberedCredentials() {
  if (state.registerMode) return;
  if ($("remember-me").checked) {
    localStorage.setItem(
      REMEMBER_KEY,
      JSON.stringify({
        remember: true,
        username: $("username").value.trim(),
        password: $("password").value,
      }),
    );
  } else {
    localStorage.removeItem(REMEMBER_KEY);
  }
}

function togglePasswordVisibility() {
  const input = $("password");
  const btn = $("toggle-password");
  const visible = input.type === "text";
  input.type = visible ? "password" : "text";
  btn.textContent = visible ? "👁" : "🙈";
  btn.setAttribute("aria-label", visible ? "显示密码" : "隐藏密码");
  btn.title = visible ? "显示密码" : "隐藏密码";
}

function resetJoinModal() {
  $("join-modal-body").hidden = false;
  $("join-modal-actions").classList.remove("hidden");
  $("join-modal-success").classList.add("hidden");
  $("join-modal-done").classList.add("hidden");
  $("join-modal-confirm").disabled = false;
}

function showJoinModal(orgName) {
  resetJoinModal();
  $("join-modal-body").innerHTML = `「<strong>${escapeHtml(orgName)}</strong>」组织名称已存在，是否加入该组织？`;
  $("join-modal").classList.remove("hidden");
}

function hideJoinModal() {
  $("join-modal").classList.add("hidden");
  resetJoinModal();
}

function showJoinSuccessInModal() {
  $("join-modal-body").hidden = true;
  $("join-modal-actions").classList.add("hidden");
  $("join-modal-success").classList.remove("hidden");
  $("join-modal-done").classList.remove("hidden");
}

async function submitJoinRequest() {
  const err = $("auth-error");
  err.hidden = true;
  $("join-modal-confirm").disabled = true;
  try {
    await api("/api/register", {
      method: "POST",
      body: {
        username: $("username").value.trim(),
        password: $("password").value,
        display_name: $("display-name").value.trim(),
        organization_name: $("org-name").value.trim(),
        mode: "join",
      },
    });
    showJoinSuccessInModal();
  } catch (ex) {
    $("join-modal-confirm").disabled = false;
    hideJoinModal();
    err.hidden = false;
    err.textContent = ex.message;
    state.registerModeType = "create";
  }
}

function setAuthMode(register) {
  state.registerMode = register;
  state.registerModeType = "create";
  $("org-name-field").classList.toggle("hidden", !register);
  $("display-name-field").classList.toggle("hidden", !register);
  $("login-help-register").classList.toggle("hidden", !register);
  $("auth-lead").classList.toggle("hidden", register);
  $("org-name").required = register;
  $("remember-wrap").classList.toggle("hidden", register);
  $("auth-title").textContent = register ? "注册用户" : "登录工作区";
  if (!register) {
    $("auth-lead").textContent = "使用帐号密码登录，无需填写组织名";
  }
  $("auth-submit").textContent = register ? "注册并进入" : "进入工作区";
  $("switch-hint").textContent = register ? "已有帐号？" : "还没有帐号？";
  $("switch-mode").textContent = register ? "去登录" : "去注册";
  $("password").autocomplete = register ? "new-password" : "current-password";
}

async function boot() {
  const me = await api("/api/me");
  if (!me.user) {
    loadRememberedCredentials();
    showLogin();
    return;
  }
  state.user = me.user;
  showApp();
  if (isSuperAdmin()) {
    switchView("superadmin");
    return;
  }
  switchView("board");
}

async function loadBoard() {
  const board = await api("/api/board");
  state.board = board;
  if (!state.selectedWeek) state.selectedWeek = board.current_week;
  renderWeekNav();
  if (state.view === "board") {
    renderBoard();
    await loadInbox();
  } else {
    await loadSummary();
  }
}

async function loadInbox() {
  state.inbox = await api("/api/inbox");
  renderInbox();
}

function renderWeekNav() {
  const label = weekLabel(state.selectedWeek);
  $("week-title").textContent = label.title;
  $("week-span").textContent = label.span;
}

function scrollToWeek(weekKey) {
  const el = document.querySelector(`[data-week-col="${weekKey}"]`);
  if (el) el.scrollIntoView({ inline: "center", block: "nearest" });
}

function syncRowHeights() {
  const rows = $("board").querySelectorAll("tbody tr");
  rows.forEach((tr) => {
    tr.querySelectorAll(".sticky-left, .sticky-goal-m, .sticky-goal-y").forEach((cell) => {
      cell.style.height = "auto";
    });
  });
  rows.forEach((tr) => {
    const h = `${tr.offsetHeight}px`;
    tr.querySelectorAll(".sticky-left, .sticky-goal-m, .sticky-goal-y").forEach((cell) => {
      cell.style.height = h;
    });
  });
}

function detectVisibleMonth() {
  const scroller = $("board-scroll");
  if (!scroller) return null;
  const stickyLeft = scroller.querySelector("thead .sticky-left");
  const stickyRight = scroller.querySelector("thead .sticky-goal-m");
  const cutL = stickyLeft ? stickyLeft.getBoundingClientRect().right : scroller.getBoundingClientRect().left;
  const cutR = stickyRight ? stickyRight.getBoundingClientRect().left : scroller.getBoundingClientRect().right;
  const weeks = [...scroller.querySelectorAll("thead .week-head[data-week-col]")];
  let best = null;
  let bestOverlap = 0;
  for (const el of weeks) {
    const r = el.getBoundingClientRect();
    const overlap = Math.min(r.right, cutR) - Math.max(r.left, cutL);
    if (overlap > bestOverlap) {
      bestOverlap = overlap;
      best = el.dataset.weekCol;
    }
  }
  if (!best && weeks.length) best = weeks[0].dataset.weekCol;
  return best ? monthKeyOfWeek(best) : null;
}

function applyVisibleMonth(monthKey) {
  if (!monthKey || !state.board) return;
  state.visibleMonth = monthKey;
  const title = $("month-goal-title");
  if (title) title.innerHTML = `${monthLabel(monthKey)}<small>月度目标</small>`;
  const sub = $("month-goal-sub");
  if (sub) sub.textContent = monthLabel(monthKey);
  const yearSub = $("year-goal-sub");
  if (yearSub) yearSub.textContent = `${monthKey.slice(0, 4)}年`;
  document.querySelectorAll(".goal-cell.sticky-goal-m[data-id]").forEach((el) => {
    const task = state.board.tasks.find((t) => t.id === Number(el.dataset.id));
    if (!task) return;
    const text = (task.month_goals || {})[monthKey] || "";
    el.innerHTML = text ? escapeHtml(text) : '<span class="placeholder">填写本月目标</span>';
  });
}

function afterBoardLayout() {
  requestAnimationFrame(() => {
    scrollToWeek(state.selectedWeek || state.board.current_week);
    requestAnimationFrame(() => {
      syncRowHeights();
      applyVisibleMonth(detectVisibleMonth() || monthKeyOfWeek(state.selectedWeek || state.board.current_week));
    });
  });
}

function renderBoard() {
  const root = $("board");
  const { categories, tasks, progress, current_week } = state.board;
  if (!categories.length) {
    root.innerHTML = `<tbody><tr><td class="empty-board">还没有分类。点左上角「新增分类」，再在分类下加任务。</td></tr></tbody>`;
    return;
  }
  const weeks = boardWeeks(state.selectedWeek, current_week);
  const groups = monthGroups(weeks);
  const progressMap = new Map(progress.map((p) => [`${p.task_id}:${p.week_key}`, p]));

  const monthHeads = groups
    .map((g) => `<th class="month-head" colspan="${g.weeks.length}">${escapeHtml(g.label)}</th>`)
    .join("");
  const weekHeads = weeks
    .map((w) => {
      const lab = weekLabel(w);
      const y = parseWeek(w).year;
      const short = y === parseWeek(current_week).year ? lab.short : `${String(y).slice(2)}${lab.short}`;
      const cls = [w === current_week ? "is-today" : "", w === state.selectedWeek ? "is-selected" : ""].filter(Boolean).join(" ");
      return `<th class="week-head week-col ${cls}" data-week-col="${w}"><b>${short}</b><span>${lab.span}</span></th>`;
    })
    .join("");

  let body = "";
  for (const cat of categories) {
    const catTasks = tasks.filter((t) => t.category_id === cat.id);
    body += `<tr class="cat-tr">
      <th class="sticky-left cat-cell">
        <span class="cat-tag" style="background:${cat.color}"></span>
        <span data-act="edit-cat" data-id="${cat.id}">${escapeHtml(cat.name)}</span>
        <span class="cat-actions">
          <button class="icon-btn" data-act="add-task" data-id="${cat.id}">＋任务</button>
          <button class="icon-btn" data-act="edit-cat" data-id="${cat.id}">改</button>
          <button class="icon-btn" data-act="del-cat" data-id="${cat.id}">删除</button>
        </span>
      </th>
      <td class="cat-span" colspan="${weeks.length}"></td>
      <td class="sticky-goal-m cat-span"></td>
      <td class="sticky-goal-y cat-span"></td>
    </tr>`;
    for (const task of catTasks) {
      const weekCells = weeks
        .map((w) => {
          const cell = progressMap.get(`${task.id}:${w}`) || { requirement: "", content: "", status: "empty" };
          const st = cell.status || "empty";
          const future = w > current_week;
          const stClass = future ? "is-future" : st;
          const today = w === current_week ? " is-today" : "";
          return `<td class="progress-cell week-col ${stClass}${today}" data-act="edit-progress" data-task="${task.id}" data-week="${w}">
            <div class="triple">
              <div><em>任务</em><span class="${cell.requirement ? "" : "placeholder"}">${cell.requirement ? renderMentions(cell.requirement) : "点击填写"}</span></div>
              <div><em>完成</em><span class="${cell.content ? "" : "placeholder"}">${cell.content ? renderMentions(cell.content) : "—"}</span></div>
              <div><em>状态</em><button type="button" class="status-mark ${st}" data-act="cycle-status" data-task="${task.id}" data-week="${w}" data-status="${st}">${STATUS_LABEL[st]}</button></div>
            </div>
            ${weekMoneyHtml(cell)}
          </td>`;
        })
        .join("");
      body += `<tr>
        <th class="sticky-left task-name">
          <div class="task-name-inner">
            <div>
              <span class="name">${escapeHtml(task.name)}</span>
              <div class="people">${mentionChips(peopleFromWork(task), task.id)}</div>
              ${task.description ? `<span class="desc">${escapeHtml(task.description)}</span>` : ""}
            </div>
            <span class="task-actions">
              <button class="icon-btn" data-act="edit-task" data-id="${task.id}">改</button>
              <button class="icon-btn" data-act="del-task" data-id="${task.id}">删</button>
            </span>
          </div>
        </th>
        ${weekCells}
        <td class="sticky-goal-m goal-cell" data-act="edit-goals" data-id="${task.id}"><span class="placeholder">填写本月目标</span></td>
        <td class="sticky-goal-y goal-cell" data-act="edit-goals" data-id="${task.id}">${task.year_goal ? escapeHtml(task.year_goal) : '<span class="placeholder">填写年度目标</span>'}</td>
      </tr>`;
    }
  }

  root.innerHTML = `
    <thead>
      <tr class="month-row">
        <th class="sticky-left">分类 / 任务</th>
        ${monthHeads}
        <th class="sticky-goal-m month-goal-head" id="month-goal-title">月度目标</th>
        <th class="sticky-goal-y year-goal-head">年度目标</th>
      </tr>
      <tr class="week-row">
        <th class="sticky-left">任务</th>
        ${weekHeads}
        <th class="sticky-goal-m" id="month-goal-sub"></th>
        <th class="sticky-goal-y" id="year-goal-sub"></th>
      </tr>
    </thead>
    <tbody>${body}</tbody>`;
  afterBoardLayout();
}

function weekMoneyHtml(cell) {
  const confirmed = Number(cell?.confirmed_revenue) || 0;
  const collected = Number(cell?.collected_revenue) || 0;
  if (!confirmed && !collected) return "";
  const chips = [];
  if (confirmed) chips.push(`<span class="is-confirmed">确定 ${escapeHtml(formatMoney(confirmed))}</span>`);
  if (collected) chips.push(`<span class="is-collected">已回 ${escapeHtml(formatMoney(collected))}</span>`);
  return `<div class="week-money">${chips.join("")}</div>`;
}

function renderMentions(text) {
  return escapeHtml(text).replace(/@([^\s@,，;；]+)/g, '<span class="at">@$1</span>');
}

function renderInbox() {
  const data = state.inbox;
  if (!data) return;
  $("inbox-week").innerHTML = renderInboxList(data.this_week, "本周工作任务里还没有 @ 到你。");
  $("inbox-overdue").innerHTML = renderInboxList(data.overdue, "没有往期未完成或受阻事项。");
}

function renderInboxList(items, emptyText) {
  if (!items.length) return `<p class="muted">${emptyText}</p>`;
  return items
    .map((item) => {
      const snippet = item.snippet || "";
      return `<button type="button" class="inbox-item ${item.status}" data-act="jump" data-task="${item.task_id}" data-week="${item.week_key}">
        <strong>${escapeHtml(item.task_name)}</strong>
        ${snippet ? `<p>${renderMentions(snippet)}</p>` : `<p class="muted">本周工作任务里还没有 @ 到你的句子。</p>`}
      </button>`;
    })
    .join("");
}

function openModal({ title, body, onSave }) {
  state.modal = { onSave };
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = body;
  $("modal").classList.remove("hidden");
  const first = $("modal-body").querySelector("input, textarea, select");
  if (first) first.focus();
}

function closeModal() {
  state.modal = null;
  $("modal").classList.add("hidden");
}

function colorSwatches(selected) {
  return `<div class="swatches">${COLORS.map(
    (c) => `<button type="button" class="swatch${c === selected ? " selected" : ""}" data-color="${c}" style="background:${c}"></button>`
  ).join("")}</div><input type="hidden" id="cat-color" value="${selected}" />`;
}

function bindModalSwatches() {
  $("modal-body").querySelectorAll(".swatch").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("modal-body").querySelectorAll(".swatch").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      $("cat-color").value = btn.dataset.color;
    });
  });
}

function bindStatusPills(current) {
  const hidden = $("prog-status");
  $("modal-body").querySelectorAll(".status-pills button").forEach((btn) => {
    btn.classList.toggle("selected", btn.dataset.status === current);
    btn.addEventListener("click", () => {
      hidden.value = btn.dataset.status;
      $("modal-body").querySelectorAll(".status-pills button").forEach((b) => b.classList.toggle("selected", b === btn));
    });
  });
}

function addCategoryModal() {
  openModal({
    title: "新增分类",
    body: `
      <label>分类名称</label>
      <input id="cat-name" placeholder="例如：研发交付" />
      <label>标记颜色</label>
      ${colorSwatches(COLORS[0])}
    `,
    onSave: async () => {
      await api("/api/categories", { method: "POST", body: { name: $("cat-name").value.trim(), color: $("cat-color").value } });
      closeModal();
      await loadBoard();
    },
  });
  bindModalSwatches();
}

function editCategoryModal(cat) {
  openModal({
    title: "编辑分类标记",
    body: `
      <label>分类名称</label>
      <input id="cat-name" value="${escapeHtml(cat.name)}" />
      <label>标记颜色</label>
      ${colorSwatches(cat.color)}
    `,
    onSave: async () => {
      await api(`/api/categories/${cat.id}`, { method: "PUT", body: { name: $("cat-name").value.trim(), color: $("cat-color").value } });
      closeModal();
      await loadBoard();
    },
  });
  bindModalSwatches();
}

function addTaskModal(categoryId) {
  openModal({
    title: "新增任务",
    body: `
      <label>任务名称（一行一个任务）</label>
      <input id="task-name" placeholder="例如：接口开发" />
      <label>说明（可选）</label>
      <input id="task-desc" placeholder="一句话说明" />
      ${taskSalesFields()}
    `,
    onSave: async () => {
      const body = {
        category_id: categoryId,
        name: $("task-name").value.trim(),
        description: $("task-desc").value.trim(),
      };
      if (isAdmin()) {
        body.sales_enabled = $("task-sales")?.checked || false;
        body.expected_revenue = $("expected-revenue")?.value || 0;
      }
      await api("/api/tasks", { method: "POST", body });
      closeModal();
      await loadBoard();
    },
  });
  bindTaskSalesToggle();
}

function editTaskModal(task) {
  const options = state.board.categories
    .map((c) => `<option value="${c.id}" ${c.id === task.category_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`)
    .join("");
  openModal({
    title: "编辑任务",
    body: `
      <label>任务名称</label>
      <input id="task-name" value="${escapeHtml(task.name)}" />
      <label>说明</label>
      <input id="task-desc" value="${escapeHtml(task.description || "")}" />
      <label>所属分类</label>
      <select id="task-cat">${options}</select>
      ${taskSalesFields(task)}
    `,
    onSave: async () => {
      const body = {
        name: $("task-name").value.trim(),
        description: $("task-desc").value.trim(),
        category_id: Number($("task-cat").value),
      };
      if (isAdmin()) {
        body.sales_enabled = $("task-sales")?.checked || false;
        body.expected_revenue = $("expected-revenue")?.value || 0;
      }
      await api(`/api/tasks/${task.id}`, { method: "PUT", body });
      closeModal();
      await loadBoard();
    },
  });
  bindTaskSalesToggle();
}

function taskSalesFields(task = {}) {
  if (!isAdmin()) return "";
  const on = !!task.sales_enabled;
  const amount = task.expected_revenue || "";
  return `
    <label class="check-line"><input type="checkbox" id="task-sales" ${on ? "checked" : ""} /> 开启销售管理</label>
    <div id="task-sales-fields" class="sales-fields ${on ? "" : "hidden"}">
      <label>预计收入（元，50万请填 500000）</label>
      <input id="expected-revenue" type="number" min="0" step="1" value="${escapeHtml(amount)}" placeholder="例如 1000000" />
    </div>`;
}

function bindTaskSalesToggle() {
  const box = $("task-sales");
  const fields = $("task-sales-fields");
  if (!box || !fields) return;
  box.addEventListener("change", () => fields.classList.toggle("hidden", !box.checked));
}

function editGoalsModal(task) {
  const monthKey = state.visibleMonth || monthKeyOfWeek(state.board.current_week);
  const monthText = (task.month_goals || {})[monthKey] || "";
  openModal({
    title: `${task.name} · 目标`,
    body: `
      <label>${monthLabel(monthKey)}目标</label>
      <textarea id="month-goal">${escapeHtml(monthText)}</textarea>
      <label>${monthKey.slice(0, 4)}年年度目标</label>
      <textarea id="year-goal">${escapeHtml(task.year_goal || "")}</textarea>
    `,
    onSave: async () => {
      await api(`/api/tasks/${task.id}`, {
        method: "PUT",
        body: {
          month_key: monthKey,
          month_goal: $("month-goal").value,
          year_goal: $("year-goal").value,
        },
      });
      closeModal();
      await loadBoard();
    },
  });
}

function editProgressModal(task, weekKey, cell, insertName) {
  const status = cell?.status || "empty";
  const meTag = `@${state.user.display_name} `;
  const contentValue = cell?.content || "";
  let requirement = cell?.requirement || "";
  if (insertName) {
    const token = `@${insertName}`;
    requirement = requirement.includes(token) ? requirement : `${requirement}${requirement && !/\s$/.test(requirement) ? " " : ""}${token} `;
  }
  const salesOn = isAdmin() && (!!cell?.sales_enabled || Number(cell?.confirmed_revenue) > 0 || Number(cell?.collected_revenue) > 0);
  const salesFields = isAdmin()
    ? `
      <button type="button" id="toggle-sales" class="ghost">${salesOn ? "收起销售管理" : "开启销售管理"}</button>
      <div id="week-sales-fields" class="sales-fields ${salesOn ? "" : "hidden"}">
        <label>确定回款（元，50万请填 500000）</label>
        <input id="confirmed-revenue" type="number" min="0" step="1" value="${cell?.confirmed_revenue || ""}" placeholder="例如 500000" />
        <label>已回款（元）</label>
        <input id="collected-revenue" type="number" min="0" step="1" value="${cell?.collected_revenue || ""}" placeholder="例如 150000" />
      </div>`
    : "";
  openModal({
    title: `${task.name} · ${weekLabel(weekKey).title}`,
    body: `
      <label>工作任务（点击人名标签插入 @）</label>
      <textarea id="prog-req" placeholder="本周要做到什么">${escapeHtml(requirement)}</textarea>
      ${memberTags()}
      <label>完成内容（保存后自动加上 @${escapeHtml(state.user.display_name)}）</label>
      <textarea id="prog-content" placeholder="${meTag}实际完成了什么">${escapeHtml(contentValue)}</textarea>
      <label>状态标记</label>
      <input type="hidden" id="prog-status" value="${status}" />
      <div class="status-pills">
        <button type="button" class="empty" data-status="empty">未填</button>
        <button type="button" class="doing" data-status="doing">进行中</button>
        <button type="button" class="done" data-status="done">已完成</button>
        <button type="button" class="blocked" data-status="blocked">受阻</button>
      </div>
      ${salesFields}
      <p class="muted">${cell?.editor_name ? `上次由 ${escapeHtml(cell.editor_name)} 于 ${shortTime(cell.updated_at)} 编辑` : "保存后会记下是你改的。"}</p>
    `,
    onSave: async () => {
      const content = withAuthorPrefix($("prog-content").value, state.user.display_name, cell?.content || "");
      const body = {
        task_id: task.id,
        week_key: weekKey,
        requirement: $("prog-req").value,
        content,
        status: $("prog-status").value,
      };
      if (isAdmin() && $("week-sales-fields")) {
        body.sales_enabled = !$("week-sales-fields").classList.contains("hidden");
        body.confirmed_revenue = $("confirmed-revenue")?.value || 0;
        body.collected_revenue = $("collected-revenue")?.value || 0;
      }
      await api("/api/progress", { method: "PUT", body });
      closeModal();
      await loadBoard();
    },
  });
  bindStatusPills(status);
  bindMentionClicks();
  const toggleSales = $("toggle-sales");
  if (toggleSales) {
    toggleSales.addEventListener("click", () => {
      const box = $("week-sales-fields");
      const hide = !box.classList.contains("hidden");
      box.classList.toggle("hidden", hide);
      toggleSales.textContent = hide ? "开启销售管理" : "收起销售管理";
    });
  }
  if (insertName) {
    const req = $("prog-req");
    if (req) {
      req.focus();
      req.setSelectionRange(req.value.length, req.value.length);
    }
  }
}

async function cycleStatus(taskId, weekKey, current) {
  const next = STATUS_CYCLE[(STATUS_CYCLE.indexOf(current) + 1) % STATUS_CYCLE.length];
  const existing = state.board.progress.find((p) => p.task_id === taskId && p.week_key === weekKey);
  await api("/api/progress", {
    method: "PUT",
    body: {
      task_id: taskId,
      week_key: weekKey,
      requirement: existing?.requirement || "",
      content: existing?.content || "",
      status: next,
    },
  });
  await loadBoard();
}

async function loadSummary() {
  const data = await api(`/api/summary?week=${encodeURIComponent(state.selectedWeek)}`);
  state.summary = data;
  renderSummary();
}

function renderSummary() {
  const { categories, tasks, progress, notes, me, week_key, week_label } = state.summary;
  const byTask = new Map(progress.map((p) => [p.task_id, p]));
  const counts = { doing: 0, done: 0, blocked: 0, filled: 0 };
  for (const p of progress) {
    if (p.requirement || p.content || p.status !== "empty") counts.filled += 1;
    if (counts[p.status] !== undefined) counts[p.status] += 1;
  }
  $("digest").innerHTML = `
    <h2>本周任务汇总</h2>
    <p>${weekLabel(week_key).title}　${week_label}　${tasks.length} 个任务　已完成 ${counts.done}　进行中 ${counts.doing}　受阻 ${counts.blocked}</p>
  `;

  $("summary-tasks").innerHTML = categories
    .map((cat) => {
      const catTasks = tasks.filter((t) => t.category_id === cat.id);
      const rows = catTasks
        .map((task) => {
          const cell = byTask.get(task.id);
          const st = cell?.status || "empty";
          const empty = !cell || (!cell.requirement && !cell.content && st === "empty");
          return `<tr>
            <td>
              <div class="sum-name">${escapeHtml(task.name)}</div>
              <div class="sum-people">${mentionChips(task.assignees) || '<span class="muted">未指派</span>'}</div>
            </td>
            <td class="sum-status">${empty ? "—" : STATUS_LABEL[st]}</td>
            <td>${empty || !cell.requirement ? "—" : renderMentions(cell.requirement)}</td>
            <td>${empty || !cell.content ? "—" : renderMentions(cell.content)}</td>
            <td class="muted">${empty ? "" : cell.editor_name ? `${escapeHtml(cell.editor_name)} ${shortTime(cell.updated_at)}` : ""}</td>
          </tr>`;
        })
        .join("");
      return `<table class="sum-table">
        <thead>
          <tr><th colspan="5">${escapeHtml(cat.name)}</th></tr>
          <tr class="sum-cols"><th>任务</th><th>状态</th><th>工作任务</th><th>完成内容</th><th>编辑</th></tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>`;
    })
    .join("");

  const mine = notes.find((n) => n.user_id === me.id);
  $("my-note").value = mine?.content || "";
  $("note-status").textContent = mine?.updated_at ? `上次保存 ${shortTime(mine.updated_at)}` : "";
  const others = notes.filter((n) => n.user_id !== me.id && n.content.trim());
  $("team-notes").innerHTML =
    others.length === 0
      ? `<p class="muted">其他同事还没有写本周记录。</p>`
      : others
          .map((n) => `<div class="person-note"><h4>${escapeHtml(n.display_name)}</h4><p>${escapeHtml(n.content)}</p><p class="muted">${shortTime(n.updated_at)}</p></div>`)
          .join("");
}

function switchView(view) {
  if (isSuperAdmin()) {
    view = "superadmin";
  } else if (view === "superadmin") {
    view = "board";
  }
  if ((view === "sales" || view === "admin") && !isAdmin()) view = "board";
  if (view === "agent" && isSuperAdmin()) view = "superadmin";
  state.view = view;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  $("board-view").classList.toggle("hidden", view !== "board");
  $("summary-view").classList.toggle("hidden", view !== "summary");
  $("sales-view").classList.toggle("hidden", view !== "sales");
  $("admin-view").classList.toggle("hidden", view !== "admin");
  $("agent-view")?.classList.toggle("hidden", view !== "agent");
  $("superadmin-view").classList.toggle("hidden", view !== "superadmin" || !isSuperAdmin());
  $("week-nav").classList.toggle("hidden", view === "sales" || view === "admin" || view === "superadmin" || view === "agent");
  if (view === "summary") loadSummary();
  else if (view === "sales") loadSales();
  else if (view === "admin") loadAdmin();
  else if (view === "agent") loadAgentPanel();
  else if (view === "superadmin" && isSuperAdmin()) loadSuperAdmin();
  else if (view === "board") {
    if (state.board) {
      renderBoard();
      loadInbox();
    } else {
      loadBoard();
    }
  }
}

$("auth-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const err = $("auth-error");
  err.hidden = true;
  try {
    const body = {
      username: $("username").value.trim(),
      password: $("password").value,
      display_name: $("display-name").value.trim(),
    };
    if (state.registerMode) {
      body.organization_name = $("org-name").value.trim();
      body.mode = state.registerModeType;
      if (!body.organization_name) {
        err.hidden = false;
        err.textContent = "请填写组织名称";
        return;
      }
      const data = await api("/api/register", { method: "POST", body });
      if (data.pending) {
        resetJoinModal();
        $("join-modal").classList.remove("hidden");
        showJoinSuccessInModal();
        return;
      }
      state.user = data.user;
    } else {
      const data = await api("/api/login", { method: "POST", body });
      state.user = data.user;
      saveRememberedCredentials();
    }
    showApp();
    if (isSuperAdmin()) {
      switchView("superadmin");
      return;
    }
    switchView("board");
  } catch (ex) {
    if (ex.code === "org_exists" && state.registerMode && state.registerModeType === "create") {
      showJoinModal(ex.payload.organization_name);
      return;
    }
    err.hidden = false;
    err.textContent = ex.message;
  }
});

$("join-modal-cancel").addEventListener("click", hideJoinModal);
$("join-modal-close").addEventListener("click", () => {
  hideJoinModal();
  setAuthMode(false);
  state.registerModeType = "create";
});
$("join-modal-confirm").addEventListener("click", submitJoinRequest);
$("join-modal").addEventListener("click", (e) => {
  if (e.target === $("join-modal")) hideJoinModal();
});

$("toggle-password").addEventListener("click", togglePasswordVisibility);
$("remember-me").addEventListener("change", () => {
  if (!$("remember-me").checked) localStorage.removeItem(REMEMBER_KEY);
});

$("switch-mode").addEventListener("click", () => setAuthMode(!state.registerMode));
$("logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", { method: "POST" });
  } catch {
    /* still clear local session UI */
  }
  state.user = null;
  resetAppViews();
  loadRememberedCredentials();
  showLogin();
});
$("prev-week").addEventListener("click", async () => {
  state.selectedWeek = shiftWeek(state.selectedWeek, -1);
  renderWeekNav();
  if (state.view === "summary") await loadSummary();
  else {
    renderBoard();
    scrollToWeek(state.selectedWeek);
  }
});
$("next-week").addEventListener("click", async () => {
  state.selectedWeek = shiftWeek(state.selectedWeek, 1);
  renderWeekNav();
  if (state.view === "summary") await loadSummary();
  else {
    renderBoard();
    scrollToWeek(state.selectedWeek);
  }
});
$("this-week").addEventListener("click", async () => {
  state.selectedWeek = state.board.current_week;
  renderWeekNav();
  if (state.view === "summary") await loadSummary();
  else {
    renderBoard();
    scrollToWeek(state.selectedWeek);
  }
});
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
});
$("add-category").addEventListener("click", (e) => {
  e.preventDefault();
  addCategoryModal();
});
$("board-scroll").addEventListener("scroll", () => {
  if (state._scrollTick) return;
  state._scrollTick = true;
  requestAnimationFrame(() => {
    state._scrollTick = false;
    applyVisibleMonth(detectVisibleMonth() || state.visibleMonth);
  });
});
$("modal-cancel").addEventListener("click", closeModal);
$("modal").addEventListener("click", (e) => {
  if (e.target.id === "modal") closeModal();
});
$("modal-save").addEventListener("click", async () => {
  if (state.modal?.onSave) {
    try {
      await state.modal.onSave();
    } catch (ex) {
      alert(ex.message);
    }
  }
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && state.modal?.onSave) {
    state.modal.onSave().catch((ex) => alert(ex.message));
  }
});

$("board-scroll").addEventListener("click", async (e) => {
  const statusBtn = e.target.closest("[data-act='cycle-status']");
  if (statusBtn) {
    e.stopPropagation();
    await cycleStatus(Number(statusBtn.dataset.task), statusBtn.dataset.week, statusBtn.dataset.status);
    return;
  }
  const insertBtn = e.target.closest("[data-act='insert-at']");
  if (insertBtn) {
    e.stopPropagation();
    const t = state.board.tasks.find((x) => x.id === Number(insertBtn.dataset.task));
    if (!t) return;
    const week = state.selectedWeek || state.board.current_week;
    const cell = state.board.progress.find((p) => p.task_id === t.id && p.week_key === week);
    editProgressModal(t, week, cell, insertBtn.dataset.name);
    return;
  }
  const actEl = e.target.closest("[data-act]");
  if (!actEl) return;
  const { act, id, task, week } = actEl.dataset;
  if (act === "add-task") addTaskModal(Number(id));
  if (act === "edit-cat") editCategoryModal(state.board.categories.find((c) => c.id === Number(id)));
  if (act === "del-cat") {
    if (confirm("删除分类会同时删除其下任务和周进度，确定吗？")) {
      await api(`/api/categories/${id}`, { method: "DELETE" });
      await loadBoard();
    }
  }
  if (act === "edit-task") editTaskModal(state.board.tasks.find((t) => t.id === Number(id)));
  if (act === "del-task") {
    if (confirm("确定删除这个任务？")) {
      await api(`/api/tasks/${id}`, { method: "DELETE" });
      await loadBoard();
    }
  }
  if (act === "edit-goals") editGoalsModal(state.board.tasks.find((t) => t.id === Number(id)));
  if (act === "edit-progress") {
    const t = state.board.tasks.find((x) => x.id === Number(task));
    const cell = state.board.progress.find((p) => p.task_id === Number(task) && p.week_key === week);
    editProgressModal(t, week, cell);
  }
});

$("my-panel").addEventListener("click", async (e) => {
  const item = e.target.closest("[data-act='jump']");
  if (!item) return;
  state.selectedWeek = item.dataset.week;
  renderWeekNav();
  renderBoard();
  const t = state.board.tasks.find((x) => x.id === Number(item.dataset.task));
  const cell = state.board.progress.find((p) => p.task_id === t.id && p.week_key === item.dataset.week);
  if (t) editProgressModal(t, item.dataset.week, cell);
});

$("save-note").addEventListener("click", async () => {
  try {
    const saved = await api("/api/weekly-notes", {
      method: "PUT",
      body: { week_key: state.selectedWeek, content: $("my-note").value },
    });
    $("note-status").textContent = `已保存 ${shortTime(saved.updated_at)}`;
    await loadSummary();
  } catch (ex) {
    alert(ex.message);
  }
});

function formatMoney(n) {
  const x = Number(n) || 0;
  if (Math.abs(x) >= 10000) return `${(x / 10000).toFixed(1).replace(/\.0$/, "")}万`;
  return x.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
}

function formatMoneyShort(n) {
  const x = Number(n) || 0;
  if (!x) return "";
  if (Math.abs(x) >= 10000) return `${Number((x / 10000).toFixed(1))}万`;
  if (Math.abs(x) >= 1000) return `${Number((x / 1000).toFixed(1))}千`;
  return String(Math.round(x));
}

const LINE_SERIES = [
  { key: "expected", name: "预计收入", color: "#44403c" },
  { key: "confirmed", name: "确定收入", color: "#ca8a04" },
  { key: "collected", name: "已回款", color: "#15803d" },
];
const BAR_SERIES = [
  { key: "confirmed", name: "确定收入", color: "#ca8a04" },
  { key: "collected", name: "已回款", color: "#15803d" },
];

function salesLegend(series) {
  return `<div class="chart-legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join("")}</div>`;
}

function mountSalesChart(el, svg, series) {
  el.innerHTML = salesLegend(series) + `<div class="chart-frame">${svg}</div>`;
}

function keysMax(points, keys) {
  return Math.max(0, ...points.flatMap((p) => keys.map((k) => Number(p[k]) || 0)));
}

function chartScale(points, series) {
  const max = keysMax(
    points,
    series.map((s) => s.key)
  );
  return { max: max > 0 ? max : 1 };
}

function chartLayout(count) {
  const pad = { l: 58, r: 20, t: 28, b: 40 };
  return { w: Math.max(640, count * 42 + pad.l + pad.r), h: 280, pad };
}

function axisLabel(n) {
  return formatMoneyShort(n) || "0";
}

function chartGrid(scale, layout) {
  const { w, h, pad } = layout;
  const innerH = h - pad.t - pad.b;
  const yAt = (v) => pad.t + innerH - (Math.max(Number(v) || 0, 0) / scale.max) * innerH;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const grid = ticks
    .map((t) => {
      const val = scale.max * t;
      const y = yAt(val);
      const label = axisLabel(val);
      return `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y}" y2="${y}" stroke="#e7e0d4" /><text x="${pad.l - 6}" y="${y + 3}" font-size="10" fill="#6f675d" text-anchor="end">${label}</text>`;
    })
    .join("");
  return { grid, yAt };
}

function lineChartSvg(points, scale, series) {
  if (!points.length) return "";
  const layout = chartLayout(points.length);
  const { w, h, pad } = layout;
  const innerW = w - pad.l - pad.r;
  const { grid, yAt } = chartGrid(scale, layout);
  const xAt = (i) => pad.l + (points.length === 1 ? innerW / 2 : (i / (points.length - 1)) * innerW);
  const lines = series.map((s, si) => {
    const d = points.map((p, i) => `${xAt(i)},${yAt(p[s.key] || 0)}`).join(" ");
    const dots = points
      .map((p, i) => {
        const val = p[s.key] || 0;
        const prev = i ? points[i - 1][s.key] || 0 : 0;
        if (!val || val === prev) return "";
        const y = yAt(val);
        const lx = xAt(i) + (si - 1) * 8;
        return `<circle cx="${xAt(i)}" cy="${y}" r="3" fill="${s.color}" /><text x="${lx}" y="${y - 8}" font-size="10" font-weight="600" fill="${s.color}" text-anchor="middle">${formatMoneyShort(val)}</text>`;
      })
      .join("");
    return `<polyline fill="none" stroke="${s.color}" stroke-width="2" points="${d}" />${dots}`;
  }).join("");
  const step = points.length > 16 ? 2 : 1;
  const ticks = points
    .map((p, i) => {
      if (i % step && i !== points.length - 1) return "";
      return `<text x="${xAt(i)}" y="${h - 10}" font-size="10" fill="#6f675d" text-anchor="middle">${p.label || ""}</text>`;
    })
    .join("");
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" preserveAspectRatio="xMidYMid meet">${grid}${lines}${ticks}</svg>`;
}

function barChartSvg(points, scale, series) {
  if (!points.length) return "";
  const layout = chartLayout(points.length);
  const { w, h, pad } = layout;
  const { grid, yAt } = chartGrid(scale, layout);
  const n = series.length;
  const innerW = w - pad.l - pad.r;
  const groupW = innerW / points.length;
  const gap = 2;
  const barW = Math.max(6, (groupW - 10 - (n - 1) * gap) / n);
  const base = yAt(0);
  let bars = "";
  points.forEach((p, i) => {
    const groupX = pad.l + i * groupW + (groupW - (n * barW + (n - 1) * gap)) / 2;
    series.forEach((s, si) => {
      const val = p[s.key] || 0;
      const x = groupX + si * (barW + gap);
      const y = yAt(val);
      const bh = Math.max(val ? 3 : 0, base - y);
      bars += `<rect x="${x}" y="${y}" width="${barW}" height="${bh}" fill="${s.color}"><title>${s.name} ${formatMoney(val)}</title></rect>`;
      if (val) {
        bars += `<text x="${x + barW / 2}" y="${y - 4}" font-size="9" font-weight="600" fill="${s.color}" text-anchor="middle">${formatMoneyShort(val)}</text>`;
      }
    });
    bars += `<text x="${pad.l + i * groupW + groupW / 2}" y="${h - 10}" font-size="10" fill="#6f675d" text-anchor="middle">${p.label || ""}</text>`;
  });
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="100%" preserveAspectRatio="xMidYMid meet">${grid}${bars}</svg>`;
}

function salesStatsHtml(data) {
  const expected = data.projects.reduce((s, p) => s + (Number(p.expected) || 0), 0);
  const confirmed = data.projects.reduce((s, p) => s + (Number(p.confirmed) || 0), 0);
  const collected = data.projects.reduce((s, p) => s + (Number(p.collected) || 0), 0);
  const uncollected = Math.max(0, confirmed - collected);
  const cards = [
    { name: "预计收入", value: expected, cls: "is-expected" },
    { name: "确定收入", value: confirmed, cls: "is-confirmed" },
    { name: "已回款", value: collected, cls: "is-collected" },
    { name: "未回款", value: uncollected, cls: "is-uncollected" },
  ];
  return cards
    .map((c) => `<div class="sales-stat ${c.cls}"><span>${c.name}</span><strong>${formatMoney(c.value)}</strong></div>`)
    .join("");
}

function monthCellHtml(month) {
  const confirmed = Number(month?.confirmed) || 0;
  const collected = Number(month?.collected) || 0;
  if (!confirmed && !collected) return `<span class="mo-empty">—</span>`;
  return `<div class="mo-cell">
    <span class="mo-conf">${confirmed ? formatMoney(confirmed) : "—"}</span>
    <span class="mo-col">${collected ? formatMoney(collected) : "—"}</span>
  </div>`;
}

function salesTableHtml(data) {
  const months = data.months || [];
  const expectedSum = data.projects.reduce((s, p) => s + (Number(p.expected) || 0), 0);
  const confirmedSum = data.projects.reduce((s, p) => s + (Number(p.confirmed) || 0), 0);
  const collectedSum = data.projects.reduce((s, p) => s + (Number(p.collected) || 0), 0);
  const monthTotals = months.map((m, i) => {
    const slot = (data.monthly || [])[i] || {};
    return { confirmed: slot.confirmed || 0, collected: slot.collected || 0 };
  });
  const head = `<th>项目</th>${months.map((m) => `<th>${escapeHtml(m.label)}</th>`).join("")}<th>预计收入</th><th>确定回款</th><th>已回款</th>`;
  const rows = data.projects
    .map((p) => {
      const byMonth = p.by_month || [];
      return `<tr class="${p.planning ? "is-planning" : ""}">
        <th>${escapeHtml(p.name)}${p.planning ? `<span class="plan-tag">策划中</span>` : ""}</th>
        ${months.map((_, i) => `<td class="num">${monthCellHtml(byMonth[i])}</td>`).join("")}
        <td class="num">${formatMoney(p.expected)}</td>
        <td class="num mo-conf">${p.planning ? "—" : formatMoney(p.confirmed)}</td>
        <td class="num mo-col">${p.planning ? "—" : formatMoney(p.collected)}</td>
      </tr>`;
    })
    .join("");
  const foot = `<tr class="sales-total">
      <th>确定回款总计</th>
      ${monthTotals.map((m) => `<td class="num mo-conf">${m.confirmed ? formatMoney(m.confirmed) : "—"}</td>`).join("")}
      <td class="num">${formatMoney(expectedSum)}</td>
      <td class="num mo-conf">${formatMoney(confirmedSum)}</td>
      <td class="num"></td>
    </tr>
    <tr class="sales-total">
      <th>已回款总计</th>
      ${monthTotals.map((m) => `<td class="num mo-col">${m.collected ? formatMoney(m.collected) : "—"}</td>`).join("")}
      <td class="num"></td>
      <td class="num"></td>
      <td class="num mo-col">${formatMoney(collectedSum)}</td>
    </tr>`;
  return `<div class="sales-table-wrap"><table class="sum-table sales-table">
    <thead><tr class="sum-cols">${head}</tr></thead>
    <tbody>${rows}</tbody>
    <tfoot>${foot}</tfoot>
  </table>
  <p class="muted">月度格内上行是确定回款，下行是已回款。灰色行为只有预计收入、尚未确定回款的策划中项目。</p>
  </div>`;
}

async function loadSales() {
  const data = await api("/api/sales");
  const linePts = data.monthly_cumulative || [];
  const barPts = data.monthly || [];
  const lineScale = chartScale(linePts, LINE_SERIES);
  const barScale = chartScale(barPts, BAR_SERIES);
  const yearLine = $("sales-year-line");
  if (yearLine) {
    const until = data.end_label || "";
    yearLine.textContent = `时间轴至 ${until}　${data.projects.length} 个项目`;
  }
  const stats = $("sales-stats");
  if (stats) stats.innerHTML = salesStatsHtml(data);
  const lineBox = $("sales-line");
  const barBox = $("sales-bars");
  if (lineBox) mountSalesChart(lineBox, lineChartSvg(linePts, lineScale, LINE_SERIES), LINE_SERIES);
  if (barBox) mountSalesChart(barBox, barChartSvg(barPts, barScale, BAR_SERIES), BAR_SERIES);
  const tableBox = $("sales-projects");
  if (!tableBox) return;
  if (!data.projects.length) {
    tableBox.innerHTML = `<p class="muted">还没有销售数据。在任务中开启销售管理并填写预计收入。</p>`;
  } else {
    tableBox.innerHTML = salesTableHtml(data);
  }
}

async function loadAdmin() {
  if ($("admin-org-name")) {
    $("admin-org-name").value = state.user?.organization_name || "";
  }
  const [usersData, joinData] = await Promise.all([api("/api/admin/users"), api("/api/admin/join-requests")]);
  const joinSection = $("join-requests-section");
  const joinTable = $("join-requests");
  if (joinData.requests.length) {
    joinSection.classList.remove("hidden");
    joinTable.innerHTML = `<thead><tr class="sum-cols"><th>帐号</th><th>显示名</th><th>申请时间</th><th>操作</th></tr></thead>
      <tbody>${joinData.requests
        .map(
          (r) => `<tr data-req="${r.id}">
        <td>${escapeHtml(r.username)}</td>
        <td>${escapeHtml(r.display_name)}</td>
        <td class="muted">${shortTime(r.created_at)}</td>
        <td><div class="admin-actions">
          <button type="button" data-act="approve-join">同意</button>
          <button type="button" class="danger" data-act="reject-join">拒绝</button>
        </div></td>
      </tr>`
        )
        .join("")}</tbody>`;
  } else {
    joinSection.classList.add("hidden");
    joinTable.innerHTML = "";
  }
  $("admin-users").innerHTML = `<thead><tr class="sum-cols"><th>帐号</th><th>显示名</th><th>状态</th><th>Agent Key</th><th>角色</th><th>新密码</th><th>操作</th></tr></thead>
    <tbody>${usersData.users
      .map(
        (u) => `<tr data-uid="${u.id}" data-username="${escapeHtml(u.username)}" class="${u.status === "pending" ? "user-status-pending" : ""}">
      <td>${escapeHtml(u.username)}</td>
      <td><input value="${escapeHtml(u.display_name)}" data-field="display_name" ${u.status === "pending" ? "disabled" : ""} /></td>
      <td>${u.status === "pending" ? '<span class="join-status-pending">待审批</span>' : "正常"}</td>
      <td class="muted">${u.has_api_key ? escapeHtml(u.api_key_prefix || "已生成") : "未生成"}</td>
      <td><label class="check-line"><input type="checkbox" data-field="is_admin" ${u.is_admin ? "checked" : ""} ${u.username === "admin" || u.status === "pending" ? "disabled" : ""} /> 管理员</label></td>
      <td><input class="pw-input" type="password" data-field="password" placeholder="填写后点重置密码" autocomplete="new-password" ${u.status === "pending" ? "disabled" : ""} /></td>
      <td><div class="admin-actions">
        <button type="button" data-act="save-user" ${u.status === "pending" ? "disabled" : ""}>保存</button>
        <button type="button" data-act="reset-password" ${u.status === "pending" ? "disabled" : ""}>重置密码</button>
        <button type="button" class="danger" data-act="delete-user" ${u.status === "pending" ? "disabled" : ""}>删除</button>
      </div></td>
    </tr>`
      )
      .join("")}</tbody>`;
}

function agentBaseUrl() {
  return `${window.location.origin}/api/agent`;
}

function buildAgentPrompt() {
  const me = state.user || {};
  const week = state.board?.current_week || state.selectedWeek || "";
  const base = agentBaseUrl();
  const host = window.location.host;
  return `你是「简进」助手。用下面 API 查/改我的周任务。先向我要 API Key，本消息不含 Key。

身份：${me.organization_name || ""} / ${me.display_name || ""}（${me.username || ""}）
周次默认：${week || "接口返回的 current_week"}
Base：${base}
鉴权：每个请求 Header → Authorization: Bearer <API_KEY>
（OpenClaw 可用 $JIANJIN_API_KEY，并绑定主机 ${host}）

## 只允许这两个读接口
GET ${base}/week?week=${week || "YYYY-Www"}
GET ${base}/my-tasks?week=${week || "YYYY-Www"}

## 唯一写接口（禁止探测其它 URL）
PUT ${base}/progress
Content-Type: application/json
{
  "task_id": 29,
  "week_key": "${week || "YYYY-Www"}",
  "content": "已完成",
  "status": "done"
}
status：empty|doing|done|blocked
不要调用 /api/tasks、/api/agent/task/:id 等。每条任务的 how_to_update 已给出正确写法。

## 查询后怎么回复我（简练，不要解读）
已完成：任务名（#id）
进行中：任务名（#id）— 一句进度
待做：任务名（#id）
受阻：任务名（#id）— 一句原因

我说「某某做完了」→ 直接按该任务 how_to_update 发 PUT，把 status 设为 done，然后用上面格式回我结果。`;
}

function renderAgentPrompt() {
  const box = $("agent-prompt");
  if (box) box.value = buildAgentPrompt();
}

async function loadAgentPanel() {
  const status = $("agent-key-status");
  const revoke = $("agent-revoke-key");
  const copyKey = $("agent-copy-key");
  const msg = $("agent-msg");
  const once = $("agent-key-once");
  if (once) once.classList.add("hidden");
  try {
    const data = await api("/api/me/api-key");
    if (data.has_api_key) {
      status.textContent = `已生成（前缀 ${data.api_key_prefix || "jj_…"}）。完整 Key 仅在生成时显示；Agent 向你要时再复制发给它。`;
      revoke?.classList.remove("hidden");
      if (state.agentApiKey) copyKey?.classList.remove("hidden");
      else copyKey?.classList.add("hidden");
    } else {
      status.textContent = "等 Agent 向你要 Key 时，再点「生成 / 重置 Key」。";
      revoke?.classList.add("hidden");
      copyKey?.classList.add("hidden");
      state.agentApiKey = null;
    }
    if (msg) msg.textContent = "";
  } catch (ex) {
    if (status) status.textContent = ex.message;
  }
  renderAgentPrompt();
}

$("agent-gen-key")?.addEventListener("click", async () => {
  if (!confirm("生成新 Key 会使旧 Key 立即失效，确定吗？")) return;
  const msg = $("agent-msg");
  const once = $("agent-key-once");
  try {
    const data = await api("/api/me/api-key", { method: "POST" });
    state.agentApiKey = data.api_key;
    once.classList.remove("hidden");
    once.innerHTML = `新 Key（复制后发给 Agent）：<br /><code>${escapeHtml(data.api_key)}</code>`;
    msg.textContent = "Key 已生成。复制发给 Agent 即可（对话或密钥库均可）。";
    $("agent-key-status").textContent = `已生成（前缀 ${data.api_key_prefix}）`;
    $("agent-revoke-key")?.classList.remove("hidden");
    $("agent-copy-key")?.classList.remove("hidden");
    renderAgentPrompt();
  } catch (ex) {
    msg.textContent = ex.message;
  }
});

$("agent-copy-key")?.addEventListener("click", async () => {
  if (!state.agentApiKey) {
    $("agent-msg").textContent = "当前页没有可复制的完整 Key。请重新生成一次。";
    return;
  }
  try {
    await navigator.clipboard.writeText(state.agentApiKey);
    $("agent-msg").textContent = "Key 已复制，发给正在向你要 Key 的 Agent。";
  } catch {
    $("agent-msg").textContent = "复制失败，请手动选中上方黄色框中的 Key。";
  }
});

$("agent-revoke-key")?.addEventListener("click", async () => {
  if (!confirm("作废后 Agent 将无法再访问，确定吗？")) return;
  try {
    await api("/api/me/api-key", { method: "DELETE" });
    state.agentApiKey = null;
    $("agent-key-once")?.classList.add("hidden");
    await loadAgentPanel();
    $("agent-msg").textContent = "已作废";
  } catch (ex) {
    $("agent-msg").textContent = ex.message;
  }
});

$("agent-copy-prompt")?.addEventListener("click", async () => {
  renderAgentPrompt();
  const text = $("agent-prompt")?.value || "";
  try {
    await navigator.clipboard.writeText(text);
    $("agent-msg").textContent = "提示词已复制。先发给 Agent；它要 Key 时再生成并发送。";
  } catch {
    $("agent-prompt")?.select();
    $("agent-msg").textContent = "请手动全选复制提示词";
  }
});

$("join-requests").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const tr = btn.closest("tr");
  const msg = $("admin-msg");
  const act = btn.dataset.act;
  try {
    if (act === "approve-join") {
      await api(`/api/admin/join-requests/${tr.dataset.req}/approve`, { method: "POST" });
      msg.textContent = "已同意加入";
    } else if (act === "reject-join") {
      if (!confirm("确定拒绝该加入申请？")) return;
      await api(`/api/admin/join-requests/${tr.dataset.req}/reject`, { method: "POST" });
      msg.textContent = "已拒绝";
    } else {
      return;
    }
    await loadAdmin();
  } catch (ex) {
    msg.textContent = ex.message;
  }
});

$("admin-org-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("admin-msg");
  try {
    const data = await api("/api/admin/organization", {
      method: "PUT",
      body: { name: $("admin-org-name").value.trim() },
    });
    state.user.organization_name = data.name;
    showApp();
    msg.textContent = "组织名称已更新";
  } catch (ex) {
    msg.textContent = ex.message;
  }
});

$("admin-add-user").addEventListener("submit", async (e) => {
  e.preventDefault();
  const msg = $("admin-msg");
  try {
    await api("/api/admin/users", {
      method: "POST",
      body: {
        username: $("new-username").value.trim(),
        display_name: $("new-display-name").value.trim(),
        password: $("new-password").value,
        is_admin: $("new-is-admin").checked,
      },
    });
    $("admin-add-user").reset();
    msg.textContent = "已开通";
    await loadAdmin();
  } catch (ex) {
    msg.textContent = ex.message;
  }
});

$("admin-users").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const tr = btn.closest("tr");
  const act = btn.dataset.act;
  const msg = $("admin-msg");
  try {
    if (act === "save-user") {
      await api(`/api/admin/users/${tr.dataset.uid}`, {
        method: "PUT",
        body: {
          display_name: tr.querySelector("[data-field='display_name']").value.trim(),
          is_admin: tr.querySelector("[data-field='is_admin']").checked,
        },
      });
      msg.textContent = "已保存";
    } else if (act === "reset-password") {
      const pw = tr.querySelector("[data-field='password']").value;
      if (!pw) {
        msg.textContent = "请先填写新密码，再点「重置密码」";
        return;
      }
      await api(`/api/admin/users/${tr.dataset.uid}`, {
        method: "PUT",
        body: { password: pw },
      });
      msg.textContent = `已重置 ${tr.dataset.username} 的密码`;
    } else if (act === "delete-user") {
      if (!confirm(`确定删除帐号「${tr.dataset.username}」？此操作不可恢复。`)) return;
      await api(`/api/admin/users/${tr.dataset.uid}`, { method: "DELETE" });
      msg.textContent = `已删除 ${tr.dataset.username}`;
    } else {
      return;
    }
    await loadAdmin();
  } catch (ex) {
    msg.textContent = ex.message;
  }
});

async function loadSuperAdmin() {
  if (!isSuperAdmin()) {
    clearSuperAdminUi();
    switchView("board");
    return;
  }
  const data = await api("/api/superadmin/overview");
  state.superadmin = data;
  const s = data.stats;
  $("sa-stats").innerHTML = [
    ["组织", s.organizations],
    ["已停用", s.suspended_organizations || 0],
    ["帐号", s.users],
    ["已激活", s.active_users],
    ["待审批", s.pending_users],
    ["任务", s.tasks],
  ]
    .map(([label, value]) => `<div class="sales-stat"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  if (!data.organizations.length) {
    $("sa-orgs").innerHTML = `<tbody><tr><td class="muted">暂无组织</td></tr></tbody>`;
    $("sa-members-section").classList.add("hidden");
    return;
  }
  $("sa-orgs").innerHTML = `<thead><tr class="sum-cols"><th>组织名称</th><th>状态</th><th>创建时间</th><th>人数</th><th>待审批</th><th>管理员</th><th>任务</th><th>操作</th></tr></thead>
    <tbody>${data.organizations
      .map((o) => {
        const suspended = o.status === "suspended";
        return `<tr data-org="${o.id}" class="${suspended ? "user-status-pending" : ""}">
      <td><button type="button" class="link-btn" data-act="view-org">${escapeHtml(o.name)}</button></td>
      <td>${suspended ? '<span class="org-status-suspended">已停用</span>' : '<span class="org-status-active">正常</span>'}</td>
      <td class="muted">${shortTime(o.created_at)}</td>
      <td>${o.active_count}/${o.user_count}</td>
      <td>${o.pending_count}</td>
      <td>${o.admin_count}</td>
      <td>${o.task_count}</td>
      <td><div class="admin-actions">
        <button type="button" data-act="toggle-org">${suspended ? "恢复" : "停用"}</button>
        <button type="button" class="danger" data-act="delete-org">删除</button>
      </div></td>
    </tr>`;
      })
      .join("")}</tbody>`;
}

function renderSuperAdminMembers(orgId) {
  const org = (state.superadmin?.organizations || []).find((o) => String(o.id) === String(orgId));
  const section = $("sa-members-section");
  const table = $("sa-members");
  if (!org) {
    section.classList.add("hidden");
    return;
  }
  $("sa-members-title").textContent = `「${org.name}」成员${org.status === "suspended" ? "（组织已停用）" : ""}`;
  section.classList.remove("hidden");
  if (!org.users.length) {
    table.innerHTML = `<tbody><tr><td class="muted">该组织暂无成员</td></tr></tbody>`;
    return;
  }
  table.innerHTML = `<thead><tr class="sum-cols"><th>帐号</th><th>显示名</th><th>状态</th><th>角色</th><th>注册时间</th><th>重置管理员密码</th></tr></thead>
    <tbody>${org.users
      .map(
        (u) => `<tr data-uid="${u.id}" data-username="${escapeHtml(u.username)}">
      <td>${escapeHtml(u.username)}</td>
      <td>${escapeHtml(u.display_name)}</td>
      <td>${u.status === "pending" ? '<span class="join-status-pending">待审批</span>' : "正常"}</td>
      <td>${u.is_admin ? "管理员" : "成员"}</td>
      <td class="muted">${shortTime(u.created_at)}</td>
      <td>${
        u.is_admin
          ? `<div class="admin-actions">
              <input class="pw-input" type="password" data-field="password" placeholder="新密码" autocomplete="new-password" />
              <button type="button" data-act="reset-admin-pw">重置</button>
            </div>`
          : '<span class="muted">—</span>'
      }</td>
    </tr>`
      )
      .join("")}</tbody>`;
}

$("sa-orgs").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const tr = btn.closest("tr");
  const orgId = tr.dataset.org;
  const msg = $("sa-msg");
  const org = (state.superadmin?.organizations || []).find((o) => String(o.id) === String(orgId));
  if (btn.dataset.act === "view-org") {
    renderSuperAdminMembers(orgId);
    return;
  }
  if (btn.dataset.act === "toggle-org") {
    const next = org?.status === "suspended" ? "active" : "suspended";
    const tip =
      next === "suspended"
        ? `确定停用组织「${org?.name || ""}」？成员登录将显示「被冻结」，数据会保留。`
        : `确定恢复组织「${org?.name || ""}」？`;
    if (!confirm(tip)) return;
    try {
      await api(`/api/superadmin/organizations/${orgId}/status`, {
        method: "POST",
        body: { status: next },
      });
      msg.textContent = next === "suspended" ? `已停用组织「${org?.name || ""}」` : `已恢复组织「${org?.name || ""}」`;
      await loadSuperAdmin();
      if (state.superadmin?.organizations?.some((o) => String(o.id) === String(orgId))) {
        renderSuperAdminMembers(orgId);
      }
    } catch (ex) {
      msg.textContent = ex.message;
    }
    return;
  }
  if (btn.dataset.act === "delete-org") {
    if (!confirm(`确定删除组织「${org?.name || ""}」？将同时删除其全部成员和数据，不可恢复。`)) return;
    try {
      await api(`/api/superadmin/organizations/${orgId}`, { method: "DELETE" });
      msg.textContent = `已删除组织「${org?.name || ""}」`;
      $("sa-members-section").classList.add("hidden");
      await loadSuperAdmin();
    } catch (ex) {
      msg.textContent = ex.message;
    }
  }
});

$("sa-members").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act='reset-admin-pw']");
  if (!btn) return;
  const tr = btn.closest("tr");
  const msg = $("sa-msg");
  const pw = tr.querySelector("[data-field='password']")?.value || "";
  if (!pw) {
    msg.textContent = "请先填写新密码，再点「重置」";
    return;
  }
  if (!confirm(`确定重置管理员「${tr.dataset.username}」的密码？`)) return;
  try {
    await api(`/api/superadmin/users/${tr.dataset.uid}/reset-password`, {
      method: "POST",
      body: { password: pw },
    });
    tr.querySelector("[data-field='password']").value = "";
    msg.textContent = `已重置管理员「${tr.dataset.username}」的密码`;
  } catch (ex) {
    msg.textContent = ex.message;
  }
});

setAuthMode(false);
boot().catch((err) => {
  console.error(err);
  showLogin();
});

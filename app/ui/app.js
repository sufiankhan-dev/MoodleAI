/* MoodleAI dashboard.
 *
 * Talks only to the local FastAPI server on the same origin. All state-changing
 * calls carry the X-MoodleAI header required by the backend's CSRF guard.
 * Rendered model output is treated as untrusted: Markdown is escaped before a
 * small allow-listed formatter runs, and links are limited to http(s).
 */
(() => {
  "use strict";

  // ---------- theme (before anything paints) ----------

  const THEME_KEY = "moodleai-theme";
  const root = document.documentElement;
  const media = window.matchMedia("(prefers-color-scheme: dark)");

  function applyTheme(theme) {
    root.dataset.theme = theme;
    const btn = document.getElementById("themeBtn");
    if (btn) {
      btn.textContent = theme === "dark" ? "☀" : "☾";
      btn.title = theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
      btn.setAttribute("aria-label", btn.title);
    }
  }

  function initialTheme() {
    try {
      const saved = localStorage.getItem(THEME_KEY);
      if (saved === "light" || saved === "dark") return saved;
    } catch (_) {
      /* storage may be disabled */
    }
    return media.matches ? "dark" : "light";
  }

  applyTheme(initialTheme());

  // ---------- element refs ----------

  const $ = (id) => document.getElementById(id);

  const els = {
    modelBadge: $("modelBadge"),
    browserBadge: $("browserBadge"),
    agentBadge: $("agentBadge"),
    stopBtn: $("stopBtn"),
    themeBtn: $("themeBtn"),
    moodleUrl: $("moodleUrl"),
    connectBtn: $("connectBtn"),
    httpUser: $("httpUser"),
    httpPass: $("httpPass"),
    loginHint: $("loginHint"),
    connState: $("connState"),
    pageTitle: $("pageTitle"),
    pageUrl: $("pageUrl"),
    checkLoginBtn: $("checkLoginBtn"),
    syncBtn: $("syncBtn"),
    syncState: $("syncState"),
    disconnectBtn: $("disconnectBtn"),
    refreshCourses: $("refreshCourses"),
    refreshAssignments: $("refreshAssignments"),
    courseList: $("courseList"),
    assignmentList: $("assignmentList"),
    courseCount: $("courseCount"),
    assignmentCount: $("assignmentCount"),
    chatTitle: $("chatTitle"),
    chatSubtitle: $("chatSubtitle"),
    newChatBtn: $("newChatBtn"),
    messages: $("messages"),
    approvalBar: $("approvalBar"),
    composer: $("composer"),
    messageInput: $("messageInput"),
    sendBtn: $("sendBtn"),
    agentActivity: $("agentActivity"),
    modelMeta: $("modelMeta"),
    refreshFiles: $("refreshFiles"),
    fileTree: $("fileTree"),
    activityLog: $("activityLog"),
    activityCount: $("activityCount"),
    approvalModal: $("approvalModal"),
    approvalHeading: $("approvalHeading"),
    approvalMessage: $("approvalMessage"),
    approvalDetails: $("approvalDetails"),
    approveBtn: $("approveBtn"),
    denyBtn: $("denyBtn"),
    draftModal: $("draftModal"),
    draftHeading: $("draftHeading"),
    draftStatus: $("draftStatus"),
    draftMeta: $("draftMeta"),
    draftHint: $("draftHint"),
    draftFiles: $("draftFiles"),
    draftCloseBtn: $("draftCloseBtn"),
    draftReviewBtn: $("draftReviewBtn"),
    draftStartBtn: $("draftStartBtn"),
    toast: $("toast"),
    toolCount: $("toolCount"),
  };

  const welcomeMarkup = els.messages ? els.messages.innerHTML : "";
  const defaultChatTitle = els.chatTitle ? els.chatTitle.textContent : "New conversation";
  const defaultChatSubtitle = els.chatSubtitle ? els.chatSubtitle.textContent : "";

  // ---------- state ----------

  const state = {
    conversationId: null,
    running: false,
    conversationStarted: false,
    streamingEl: null,
    streamingText: "",
    lastAssistantEl: null,
    lastUserText: "",
    pendingTools: [],
    approvals: new Map(),
    currentApprovalId: null,
    activity: [],
    files: { workspace: [], generated: [] },
    openDirs: new Set(),
    syncing: false,
    courses: [],
    assignments: [],
    activeTab: "workspace",
    tools: [],
    connected: false,
    sseFailed: false,
    lastBrowser: null,
    draftId: null,
    draft: null,
  };

  let toastTimer = 0;
  let dataRefreshTimer = 0;
  let browserRefreshTimer = 0;
  let browserPollBusy = false;
  let resetEchoUntil = 0;

  // ---------- helpers ----------

  const ACTIVITY_KINDS = new Set([
    "tool",
    "reading",
    "navigate",
    "download",
    "file",
    "error",
    "warning",
    "browser",
    "approval_required",
    "approval_timeout",
    "stopped",
    "done",
    "reset",
  ]);

  const ACTIVITY_ICONS = {
    tool: "🔧",
    reading: "📖",
    navigate: "🧭",
    download: "⬇",
    file: "📄",
    error: "⚠",
    warning: "⚠",
    browser: "🌐",
    approval_required: "🔒",
    approval_timeout: "⏱",
    stopped: "■",
    done: "✓",
    reset: "🧹",
  };

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => {
      switch (char) {
        case "&":
          return "&amp;";
        case "<":
          return "&lt;";
        case ">":
          return "&gt;";
        case '"':
          return "&quot;";
        default:
          return "&#39;";
      }
    });
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/`/g, "&#96;");
  }

  function truncate(text, max) {
    const value = String(text ?? "");
    return value.length > max ? `${value.slice(0, max - 1)}…` : value;
  }

  function formatBytes(bytes) {
    const size = Number(bytes) || 0;
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatClock(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  }

  function humanize(value) {
    const text = String(value ?? "").replace(/[_-]+/g, " ").trim();
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "this action";
  }

  function plural(count, one, many) {
    return `${count} ${count === 1 ? one : many || `${one}s`}`;
  }

  function showToast(message, tone) {
    if (!els.toast) return;
    window.clearTimeout(toastTimer);
    els.toast.textContent = message;
    els.toast.classList.remove("hidden", "ok", "bad");
    if (tone) els.toast.classList.add(tone);
    toastTimer = window.setTimeout(() => els.toast.classList.add("hidden"), 4200);
  }

  function setText(el, text) {
    if (el) el.textContent = text;
  }

  function setBadge(el, label, kind, title) {
    if (!el) return;
    el.classList.remove("pill-ok", "pill-warn", "pill-bad", "pill-busy", "pill-idle", "pill-muted");
    el.classList.add(`pill-${kind}`);
    const span = el.querySelector("span");
    if (span) span.textContent = label;
    else el.textContent = label;
    el.title = title || label;
  }

  async function api(path, options = {}) {
    const method = (options.method || "GET").toUpperCase();
    const headers = {};
    if (options.body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET") headers["X-MoodleAI"] = "1";

    let response;
    try {
      response = await fetch(path, {
        method,
        headers,
        body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
        cache: "no-store",
      });
    } catch (error) {
      throw new Error(
        "Cannot reach the local MoodleAI server. Is `python main.py` still running?"
      );
    }

    const type = response.headers.get("content-type") || "";
    let payload = null;
    if (type.includes("application/json")) {
      payload = await response.json().catch(() => null);
    } else {
      payload = await response.text().catch(() => "");
    }

    if (!response.ok) {
      const detail =
        (payload && (payload.detail || payload.error || payload.message)) ||
        (typeof payload === "string" && payload) ||
        `${response.status} ${response.statusText}`;
      const error = new Error(String(detail));
      error.status = response.status;
      throw error;
    }
    return payload;
  }

  async function withLoading(button, task) {
    if (!button) return task();
    if (button.dataset.loading === "1") return undefined;
    button.dataset.loading = "1";
    button.classList.add("loading");
    button.disabled = true;
    try {
      return await task();
    } finally {
      button.dataset.loading = "";
      button.classList.remove("loading");
      button.disabled = false;
    }
  }

  // ---------- safe markdown ----------

  function safeHref(raw) {
    const value = String(raw || "").replace(/&amp;/g, "&");
    try {
      const url = new URL(value);
      if (url.protocol !== "http:" && url.protocol !== "https:") return null;
      return escapeAttr(url.href);
    } catch (_) {
      return null;
    }
  }

  function inlineMarkdown(text) {
    const codes = [];
    let out = text.replace(/`([^`\n]+)`/g, (_, code) => {
      codes.push(`<code>${code}</code>`);
      return `\u0000${codes.length - 1}\u0000`;
    });

    out = out.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    out = out.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    out = out.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");

    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (match, label, url) => {
      const href = safeHref(url);
      if (!href) return match;
      return `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });

    out = out.replace(/\u0000(\d+)\u0000/g, (_, index) => codes[Number(index)] || "");
    return out;
  }

  function renderMarkdown(source) {
    const escaped = escapeHtml(source).replace(/\r\n?/g, "\n");
    const lines = escaped.split("\n");
    const html = [];

    let paragraph = [];
    let listType = null;
    let listItems = [];
    let inCode = false;
    let codeLang = "";
    let codeLines = [];

    function flushParagraph() {
      if (!paragraph.length) return;
      html.push(`<p>${inlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    }

    function flushList() {
      if (!listType) return;
      html.push(`<${listType}>${listItems.join("")}</${listType}>`);
      listType = null;
      listItems = [];
    }

    function flushAll() {
      flushParagraph();
      flushList();
    }

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];

      if (/^\s*```/.test(line)) {
        if (!inCode) {
          flushAll();
          inCode = true;
          codeLang = line.trim().replace(/^```/, "").trim().replace(/[^a-zA-Z0-9+#.-]/g, "");
          codeLines = [];
        } else {
          const cls = codeLang ? ` class="language-${escapeAttr(codeLang)}"` : "";
          html.push(`<pre><code${cls}>${codeLines.join("\n")}</code></pre>`);
          inCode = false;
          codeLang = "";
        }
        continue;
      }

      if (inCode) {
        codeLines.push(line);
        continue;
      }

      if (!line.trim()) {
        flushAll();
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.*)$/);
      if (heading) {
        flushAll();
        const level = heading[1].length === 1 ? "h3" : heading[1].length === 2 ? "h4" : "h5";
        html.push(`<${level}>${inlineMarkdown(heading[2].trim())}</${level}>`);
        continue;
      }

      if (/^\s*([-*_])\s*(?:\1\s*){2,}$/.test(line)) {
        flushAll();
        html.push("<hr>");
        continue;
      }

      if (/^>\s?/.test(line)) {
        flushAll();
        const quote = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^>\s?/, ""));
          i += 1;
        }
        i -= 1;
        html.push(`<blockquote>${inlineMarkdown(quote.join(" "))}</blockquote>`);
        continue;
      }

      const unordered = line.match(/^\s*[-*+]\s+(.*)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);

      if (unordered || ordered) {
        flushParagraph();
        const type = unordered ? "ul" : "ol";
        if (listType && listType !== type) flushList();
        listType = type;
        listItems.push(`<li>${inlineMarkdown((unordered || ordered)[1])}</li>`);
        continue;
      }

      flushList();
      paragraph.push(line.trim());
    }

    if (inCode && codeLines.length) {
      html.push(`<pre><code>${codeLines.join("\n")}</code></pre>`);
    }
    flushAll();
    return html.join("");
  }

  // ---------- chat rendering ----------

  function nearBottom() {
    if (!els.messages) return true;
    const el = els.messages;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  }

  function scrollToBottom(force) {
    if (!els.messages) return;
    if (force || nearBottom()) {
      els.messages.scrollTop = els.messages.scrollHeight;
    }
  }

  function removeWelcome() {
    const welcome = els.messages?.querySelector(".welcome");
    if (welcome) welcome.remove();
  }

  function restoreWelcome() {
    if (!els.messages) return;
    els.messages.innerHTML = welcomeMarkup;
    els.messages.scrollTop = 0;
    renderSuggestions();
  }

  function createMessage(role, text, options = {}) {
    const msg = document.createElement("div");
    msg.className = `msg ${role}`;

    if (role === "assistant") {
      const avatar = document.createElement("div");
      avatar.className = "msg-avatar";
      avatar.setAttribute("aria-hidden", "true");
      avatar.textContent = "M";
      msg.appendChild(avatar);
    }

    const main = document.createElement("div");
    main.className = "msg-main";

    const author = document.createElement("div");
    author.className = "msg-author";
    author.textContent =
      role === "user" ? "You" : options.error ? "MoodleAI · error" : "MoodleAI";
    main.appendChild(author);

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (options.error) bubble.classList.add("error");
    if (options.notice) bubble.classList.add("notice");
    if (options.streaming) bubble.classList.add("streaming");
    if (text) {
      if (options.plain) bubble.textContent = text;
      else bubble.innerHTML = renderMarkdown(text);
    }

    main.appendChild(bubble);
    msg.appendChild(main);
    els.messages.appendChild(msg);
    scrollToBottom(true);
    return { msg, bubble };
  }

  function appendUser(text) {
    removeWelcome();
    createMessage("user", text, { plain: true });
    state.lastUserText = text;
    state.conversationStarted = true;
  }

  function appendAssistant(text, options = {}) {
    removeWelcome();
    const { msg, bubble } = createMessage("assistant", text, options);
    if (!options.streaming) state.lastAssistantEl = msg;
    return { msg, bubble };
  }

  function appendError(text) {
    appendAssistant(text, { error: true, plain: true });
  }

  function appendNotice(text) {
    appendAssistant(text, { notice: true });
  }

  function ensureStreaming() {
    if (state.streamingEl) return state.streamingEl;
    removeWelcome();
    const { msg, bubble } = createMessage("assistant", "", { streaming: true });
    state.streamingEl = bubble;
    state.streamingText = "";
    state.lastAssistantEl = msg;
    return bubble;
  }

  function finalizeStreaming(finalText) {
    const bubble = state.streamingEl;
    const text =
      finalText === undefined || finalText === null ? state.streamingText : String(finalText);

    if (!bubble) {
      state.streamingText = "";
      if (text.trim()) return appendAssistant(text).msg;
      return null;
    }

    const msg = bubble.closest(".msg");
    bubble.classList.remove("streaming");
    if (text.trim()) {
      bubble.innerHTML = renderMarkdown(text);
      state.lastAssistantEl = msg;
    } else if (msg) {
      msg.remove();
      if (state.lastAssistantEl === msg) state.lastAssistantEl = null;
    }

    state.streamingEl = null;
    state.streamingText = "";
    scrollToBottom(false);
    return msg;
  }

  function addMessageMeta(msg, text) {
    if (!msg || !text) return;
    const main = msg.querySelector(".msg-main");
    if (!main) return;
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    meta.textContent = text;
    main.appendChild(meta);
  }

  function appendToolRow(name, args) {
    const row = document.createElement("div");
    row.className = "tool-row";

    const status = document.createElement("span");
    status.className = "tool-state";
    const spinner = document.createElement("span");
    spinner.className = "tool-spinner";
    status.appendChild(spinner);

    const label = document.createElement("span");
    label.className = "tool-name";
    label.textContent = name;

    row.appendChild(status);
    row.appendChild(label);

    if (args && typeof args === "object" && Object.keys(args).length) {
      row.title = truncate(JSON.stringify(args), 500);
    }

    els.messages.appendChild(row);
    state.pendingTools.push(row);
    scrollToBottom(true);
    return row;
  }

  function finishToolRow(payload, message) {
    const row = state.pendingTools.pop();
    if (!row) return;

    const ok = !(payload && payload.ok === false);
    row.classList.add(ok ? "ok" : "fail");

    const status = row.querySelector(".tool-state");
    if (status) status.textContent = ok ? "✓" : "✕";

    const text = message || (payload && (payload.message || payload.error)) || (ok ? "Done" : "Failed");
    if (text) {
      const detail = document.createElement("span");
      detail.className = "tool-msg";
      detail.textContent = text;
      detail.title = text;
      row.appendChild(detail);
    }
    scrollToBottom(false);
  }

  function setRunning(running) {
    state.running = Boolean(running);
    if (els.sendBtn) els.sendBtn.disabled = state.running;
    if (els.stopBtn) els.stopBtn.disabled = !state.running;
    if (els.messageInput) {
      els.messageInput.placeholder = state.running
        ? "The agent is working… press Stop to interrupt"
        : "Ask MoodleAI…";
    }
  }

  function setAgentStatus(status, message) {
    const map = {
      idle: { label: "Idle", kind: "idle", title: "Agent idle" },
      running: { label: "Working", kind: "busy", title: "Agent is working" },
      waiting_approval: { label: "Approval", kind: "warn", title: "Waiting for your approval" },
      stopping: { label: "Stopping", kind: "warn", title: "Stopping the agent" },
    };
    const info = map[status] || map.idle;
    setBadge(els.agentBadge, info.label, info.kind, info.title);

    if (message) setText(els.agentActivity, message);
    else setText(els.agentActivity, info.label);

    if (status === "idle") setRunning(false);
    else if (status === "running" || status === "waiting_approval" || status === "stopping") {
      setRunning(true);
    }
  }

  // ---------- courses & assignments ----------

  function fillComposer(text) {
    if (!els.messageInput) return;
    els.messageInput.value = text;
    autosize();
    els.messageInput.focus();
    showToast("Prompt ready — press Send.");
  }

  // ---------- collapsible cards ----------

  // Per-viewer convenience only: a blocked or empty storage just means every
  // card starts in its default state.
  const CARDS_KEY = "moodleai-collapsed";
  const DEFAULT_COLLAPSED = { activity: true };

  function loadCardPrefs() {
    try {
      const parsed = JSON.parse(localStorage.getItem(CARDS_KEY) || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_) {
      return {};
    }
  }

  const cardPrefs = loadCardPrefs();

  function setCardCollapsed(card, collapsed) {
    card.classList.toggle("collapsed", collapsed);
    const toggle = card.querySelector(".card-toggle");
    if (toggle) {
      toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      toggle.title = collapsed ? "Expand" : "Collapse";
    }
  }

  function initCards() {
    document.querySelectorAll(".card[data-card]").forEach((card) => {
      const key = card.dataset.card;
      const saved = cardPrefs[key];
      setCardCollapsed(card, typeof saved === "boolean" ? saved : Boolean(DEFAULT_COLLAPSED[key]));
      card.querySelector(".card-toggle")?.addEventListener("click", () => {
        const collapsed = !card.classList.contains("collapsed");
        setCardCollapsed(card, collapsed);
        cardPrefs[key] = collapsed;
        try {
          localStorage.setItem(CARDS_KEY, JSON.stringify(cardPrefs));
        } catch (_) {
          /* ignore */
        }
      });
    });
  }

  // Once logged in, the connection form is just noise. Fold it away unless the
  // user has chosen a state for that card themselves.
  function autoCollapseConnection(loggedIn) {
    if (typeof cardPrefs.connection === "boolean") return;
    const card = document.querySelector('.card[data-card="connection"]');
    if (card) setCardCollapsed(card, loggedIn === true);
  }

  function setCount(el, value) {
    if (!el) return;
    el.textContent = value ? String(value) : "";
  }

  // ---------- suggestions (built from the synced data) ----------

  function suggestionButton(label, prompt, title) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.dataset.prompt = prompt;
    button.textContent = label;
    button.title = title || prompt;
    return button;
  }

  function quoted(name, max) {
    return `“${truncate(String(name || ""), max)}”`;
  }

  // "Artificial Intelligence Lab(BS-CYS-5A)" → "Artificial Intelligence Lab":
  // section codes make every label start the same and truncate identically.
  function shortCourse(name) {
    const text = String(name || "").trim();
    return text.replace(/\s*\([^()]*\)\s*$/, "").trim() || text;
  }

  function renderSuggestions() {
    const holder = els.messages?.querySelector("#suggestions");
    if (!holder) return;
    holder.replaceChildren();

    const pending = (state.assignments || []).filter((row) => row.pending);
    const courses = state.courses || [];
    const items = [];

    if (pending.length) {
      const lines = pending.slice(0, 12).map((row) => {
        const due = row.due_text ? ` — due ${row.due_text}` : "";
        const course = row.course_name ? ` (${row.course_name})` : "";
        return `- ${row.name}${course}${due}`;
      });
      items.push([
        "What should I do first?",
        `These are my pending Moodle assignments:\n${lines.join("\n")}\n\nWhich should I work on first, and why? Keep it short.`,
        "Prioritise your pending assignments",
      ]);

      const next = pending[0];
      const inCourse = next.course_name ? ` in "${next.course_name}"` : "";
      items.push([
        `Explain ${quoted(next.name, 26)}`,
        `Open assignment "${next.name}"${inCourse} and explain what I have to do, step by step, in simple language.`,
      ]);
      items.push([
        `Plan ${quoted(next.name, 26)}`,
        `Open assignment "${next.name}"${inCourse}, read its attached files, and give me a plan with steps to finish it before the deadline.`,
      ]);
    }

    const [first, second] = courses;
    if (first) {
      items.push([
        `Summarise ${quoted(shortCourse(first.name), 28)}`,
        `Open my "${first.name}" course and summarise it: the topics, the materials posted, and any upcoming work.`,
      ]);
    }
    if (second && !pending.length) {
      items.push([
        `Study materials in ${quoted(shortCourse(second.name), 24)}`,
        `Open my "${second.name}" course and list the lecture slides and files I should study first, newest first.`,
      ]);
    }
    if (courses.length > 1 && !pending.length) {
      items.push([
        "Any new announcements?",
        "Check my Moodle dashboard for recent announcements or course updates and summarise anything important.",
      ]);
    }

    if (!items.length) {
      items.push([
        "List my courses",
        "List my Moodle courses.",
        "Works once you are logged in to Moodle in Chrome",
      ]);
      items.push([
        "What can you help with?",
        "In two or three sentences, what can you help me with in my Moodle courses?",
      ]);
    }

    items.slice(0, 4).forEach(([label, prompt, title]) => {
      holder.appendChild(suggestionButton(label, prompt, title));
    });
  }

  function courseItem(course) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "list-item";
    button.title = `Ask the agent to open ${course.name}`;

    const glyph = document.createElement("span");
    glyph.className = "glyph";
    glyph.textContent = (course.name || "?").trim().charAt(0).toUpperCase() || "?";

    const body = document.createElement("span");
    body.className = "body";
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = course.name || "Untitled course";
    body.appendChild(name);

    const sub = document.createElement("span");
    sub.className = "sub";
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = course.moodle_id ? `Moodle #${course.moodle_id}` : "Course";
    sub.appendChild(chip);
    body.appendChild(sub);

    button.appendChild(glyph);
    button.appendChild(body);
    button.addEventListener("click", () => {
      fillComposer(`Open my "${course.name}" course`);
    });
    return button;
  }

  const STATUS_STYLES = {
    graded: "chip chip-ok",
    submitted: "chip chip-ok",
    submitted_for_grading: "chip chip-ok",
    no_submission_required: "chip",
    draft: "chip chip-warn",
    reopened: "chip chip-warn",
    not_submitted: "chip",
    unknown: "chip",
  };

  const URGENCY_RANK = { overdue: 0, urgent: 1, soon: 2, upcoming: 3, no_due_date: 4 };

  function hoursLabel(hours) {
    if (hours === null || hours === undefined) return "";
    const value = Number(hours);
    if (!Number.isFinite(value)) return "";
    const magnitude = Math.abs(value);
    if (magnitude < 1) return "under an hour";
    if (magnitude < 48) return `${Math.round(magnitude)}h`;
    return `${Math.round(magnitude / 24)}d`;
  }

  // The badge is built from the backend's urgency, never from Date parsing the
  // Moodle display text: the stored instant is the only trustworthy input.
  function dueBadge(assignment) {
    const urgency = assignment.urgency || "no_due_date";
    const relative = hoursLabel(assignment.due_in_hours);
    let className = "chip";
    let label = "";

    if (urgency === "overdue") {
      className = "chip chip-danger";
      label = relative ? `Overdue by ${relative}` : "Overdue";
    } else if (urgency === "urgent") {
      className = "chip chip-danger";
      label = relative ? `Due in ${relative}` : "Due soon";
    } else if (urgency === "soon") {
      className = "chip chip-warn";
      label = relative ? `Due in ${relative}` : "Due soon";
    } else if (assignment.due_text) {
      label = truncate(assignment.due_text, 40);
    }

    if (!label) return null;
    const chip = document.createElement("span");
    chip.className = className;
    chip.textContent = label;
    chip.title = assignment.due_text ? `Due: ${assignment.due_text}` : label;
    return chip;
  }

  function statusBadge(assignment) {
    const status = assignment.status || "unknown";
    const chip = document.createElement("span");
    chip.className = STATUS_STYLES[status] || "chip";
    chip.textContent = truncate(
      assignment.status_label || assignment.status_text || "Status unknown",
      40
    );
    chip.title = assignment.status_text || chip.textContent;
    return chip;
  }

  function assignmentItem(assignment) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "list-item";
    button.title = "Ask the agent to open this assignment";

    const glyph = document.createElement("span");
    glyph.className = "glyph";
    glyph.textContent = "✎";

    const body = document.createElement("span");
    body.className = "body";

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = assignment.name || "Untitled assignment";
    body.appendChild(name);

    const sub = document.createElement("span");
    sub.className = "sub sub-chips";

    if (assignment.course_name) {
      const course = document.createElement("span");
      course.textContent = assignment.course_name;
      sub.appendChild(course);
    }

    if (assignment.due_text || assignment.urgency) {
      const due = dueBadge(assignment);
      if (due) sub.appendChild(due);
    }

    sub.appendChild(statusBadge(assignment));

    body.appendChild(sub);
    button.appendChild(glyph);
    button.appendChild(body);

    button.addEventListener("click", () => {
      const inCourse = assignment.course_name ? ` in "${assignment.course_name}"` : "";
      fillComposer(`Open assignment "${assignment.name}"${inCourse}`);
    });

    // A sibling button, not a nested one: buttons cannot contain buttons.
    const row = document.createElement("div");
    row.className = "assignment-row";
    row.appendChild(button);
    if (assignment.id !== undefined && assignment.id !== null) {
      const draft = document.createElement("button");
      draft.type = "button";
      draft.className = "btn btn-ghost small row-action";
      draft.textContent = "Draft";
      draft.title = "Draft this assignment with the local model, then review it";
      draft.addEventListener("click", () => openDraft(assignment.id));
      row.appendChild(draft);
    }
    return row;
  }

  // ---------- assignment drafts ----------

  const DRAFT_STATUS = {
    none: ["No draft yet", "chip"],
    draft: ["Needs review", "chip chip-warn"],
    changed: ["Changed since review", "chip chip-warn"],
    reviewed: ["Reviewed", "chip chip-ok"],
  };

  function formatBytes(size) {
    const value = Number(size) || 0;
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  }

  function draftFileItem(file, extraClass) {
    const item = document.createElement("li");
    item.className = `draft-file${extraClass ? ` ${extraClass}` : ""}`;
    const link = document.createElement("a");
    link.href = file.download_url;
    link.textContent = file.name || file.path;
    link.title = file.path;
    link.setAttribute("download", "");
    item.appendChild(link);
    const meta = document.createElement("span");
    meta.className = "draft-file-meta";
    const bits = [];
    if (file.folder) bits.push(`${file.folder}/`);
    if (file.size !== undefined) bits.push(formatBytes(file.size));
    if (file.changed) bits.push("changed");
    meta.textContent = bits.join(" · ");
    item.appendChild(meta);
    return item;
  }

  function renderDraft(data) {
    state.draft = data;
    const info = data?.assignment || {};
    setText(els.draftHeading, info.name || "Assignment draft");
    const metaParts = [];
    if (info.course_name) metaParts.push(info.course_name);
    if (info.due_text) metaParts.push(`Due ${info.due_text}`);
    setText(els.draftMeta, metaParts.join(" · "));

    const [label, className] = DRAFT_STATUS[data?.status] || DRAFT_STATUS.none;
    if (els.draftStatus) {
      els.draftStatus.className = className;
      els.draftStatus.textContent = label;
    }

    const files = Array.isArray(data?.files) ? data.files : [];
    let hint = "";
    if (state.running) {
      hint = "The agent is working. Files appear here when the run finishes.";
    } else if (data?.status === "none") {
      hint = "Draft with AI reads the saved instructions and teacher files, then writes every deliverable into this assignment's folder.";
    } else if (data?.status === "changed") {
      hint = "Files changed after your last review. Check them, then mark the draft reviewed again.";
    } else if (data?.status === "reviewed") {
      hint = `Reviewed ${data.reviewed_at ? new Date(data.reviewed_at).toLocaleString() : ""}. Upload these files in Moodle yourself.`;
    } else {
      hint = "Open each file and check it against REVIEW.md before marking it reviewed.";
    }
    setText(els.draftHint, hint);

    if (els.draftFiles) {
      els.draftFiles.replaceChildren();
      if (data?.review_note) {
        els.draftFiles.appendChild(
          draftFileItem(
            {
              name: "REVIEW.md",
              path: data.review_note,
              folder: "explanations",
              download_url: `/api/files/download?root=workspace&path=${encodeURIComponent(data.review_note)}`,
            },
            "draft-note"
          )
        );
      }
      files.forEach((file) => els.draftFiles.appendChild(draftFileItem(file)));
      els.draftFiles.classList.toggle("hidden", !files.length && !data?.review_note);
    }

    if (els.draftStartBtn) {
      els.draftStartBtn.textContent = files.length ? "Redraft" : "Draft with AI";
      els.draftStartBtn.disabled = state.running;
    }
    if (els.draftReviewBtn) {
      els.draftReviewBtn.disabled = state.running || !files.length || data?.status === "reviewed";
    }
  }

  async function loadDraft(assignmentId) {
    const data = await api(`/api/assignments/${encodeURIComponent(assignmentId)}/draft`);
    if (state.draftId === assignmentId) renderDraft(data);
    return data;
  }

  async function openDraft(assignmentId) {
    state.draftId = assignmentId;
    renderDraft({ assignment: (state.assignments || []).find((row) => row.id === assignmentId), status: "none", files: [] });
    els.draftModal?.classList.remove("hidden");
    try {
      await loadDraft(assignmentId);
    } catch (error) {
      showToast(error.message, "bad");
    }
  }

  function closeDraft() {
    state.draftId = null;
    els.draftModal?.classList.add("hidden");
  }

  async function startDraft() {
    const assignmentId = state.draftId;
    if (assignmentId === null || state.running) return;
    const hasFiles = Boolean(state.draft?.files?.length);
    if (
      hasFiles &&
      !window.confirm("Redraft this assignment? Existing files are only replaced if you approve each overwrite.")
    ) {
      return;
    }
    const name = state.draft?.assignment?.name || "assignment";
    await withLoading(els.draftStartBtn, async () => {
      // The run is an ordinary agent conversation shown in the chat. Clear the
      // chat before the request so the run's first live event is not wiped.
      restoreWelcome();
      state.lastAssistantEl = null;
      state.lastUserText = "";
      try {
        const response = await api(`/api/assignments/${encodeURIComponent(assignmentId)}/draft`, {
          method: "POST",
          body: {},
        });
        state.conversationId = response.conversation_id;
        state.conversationStarted = true;
        setText(els.chatTitle, truncate(`Draft: ${name}`, 70));
        setText(els.chatSubtitle, "Drafting · qwen3:8b · nothing is submitted");
        setRunning(true);
        closeDraft();
        showToast("Drafting started. Approve any overwrite the agent asks for.");
      } catch (error) {
        showToast(error.message, "bad");
      }
    });
  }

  async function markReviewed() {
    const assignmentId = state.draftId;
    if (assignmentId === null) return;
    let data = null;
    await withLoading(els.draftReviewBtn, async () => {
      try {
        data = await api(`/api/assignments/${encodeURIComponent(assignmentId)}/review`, {
          method: "POST",
          body: {},
        });
        showToast("Marked reviewed. Any later edit will show as changed.", "ok");
      } catch (error) {
        showToast(error.message, "bad");
      }
    });
    // Rendered after withLoading re-enables the button, so "reviewed" can disable it.
    if (data && state.draftId === assignmentId) renderDraft(data);
  }

  function renderCourses(courses) {
    state.courses = Array.isArray(courses) ? courses : [];
    setCount(els.courseCount, state.courses.length);
    renderSuggestions();
    if (!els.courseList) return;
    els.courseList.replaceChildren();
    if (!Array.isArray(courses) || !courses.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "No courses yet. Connect to Moodle and log in to sync them.";
      els.courseList.appendChild(empty);
      return;
    }
    courses.forEach((course) => els.courseList.appendChild(courseItem(course)));
  }

  function renderAssignments(assignments) {
    const rows = Array.isArray(assignments) ? assignments.slice() : [];
    state.assignments = rows;
    setCount(els.assignmentCount, rows.filter((row) => row.pending).length);
    renderSuggestions();
    if (!els.assignmentList) return;
    els.assignmentList.replaceChildren();
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Assignments appear here after you log in and the sync runs.";
      els.assignmentList.appendChild(empty);
      return;
    }

    rows.sort((a, b) => {
      const pendingDelta = Number(!a.pending) - Number(!b.pending);
      if (pendingDelta) return pendingDelta;
      const rankA = URGENCY_RANK[a.urgency] ?? 9;
      const rankB = URGENCY_RANK[b.urgency] ?? 9;
      if (rankA !== rankB) return rankA - rankB;
      const hoursA = a.due_in_hours === null || a.due_in_hours === undefined ? 1e9 : Number(a.due_in_hours);
      const hoursB = b.due_in_hours === null || b.due_in_hours === undefined ? 1e9 : Number(b.due_in_hours);
      if (hoursA !== hoursB) return hoursA - hoursB;
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
    const pending = rows.filter((row) => row.pending);
    const rest = rows.filter((row) => !row.pending);

    pending.slice(0, 60).forEach((row) => {
      els.assignmentList.appendChild(assignmentItem(row));
    });

    if (!pending.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Nothing pending. Nice.";
      els.assignmentList.appendChild(empty);
    }

    // Submitted, graded and unknown-status work stays reachable but folded away.
    if (rest.length) {
      const group = document.createElement("details");
      group.className = "list-group";
      const summary = document.createElement("summary");
      summary.textContent = `${rest.length} submitted or no status`;
      summary.title = "Submitted, graded, or no status reported by Moodle";
      group.appendChild(summary);
      rest.slice(0, 60).forEach((row) => group.appendChild(assignmentItem(row)));
      els.assignmentList.appendChild(group);
    }
  }

  function renderSync(info) {
    const syncing = Boolean(info && (info.running || info.state === "running" || info.phase || info.step));
    if (syncing !== state.syncing) {
      state.syncing = syncing;
      if (state.lastBrowser) updateBrowser(state.lastBrowser);
    }
    if (!els.syncState) return;
    if (!info) {
      setText(els.syncState, "Not synced yet");
      return;
    }
    if (info.phase === "files" && info.assignment) {
      const where = info.step && info.total ? ` · course ${info.step}/${info.total}` : "";
      setText(els.syncState, `Saving ${info.assignment}${where}`);
      return;
    }
    if (info.step && info.total) {
      setText(els.syncState, `Reading ${info.course || "course"} (${info.step}/${info.total})`);
      return;
    }
    const message = info.message || (info.running ? "Syncing…" : "Not synced yet");
    setText(els.syncState, message);
    els.syncState.title = (info.errors && info.errors.length ? info.errors.join("\n") : message) || "";
    els.syncState.classList.toggle("bad", info.state === "failed");
    els.syncState.classList.toggle("ok", info.state === "done");
    els.syncState.classList.toggle("warn", info.state === "running");
  }

  // ---------- files ----------

  function buildDownloadUrl(file) {
    const path = String(file.path || "")
      .split("/")
      .map((segment) => encodeURIComponent(segment))
      .join("/");
    return `/api/files/download?root=${encodeURIComponent(file.root || state.activeTab)}&path=${path}`;
  }

  function fileIcon(name) {
    const ext = String(name).split(".").pop().toLowerCase();
    if (ext === "pdf") return "📕";
    if (["doc", "docx", "odt", "rtf"].includes(ext)) return "📘";
    if (["xls", "xlsx", "csv", "ods"].includes(ext)) return "📊";
    if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "🖼";
    if (["zip", "7z", "rar", "tar", "gz"].includes(ext)) return "📦";
    if (["py", "js", "ts", "tsx", "jsx", "java", "c", "cpp", "cs", "go", "rs", "php", "rb", "sh", "html", "css", "json", "xml", "yaml", "yml", "sql"].includes(ext)) {
      return "📜";
    }
    if (["md", "txt", "log"].includes(ext)) return "📝";
    return "📄";
  }

  function hasFiles(node) {
    if (node.type === "file") return true;
    return Array.isArray(node.children) && node.children.some(hasFiles);
  }

  // Course folders (depth 0) always show, so every course has its place. Below
  // that, empty scaffolding folders (code/, pdfs/, submission/…) are hidden
  // until something is saved in them. Folders start closed and remember what
  // the user opened across refreshes.
  function treeFiles(nodes, container, depth = 0) {
    nodes.forEach((node) => {
      if (node.type === "notice") {
        const note = document.createElement("p");
        note.className = "empty";
        note.textContent = node.name || "More files not shown.";
        container.appendChild(note);
        return;
      }

      if (node.type === "dir") {
        if (depth > 0 && !hasFiles(node)) return;
        const key = `${state.activeTab}:${node.path || node.name}`;
        const details = document.createElement("details");
        details.className = "tree-dir";
        details.open = state.openDirs.has(key);
        details.addEventListener("toggle", () => {
          if (details.open) state.openDirs.add(key);
          else state.openDirs.delete(key);
        });

        const summary = document.createElement("summary");
        const folder = document.createElement("span");
        folder.className = "folder";
        folder.textContent = "📁";
        const label = document.createElement("span");
        label.textContent = node.name || "folder";
        summary.appendChild(folder);
        summary.appendChild(label);
        details.appendChild(summary);

        const children = document.createElement("div");
        children.className = "tree-children";
        if (hasFiles(node)) {
          treeFiles(node.children, children, depth + 1);
        } else {
          const empty = document.createElement("p");
          empty.className = "empty";
          empty.textContent = "Empty";
          children.appendChild(empty);
        }
        details.appendChild(children);
        container.appendChild(details);
        return;
      }

      if (node.type === "file") {
        const link = document.createElement("a");
        link.className = "tree-file";
        link.href = buildDownloadUrl(node);
        link.download = node.name || "";
        link.title = `Download ${node.name}`;

        const icon = document.createElement("span");
        icon.className = "file-icon";
        icon.textContent = fileIcon(node.name);

        const name = document.createElement("span");
        name.className = "file-name";
        name.textContent = node.name || "file";

        const size = document.createElement("span");
        size.className = "file-size";
        size.textContent = formatBytes(node.size);

        link.appendChild(icon);
        link.appendChild(name);
        link.appendChild(size);
        container.appendChild(link);
      }
    });
  }

  function renderFiles() {
    if (!els.fileTree) return;
    els.fileTree.replaceChildren();
    const nodes = state.files[state.activeTab] || [];
    const root = document.createElement("div");
    root.className = "tree";

    if (!Array.isArray(nodes) || !nodes.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent =
        state.activeTab === "workspace"
          ? "Course folders appear here after the first sync."
          : "Files made outside any course appear here.";
      els.fileTree.appendChild(empty);
      return;
    }

    treeFiles(nodes, root);
    els.fileTree.appendChild(root);
  }

  // ---------- activity ----------

  async function refreshActivity() {
    try {
      const data = await api("/api/activity");
      renderActivity(data.activity);
    } catch (_) {
      renderActivity([]);
    }
  }

  function activityItem(item) {
    const row = document.createElement("div");
    row.className = "activity-item";
    row.dataset.kind = item.kind || "other";
    row.dataset.level = item.level === "error" ? "error" : "info";

    const time = document.createElement("time");
    time.textContent = formatClock(item.created_at || item.ts);

    const icon = document.createElement("span");
    icon.className = "act-icon";
    icon.textContent = ACTIVITY_ICONS[item.kind] || "•";

    const text = document.createElement("span");
    text.className = "act-text";
    text.textContent = item.message || "";
    text.title = item.message || "";

    row.appendChild(icon);
    row.appendChild(text);
    row.appendChild(time);
    return row;
  }

  function addActivity(item, live) {
    if (!els.activityLog) return;
    const kind = item.kind || "other";
    if (!ACTIVITY_KINDS.has(kind)) return;

    if (!state.activity.length) {
      els.activityLog.replaceChildren();
    }

    state.activity.push(item);
    if (state.activity.length > 250) state.activity.shift();

    const empty = els.activityLog.querySelector(".empty");
    if (empty) empty.remove();

    els.activityLog.appendChild(activityItem(item));
    while (els.activityLog.children.length > 250) {
      els.activityLog.removeChild(els.activityLog.firstElementChild);
    }
    if (!live || nearBottom()) {
      els.activityLog.scrollTop = els.activityLog.scrollHeight;
    }
    setText(els.activityCount, String(state.activity.length));
  }

  function renderActivity(items) {
    if (!els.activityLog) return;
    els.activityLog.replaceChildren();
    state.activity = [];
    const rows = (Array.isArray(items) ? items : []).filter((item) => ACTIVITY_KINDS.has(item.kind));
    if (!rows.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Agent activity will appear here.";
      els.activityLog.appendChild(empty);
      setText(els.activityCount, "0");
      return;
    }
    rows.slice(-180).forEach((item) => {
      state.activity.push(item);
      els.activityLog.appendChild(activityItem(item));
    });
    els.activityLog.scrollTop = els.activityLog.scrollHeight;
    setText(els.activityCount, String(state.activity.length));
  }

  // ---------- browser ----------

  function updateOllama(ollama) {
    if (!ollama) return;
    if (!ollama.reachable) {
      setBadge(els.modelBadge, "Ollama offline", "bad", ollama.error || "Ollama is not reachable");
    } else if (!ollama.installed) {
      setBadge(els.modelBadge, `${ollama.model} missing`, "warn", ollama.error || "Model not installed");
    } else {
      const latency = ollama.latency_ms ? ` · ${ollama.latency_ms} ms` : "";
      setBadge(els.modelBadge, ollama.model, "ok", `Ollama ready${latency}`);
    }
    setText(els.modelMeta, `${ollama.model || "qwen3:8b"} · local${ollama.reachable ? "" : " · offline"}`);
  }

  function updateBrowser(browser) {
    if (!browser) return;
    state.lastBrowser = browser;
    const browserState = browser.state || "disconnected";
    const page = browser.page || null;
    const loggedIn = browser.logged_in;

    state.connected = Boolean(browser.connected);
    autoCollapseConnection(loggedIn);

    const badgeMap = {
      disconnected: { label: "Browser off", kind: "muted", title: "Browser disconnected" },
      starting: { label: "Starting", kind: "warn", title: "Launching visible Chrome" },
      login_required: { label: "Login needed", kind: "warn", title: "Log in manually in Chrome" },
      ready: { label: "Moodle ready", kind: "ok", title: "Logged in and ready" },
      unknown: { label: "Browser open", kind: "warn", title: "Login state unknown" },
      error: { label: "Browser error", kind: "bad", title: browser.error || "Browser error" },
    };
    const info = badgeMap[browserState] || badgeMap.unknown;
    setBadge(els.browserBadge, info.label, info.kind, browser.error || info.title);

    const connMap = {
      disconnected: { label: "disconnected", cls: "state-off" },
      starting: { label: "starting…", cls: "state-warn" },
      login_required: { label: "login required", cls: "state-warn" },
      ready: { label: "logged in", cls: "state-on" },
      unknown: { label: "check login", cls: "state-warn" },
      error: { label: "error", cls: "state-bad" },
    };
    let conn = connMap[browserState] || connMap.unknown;
    if (state.syncing && browserState === "ready") conn = { label: "syncing…", cls: "state-warn" };
    if (els.connState) {
      els.connState.className = `state ${conn.cls}`;
      els.connState.textContent = conn.label;
    }

    setText(els.pageTitle, page?.title || browser.title || (browserState === "disconnected" ? "No page open" : "Moodle"));
    setText(els.pageUrl, page?.url || browser.url || "—");

    const hint = els.loginHint;
    if (hint) {
      hint.classList.remove("warn", "ok", "bad");
      switch (browserState) {
        case "ready":
          hint.textContent =
            "Logged in. The agent can now read your courses and assignments in the visible window.";
          hint.classList.add("ok");
          break;
        case "login_required":
          hint.textContent =
            "Chrome is open. Log in to Moodle manually (including SSO/MFA). MoodleAI never sees or stores your password.";
          hint.classList.add("warn");
          break;
        case "starting":
          hint.textContent = "Launching visible Chrome with your persistent profile…";
          break;
        case "error":
          hint.textContent = browser.error || "The browser could not start. Check Chrome and try again.";
          hint.classList.add("bad");
          break;
        case "unknown":
          hint.textContent =
            browser.error ||
            "Chrome is open. If you are logged in, the agent will confirm it on the next read.";
          if (browser.error) hint.classList.add("warn");
          break;
        default:
          hint.textContent =
            "A visible Chrome window will open. Log in manually — this app never asks for or stores your password.";
          break;
      }
    }

    if (browser.error && browserState === "error") {
      els.browserBadge.title = browser.error;
    }

    if (browser.moodle_url && !els.moodleUrl.value) {
      els.moodleUrl.value = browser.moodle_url;
    }
  }

  async function refreshBrowserStatus() {
    if (browserPollBusy || document.hidden) return;
    browserPollBusy = true;
    try {
      const status = await api("/api/browser/status");
      updateBrowser(status);
      if (status?.moodle_url) {
        state.moodleUrl = status.moodle_url;
        if (!els.moodleUrl.value) els.moodleUrl.value = status.moodle_url;
      }
    } catch (_) {
      /* the SSE stream remains the primary channel */
    } finally {
      browserPollBusy = false;
    }
  }

  function scheduleBrowserRefresh() {
    window.clearTimeout(browserRefreshTimer);
    browserRefreshTimer = window.setTimeout(refreshBrowserStatus, 280);
  }

  function scheduleDataRefresh() {
    window.clearTimeout(dataRefreshTimer);
    dataRefreshTimer = window.setTimeout(refreshData, 400);
  }

  async function refreshData() {
    const results = await Promise.allSettled([
      api("/api/courses"),
      api("/api/assignments"),
      api("/api/workspace"),
    ]);
    if (results[0].status === "fulfilled") renderCourses(results[0].value.courses);
    if (results[1].status === "fulfilled") renderAssignments(results[1].value.assignments);
    if (results[2].status === "fulfilled") {
      state.files = results[2].value || state.files;
      renderFiles();
    }
  }

  // ---------- approvals ----------

  function renderApprovalBar() {
    if (!els.approvalBar) return;
    if (!state.currentApprovalId) {
      els.approvalBar.classList.add("hidden");
      els.approvalBar.replaceChildren();
      return;
    }
    const meta = state.approvals.get(state.currentApprovalId);
    els.approvalBar.replaceChildren();
    const text = document.createElement("span");
    text.textContent = `Approval required: ${humanize(meta?.action)}`;
    els.approvalBar.appendChild(text);
    els.approvalBar.classList.remove("hidden");
    setText(els.agentActivity, "Waiting for your approval");
  }

  function openApproval(meta) {
    if (!meta || !meta.approval_id) return;
    state.approvals.set(meta.approval_id, meta);
    if (state.currentApprovalId) {
      renderApprovalBar();
      return;
    }

    state.currentApprovalId = meta.approval_id;
    setText(els.approvalHeading, "Approval required");
    setText(
      els.approvalMessage,
      `The agent wants to ${humanize(meta.action).toLowerCase()}. Review the details before approving.`
    );
    if (els.approvalDetails) {
      els.approvalDetails.textContent = meta.details
        ? JSON.stringify(meta.details, null, 2)
        : "No extra details.";
    }
    els.approvalModal?.classList.remove("hidden");
    renderApprovalBar();
    setAgentStatus("waiting_approval", "Waiting for your approval");
    els.approveBtn?.focus();
  }

  function hideApprovalModal() {
    els.approvalModal?.classList.add("hidden");
    if (els.approvalDetails) els.approvalDetails.textContent = "";
  }

  function showNextApproval() {
    const next = state.approvals.keys().next();
    if (next.done) {
      state.currentApprovalId = null;
      hideApprovalModal();
      renderApprovalBar();
      return;
    }
    const meta = state.approvals.get(next.value);
    state.currentApprovalId = next.value;
    state.approvals.delete(next.value);
    openApproval(meta);
  }

  async function resolveApproval(approvalId, approved) {
    if (!approvalId) return;
    if (els.approveBtn) els.approveBtn.disabled = true;
    if (els.denyBtn) els.denyBtn.disabled = true;
    try {
      await api(`/api/approvals/${encodeURIComponent(approvalId)}`, {
        method: "POST",
        body: { approved },
      });
      state.approvals.delete(approvalId);
      if (state.currentApprovalId === approvalId) {
        state.currentApprovalId = null;
        hideApprovalModal();
      }
      showNextApproval();
      showToast(approved ? "Action approved." : "Action denied.", approved ? "ok" : undefined);
    } catch (error) {
      showToast(error.message, "bad");
    } finally {
      if (els.approveBtn) els.approveBtn.disabled = false;
      if (els.denyBtn) els.denyBtn.disabled = false;
    }
  }

  // ---------- chat actions ----------

  function autosize() {
    const input = els.messageInput;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  async function sendMessage(rawText) {
    const text = String(rawText ?? "").trim();
    if (!text) return;
    if (state.running) {
      showToast("The agent is already working. Stop it or wait for it to finish.");
      return;
    }
    if (isResetCommand(text)) {
      resetSavedData();
      return;
    }
    if (text.length > 8000) {
      showToast("That message is too long (maximum 8000 characters).", "bad");
      return;
    }

    appendUser(text);
    if (els.messageInput) {
      els.messageInput.value = "";
      autosize();
    }
    if (!state.conversationStarted || state.conversationId === null) {
      setText(els.chatTitle, truncate(text, 70));
      setText(els.chatSubtitle, "Local conversation · qwen3:8b");
    }
    state.conversationStarted = true;
    setRunning(true);
    setText(els.agentActivity, "Starting…");

    try {
      const response = await api("/api/chat", {
        method: "POST",
        body: { message: text, conversation_id: state.conversationId },
      });
      state.conversationId = response.conversation_id;
    } catch (error) {
      setRunning(false);
      if (error.status === 409) {
        showToast(error.message);
      } else {
        appendError(error.message);
      }
      setText(els.agentActivity, "Ready");
    }
  }

  async function stopAgent() {
    if (!state.running) return;
    els.stopBtn.disabled = true;
    try {
      await api("/api/agent/stop", { method: "POST", body: {} });
      setText(els.agentActivity, "Stopping after the current step…");
      showToast("Stop requested.");
    } catch (error) {
      showToast(error.message, "bad");
      els.stopBtn.disabled = false;
    }
  }

  function newChat() {
    if (state.running) {
      showToast("Stop the agent before starting a new chat.");
      return;
    }
    state.conversationId = null;
    state.lastAssistantEl = null;
    state.lastUserText = "";
    state.streamingEl = null;
    state.streamingText = "";
    state.pendingTools = [];
    restoreWelcome();
    setText(els.chatTitle, defaultChatTitle);
    setText(els.chatSubtitle, defaultChatSubtitle);
    setText(els.agentActivity, "Ready");
    els.messageInput?.focus();
  }

  // ---------- /reset ----------

  const RESET_COMMAND = "/reset";

  function isResetCommand(text) {
    return String(text ?? "").trim().toLowerCase() === RESET_COMMAND;
  }

  function resetSummary(summary) {
    if (!summary) return "Saved data cleared.";
    const parts = [
      plural(Number(summary.courses) || 0, "course"),
      plural(Number(summary.assignments) || 0, "assignment"),
      plural(Number(summary.conversations) || 0, "conversation"),
    ];
    const files = (Number(summary.workspace_files) || 0) + (Number(summary.generated_files) || 0);
    if (files) parts.push(plural(files, "file"));
    return `Cleared ${parts.join(", ")}.`;
  }

  function applyLocalReset() {
    state.conversationId = null;
    state.conversationStarted = false;
    state.lastAssistantEl = null;
    state.lastUserText = "";
    state.streamingEl = null;
    state.streamingText = "";
    state.pendingTools = [];
    state.approvals.clear();
    state.currentApprovalId = null;
    state.files = { workspace: [], generated: [] };

    hideApprovalModal();
    renderApprovalBar();
    renderCourses([]);
    renderAssignments([]);
    renderFiles();
    renderActivity([]);
    restoreWelcome();

    setText(els.chatTitle, defaultChatTitle);
    setText(els.chatSubtitle, defaultChatSubtitle);
    setText(els.agentActivity, "Ready");
    setRunning(false);
  }

  async function resetSavedData() {
    const confirmed = window.confirm(
      [
        "Clear all saved Moodle data?",
        "",
        "This removes courses, assignments, chats, activity and workspace files.",
        "Your Moodle login in Chrome is kept. This cannot be undone.",
      ].join("\n")
    );
    if (!confirmed) {
      showToast("Reset cancelled.");
      els.messageInput?.focus();
      return;
    }

    appendUser(RESET_COMMAND);
    if (els.messageInput) {
      els.messageInput.value = "";
      autosize();
    }
    setRunning(true);
    setText(els.agentActivity, "Clearing saved data…");
    resetEchoUntil = Date.now() + 3000;

    try {
      const summary = await api("/api/reset", { method: "POST", body: {} });
      applyLocalReset();
      refreshData();
      refreshActivity();
      showToast(resetSummary(summary), "ok");
    } catch (error) {
      setRunning(false);
      appendError(error.message);
      setText(els.agentActivity, "Ready");
    }
  }

  // ---------- SSE ----------

  function handleEvent(event) {
    if (!event || !event.type) return;
    const { type, message, data } = event;

    switch (type) {
      case "snapshot": {
        if (data) {
          updateBrowser(data.browser);
          renderSync(data.sync);
          if (data.moodle_url) {
            state.moodleUrl = data.moodle_url;
            if (!els.moodleUrl.value) els.moodleUrl.value = data.moodle_url;
          }
          if (data.agent) {
            setAgentStatus(data.agent.status, data.agent.status === "idle" ? "Ready" : undefined);
            if (data.agent.running) setRunning(true);
          }
          (data.approvals || []).forEach((meta) => openApproval(meta));
        }
        break;
      }

      case "user_message": {
        const alreadyShown =
          state.lastUserText === message &&
          els.messages?.lastElementChild?.classList.contains("user");
        if (!alreadyShown) appendUser(message);
        state.lastUserText = message;
        break;
      }

      case "assistant_delta": {
        if (!message) break;
        const bubble = ensureStreaming();
        state.streamingText += message;
        bubble.textContent = state.streamingText;
        scrollToBottom(false);
        break;
      }

      case "assistant_message": {
        finalizeStreaming(message);
        break;
      }

      case "tool_start": {
        appendToolRow(data?.tool || "tool", data?.arguments);
        break;
      }

      case "tool_result": {
        finishToolRow(data, message);
        if (data && (data.ok === false || data.error)) {
          addActivity(
            {
              kind: "error",
              level: "error",
              message: message || data.error || "Tool failed",
              created_at: event.ts,
            },
            true
          );
        }
        scheduleDataRefresh();
        scheduleBrowserRefresh();
        break;
      }

      case "status": {
        const status = data?.status;
        if (status) setAgentStatus(status, status === "idle" ? "Ready" : message);
        else if (message) setText(els.agentActivity, message);
        break;
      }

      case "approval_required": {
        if (data) openApproval(data);
        addActivity(
          { kind: "approval_required", level: "info", message: message || "Approval required", created_at: event.ts },
          true
        );
        break;
      }

      case "approval_resolved": {
        if (data?.approval_id) {
          state.approvals.delete(data.approval_id);
          if (state.currentApprovalId === data.approval_id) {
            state.currentApprovalId = null;
            hideApprovalModal();
            showNextApproval();
          } else {
            renderApprovalBar();
          }
        }
        break;
      }

      case "approval_timeout": {
        if (data?.approval_id) {
          state.approvals.delete(data.approval_id);
          if (state.currentApprovalId === data.approval_id) {
            state.currentApprovalId = null;
            hideApprovalModal();
            showNextApproval();
          }
        }
        showToast("The approval timed out, so the action was denied.", "bad");
        break;
      }

      case "browser": {
        scheduleBrowserRefresh();
        addActivity({ kind: "browser", level: "info", message, created_at: event.ts }, true);
        break;
      }

      case "sync": {
        renderSync(data);
        addActivity({ kind: "sync", level: "info", message, created_at: event.ts }, true);
        break;
      }

      case "sync_done": {
        renderSync(data?.sync || data);
        addActivity(
          {
            kind: "sync",
            level: data?.ok === false ? "error" : "info",
            message,
            created_at: event.ts,
          },
          true
        );
        scheduleDataRefresh();
        showToast(message, data?.ok === false ? "bad" : "ok");
        break;
      }

      case "navigate": {
        if (els.pageUrl && data?.url) setText(els.pageUrl, data.url);
        scheduleBrowserRefresh();
        addActivity({ kind: "navigate", level: "info", message, created_at: event.ts }, true);
        break;
      }

      case "reading":
      case "download":
      case "file": {
        addActivity({ kind: type, level: "info", message, created_at: event.ts }, true);
        if (type === "file" || type === "download") scheduleDataRefresh();
        break;
      }

      case "warning": {
        addActivity({ kind: "warning", level: "info", message, created_at: event.ts }, true);
        break;
      }

      case "done": {
        finalizeStreaming();
        setRunning(false);
        if (data) {
          const parts = [];
          if (data.steps) parts.push(plural(data.steps, "model round"));
          if (data.tool_calls) parts.push(plural(data.tool_calls, "tool call"));
          addMessageMeta(state.lastAssistantEl, parts.join(" · "));
        }
        setText(els.agentActivity, "Ready");
        scheduleDataRefresh();
        break;
      }

      case "stopped": {
        finalizeStreaming();
        appendNotice("Stopped. You can ask a smaller next step.");
        setRunning(false);
        setText(els.agentActivity, "Stopped");
        addActivity({ kind: "stopped", level: "info", message: message || "Agent stopped", created_at: event.ts }, true);
        break;
      }

      case "draft": {
        addActivity({ kind: "draft", level: "info", message, created_at: event.ts }, true);
        if (data?.state === "done") {
          showToast(message || "Draft finished.", data.files ? "ok" : undefined);
          scheduleDataRefresh();
        }
        if (state.draftId !== null && data?.assignment_id === state.draftId) {
          loadDraft(state.draftId).catch(() => {});
        }
        break;
      }

      case "reset": {
        closeDraft();
        applyLocalReset();
        refreshData();
        refreshActivity();
        if (Date.now() >= resetEchoUntil) {
          showToast(message || "Saved data cleared.", "ok");
        }
        break;
      }

      case "error": {
        const wasRunning = state.running;
        finalizeStreaming();
        if (wasRunning) appendError(message || "Something went wrong.");
        else showToast(message || "Something went wrong.", "bad");
        setText(els.agentActivity, "Error — see the chat");
        addActivity({ kind: "error", level: "error", message, created_at: event.ts }, true);
        break;
      }

      default:
        break;
    }
  }

  function connectEvents() {
    if (!window.EventSource) {
      appendError("This browser does not support live updates. Use a current Chrome or Edge build.");
      return;
    }

    const source = new EventSource("/api/events");

    source.onopen = () => {
      if (state.sseFailed) {
        state.sseFailed = false;
        showToast("Reconnected to the local server.", "ok");
      }
    };

    source.onmessage = (event) => {
      if (!event.data) return;
      let parsed;
      try {
        parsed = JSON.parse(event.data);
      } catch (_) {
        return;
      }
      handleEvent(parsed);
    };

    source.onerror = () => {
      if (!state.sseFailed) {
        state.sseFailed = true;
        showToast("Live connection lost — retrying…", "bad");
      }
    };
  }

  // ---------- initial state ----------

  async function hydrate() {
    try {
      const snapshot = await api("/api/state");
      updateOllama(snapshot.ollama);
      updateBrowser(snapshot.browser);
      renderSync(snapshot.sync);
      renderCourses(snapshot.courses);
      renderAssignments(snapshot.assignments);
      state.files = snapshot.files || state.files;
      renderFiles();
      renderActivity(snapshot.activity);

      state.tools = snapshot.tools || [];
      setText(
        els.toolCount,
        state.tools.length
          ? `${plural(state.tools.length, "tool")} · loopback 127.0.0.1`
          : "loopback 127.0.0.1"
      );

      if (snapshot.moodle_url) {
        state.moodleUrl = snapshot.moodle_url;
        if (els.moodleUrl) els.moodleUrl.value = snapshot.moodle_url;
      }

      if (snapshot.agent) {
        setAgentStatus(snapshot.agent.status);
        if (snapshot.agent.running) setRunning(true);
      } else {
        setAgentStatus("idle", "Ready");
      }

      (snapshot.approvals || []).forEach((meta) => openApproval(meta));

      const fileCount =
        (snapshot.files?.workspace?.length || 0) + (snapshot.files?.generated?.length || 0);
      if (fileCount && state.activeTab === "workspace" && !snapshot.files?.workspace?.length) {
        // no-op: keep workspace selected so the empty state stays predictable
      }
    } catch (error) {
      appendError(
        `${error.message}\n\nStart the dashboard with \`python main.py\`, then reload this page.`
      );
      setText(els.agentActivity, "Offline");
    }
  }

  // ---------- wiring ----------

  function bind() {
    els.themeBtn?.addEventListener("click", () => {
      const next = root.dataset.theme === "dark" ? "light" : "dark";
      applyTheme(next);
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch (_) {
        /* ignore */
      }
    });

    media.addEventListener?.("change", (event) => {
      let saved = null;
      try {
        saved = localStorage.getItem(THEME_KEY);
      } catch (_) {
        saved = null;
      }
      if (!saved) applyTheme(event.matches ? "dark" : "light");
    });

    els.composer?.addEventListener("submit", (event) => {
      event.preventDefault();
      sendMessage(els.messageInput?.value);
    });

    els.messageInput?.addEventListener("input", autosize);
    els.messageInput?.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage(els.messageInput.value);
      }
    });

    els.messages?.addEventListener("click", (event) => {
      const suggestion = event.target.closest(".suggestion");
      if (!suggestion) return;
      const prompt = suggestion.dataset.prompt;
      if (prompt) sendMessage(prompt);
    });

    els.stopBtn?.addEventListener("click", stopAgent);
    els.newChatBtn?.addEventListener("click", newChat);

    els.connectBtn?.addEventListener("click", () => {
      const raw = (els.moodleUrl?.value || "").trim();
      if (!raw) {
        showToast("Enter your Moodle URL first.", "bad");
        els.moodleUrl?.focus();
        return;
      }
      let candidate = raw;
      if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(candidate)) candidate = `https://${candidate}`;
      let parsed;
      try {
        parsed = new URL(candidate);
        if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
          throw new Error("Only http and https addresses are allowed.");
        }
      } catch (error) {
        showToast(error.message || "That does not look like a valid URL.", "bad");
        return;
      }

      withLoading(els.connectBtn, async () => {
        try {
          // Optional HTTP Basic credentials for campuses that front Moodle with a
          // popup login. They go to Chrome via the backend and are never stored:
          // the password field is cleared as soon as the request returns.
          const httpUsername = (els.httpUser?.value || "").trim();
          const httpPassword = els.httpPass?.value || "";
          const status = await api("/api/browser/connect", {
            method: "POST",
            body: {
              url: parsed.href,
              http_username: httpUsername,
              http_password: httpPassword,
            },
          });
          if (els.httpPass) els.httpPass.value = "";
          updateBrowser(status);
          showToast("Chrome opened. Log in to Moodle manually.");
          scheduleBrowserRefresh();
        } catch (error) {
          showToast(error.message, "bad");
          if (els.loginHint) {
            els.loginHint.textContent = error.message;
            els.loginHint.classList.remove("ok", "warn");
            els.loginHint.classList.add("bad");
          }
        }
      });
    });

    els.moodleUrl?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        els.connectBtn?.click();
      }
    });

    els.checkLoginBtn?.addEventListener("click", () => {
      withLoading(els.checkLoginBtn, async () => {
        try {
          const status = await api("/api/browser/check-login", {
            method: "POST",
            body: {},
          });
          updateBrowser(status);
          if (status.logged_in === true) showToast("Moodle login confirmed.", "ok");
          else if (status.logged_in === false) {
            showToast("Still waiting for login in Chrome.", "bad");
          } else showToast("Login state is unclear — try opening a course.");
        } catch (error) {
          showToast(error.message, "bad");
        }
      });
    });

    els.syncBtn?.addEventListener("click", () => {
      withLoading(els.syncBtn, async () => {
        try {
          const result = await api("/api/sync", { method: "POST", body: {} });
          renderSync(result.sync);
          showToast(result.started ? "Reading Moodle…" : "A sync is already running.");
        } catch (error) {
          showToast(error.message, "bad");
        }
      });
    });

    els.disconnectBtn?.addEventListener("click", () => {
      withLoading(els.disconnectBtn, async () => {
        try {
          const status = await api("/api/browser/disconnect", {
            method: "POST",
            body: {},
          });
          updateBrowser(status);
          showToast("Browser disconnected.");
        } catch (error) {
          showToast(error.message, "bad");
        }
      });
    });

    els.refreshCourses?.addEventListener("click", () => {
      withLoading(els.refreshCourses, async () => {
        try {
          const data = await api("/api/courses");
          renderCourses(data.courses);
          showToast(`${plural((data.courses || []).length, "course")} loaded.`);
        } catch (error) {
          showToast(error.message, "bad");
        }
      });
    });

    els.refreshAssignments?.addEventListener("click", () => {
      withLoading(els.refreshAssignments, async () => {
        try {
          const data = await api("/api/assignments");
          renderAssignments(data.assignments);
          showToast(`${plural((data.assignments || []).length, "assignment")} loaded.`);
        } catch (error) {
          showToast(error.message, "bad");
        }
      });
    });

    els.refreshFiles?.addEventListener("click", () => {
      withLoading(els.refreshFiles, async () => {
        try {
          const data = await api("/api/workspace");
          state.files = data || state.files;
          renderFiles();
          showToast("File list refreshed.");
        } catch (error) {
          showToast(error.message, "bad");
        }
      });
    });

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        state.activeTab = tab.dataset.tab === "generated" ? "generated" : "workspace";
        document.querySelectorAll(".tab").forEach((item) => {
          const active = item === tab;
          item.classList.toggle("active", active);
          item.setAttribute("aria-selected", active ? "true" : "false");
        });
        renderFiles();
      });
    });

    els.approveBtn?.addEventListener("click", () => {
      if (state.currentApprovalId) resolveApproval(state.currentApprovalId, true);
    });

    els.denyBtn?.addEventListener("click", () => {
      if (state.currentApprovalId) resolveApproval(state.currentApprovalId, false);
    });

    els.draftCloseBtn?.addEventListener("click", closeDraft);
    els.draftStartBtn?.addEventListener("click", startDraft);
    els.draftReviewBtn?.addEventListener("click", markReviewed);
    els.draftModal?.addEventListener("click", (event) => {
      if (event.target === els.draftModal) closeDraft();
    });
    document.addEventListener("keydown", (event) => {
      // Escape closes the draft dialog only; the approval dialog stays blocking.
      if (event.key === "Escape" && state.draftId !== null && !state.currentApprovalId) closeDraft();
    });

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshBrowserStatus();
      }
    });

    window.setInterval(refreshBrowserStatus, 5000);
  }

  // ---------- start ----------

  initCards();
  renderSuggestions();
  bind();
  setRunning(false);
  setAgentStatus("idle", "Ready");
  autosize();
  hydrate().then(connectEvents);
})();

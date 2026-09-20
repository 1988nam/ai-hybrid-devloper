const $ = (id) => document.getElementById(id);

const state = {
  projects: [],
  project: null,
  status: null,
  poller: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function toast(message, isError = false) {
  const el = $("toast");
  el.textContent = message;
  el.className = `toast show${isError ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.className = "toast"; }, 3000);
}

function currentProject() {
  return state.projects.find((project) => project.name === state.project);
}

async function loadProjects() {
  const health = await api("/api/health");
  $("healthDot").classList.add("ok");
  $("healthText").textContent = `Factory ready · ${health.factory_home}`;

  const data = await api("/api/projects");
  state.projects = data.projects || [];
  const select = $("projectSelect");
  select.innerHTML = state.projects
    .map((project) => `<option value="${escapeHtml(project.name)}">${escapeHtml(project.name)}</option>`)
    .join("");
  state.project = select.value || null;
  syncProjectMeta();
  if (state.project) await refreshStatus();
}

function syncProjectMeta() {
  const project = currentProject();
  $("developerModel").textContent = project?.developer_model || "-";
  $("reviewerModel").textContent = project?.reviewer_model || "-";
}

function statusLabel(status) {
  return String(status || "not_started").replaceAll("_", " ").toUpperCase();
}

function renderStatus(payload) {
  state.status = payload;
  const stories = payload.stories || [];
  const completed = stories.filter((story) => story.status === "complete").length;
  const total = stories.length;
  const percent = total ? Math.round((completed / total) * 100) : 0;

  $("progressBar").style.width = `${percent}%`;
  $("progressText").textContent = `${completed} / ${total}`;
  $("statusBadge").textContent = statusLabel(payload.status);
  $("statusBadge").className = `badge ${
    payload.status === "complete"
      ? "success"
      : payload.status === "failed"
        ? "danger"
        : "neutral"
  }`;
  $("runTitle").textContent = payload.run?.run_id ? `Run ${payload.run.run_id}` : "아직 실행 없음";
  $("branchValue").textContent = payload.run?.branch || "-";
  $("currentValue").textContent = payload.inflight
    ? `${payload.inflight.id} · ${payload.inflight.phase}`
    : (payload.status === "complete" ? "Complete" : "-");
  $("processValue").textContent = payload.process?.alive
    ? `PID ${payload.process.pid} · running`
    : "idle";

  const list = $("storyList");
  if (!stories.length) {
    list.className = "story-list empty-state";
    list.textContent = "실행이 시작되면 Story 진행 상황이 표시됩니다.";
    return;
  }

  list.className = "story-list";
  list.innerHTML = stories.map((story, index) => {
    const icon = story.status === "complete"
      ? "✓"
      : story.status === "pending"
        ? String(index + 1)
        : "↻";
    return `<div class="story-row ${escapeHtml(story.status)}">
      <div class="story-icon">${icon}</div>
      <div><strong>${escapeHtml(story.id)}</strong><span>${escapeHtml(story.title || "")}</span></div>
      <em>${escapeHtml(statusLabel(story.status))}</em>
    </div>`;
  }).join("");
}

async function refreshStatus() {
  if (!state.project) return;
  try {
    renderStatus(await api(`/api/projects/${encodeURIComponent(state.project)}/status`));
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshLogs() {
  if (!state.project) return;
  try {
    const data = await api(`/api/projects/${encodeURIComponent(state.project)}/logs`);
    $("logOutput").textContent = data.log || "No logs yet.";
    $("logOutput").scrollTop = $("logOutput").scrollHeight;
  } catch (error) {
    toast(error.message, true);
  }
}

async function refreshDiff() {
  if (!state.project) return;
  try {
    const data = await api(`/api/projects/${encodeURIComponent(state.project)}/diff`);
    $("diffOutput").textContent = data.diff || "No diff yet.";
    $("diffStat").textContent = data.stat || data.branch || "";
  } catch (error) {
    toast(error.message, true);
  }
}

function parseStories() {
  const raw = $("storiesInput").value.trim();
  if (!raw) throw new Error("Story JSON을 입력하세요.");
  const stories = JSON.parse(raw);
  if (!Array.isArray(stories) || !stories.length) {
    throw new Error("Story는 비어 있지 않은 JSON 배열이어야 합니다.");
  }
  return stories;
}

function validateStoriesUi() {
  try {
    const stories = parseStories();
    $("storyValidation").textContent = `${stories.length} stories ready`;
    $("storyValidation").className = "hint ok";
    return true;
  } catch (error) {
    $("storyValidation").textContent = error.message;
    $("storyValidation").className = "hint";
    return false;
  }
}

async function startRun() {
  try {
    const stories = parseStories();
    const result = await api(
      `/api/projects/${encodeURIComponent(state.project)}/runs`,
      {
        method: "POST",
        body: JSON.stringify({
          title: $("titleInput").value,
          design_md: $("designInput").value,
          stories,
        }),
      },
    );
    toast(`Started ${result.package.story_count} stories`);
    await refreshStatus();
    switchView("logs");
  } catch (error) {
    toast(error.message, true);
  }
}

async function postAction(action) {
  if (!state.project) return;
  try {
    await api(
      `/api/projects/${encodeURIComponent(state.project)}/${action}`,
      { method: "POST", body: "{}" },
    );
    toast(`${action} requested`);
    await refreshStatus();
  } catch (error) {
    toast(error.message, true);
  }
}

function switchView(view) {
  document
    .querySelectorAll(".view")
    .forEach((el) => el.classList.toggle("active", el.id === `view-${view}`));
  document
    .querySelectorAll(".nav-item")
    .forEach((el) => el.classList.toggle("active", el.dataset.view === view));

  if (view === "logs") refreshLogs();
  if (view === "diff") refreshDiff();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadFile(input, target) {
  const file = input.files?.[0];
  if (!file) return;
  $(target).value = await file.text();
  if (target === "storiesInput") validateStoriesUi();
}

function bind() {
  $("projectSelect").addEventListener("change", async (event) => {
    state.project = event.target.value;
    syncProjectMeta();
    await refreshStatus();
  });
  $("designFile").addEventListener("change", (event) => loadFile(event.target, "designInput"));
  $("storiesFile").addEventListener("change", (event) => loadFile(event.target, "storiesInput"));
  $("storiesInput").addEventListener("input", validateStoriesUi);
  $("startBtn").addEventListener("click", startRun);
  $("resumeBtn").addEventListener("click", () => postAction("resume"));
  $("stopBtn").addEventListener("click", () => postAction("stop"));
  $("refreshLogsBtn").addEventListener("click", refreshLogs);
  $("refreshDiffBtn").addEventListener("click", refreshDiff);
  $("openVscodeBtn").addEventListener("click", () => postAction("open-vscode"));
  document
    .querySelectorAll(".nav-item")
    .forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
}

async function boot() {
  bind();
  try {
    await loadProjects();
  } catch (error) {
    $("healthText").textContent = error.message;
    toast(error.message, true);
  }

  state.poller = setInterval(async () => {
    await refreshStatus();
    if (document.querySelector("#view-logs.active")) await refreshLogs();
  }, 2500);
}

boot();

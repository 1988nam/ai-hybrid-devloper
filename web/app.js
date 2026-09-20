const $ = (id) => document.getElementById(id);

const state = {
  projects: [],
  project: null,
  status: null,
  poller: null,
  planners: [],
  plannerProvider: "codex",
  plannerLoginPoller: null,
  plannerModel: localStorage.getItem("aiHybridCodexModel") || "",
  plannerReasoningEffort:
    localStorage.getItem("aiHybridCodexReasoningEffort") || "",
  planning: false,
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
  toast.timer = setTimeout(() => { el.className = "toast"; }, 3500);
}

function currentProject() {
  return state.projects.find((project) => project.name === state.project);
}

function currentPlanner() {
  return state.planners.find((planner) => planner.id === state.plannerProvider);
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

async function loadPlanners() {
  try {
    const data = await api("/api/planners");
    state.planners = data.providers || [];
    renderPlanner();
  } catch (error) {
    state.planners = [];
    renderPlanner(error.message);
  }
}

function syncProjectMeta() {
  const project = currentProject();
  $("developerModel").textContent = project?.developer_model || "-";
  $("reviewerModel").textContent = project?.reviewer_model || "-";
}

function plannerConnectionLabel(planner) {
  if (!planner) return "Provider status unavailable";
  if (!planner.installed) return planner.detail || "CLI not installed";
  if (!planner.connected) return planner.detail || "Not connected";

  const parts = [];
  if (planner.plan) parts.push(planner.plan);
  if (planner.email) parts.push(planner.email);
  if (planner.auth_mode) parts.push(planner.auth_mode);
  return parts.length ? parts.join(" · ") : (planner.detail || "Connected");
}

function persistPlannerModel(value) {
  state.plannerModel = value || "";
  localStorage.setItem("aiHybridCodexModel", state.plannerModel);
}

function persistPlannerReasoningEffort(value) {
  state.plannerReasoningEffort = value || "";
  localStorage.setItem(
    "aiHybridCodexReasoningEffort",
    state.plannerReasoningEffort,
  );
}

function renderPlannerModel(planner) {
  const row = $("codexModelRow");
  const select = $("plannerModelSelect");
  const custom = $("plannerCustomModel");
  const hint = $("plannerModelHint");

  const isCodex = state.plannerProvider === "codex";
  row.classList.toggle("hidden", !isCodex);
  if (!isCodex) return;

  const models = Array.isArray(planner?.models) ? planner.models : [];
  const selected = state.plannerModel || "";
  const knownIds = new Set(models.map((item) => item.id));
  const customSelected = Boolean(selected) && !knownIds.has(selected);

  select.innerHTML = [
    '<option value="">Codex default</option>',
    ...models.map(
      (item) =>
        `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label || item.id)}</option>`,
    ),
    '<option value="__custom__">Custom model ID…</option>',
  ].join("");

  select.value = customSelected ? "__custom__" : selected;
  custom.classList.toggle("hidden", !customSelected);
  custom.value = customSelected ? selected : "";

  hint.textContent = selected
    ? `설계 생성에 ${selected} 모델을 사용합니다.`
    : "Codex CLI의 기본 모델을 사용합니다.";

  const effortSelect = $("plannerEffortSelect");
  const effortHint = $("plannerEffortHint");
  const efforts = Array.isArray(planner?.reasoning_efforts)
    ? planner.reasoning_efforts
    : [];
  const effort = state.plannerReasoningEffort || "";

  if (efforts.length) {
    effortSelect.innerHTML = efforts
      .map(
        (item) =>
          `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label || item.id || "Codex default")}</option>`,
      )
      .join("");
  }

  effortSelect.value = effort;
  if (effortSelect.value !== effort) {
    effortSelect.value = "";
    persistPlannerReasoningEffort("");
  }

  effortHint.textContent = state.plannerReasoningEffort
    ? `추론 수준: ${state.plannerReasoningEffort}`
    : "모델의 기본 추론 수준을 사용합니다.";
}

function renderPlanner(error = "") {
  document.querySelectorAll(".provider-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.provider === state.plannerProvider);
  });

  const planner = currentPlanner();
  renderPlannerModel(planner);
  const dot = $("plannerStatusDot");
  const title = $("plannerStatusTitle");
  const detail = $("plannerStatusDetail");
  const connect = $("plannerConnectBtn");
  const logout = $("plannerLogoutBtn");
  const generate = $("generatePlanBtn");
  const reslice = $("reslicePackageBtn");

  if (error) {
    dot.className = "provider-status-dot error";
    title.textContent = "Provider status error";
    detail.textContent = error;
    connect.disabled = false;
    generate.disabled = state.planning;
    reslice.disabled = state.planning;
    return;
  }

  if (!planner) {
    dot.className = "provider-status-dot";
    title.textContent = "Checking provider…";
    detail.textContent = "";
    connect.disabled = true;
    generate.disabled = true;
    reslice.disabled = true;
    return;
  }

  if (!planner.installed) {
    dot.className = "provider-status-dot error";
    title.textContent = `${planner.name} CLI not installed`;
  } else if (planner.connected) {
    dot.className = "provider-status-dot connected";
    title.textContent = `${planner.name} connected`;
  } else {
    dot.className = "provider-status-dot";
    title.textContent = `${planner.name} not connected`;
  }

  detail.textContent = plannerConnectionLabel(planner);
  connect.disabled = !planner.installed || state.planning;
  connect.textContent = planner.connected
    ? (state.plannerProvider === "codex" ? "Reconnect" : "Sign in again")
    : "Connect";

  logout.classList.toggle(
    "hidden",
    state.plannerProvider !== "codex" || !planner.connected,
  );
  logout.disabled = state.planning;

  generate.disabled = !planner.installed || !planner.connected || state.planning;
  reslice.disabled = !planner.installed || !planner.connected || state.planning;
}

function selectPlanner(provider) {
  if (!["codex", "gemini"].includes(provider)) return;
  state.plannerProvider = provider;
  renderPlanner();
}

async function connectPlanner() {
  const planner = currentPlanner();
  if (!planner?.installed) {
    toast(`${planner?.name || "Planner"} CLI가 설치되어 있지 않습니다.`, true);
    return;
  }

  try {
    if (state.plannerProvider === "codex") {
      $("plannerProgress").textContent = "ChatGPT OAuth를 시작합니다…";
      const result = await api("/api/planners/codex/connect", {
        method: "POST",
        body: "{}",
      });

      if (result.auth_url) {
        window.open(result.auth_url, "_blank", "noopener,noreferrer");
      }

      clearInterval(state.plannerLoginPoller);
      state.plannerLoginPoller = setInterval(pollCodexLogin, 1500);
      await pollCodexLogin();
      return;
    }

    $("plannerProgress").textContent =
      "Gemini CLI 로그인 창을 엽니다. 처음 한 번 Google 계정 로그인을 완료하세요.";
    const result = await api("/api/planners/gemini/connect", {
      method: "POST",
      body: JSON.stringify({ project: state.project }),
    });
    toast(result.detail || "Gemini login window opened");
  } catch (error) {
    $("plannerProgress").textContent = "Planner 연결 실패";
    toast(error.message, true);
  }
}

async function pollCodexLogin() {
  try {
    const result = await api("/api/planners/codex/login-status");
    if (result.pending) {
      $("plannerProgress").textContent =
        "ChatGPT 로그인 완료를 기다리는 중입니다. 브라우저 OAuth를 완료하세요.";
      return;
    }

    clearInterval(state.plannerLoginPoller);
    state.plannerLoginPoller = null;

    if (!result.connected) {
      throw new Error(result.error || "Codex ChatGPT login failed");
    }

    $("plannerProgress").textContent = "OpenAI Codex 연결 완료.";
    toast("OpenAI Codex connected");
    await loadPlanners();
  } catch (error) {
    clearInterval(state.plannerLoginPoller);
    state.plannerLoginPoller = null;
    $("plannerProgress").textContent = "Codex 로그인 상태 확인 실패";
    toast(error.message, true);
  }
}

async function logoutCodex() {
  try {
    await api("/api/planners/codex/logout", {
      method: "POST",
      body: "{}",
    });
    toast("Codex logged out");
    await loadPlanners();
  } catch (error) {
    toast(error.message, true);
  }
}

async function generatePlan() {
  if (!state.project) {
    toast("Project를 먼저 선택하세요.", true);
    return;
  }

  const requirement = $("plannerRequirement").value.trim();
  if (!requirement) {
    toast("요구사항을 입력하세요.", true);
    $("plannerRequirement").focus();
    return;
  }

  const planner = currentPlanner();
  if (!planner?.connected) {
    toast("선택한 Frontier Planner를 먼저 연결하세요.", true);
    return;
  }

  state.planning = true;
  renderPlanner();
  const button = $("generatePlanBtn");
  button.textContent = "Repository 분석 중…";
  $("plannerProgress").textContent =
    `${planner.name}이 repository를 read-only로 조사하고 설계와 Story를 생성하고 있습니다…`;

  try {
    const result = await api(
      `/api/projects/${encodeURIComponent(state.project)}/plan`,
      {
        method: "POST",
        body: JSON.stringify({
          provider: state.plannerProvider,
          requirement,
          model: state.plannerProvider === "codex" ? state.plannerModel : "",
          reasoning_effort:
            state.plannerProvider === "codex"
              ? state.plannerReasoningEffort
              : "",
        }),
      },
    );

    $("titleInput").value = result.title || "";
    $("designInput").value = result.design_markdown || "";
    $("storiesInput").value = JSON.stringify(result.stories || [], null, 2);
    validateStoriesUi();

    const modelLabel = result.model ? ` · ${result.model}` : "";
    const effortLabel = result.reasoning_effort
      ? ` · ${result.reasoning_effort}`
      : "";
    const budget = result.story_budget || {};
    const sliceLabel = budget.reslice_passes
      ? ` · 자동 재분해 ${budget.reslice_passes}회`
      : "";
    const budgetLabel = budget.status === "PASS"
      ? ` · local-fit PASS (max ${budget.hard_max_paths || "?"} files)`
      : "";

    $("plannerProgress").textContent =
      `${planner.name} 설계 완료${modelLabel}${effortLabel} · ${result.stories?.length || 0} stories${sliceLabel}${budgetLabel} · ${result.elapsed_seconds ?? "?"}s`;
    toast("설계서와 Story가 생성되었습니다.");
  } catch (error) {
    $("plannerProgress").textContent = "Frontier planning failed. 로그/연결 상태를 확인하세요.";
    toast(error.message, true);
  } finally {
    state.planning = false;
    button.textContent = "Repository 분석 + 설계 생성";
    renderPlanner();
  }
}

async function reslicePackage() {
  if (!state.project) {
    toast("Project를 먼저 선택하세요.", true);
    return;
  }

  const design = $("designInput").value.trim();
  if (!design) {
    toast("기존 MD 설계서를 먼저 불러오거나 붙여넣으세요.", true);
    $("designInput").focus();
    return;
  }

  let stories;
  try {
    stories = parseStories();
  } catch (error) {
    toast(error.message, true);
    $("storiesInput").focus();
    return;
  }

  const planner = currentPlanner();
  if (!planner?.connected) {
    toast("선택한 Frontier Planner를 먼저 연결하세요.", true);
    return;
  }

  state.planning = true;
  renderPlanner();
  const button = $("reslicePackageBtn");
  const originalLabel = button.textContent;
  button.textContent = "Story 재분할 중…";
  $("plannerProgress").textContent =
    `${planner.name}이 기존 MD를 잠그고 ${stories.length}개 Story를 Local Qwen용으로 다시 쪼개고 있습니다…`;

  try {
    const result = await api(
      `/api/projects/${encodeURIComponent(state.project)}/reslice`,
      {
        method: "POST",
        body: JSON.stringify({
          provider: state.plannerProvider,
          requirement: $("plannerRequirement").value.trim(),
          title: $("titleInput").value,
          design_md: design,
          stories,
          model: state.plannerProvider === "codex" ? state.plannerModel : "",
          reasoning_effort:
            state.plannerProvider === "codex"
              ? state.plannerReasoningEffort
              : "",
        }),
      },
    );

    $("titleInput").value = result.title || $("titleInput").value;
    $("designInput").value = result.design_markdown || design;
    $("storiesInput").value = JSON.stringify(result.stories || [], null, 2);
    validateStoriesUi();

    const budget = result.story_budget || {};
    const sourceCount = budget.source_story_count ?? stories.length;
    const finalCount = budget.story_count ?? result.stories?.length ?? 0;
    const passes = budget.reslice_passes ?? 0;
    $("plannerProgress").textContent =
      `${planner.name} 재분할 완료 · ${sourceCount} → ${finalCount} stories · ${passes} pass · local-fit ${budget.status || "?"}`;
    toast("기존 MD를 유지한 채 Story를 다시 쪼갰습니다.");
  } catch (error) {
    $("plannerProgress").textContent =
      "기존 Work Package 재분할 실패. Story budget 또는 provider 로그를 확인하세요.";
    toast(error.message, true);
  } finally {
    state.planning = false;
    button.textContent = originalLabel;
    renderPlanner();
  }
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

  document.querySelectorAll(".provider-tab").forEach((button) => {
    button.addEventListener("click", () => selectPlanner(button.dataset.provider));
  });

  $("plannerModelSelect").addEventListener("change", (event) => {
    const value = event.target.value;
    const custom = $("plannerCustomModel");

    if (value === "__custom__") {
      custom.classList.remove("hidden");
      custom.value = state.plannerModel;
      custom.focus();
      $("plannerModelHint").textContent = state.plannerModel
        ? `설계 생성에 ${state.plannerModel} 모델을 사용합니다.`
        : "Custom model ID를 입력하세요.";
      return;
    }

    custom.classList.add("hidden");
    custom.value = "";
    persistPlannerModel(value);
    renderPlannerModel(currentPlanner());
  });

  $("plannerCustomModel").addEventListener("input", (event) => {
    persistPlannerModel(event.target.value.trim());
    $("plannerModelHint").textContent = state.plannerModel
      ? `설계 생성에 ${state.plannerModel} 모델을 사용합니다.`
      : "Custom model ID를 입력하세요.";
  });

  $("plannerEffortSelect").addEventListener("change", (event) => {
    persistPlannerReasoningEffort(event.target.value);
    $("plannerEffortHint").textContent = state.plannerReasoningEffort
      ? `추론 수준: ${state.plannerReasoningEffort}`
      : "모델의 기본 추론 수준을 사용합니다.";
  });

  $("plannerRefreshBtn").addEventListener("click", loadPlanners);
  $("plannerConnectBtn").addEventListener("click", connectPlanner);
  $("plannerLogoutBtn").addEventListener("click", logoutCodex);
  $("generatePlanBtn").addEventListener("click", generatePlan);
  $("reslicePackageBtn").addEventListener("click", reslicePackage);

  document.querySelectorAll(".file-button").forEach((label) => {
    label.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        label.querySelector('input[type="file"]')?.click();
      }
    });
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
    await Promise.all([loadProjects(), loadPlanners()]);
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

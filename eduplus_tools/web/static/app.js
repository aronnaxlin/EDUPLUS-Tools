const form = document.getElementById("run-form");
const clearViewButton = document.getElementById("clear-view");
const clearLogButton = document.getElementById("clear-log");
const logView = document.getElementById("job-log");
const jobStatusBadge = document.getElementById("job-status");
const jobStatusValue = document.getElementById("job-status-value");
const jobId = document.getElementById("job-id");
const summary = document.getElementById("job-summary");
const healthBadge = document.getElementById("health-badge");
const downloadActions = document.getElementById("download-actions");
const downloadBundle = document.getElementById("download-bundle");
const artifactMeta = document.getElementById("artifact-meta");
const artifactList = document.getElementById("artifact-list");
const artifactCount = document.getElementById("artifact-count");
const latestArtifactName = document.getElementById("latest-artifact-name");
const latestArtifactHint = document.getElementById("latest-artifact-hint");
const executionMode = document.getElementById("execution-mode");
const modeTitle = document.getElementById("mode-title");
const modeCopy = document.getElementById("mode-copy");
const outputLabel = document.getElementById("output-label");
const outputInput = document.getElementById("output-input");
const sessionInput = form.querySelector('input[name="session"]');
const sessionLabel = document.getElementById("session-label");
const sessionHint = document.getElementById("session-hint");
const sessionSourceBadge = document.getElementById("session-source-badge");
const modeToggle = document.querySelector(".mode-toggle");
const modeButtons = Array.from(document.querySelectorAll(".mode-pill"));
const themeToggleButton = document.getElementById("theme-toggle");

let pollTimer = null;
let currentServerConfig = null;
const THEME_STORAGE_KEY = "eduplus-theme";
const SUMMARY_LABELS = {
  mode: "运行模式",
  course_id: "课程 ID",
  course_name: "课程名称",
  output: "输出目录",
  session_source: "SESSION 来源",
  session: "SESSION",
  artifacts: "输出文件",
  bundle: "打包结果",
};
const ERROR_MESSAGE_MAP = {
  "Failed to fetch artifacts": "获取输出文件失败",
  "health check failed": "服务检查失败",
  "Failed to load config": "读取服务配置失败",
  "Failed to create job": "提交任务失败",
  "Failed to fetch job": "获取任务状态失败",
  "invalid command": "不支持的任务类型",
  "local output mode is disabled on this server": "当前服务未开启本地输出模式",
  "job not found": "未找到任务",
  "job has no downloadable artifacts": "当前任务没有可下载文件",
  "invalid json body": "请求内容不是有效的 JSON",
  "json body must be an object": "请求内容必须是 JSON 对象",
};

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatCount(count) {
  return `${count} 个`;
}

function getCurrentTheme() {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function updateThemeToggle() {
  if (!themeToggleButton) {
    return;
  }

  const nextModeLabel = getCurrentTheme() === "dark" ? "切换到浅色模式" : "切换到夜间模式";
  themeToggleButton.setAttribute("aria-label", nextModeLabel);
  themeToggleButton.setAttribute("title", nextModeLabel);
}

function applyTheme(theme, persist = true) {
  const resolvedTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = resolvedTheme;
  updateThemeToggle();

  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, resolvedTheme);
    } catch (error) {
      // Ignore localStorage errors and keep the current in-memory theme.
    }
  }
}

function toggleTheme() {
  applyTheme(getCurrentTheme() === "dark" ? "light" : "dark");
}

function setBadge(element, text, kind = "muted") {
  element.className = `badge ${kind}`;
  element.textContent = text;
}

function setJobState(label, kind = "muted", value = label) {
  setBadge(jobStatusBadge, label, kind);
  jobStatusValue.textContent = value;
}

function renderLog(lines = []) {
  const entries = Array.isArray(lines) && lines.length ? lines : ["等待任务开始...", "当前为公共模式", "任务完成后可下载 ZIP"];
  logView.replaceChildren();
  entries.forEach((line, index) => {
    const row = document.createElement("div");
    const number = document.createElement("span");
    const text = document.createElement("span");
    row.className = "log-line";
    number.className = "log-line-number";
    text.className = "log-line-text";
    number.textContent = String(index + 1);
    text.textContent = String(line);
    row.append(number, text);
    logView.appendChild(row);
  });
  logView.scrollTop = logView.scrollHeight;
}

function humanizeKey(key) {
  return SUMMARY_LABELS[key] || key.replace(/_/g, " ");
}

function localizeMessage(message) {
  const text = String(message || "").trim();
  return ERROR_MESSAGE_MAP[text] || text;
}

function renderSummary(data = {}) {
  const entries = Object.entries(data).filter(([, value]) => value !== null && value !== undefined && value !== "");
  summary.replaceChildren();
  if (entries.length === 0) {
    summary.classList.add("hidden");
    return;
  }

  summary.classList.remove("hidden");
  for (const [key, value] of entries) {
    const row = document.createElement("div");
    const label = document.createElement("span");
    const strong = document.createElement("strong");
    row.className = "summary-row";
    label.textContent = humanizeKey(key);
    strong.textContent = localizeMessage(value);
    row.append(label, strong);
    summary.appendChild(row);
  }
}

function updateLatestArtifact(file = null, totalBytes = 0) {
  if (!file) {
    latestArtifactName.textContent = "暂无产物";
    latestArtifactHint.textContent = "任务完成后，产物将显示在这里。";
    return;
  }

  latestArtifactName.textContent = file.path;
  latestArtifactHint.textContent = `${formatBytes(file.size)}${totalBytes ? ` · 总计 ${formatBytes(totalBytes)}` : ""}`;
}

function setBusy(busy) {
  Array.from(form.elements).forEach((element) => {
    if (element instanceof HTMLButtonElement || element instanceof HTMLInputElement || element instanceof HTMLSelectElement) {
      if (element.id !== "clear-log") {
        element.disabled = busy;
      }
    }
  });

  modeButtons.forEach((button) => {
    button.disabled = busy;
  });

  clearViewButton.disabled = busy;
}

function resetArtifacts() {
  artifactCount.textContent = formatCount(0);
  artifactMeta.textContent = "等待任务完成后生成";
  downloadActions.classList.add("hidden");
  downloadBundle.removeAttribute("href");
  artifactList.classList.add("hidden");
  artifactList.replaceChildren();
  updateLatestArtifact();
}

function renderArtifacts(data = {}) {
  resetArtifacts();

  const count = Number(data.artifact_count) || 0;
  artifactCount.textContent = formatCount(count);
  artifactMeta.textContent = count ? formatBytes(data.total_bytes) : "等待任务完成后生成";

  if (!count) {
    return;
  }

  const files = Array.isArray(data.files) ? data.files : [];
  const latestFile = files[files.length - 1] || null;
  updateLatestArtifact(latestFile, data.total_bytes);

  if (data.bundle_url) {
    downloadBundle.href = data.bundle_url;
    downloadActions.classList.remove("hidden");
  }

  const title = document.createElement("p");
  title.className = "metric-label";
  title.textContent = "输出列表";

  const list = document.createElement("ul");
  files.forEach((file) => {
    const item = document.createElement("li");
    item.textContent = `${file.path} (${formatBytes(file.size)})`;
    list.appendChild(item);
  });

  artifactList.append(title, list);
  artifactList.classList.remove("hidden");
}

async function fetchArtifacts(id) {
  const response = await fetch(`/api/jobs/${id}/artifacts`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(localizeMessage(data.error || "Failed to fetch artifacts"));
  }
  return data;
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error("health check failed");
    }
    setBadge(healthBadge, "服务正常", "success");
  } catch {
    setBadge(healthBadge, "服务不可用", "failed");
  }
}

function syncModeButtons(mode) {
  modeButtons.forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", String(active));
  });

  if (modeToggle) {
    const visibleButtons = modeButtons.filter((button) => !button.classList.contains("is-hidden"));
    modeToggle.classList.toggle("is-single", visibleButtons.length === 1);
  }
}

async function loadServerConfig() {
  try {
    const response = await fetch("/api/config");
    const data = await response.json();
    if (!response.ok) {
      throw new Error(localizeMessage(data.error || "Failed to load config"));
    }

    if (!data.enable_local_output) {
      const localOption = executionMode.querySelector('option[value="local"]');
      if (localOption) {
        localOption.remove();
      }

      const localButton = modeButtons.find((button) => button.dataset.mode === "local");
      if (localButton) {
        localButton.classList.add("is-hidden");
      }
      executionMode.value = "public";
    }

    if (data.public_output_root && executionMode.value === "public") {
      outputInput.value = data.public_output_root;
    }

    currentServerConfig = data;
    applyExecutionMode(executionMode.value, data);
  } catch {
    applyExecutionMode(executionMode.value);
  }
}

function applyExecutionMode(mode, serverConfig = null) {
  currentServerConfig = serverConfig || currentServerConfig;
  executionMode.value = mode;
  syncModeButtons(mode);
  const hasServerSession = Boolean(currentServerConfig?.has_server_session);

  if (mode === "local") {
    modeTitle.textContent = "本地输出";
    modeCopy.textContent = hasServerSession
      ? "结果会直接写入你指定的目录，可留空 SESSION 并使用服务端配置，适合自己部署自己使用。"
      : "结果会直接写入你指定的目录，适合自己部署自己使用。";
    outputLabel.textContent = "输出目录";
    const defaultLocalRoot = currentServerConfig?.local_output_root || "downloads";
    if (!outputInput.value || outputInput.value === "downloads/web-jobs") {
      outputInput.value = defaultLocalRoot;
    }
    outputInput.placeholder = `直接写入的本地目录，例如 ${defaultLocalRoot}`;
    sessionLabel.textContent = hasServerSession ? "SESSION（可留空）" : "SESSION";
    sessionInput.required = !hasServerSession;
    sessionInput.placeholder = hasServerSession
      ? "可留空，留空时使用服务端 config.json 中的 SESSION"
      : "当前服务未配置默认 SESSION，请手动填写";
    sessionSourceBadge.textContent = hasServerSession ? "可用服务端配置" : "需要手动填写";
    sessionSourceBadge.dataset.kind = hasServerSession ? "server" : "manual";
    sessionHint.textContent = hasServerSession
      ? "仅适合自己部署自己使用。留空时会改用服务端配置中的 SESSION，不会展示原值。"
      : "当前服务端没有默认 SESSION，本地输出模式下仍需要你手动填写。";
    return;
  }

  modeTitle.textContent = "公共模式";
  modeCopy.textContent = "每次任务会单独处理，适合在公共环境中使用。需要填写你自己的 SESSION，ZIP 下载完成后会自动清理公共任务文件。";
  outputLabel.textContent = "输出目录";
  const defaultPublicRoot = currentServerConfig?.public_output_root || "downloads/web-jobs";
  if (!outputInput.value || outputInput.value === "downloads") {
    outputInput.value = defaultPublicRoot;
  }
  outputInput.placeholder = `任务隔离目录根，例如 ${defaultPublicRoot}`;
  sessionLabel.textContent = "SESSION（必填）";
  sessionInput.required = true;
  sessionInput.placeholder = "公共模式下请填写你自己的 EDUPLUS SESSION";
  sessionSourceBadge.textContent = "用户填写";
  sessionSourceBadge.dataset.kind = "manual";
  sessionHint.textContent = "公共模式不会读取服务端默认 SESSION。你填写的 SESSION 仅用于当前任务，不会写入服务端配置。";
}

function collectPayload() {
  const formData = new FormData(form);
  return {
    execution_mode: formData.get("execution_mode"),
    command: formData.get("command"),
    course_id: formData.get("course_id"),
    session: formData.get("session"),
    course_name: formData.get("course_name"),
    hm_lvt: formData.get("hm_lvt"),
    output: formData.get("output"),
    base_url: formData.get("base_url"),
    timeout: formData.get("timeout"),
    dry_run: formData.get("dry_run") === "on",
    overwrite: formData.get("overwrite") === "on",
    skip_existing_homework_convert: formData.get("skip_existing_homework_convert") === "on",
    verbose: formData.get("verbose") === "on",
  };
}

async function startJob(event) {
  event.preventDefault();
  const payload = collectPayload();
  setBusy(true);
  renderSummary({});
  resetArtifacts();
  renderLog(["正在提交任务...", "正在检查参数...", `当前模式: ${payload.execution_mode === "local" ? "本地输出" : "公共模式"}`]);
  setJobState("等待中", "running", "等待中");

  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(localizeMessage(data.error || "Failed to create job"));
    }

    jobId.textContent = `任务 ${data.job_id}`;
    pollJob(data.job_id);
  } catch (error) {
    renderLog([localizeMessage(error instanceof Error ? error.message : String(error))]);
    setJobState("失败", "failed", "失败");
    setBusy(false);
  }
}

async function pollJob(id) {
  clearInterval(pollTimer);

  const tick = async () => {
    try {
      const response = await fetch(`/api/jobs/${id}`);
      const data = await response.json();
      if (!response.ok) {
        throw new Error(localizeMessage(data.error || "Failed to fetch job"));
      }

      renderSummary(data.summary);
      renderLog(data.logs);

      const status = data.status;
      if (status === "completed") {
        setJobState(data.exit_code === 0 ? "已完成" : "已完成（有错误）", data.exit_code === 0 ? "success" : "failed", data.exit_code === 0 ? "完成" : "检查日志");
        renderArtifacts(await fetchArtifacts(id));
        clearInterval(pollTimer);
        setBusy(false);
        return;
      }

      if (status === "failed") {
        setJobState("失败", "failed", "失败");
        clearInterval(pollTimer);
        setBusy(false);
        return;
      }

      setJobState(status === "running" ? "运行中" : "等待中", "running", status === "running" ? "运行中" : "等待中");
    } catch (error) {
      setJobState("轮询失败", "failed", "错误");
      renderLog([`获取任务状态失败：${localizeMessage(error instanceof Error ? error.message : String(error))}`]);
      clearInterval(pollTimer);
      setBusy(false);
    }
  };

  await tick();
  pollTimer = setInterval(tick, 1200);
}

function resetView() {
  clearInterval(pollTimer);
  renderSummary({});
  resetArtifacts();
  applyExecutionMode(executionMode.value);
  jobId.textContent = "暂无任务";
  setJobState("空闲", "muted", "空闲");
  renderLog();
}

function clearLog() {
  renderLog(["日志已清空"]);
}

form.addEventListener("submit", startJob);
clearViewButton.addEventListener("click", resetView);
clearLogButton.addEventListener("click", clearLog);
if (themeToggleButton) {
  themeToggleButton.addEventListener("click", toggleTheme);
}

modeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    if (button.disabled || button.classList.contains("is-hidden")) {
      return;
    }
    applyExecutionMode(button.dataset.mode);
  });
});

renderLog();
resetArtifacts();
updateThemeToggle();
applyExecutionMode(executionMode.value);
loadServerConfig();
checkHealth();

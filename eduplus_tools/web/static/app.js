const form = document.getElementById("run-form");
const clearLogButton = document.getElementById("clear-log");
const logView = document.getElementById("job-log");
const jobStatus = document.getElementById("job-status");
const jobId = document.getElementById("job-id");
const summary = document.getElementById("job-summary");
const healthBadge = document.getElementById("health-badge");
const downloadActions = document.getElementById("download-actions");
const downloadBundle = document.getElementById("download-bundle");
const artifactMeta = document.getElementById("artifact-meta");
const artifactList = document.getElementById("artifact-list");
const executionMode = document.getElementById("execution-mode");
const modeTitle = document.getElementById("mode-title");
const modeCopy = document.getElementById("mode-copy");
const outputLabel = document.getElementById("output-label");
const outputInput = document.getElementById("output-input");

let pollTimer = null;

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

function setBadge(element, text, kind = "muted") {
  element.className = `badge ${kind}`;
  element.textContent = text;
}

function renderSummary(data = {}) {
  const entries = Object.entries(data).filter(([, value]) => value);
  summary.replaceChildren();
  if (entries.length === 0) {
    summary.textContent = "";
    return;
  }
  for (const [key, value] of entries) {
    const row = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = key;
    row.appendChild(strong);
    row.append(`: ${String(value)}`);
    summary.appendChild(row);
  }
}

function setBusy(busy) {
  Array.from(form.elements).forEach((element) => {
    if (element instanceof HTMLButtonElement || element instanceof HTMLInputElement || element instanceof HTMLSelectElement) {
      if (element.id !== "clear-log") {
        element.disabled = busy;
      }
    }
  });
}

function resetArtifacts() {
  downloadActions.classList.add("hidden");
  downloadBundle.removeAttribute("href");
  artifactMeta.textContent = "";
  artifactList.classList.add("hidden");
  artifactList.replaceChildren();
}

function renderArtifacts(data = {}) {
  resetArtifacts();
  if (!data.artifact_count) {
    return;
  }

  if (data.bundle_url) {
    downloadBundle.href = data.bundle_url;
    downloadActions.classList.remove("hidden");
  }
  artifactMeta.textContent = `${data.artifact_count} files, ${formatBytes(data.total_bytes)}`;

  const title = document.createElement("p");
  title.className = "card-label";
  title.textContent = "Artifacts";
  const list = document.createElement("ul");
  for (const file of data.files || []) {
    const item = document.createElement("li");
    item.textContent = `${file.path} (${formatBytes(file.size)})`;
    list.appendChild(item);
  }
  artifactList.appendChild(title);
  artifactList.appendChild(list);
  artifactList.classList.remove("hidden");
}

async function fetchArtifacts(id) {
  const response = await fetch(`/api/jobs/${id}/artifacts`);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Failed to fetch artifacts");
  }
  return data;
}

async function checkHealth() {
  try {
    const response = await fetch("/api/health");
    if (!response.ok) {
      throw new Error("health check failed");
    }
    setBadge(healthBadge, "Server ready", "success");
  } catch {
    setBadge(healthBadge, "Server unavailable", "failed");
  }
}

function applyExecutionMode(mode) {
  if (mode === "local") {
    modeTitle.textContent = "Local output mode";
    modeCopy.textContent = "结果直接写入你指定的本地目录，更适合自用部署。仍然保留 ZIP 下载，方便浏览器取回。";
    outputLabel.textContent = "Output Directory";
    if (!outputInput.value || outputInput.value === "downloads/web-jobs") {
      outputInput.value = "downloads";
    }
    outputInput.placeholder = "直接写入的本地目录，例如 downloads";
    return;
  }

  modeTitle.textContent = "Public service mode";
  modeCopy.textContent = "每次任务隔离到独立目录，适合公共服务，完成后优先通过 ZIP 下载结果。";
  outputLabel.textContent = "Output Base";
  if (!outputInput.value || outputInput.value === "downloads") {
    outputInput.value = "downloads/web-jobs";
  }
  outputInput.placeholder = "任务隔离目录根，例如 downloads/web-jobs";
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
  logView.textContent = "Submitting job...";
  setBadge(jobStatus, "Queued", "running");
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Failed to create job");
    }
    jobId.textContent = `Job ${data.job_id}`;
    pollJob(data.job_id);
  } catch (error) {
    logView.textContent = String(error);
    setBadge(jobStatus, "Failed", "failed");
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
        throw new Error(data.error || "Failed to fetch job");
      }

      renderSummary(data.summary);
      logView.textContent = data.logs.length ? data.logs.join("\n") : "Running...";
      logView.scrollTop = logView.scrollHeight;

      const status = data.status;
      if (status === "completed") {
        setBadge(jobStatus, `Completed (${data.exit_code})`, data.exit_code === 0 ? "success" : "failed");
        renderArtifacts(await fetchArtifacts(id));
        clearInterval(pollTimer);
        setBusy(false);
        return;
      }
      if (status === "failed") {
        setBadge(jobStatus, `Failed (${data.exit_code ?? 1})`, "failed");
        clearInterval(pollTimer);
        setBusy(false);
        return;
      }
      setBadge(jobStatus, status === "running" ? "Running" : "Queued", "running");
    } catch (error) {
      setBadge(jobStatus, "Polling failed", "failed");
      logView.textContent += `\n${String(error)}`;
      clearInterval(pollTimer);
      setBusy(false);
    }
  };

  await tick();
  pollTimer = setInterval(tick, 1200);
}

form.addEventListener("submit", startJob);
clearLogButton.addEventListener("click", () => {
  renderSummary({});
  resetArtifacts();
  jobId.textContent = "No job yet";
  setBadge(jobStatus, "Idle", "muted");
  logView.textContent = "等待任务开始…";
});

executionMode.addEventListener("change", () => {
  applyExecutionMode(executionMode.value);
});

applyExecutionMode(executionMode.value);
checkHealth();

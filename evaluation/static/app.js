(function () {
  const state = {
    sessionId: crypto.randomUUID ? crypto.randomUUID() : `sess_${Date.now()}`,
    lastPrompt: "",
    lastMode: "semantic",
    lastCandidateGroups: [],
    lastTraceSteps: [],
    pinnedByEntity: {},
  };

  const chatLog = document.getElementById("chatLog");
  const chatForm = document.getElementById("chatForm");
  const chatInput = document.getElementById("chatInput");
  const sendBtn = document.getElementById("sendBtn");
  const modeSelect = document.getElementById("modeSelect");
  const workspaceInput = document.getElementById("workspaceInput");
  const databasesInput = document.getElementById("databasesInput");
  const statusPill = document.getElementById("statusPill");
  const traceSummary = document.getElementById("traceSummary");
  const traceSearchInput = document.getElementById("traceSearchInput");
  const traceTypeFilter = document.getElementById("traceTypeFilter");
  const traceTableBody = document.getElementById("traceTableBody");
  const candidateContainer = document.getElementById("candidateContainer");
  const candidateSearchInput = document.getElementById("candidateSearchInput");
  const candidateScoreFilter = document.getElementById("candidateScoreFilter");
  const candidateScoreValue = document.getElementById("candidateScoreValue");
  const candidatePinnedCount = document.getElementById("candidatePinnedCount");
  const rerunBtn = document.getElementById("rerunBtn");
  const resetSessionBtn = document.getElementById("resetSessionBtn");
  const bubbleTemplate = document.getElementById("bubbleTemplate");
  const sessionMeta = document.getElementById("sessionMeta");
  const railButtons = Array.from(document.querySelectorAll(".rail-btn"));

  function setStatus(text, kind) {
    statusPill.textContent = text;
    if (kind === "error") {
      statusPill.style.background = "#fceceb";
      statusPill.style.color = "#8c302b";
      statusPill.style.borderColor = "#e6b2ae";
    } else if (kind === "busy") {
      statusPill.style.background = "#fff3e8";
      statusPill.style.color = "#8b4d19";
      statusPill.style.borderColor = "#efc48f";
    } else {
      statusPill.style.background = "#eaf6ef";
      statusPill.style.color = "#1f6f45";
      statusPill.style.borderColor = "#c4d9c9";
    }
  }

  function updateRailMode(mode) {
    railButtons.forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.mode === mode);
    });
    modeSelect.value = mode;
  }

  function appendBubble(role, content) {
    const frag = bubbleTemplate.content.cloneNode(true);
    const el = frag.querySelector(".bubble");
    const roleEl = frag.querySelector(".role");
    const textEl = frag.querySelector(".text");
    el.classList.add(role);
    roleEl.textContent = role.toUpperCase();
    textEl.textContent = content;
    chatLog.appendChild(frag);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function parseDatabases() {
    return databasesInput.value
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean);
  }

  function previewText(value, maxLen) {
    const text = String(value || "");
    if (text.length <= maxLen) return text;
    return `${text.slice(0, maxLen)}...`;
  }

  function candidateKey(candidate) {
    return `${candidate.database}|${candidate.node_id}`;
  }

  function parseScore(value) {
    const score = Number(value);
    if (Number.isFinite(score)) return score;
    return 0;
  }

  async function sendChatMessage(message, overrides) {
    const payload = {
      session_id: state.sessionId,
      message,
      mode: modeSelect.value,
      workspace_id: workspaceInput.value.trim() || "default",
      databases: parseDatabases(),
      entity_overrides: overrides || null,
    };

    setStatus("Running", "busy");
    const response = await fetch("/api/chat/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`${response.status} ${errText}`);
    }

    const data = await response.json();
    setStatus("Ready", "ok");
    return data;
  }

  function renderTraceSummary(summaryObj) {
    const entries = Object.entries(summaryObj || {});
    if (!entries.length) {
      traceSummary.textContent = "-";
      return;
    }
    const lines = entries
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `${k}: ${v}`);
    traceSummary.textContent = lines.join("\n");
  }

  function syncTraceTypeFilter(steps) {
    const current = traceTypeFilter.value;
    const types = Array.from(new Set((steps || []).map((s) => String(s.type || "UNKNOWN")))).sort();
    traceTypeFilter.innerHTML = "";
    const allOpt = document.createElement("option");
    allOpt.value = "";
    allOpt.textContent = "All types";
    traceTypeFilter.appendChild(allOpt);
    types.forEach((type) => {
      const opt = document.createElement("option");
      opt.value = type;
      opt.textContent = type;
      traceTypeFilter.appendChild(opt);
    });
    if (types.includes(current)) {
      traceTypeFilter.value = current;
    }
  }

  function renderTraceTable() {
    const searchText = (traceSearchInput.value || "").trim().toLowerCase();
    const typeFilter = traceTypeFilter.value;
    const rows = (state.lastTraceSteps || []).filter((step) => {
      const matchesType = !typeFilter || String(step.type || "") === typeFilter;
      if (!matchesType) return false;
      if (!searchText) return true;
      const haystack = [
        step.type,
        step.agent,
        step.content,
        JSON.stringify(step.metadata || {}),
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(searchText);
    });

    traceTableBody.innerHTML = "";
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 4;
      td.textContent = "No matching trace rows.";
      tr.appendChild(td);
      traceTableBody.appendChild(tr);
      return;
    }

    rows.forEach((step, idx) => {
      const tr = document.createElement("tr");
      const cols = [
        String(idx + 1),
        String(step.type || "UNKNOWN"),
        String(step.agent || "-"),
        previewText(step.content || "", 120),
      ];
      cols.forEach((value) => {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      });
      traceTableBody.appendChild(tr);
    });
  }

  function updatePinnedStateText() {
    const count = Object.keys(state.pinnedByEntity).length;
    candidatePinnedCount.textContent = `Pinned: ${count}`;
    rerunBtn.disabled = !(count > 0 && modeSelect.value === "semantic");
  }

  function renderCandidates() {
    const searchText = (candidateSearchInput.value || "").trim().toLowerCase();
    const minScore = parseScore(candidateScoreFilter.value);
    candidateScoreValue.textContent = minScore.toFixed(1);

    candidateContainer.innerHTML = "";
    const groups = state.lastCandidateGroups || [];
    let rendered = 0;

    groups.forEach((group) => {
      const entity = String(group.question_entity || "");
      const candidates = Array.isArray(group.candidates) ? [...group.candidates] : [];
      candidates.sort((a, b) => parseScore(b.score) - parseScore(a.score));
      const filtered = candidates.filter((candidate) => {
        const score = parseScore(candidate.score);
        if (score < minScore) return false;
        if (!searchText) return true;
        const haystack = [
          entity,
          candidate.display_name,
          candidate.database,
          (candidate.labels || []).join(" "),
          candidate.source,
        ]
          .join(" ")
          .toLowerCase();
        return haystack.includes(searchText);
      });

      if (!filtered.length) return;

      rendered += 1;
      const groupWrap = document.createElement("div");
      groupWrap.className = "candidate-item";

      const title = document.createElement("div");
      title.className = "candidate-question";
      title.textContent = entity;
      groupWrap.appendChild(title);

      filtered.forEach((candidate) => {
        const row = document.createElement("div");
        row.className = "candidate-row";

        const meta = document.createElement("div");
        meta.className = "candidate-meta";
        const main = document.createElement("div");
        main.className = "candidate-main";
        main.textContent = `${candidate.display_name} (${candidate.database})`;
        const sub = document.createElement("div");
        sub.className = "candidate-sub";
        sub.textContent = `node=${candidate.node_id} score=${parseScore(candidate.score).toFixed(3)} source=${candidate.source}`;
        meta.appendChild(main);
        meta.appendChild(sub);
        row.appendChild(meta);

        const button = document.createElement("button");
        button.className = "pin-btn";
        const key = candidateKey(candidate);
        const pinned = state.pinnedByEntity[entity] && candidateKey(state.pinnedByEntity[entity]) === key;
        button.textContent = pinned ? "Unpin" : "Pin";
        button.classList.toggle("active", pinned);
        button.addEventListener("click", () => {
          const existing = state.pinnedByEntity[entity];
          if (existing && candidateKey(existing) === key) {
            delete state.pinnedByEntity[entity];
          } else {
            state.pinnedByEntity[entity] = {
              question_entity: entity,
              database: candidate.database,
              node_id: candidate.node_id,
              display_name: candidate.display_name,
              labels: candidate.labels || [],
            };
          }
          renderCandidates();
          updatePinnedStateText();
        });
        row.appendChild(button);
        groupWrap.appendChild(row);
      });

      candidateContainer.appendChild(groupWrap);
    });

    if (!rendered) {
      const empty = document.createElement("div");
      empty.className = "candidate-item";
      empty.textContent = "No candidates for current filter.";
      candidateContainer.appendChild(empty);
    }

    updatePinnedStateText();
  }

  function collectOverrides() {
    return Object.values(state.pinnedByEntity);
  }

  function applyResponse(data) {
    appendBubble("assistant", data.assistant_message || "(empty response)");
    const uiPayload = data.ui_payload || {};
    renderTraceSummary(uiPayload.trace_summary || {});

    state.lastTraceSteps = data.trace_steps || [];
    syncTraceTypeFilter(state.lastTraceSteps);
    renderTraceTable();

    state.lastCandidateGroups = uiPayload.entity_candidates || [];
    state.pinnedByEntity = {};
    renderCandidates();
  }

  chatForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = chatInput.value.trim();
    if (!message) return;

    appendBubble("user", message);
    chatInput.value = "";
    sendBtn.disabled = true;

    try {
      state.lastPrompt = message;
      state.lastMode = modeSelect.value;
      const data = await sendChatMessage(message);
      applyResponse(data);
    } catch (err) {
      console.error(err);
      setStatus("Error", "error");
      appendBubble("assistant", `Error: ${err.message}`);
    } finally {
      sendBtn.disabled = false;
    }
  });

  rerunBtn.addEventListener("click", async () => {
    if (!state.lastPrompt || modeSelect.value !== "semantic") return;
    const overrides = collectOverrides();
    if (!overrides.length) return;

    appendBubble("user", `[Override re-run] ${state.lastPrompt}`);
    rerunBtn.disabled = true;

    try {
      const data = await sendChatMessage(state.lastPrompt, overrides);
      applyResponse(data);
    } catch (err) {
      console.error(err);
      setStatus("Error", "error");
      appendBubble("assistant", `Error: ${err.message}`);
    } finally {
      rerunBtn.disabled = false;
    }
  });

  resetSessionBtn.addEventListener("click", async () => {
    try {
      await fetch(`/api/chat/session/${state.sessionId}`, { method: "DELETE" });
      chatLog.innerHTML = "";
      candidateContainer.innerHTML = "";
      traceSummary.textContent = "-";
      state.lastTraceSteps = [];
      state.lastCandidateGroups = [];
      state.pinnedByEntity = {};
      renderTraceTable();
      updatePinnedStateText();
      appendBubble("assistant", "Session reset.");
    } catch (err) {
      appendBubble("assistant", `Reset failed: ${err.message}`);
    }
  });

  modeSelect.addEventListener("change", () => {
    updateRailMode(modeSelect.value);
    updatePinnedStateText();
  });

  railButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      updateRailMode(btn.dataset.mode);
    });
  });

  traceSearchInput.addEventListener("input", renderTraceTable);
  traceTypeFilter.addEventListener("change", renderTraceTable);
  candidateSearchInput.addEventListener("input", renderCandidates);
  candidateScoreFilter.addEventListener("input", renderCandidates);

  async function bootstrap() {
    setStatus("Loading", "busy");
    try {
      if (sessionMeta) {
        sessionMeta.textContent = `Session ${state.sessionId.slice(0, 8)}...`;
      }
      const response = await fetch("/api/config");
      if (!response.ok) throw new Error(`Config error: ${response.status}`);
      const cfg = await response.json();
      if (Array.isArray(cfg.databases) && cfg.databases.length) {
        databasesInput.value = cfg.databases.join(",");
      }
      updateRailMode(cfg.default_mode || "semantic");
      setStatus("Ready", "ok");
      renderTraceTable();
      updatePinnedStateText();
      appendBubble("assistant", "Operations console online.");
    } catch (err) {
      setStatus("Error", "error");
      appendBubble("assistant", `Failed to initialize: ${err.message}`);
    }
  }

  bootstrap();
})();


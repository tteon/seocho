(function () {
  const state = {
    sessionId: crypto.randomUUID ? crypto.randomUUID() : `sess_${Date.now()}`,
    lastPrompt: "",
    lastMode: "semantic",
    lastCandidates: [],
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
  const candidateContainer = document.getElementById("candidateContainer");
  const rerunBtn = document.getElementById("rerunBtn");
  const resetSessionBtn = document.getElementById("resetSessionBtn");
  const bubbleTemplate = document.getElementById("bubbleTemplate");

  function setStatus(text, kind) {
    statusPill.textContent = text;
    if (kind === "error") {
      statusPill.style.background = "#fde8e4";
      statusPill.style.color = "#7f2c1c";
      statusPill.style.borderColor = "#f0b2a6";
    } else if (kind === "busy") {
      statusPill.style.background = "#fff1de";
      statusPill.style.color = "#864d16";
      statusPill.style.borderColor = "#efc48f";
    } else {
      statusPill.style.background = "#e6f5ef";
      statusPill.style.color = "#1a4f3a";
      statusPill.style.borderColor = "#bfd6cc";
    }
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

  function renderCandidates(candidateGroups) {
    candidateContainer.innerHTML = "";
    state.lastCandidates = candidateGroups || [];
    rerunBtn.disabled = !(candidateGroups && candidateGroups.length && modeSelect.value === "semantic");

    (candidateGroups || []).forEach((group, idx) => {
      const wrap = document.createElement("div");
      wrap.className = "candidate-item";
      const label = document.createElement("label");
      label.textContent = group.question_entity;
      const select = document.createElement("select");
      select.dataset.entity = group.question_entity;

      const auto = document.createElement("option");
      auto.value = "";
      auto.textContent = "Auto (top candidate)";
      select.appendChild(auto);

      (group.candidates || []).forEach((candidate, cidx) => {
        const opt = document.createElement("option");
        opt.value = String(cidx);
        opt.textContent = `${candidate.display_name} | db=${candidate.database} | node=${candidate.node_id} | score=${candidate.score}`;
        select.appendChild(opt);
      });

      wrap.appendChild(label);
      wrap.appendChild(select);
      candidateContainer.appendChild(wrap);
    });
  }

  function collectOverrides() {
    const selects = candidateContainer.querySelectorAll("select[data-entity]");
    const overrides = [];
    selects.forEach((selectEl) => {
      if (selectEl.value === "") return;
      const questionEntity = selectEl.dataset.entity;
      const idx = Number(selectEl.value);
      const group = state.lastCandidates.find((g) => g.question_entity === questionEntity);
      if (!group || !group.candidates || !group.candidates[idx]) return;
      const chosen = group.candidates[idx];
      overrides.push({
        question_entity: questionEntity,
        database: chosen.database,
        node_id: chosen.node_id,
        display_name: chosen.display_name,
        labels: chosen.labels || [],
      });
    });
    return overrides;
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
      appendBubble("assistant", data.assistant_message || "(empty response)");
      renderTraceSummary((data.ui_payload || {}).trace_summary || {});
      renderCandidates((data.ui_payload || {}).entity_candidates || []);
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

    appendBubble("user", `[Re-run with overrides] ${state.lastPrompt}`);
    rerunBtn.disabled = true;

    try {
      const data = await sendChatMessage(state.lastPrompt, overrides);
      appendBubble("assistant", data.assistant_message || "(empty response)");
      renderTraceSummary((data.ui_payload || {}).trace_summary || {});
      renderCandidates((data.ui_payload || {}).entity_candidates || []);
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
      appendBubble("assistant", "Session reset.");
    } catch (err) {
      appendBubble("assistant", `Reset failed: ${err.message}`);
    }
  });

  modeSelect.addEventListener("change", () => {
    rerunBtn.disabled = modeSelect.value !== "semantic";
  });

  async function bootstrap() {
    setStatus("Loading", "busy");
    try {
      const response = await fetch("/api/config");
      if (!response.ok) throw new Error(`Config error: ${response.status}`);
      const cfg = await response.json();
      if (Array.isArray(cfg.databases) && cfg.databases.length) {
        databasesInput.value = cfg.databases.join(",");
      }
      setStatus("Ready", "ok");
      appendBubble("assistant", "Custom platform is online. Ask your graph question.");
    } catch (err) {
      setStatus("Error", "error");
      appendBubble("assistant", `Failed to initialize: ${err.message}`);
    }
  }

  bootstrap();
})();


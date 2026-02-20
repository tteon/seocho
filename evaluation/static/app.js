(function () {
  const state = {
    sessionId: crypto.randomUUID ? crypto.randomUUID() : `sess_${Date.now()}`,
    lastPrompt: "",
    lastMode: "semantic",
    lastTraceSteps: [],
  };

  const chatLog = document.getElementById("chatLog");
  const chatForm = document.getElementById("chatForm");
  const chatInput = document.getElementById("chatInput");
  const sendBtn = document.getElementById("sendBtn");
  const modeSelect = document.getElementById("modeSelect");
  const workspaceInput = document.getElementById("workspaceInput");
  const databasesInput = document.getElementById("databasesInput");
  const statusPill = document.getElementById("statusPill");
  const resetSessionBtn = document.getElementById("resetSessionBtn");
  const bubbleTemplate = document.getElementById("bubbleTemplate");
  const sessionMeta = document.getElementById("sessionMeta");
  const railButtons = Array.from(document.querySelectorAll(".rail-btn"));
  const dagCanvas = document.getElementById("dagCanvas");
  const dagNodeTemplate = document.getElementById("dagNodeTemplate");

  function setStatus(text, kind) {
    statusPill.textContent = text;
    statusPill.style.color = "#fff";
    if (kind === "error") {
      statusPill.style.background = "rgba(248, 81, 73, 0.2)";
      statusPill.style.border = "1px solid rgba(248, 81, 73, 0.5)";
    } else if (kind === "busy") {
      statusPill.style.background = "rgba(210, 153, 34, 0.2)";
      statusPill.style.border = "1px solid rgba(210, 153, 34, 0.5)";
    } else {
      statusPill.style.background = "rgba(46, 160, 67, 0.2)";
      statusPill.style.border = "1px solid rgba(46, 160, 67, 0.5)";
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

  async function sendChatMessage(message) {
    const payload = {
      session_id: state.sessionId,
      message,
      mode: modeSelect.value,
      workspace_id: workspaceInput.value.trim() || "default",
      databases: parseDatabases(),
      entity_overrides: null,
    };

    setStatus("Executing Workflow...", "busy");
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
    setStatus("Idle", "ok");
    return data;
  }

  function createDagNode(step) {
    const frag = dagNodeTemplate.content.cloneNode(true);
    const el = frag.querySelector(".workflow-node");
    const typeEl = frag.querySelector(".node-type");
    const nameEl = frag.querySelector(".node-agent-name");
    const contentEl = frag.querySelector(".node-content");

    nameEl.textContent = step.agent || Object.keys(step.metadata || {})[0] || "System Agent";

    // Determine Palantir styling based on step phase
    let phaseClass = "agent";
    let phaseLabel = "Worker";
    const phase = step.metadata?.phase || "";
    if (phase === "orchestration") {
      phaseClass = "orchestrator";
      phaseLabel = "Orchestrator";
    } else if (phase === "synthesis" || step.agent?.includes("Supervisor")) {
      phaseClass = "supervisor";
      phaseLabel = "Supervisor";
    } else if (phase === "fan-out") {
      phaseLabel = `DB: ${step.metadata.db || "Unknown"}`;
      phaseClass = "agent";
    }

    typeEl.textContent = phaseLabel;
    typeEl.classList.add(phaseClass);

    if (step.content) {
      let preview = step.content;
      if (preview.length > 300) preview = preview.substring(0, 300) + '...';
      contentEl.textContent = preview;
    } else {
      contentEl.textContent = "// Payload empty or internal state change";
      contentEl.style.color = "rgba(139, 148, 158, 0.5)";
    }

    return el;
  }

  function renderDag() {
    dagCanvas.innerHTML = '<div class="canvas-header">Workflow Builder Live Trace Graph</div>';

    if (!state.lastTraceSteps || state.lastTraceSteps.length === 0) {
      dagCanvas.innerHTML += `<div class="canvas-empty">
        [ NO ACTIVE RUN ]<br/><br/>
        Awaiting payload to render DAG Trace...
      </div>`;
      return;
    }

    const container = document.createElement("div");
    container.className = "dag-container";

    // 1. Group steps by Phase for horizontal placement
    const tiers = {
      start: [],     // Route/Orchestration nodes
      parallel: [],  // Fan-out / Workers
      end: []        // Synthesis / Supervisor
    };

    state.lastTraceSteps.forEach(step => {
      const p = step.metadata?.phase || "";
      const isSuper = step.agent?.includes("Supervisor");
      if (p === "orchestration" || (!p && !isSuper && tiers.start.length === 0)) {
        tiers.start.push(step);
      } else if (p === "synthesis" || isSuper) {
        tiers.end.push(step);
      } else {
        tiers.parallel.push(step);
      }
    });

    // Helper to render a tier row
    const renderTier = (stepArray) => {
      if (stepArray.length === 0) return null;
      const tierEl = document.createElement("div");
      tierEl.className = "dag-tier";
      stepArray.forEach((step, idx) => {
        const nodeEl = createDagNode(step);
        // Stagger animation delay
        nodeEl.style.animationDelay = `${idx * 0.15}s`;
        tierEl.appendChild(nodeEl);
      });
      return tierEl;
    };

    // Draw Tiers with connecting edges
    const tStart = renderTier(tiers.start);
    const tPar = renderTier(tiers.parallel);
    const tEnd = renderTier(tiers.end);

    if (tStart) container.appendChild(tStart);
    if (tStart && tPar) {
      const edge = document.createElement("div");
      edge.className = "dag-edge-down";
      container.appendChild(edge);
    }
    if (tPar) container.appendChild(tPar);
    if ((tStart || tPar) && tEnd) {
      const edge = document.createElement("div");
      edge.className = "dag-edge-down";
      container.appendChild(edge);
    }
    if (tEnd) container.appendChild(tEnd);

    dagCanvas.appendChild(container);

    // Scroll to bottom of DAG to see synthesis
    setTimeout(() => {
      dagCanvas.scrollTop = dagCanvas.scrollHeight;
    }, 100);
  }

  function applyResponse(data) {
    appendBubble("assistant", data.assistant_message || "(empty response)");
    state.lastTraceSteps = data.trace_steps || [];
    renderDag();
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

  resetSessionBtn.addEventListener("click", async () => {
    try {
      await fetch(`/api/chat/session/${state.sessionId}`, { method: "DELETE" });
      chatLog.innerHTML = '<div style="font-family:var(--font-mono); font-size:0.7rem; color:var(--text-muted); text-align:center;">// Session reset.</div>';
      state.lastTraceSteps = [];
      renderDag();
    } catch (err) {
      appendBubble("assistant", `Reset failed: ${err.message}`);
    }
  });

  modeSelect.addEventListener("change", () => {
    updateRailMode(modeSelect.value);
  });

  railButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      updateRailMode(btn.dataset.mode);
    });
  });

  async function bootstrap() {
    setStatus("Loading", "busy");
    try {
      if (sessionMeta) {
        sessionMeta.textContent = `Session ${state.sessionId.slice(0, 8)}`;
      }
      const response = await fetch("/api/config");
      if (!response.ok) throw new Error(`Config error: ${response.status}`);
      const cfg = await response.json();
      if (Array.isArray(cfg.databases) && cfg.databases.length) {
        databasesInput.value = cfg.databases.join(",");
      }
      updateRailMode(cfg.default_mode || "semantic");
      setStatus("Idle", "ok");
    } catch (err) {
      setStatus("Offline", "error");
      console.warn("Could not connect to backend, UI running in fallback mode.");
    }
  }

  bootstrap();
})();

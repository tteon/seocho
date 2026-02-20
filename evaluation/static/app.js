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
  const ingestDbInput = document.getElementById("ingestDbInput");
  const ingestInput = document.getElementById("ingestInput");
  const ingestBtn = document.getElementById("ingestBtn");
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

    // Add green highlight if Backend implies Confidence > 0.15 is true
    // Note: We scan the JSON dynamically for is_confident as the response from Answer generation might mention it indirectly.
    // However, the specification is visually highlighting candidate lists. Since candidates are hidden in DAG mode, we highlight the bubble itself if it contains confident overrides.
    if (content.includes("is_confident")) {
      el.classList.add("confident-highlight");
    }

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

  function buildRawRecords() {
    if (!ingestInput) return [];
    const lines = ingestInput.value
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    return lines.map((line, idx) => ({
      id: `raw_${Date.now()}_${idx}`,
      content: line,
      category: "general",
      metadata: {},
    }));
  }

  async function ingestRawRecords() {
    const records = buildRawRecords();
    if (!records.length) {
      throw new Error("Add at least one non-empty line in Raw Records.");
    }
    const payload = {
      workspace_id: workspaceInput.value.trim() || "default",
      target_database: (ingestDbInput?.value || "kgnormal").trim() || "kgnormal",
      records,
      enable_rule_constraints: true,
      create_database_if_missing: true,
    };

    setStatus("Ingesting Raw Data...", "busy");
    const response = await fetch("/api/ingest/raw", {
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

    // Inject exact backend Schema Node ID
    el.id = step.metadata?.node_id || `__fallback_${Math.random().toString(36).substr(2, 9)}`;

    // Store parent references in DOM dataset for edge drawing later
    if (step.metadata?.parent_id) el.dataset.parentId = step.metadata.parent_id;
    if (step.metadata?.parent_ids) el.dataset.parentIds = JSON.stringify(step.metadata.parent_ids);

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

  function drawEdges() {
    const svg = document.getElementById("dagEdges");
    const container = document.getElementById("dagScrollLayer");
    if (!svg || !container) return;

    svg.innerHTML = "";
    const containerRect = container.getBoundingClientRect();
    const nodes = document.querySelectorAll(".workflow-node");

    nodes.forEach(childEl => {
      let parentIds = [];
      if (childEl.dataset.parentId) parentIds.push(childEl.dataset.parentId);
      if (childEl.dataset.parentIds) {
        try {
          const arr = JSON.parse(childEl.dataset.parentIds);
          parentIds = parentIds.concat(arr);
        } catch (e) { }
      }

      parentIds.forEach(pId => {
        const parentEl = document.getElementById(pId);
        if (parentEl) {
          const pRect = parentEl.getBoundingClientRect();
          const cRect = childEl.getBoundingClientRect();

          // Calculate center bottom of parent, center top of child, relative to scrolling container
          const startX = (pRect.left + pRect.width / 2) - containerRect.left;
          const startY = (pRect.bottom) - containerRect.top;
          const endX = (cRect.left + cRect.width / 2) - containerRect.left;
          const endY = (cRect.top) - containerRect.top;

          if (startY > endY) return; // avoid backwards curves if not needed

          const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
          // Beautiful API-style Curve
          const curveY = (startY + endY) / 2;
          path.setAttribute("d", `M ${startX} ${startY} C ${startX} ${curveY}, ${endX} ${curveY}, ${endX} ${endY}`);
          path.setAttribute("stroke", "rgba(63, 185, 80, 0.35)"); // Palantir flow line
          path.setAttribute("stroke-width", "2");
          path.setAttribute("fill", "none");
          // Add blueprint dash-array animation class
          path.classList.add("edge-flow-anim");

          svg.appendChild(path);
        }
      });
    });
  }

  function renderDag() {
    const emptyState = document.getElementById("canvasEmptyState");
    const container = document.getElementById("dagContainer");
    const svg = document.getElementById("dagEdges");
    if (!container) return;

    if (!state.lastTraceSteps || state.lastTraceSteps.length === 0) {
      if (emptyState) emptyState.style.display = "block";
      container.innerHTML = "";
      if (svg) svg.innerHTML = "";
      return;
    }

    if (emptyState) emptyState.style.display = "none";
    container.innerHTML = "";
    if (svg) svg.innerHTML = "";

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

    // Draw Tiers
    const tStart = renderTier(tiers.start);
    const tPar = renderTier(tiers.parallel);
    const tEnd = renderTier(tiers.end);

    if (tStart) container.appendChild(tStart);
    if (tPar) container.appendChild(tPar);
    if (tEnd) container.appendChild(tEnd);

    // Wait for DOM layout then draw exact SVG edges
    setTimeout(() => {
      drawEdges();
      // Scroll to bottom of DAG to see synthesis
      if (dagCanvas) {
        dagCanvas.scrollTop = dagCanvas.scrollHeight;
      }
    }, 150);
  }

  function applyResponse(data) {
    let assistantMsg = data.assistant_message || "(empty response)";

    // Inject confidence highlights text parsing directly into UI chat
    if (data.semantic_context && data.semantic_context.matches) {
      for (const [entity, candidates] of Object.entries(data.semantic_context.matches)) {
        if (candidates.length > 0 && candidates[0].is_confident) {
          assistantMsg += `\n\n[CONFIDENCE GAP DETECTED]: Auto-selecting highly confident entity resolving to '${candidates[0].display_name}'. -> is_confident:true`;
        }
      }
    }

    appendBubble("assistant", assistantMsg);
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

  if (ingestBtn) {
    ingestBtn.addEventListener("click", async () => {
      ingestBtn.disabled = true;
      try {
        const result = await ingestRawRecords();
        const targetDb = result.target_database || "kgnormal";
        const dbs = parseDatabases();
        if (!dbs.includes(targetDb)) {
          dbs.push(targetDb);
          databasesInput.value = dbs.join(",");
        }
        appendBubble(
          "assistant",
          [
            `[RAW INGEST] status=${result.status}`,
            `db=${targetDb}, processed=${result.records_processed}/${result.records_received}`,
            `nodes=${result.total_nodes}, rels=${result.total_relationships}`,
            `fallback=${result.fallback_records || 0}`,
            result.records_failed > 0
              ? `failed=${result.records_failed} (see server logs/details)`
              : "failed=0",
          ].join("\n")
        );
      } catch (err) {
        console.error(err);
        setStatus("Error", "error");
        appendBubble("assistant", `Raw ingest error: ${err.message}`);
      } finally {
        ingestBtn.disabled = false;
      }
    });
  }

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
      if (modeSelect.value !== btn.dataset.mode) {
        modeSelect.value = btn.dataset.mode;
      }
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

  // Redraw edges on resize
  window.addEventListener('resize', () => {
    if (state.lastTraceSteps && state.lastTraceSteps.length > 0) {
      drawEdges();
    }
  });

  bootstrap();
})();

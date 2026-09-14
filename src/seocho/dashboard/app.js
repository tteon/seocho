"use strict";
const $ = (id) => document.getElementById(id);
const state = { runs: [], report: null, selected: "", route: 0, comparing: 0 };
const text = (value) =>
  value == null || value === ""
    ? "Not recorded"
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : String(value);
const node = (tag, content, cls) => {
  const el = document.createElement(tag);
  if (content !== undefined) el.textContent = text(content);
  if (cls) el.className = cls;
  return el;
};
const replace = (id, ...children) => $(id).replaceChildren(...children);
const badge = (value) =>
  node(
    "span",
    value,
    `badge ${["completed", "partial", "failed", "interrupted", "running", "answered", "error", "empty", "skipped"].includes(value) ? value : "unknown"}`,
  );
const seconds = (value) =>
  typeof value === "number" ? `${value.toFixed(2)}s` : "Not recorded";
const date = (value) =>
  value && Number.isFinite(Date.parse(value))
    ? new Date(value).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "Not recorded";
const metric = (value) =>
  typeof value === "number"
    ? Number(value.toFixed(4)).toLocaleString()
    : "Not recorded";
const outcome = (q) => state.outcomes.get(q) || "unknown";
async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok)
    throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function error(message) {
  $("global-error").hidden = !message;
  $("global-error").textContent = message || "";
}
function stat(label, value, note) {
  const el = node("div", undefined, "stat");
  el.append(
    node("div", label, "stat-label"),
    node("div", value, "stat-value"),
    node("div", note, "stat-note"),
  );
  return el;
}
function options(id, values, placeholder) {
  const el = $(id),
    old = el.value;
  el.replaceChildren();
  if (placeholder) el.append(new Option(placeholder, ""));
  values.forEach(([value, label]) => el.append(new Option(label, value)));
  if ([...el.options].some((o) => o.value === old)) el.value = old;
}
function invalidateComparison() {
  state.comparing++;
  replace("comparison-result");
}
async function load(refresh = false) {
  $("refresh").disabled = true;
  error("");
  try {
    const data = await api(`/api/runs${refresh ? "?refresh=1" : ""}`);
    state.runs = data.runs;
    $("nav-count").textContent = data.runs.length;
    replace(
      "stats",
      stat("Saved runs", data.runs.length, "In the current catalog"),
      stat(
        "Completed",
        data.runs.filter((r) => r.status === "completed").length,
        "Execution finished",
      ),
      stat(
        "Needs attention",
        data.runs.filter((r) =>
          ["partial", "failed", "interrupted"].includes(r.status),
        ).length,
        "Inspect failures & diagnostics",
      ),
      stat(
        "Workspaces",
        new Set(data.runs.map((r) => r.workspace_id)).size,
        "Explicit run namespaces",
      ),
    );
    for (const [id, values, label] of [
      ["status-filter", data.runs.map((r) => r.status), "All statuses"],
      [
        "workspace-filter",
        data.runs.map((r) => r.workspace_id),
        "All workspaces",
      ],
      [
        "model-filter",
        data.runs.flatMap((r) => Object.values(r.models)),
        "All models",
      ],
    ])
      options(
        id,
        [...new Set(values)].sort().map((v) => [v, v]),
        label,
      );
    $("refreshed").textContent = `Updated ${date(data.refreshed_at)}`;
    replace("sources", ...data.roots.map((r) => node("pre", r)));
    replace(
      "scan-warnings",
      ...data.warnings.map((w) => node("li", `${w.path}: ${w.reason}`)),
    );
    $("scan-summary").textContent =
      `Data sources & scan details · ${data.warnings.length} notices`;
    const choices = data.runs.map((r) => [
      r.id,
      `${r.name} · ${r.workspace_id} · ${date(r.started_at)} · ${r.path}`,
    ]);
    options("baseline-select", choices, "Select baseline");
    options("candidate-select", choices, "Select candidate");
    if (!$("baseline-select").value && choices.length)
      $("baseline-select").value = choices[0][0];
    if (!$("candidate-select").value && choices.length > 1)
      $("candidate-select").value = choices.find(
        (c) => c[0] !== $("baseline-select").value,
      )[0];
    if (!$("change-options").children.length)
      for (const name of data.changeable) {
        const label = node("label"),
          input = node("input");
        input.type = "checkbox";
        input.value = name;
        input.addEventListener("change", invalidateComparison);
        label.append(input, document.createTextNode(name.replaceAll("_", " ")));
        $("change-options").append(label);
      }
    invalidateComparison();
    renderRuns();
    await route();
  } catch (e) {
    error(e.message);
  } finally {
    $("refresh").disabled = false;
  }
}
function renderRuns() {
  const needle = $("search").value.toLowerCase();
  const runs = state.runs.filter(
    (r) =>
      (!$("status-filter").value || r.status === $("status-filter").value) &&
      (!$("workspace-filter").value ||
        r.workspace_id === $("workspace-filter").value) &&
      (!$("model-filter").value ||
        Object.values(r.models).includes($("model-filter").value)) &&
      [r.name, r.workspace_id, r.path, ...Object.values(r.models)]
        .join(" ")
        .toLowerCase()
        .includes(needle),
  );
  replace(
    "run-rows",
    ...runs.map((r) => {
      const row = node("tr"),
        name = node("td"),
        link = node("a", r.name, "run-title");
      link.href = `#run/${r.id}`;
      name.append(
        link,
        node("div", `${r.workspace_id} · ${r.path}`, "run-subtitle"),
      );
      const model = node("td");
      model.append(
        node(
          "span",
          r.models.query || r.models.indexing || "Not recorded",
          "model",
        ),
      );
      const actions = node("td"),
        button = node("button", "Compare →", "text-button");
      button.setAttribute("aria-label", `Use ${r.name} as baseline`);
      button.addEventListener("click", () => baseline(r.id));
      actions.append(button);
      const status = node("td");
      status.append(badge(r.status));
      row.append(
        name,
        status,
        model,
        node("td", `${r.answered} / ${r.questions_recorded}`),
        node("td", seconds(r.query_seconds)),
        node("td", date(r.started_at)),
        actions,
      );
      return row;
    }),
  );
  $("result-count").textContent = runs.length;
  $("empty").hidden = !!runs.length;
  $("empty-copy").textContent = state.runs.length
    ? "No experiments match these filters. Reset filters to see all runs."
    : "Point the dashboard at the directory containing your saved runs.";
}
function baseline(id) {
  $("baseline-select").value = id;
  if ($("candidate-select").value === id)
    $("candidate-select").value = state.runs.find((r) => r.id !== id)?.id || "";
  invalidateComparison();
  location.hash = "#compare";
}
function tab(name) {
  document.querySelectorAll("[data-tab]").forEach((button) => {
    const selected = button.dataset.tab === name;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
    $(`${button.dataset.tab}-tab`).hidden = !selected;
  });
}
function evidence(label, value) {
  const box = node("div", undefined, "evidence-box");
  box.append(node("div", label, "answer-label"), node("pre", text(value)));
  return box;
}
function questions() {
  const all = state.report?.queries || [],
    needle = $("question-search").value.toLowerCase();
  const visible = all.filter(
    (q) =>
      (!$("question-filter").value ||
        outcome(q) === $("question-filter").value) &&
      [q.id, q.question, q.answer].join(" ").toLowerCase().includes(needle),
  );
  $("question-count").textContent =
    `${visible.length} of ${all.length} questions`;
  replace(
    "question-list",
    ...visible.map((q) => {
      const el = node("details", undefined, "question"),
        summary = node("summary");
      summary.append(
        node("span", q.id, "question-id"),
        node("span", text(q.question), "question-title"),
        badge(outcome(q)),
        node("span", seconds(q.latency_s), "subtle"),
      );
      const body = node("div", undefined, "question-body");
      body.append(
        node("div", "Answer", "answer-label"),
        node("div", text(q.answer), "answer"),
      );
      const grid = node("div", undefined, "evidence-grid");
      grid.append(
        evidence("Expected reference", q.expect),
        evidence("Support status", q.support_status),
        evidence("Coverage", q.coverage),
        evidence("Missing slots", q.missing_slots),
      );
      body.append(grid);
      if (q.error) body.append(node("div", q.error, "notice danger"));
      const triples = node("details");
      triples.append(
        node("summary", "Graph evidence"),
        node("pre", q.evidence_bundle),
      );
      const raw = node("details");
      raw.append(node("summary", "Full recorded question"), node("pre", q));
      body.append(triples, raw);
      el.append(summary, body);
      return el;
    }),
  );
  if (!visible.length)
    $("question-list").append(
      node("p", "No questions match this view.", "notice"),
    );
}
function detail(payload, id) {
  const { report, summary: row, query_states: queryStates } = payload;
  state.outcomes = new WeakMap(
    (report.queries || []).map((q, i) => [q, queryStates[i]]),
  );
  state.report = report;
  state.selected = id;
  const run = report.run;
  $("detail-name").textContent = row.name;
  $("detail-workspace").textContent = row.workspace_id;
  replace(
    "detail-meta",
    badge(row.status),
    node("span", date(run.started_at)),
    node("span", row.path, "subtle"),
    node(
      "span",
      `${row.questions_recorded} recorded / ${row.questions_requested ?? "unknown"} requested questions`,
      "subtle",
    ),
  );
  $("export-view").href = `/view/${id}`;
  replace(
    "detail-stats",
    stat(
      "Answers",
      `${row.answered} / ${row.questions_recorded}`,
      "Nonempty, not a quality grade",
    ),
    stat("Question errors", row.errors, "Original failures preserved"),
    stat("Index phase", seconds(row.index_seconds), "Recorded wall time"),
    stat("Query phase", seconds(row.query_seconds), "Recorded wall time"),
  );
  $("active-question").hidden = !report.active_question && !report.fatal_error;
  $("active-question").textContent = report.fatal_error
    ? `Run failure: ${text(report.fatal_error)}`
    : `Last active question: ${text(report.active_question)}`;
  replace(
    "phases",
    evidence("01 / INDEX", seconds(row.index_seconds)),
    evidence("02 / QUERY", seconds(row.query_seconds)),
    node(
      "p",
      "Phase observations only. Span timings and token costs are not recorded here.",
      "subtle",
    ),
  );
  $("question-search").value = "";
  $("question-filter").value = "";
  tab("questions");
  questions();
  const diagnostics = Array.isArray(report.diagnostics)
    ? report.diagnostics
    : [];
  replace(
    "diagnostics-tab",
    ...diagnostics.map((d) => {
      const el = node("article", undefined, "diagnostic");
      el.append(
        node("h3", `${d.stage || "Run"} · ${d.code || "Diagnostic"}`),
        node("p", d.message),
        node("p", d.action, "subtle"),
      );
      if (d.item_id) el.append(node("code", d.item_id));
      return el;
    }),
  );
  if (!diagnostics.length)
    $("diagnostics-tab").append(
      node("p", "No diagnostic entries recorded.", "notice"),
    );
  if (report.outcome?.reasons?.length)
    $("diagnostics-tab").append(
      evidence("Outcome reasons", report.outcome.reasons),
    );
  const receipt = report.reproducibility || {},
    table = node("table", undefined, "conditions"),
    body = node("tbody");
  for (const [key, value] of Object.entries({
    "Receipt schema": receipt.schema_version,
    "Source revision": receipt.source?.git_revision,
    Workspace: run.workspace_id,
    Database: run.database,
    ...receipt.conditions,
  })) {
    const tr = node("tr");
    tr.append(node("th", key), node("td", text(value)));
    body.append(tr);
  }
  table.append(body);
  replace(
    "conditions-tab",
    table,
    evidence("Evidence gaps", receipt.gaps),
    evidence("Limitations", receipt.limitations),
  );
}
async function route() {
  const version = ++state.route,
    hash = location.hash.slice(1),
    isDetail = hash.startsWith("run/"),
    page = isDetail
      ? "detail"
      : ["compare", "guide"].includes(hash)
        ? hash
        : "runs";
  for (const name of ["runs", "detail", "compare", "guide"])
    $(`${name}-page`).hidden = name !== page;
  for (const name of ["runs", "compare", "guide"]) {
    const active = name === (isDetail ? "runs" : page);
    $(`nav-${name}`).classList.toggle("active", active);
    if (active) $(`nav-${name}`).setAttribute("aria-current", "page");
    else $(`nav-${name}`).removeAttribute("aria-current");
  }
  $("breadcrumb").textContent = {
    runs: "Experiments",
    detail: "Run detail",
    compare: "Compare runs",
    guide: "Getting started",
  }[page];
  document.title = `SEOCHO · ${$("breadcrumb").textContent}`;
  error("");
  if (isDetail) {
    $("detail-page").hidden = true;
    try {
      const id = hash.slice(4),
        report = await api(`/api/runs/${encodeURIComponent(id)}`);
      if (version !== state.route) return;
      detail(report, id);
      $("detail-page").hidden = false;
    } catch (e) {
      if (version === state.route) error(e.message);
    }
  }
}
async function compare() {
  const version = ++state.comparing;
  $("compare-button").disabled = true;
  replace("comparison-result");
  error("");
  try {
    const query = new URLSearchParams({
      baseline: $("baseline-select").value,
      candidate: $("candidate-select").value,
      hypothesis: $("hypothesis").value,
    });
    document
      .querySelectorAll("#change-options input:checked")
      .forEach((el) => query.append("change", el.value));
    const result = await api(`/api/compare?${query}`);
    if (version !== state.comparing) return;
    const heading = node(
      "div",
      undefined,
      `notice ${result.comparable ? "" : "warning"}`,
    );
    heading.append(
      node(
        "h2",
        result.comparable
          ? "Matched evidence · descriptive comparison"
          : "Comparison restricted",
      ),
      node(
        "p",
        result.comparable
          ? `${result.matched_questions} matched questions. Deltas describe these recorded runs; they do not prove a causal improvement.`
          : "The recorded conditions do not support aggregate deltas. Individual results remain available.",
      ),
    );
    if (result.issues.length) {
      const list = node("ul");
      result.issues.forEach((i) => list.append(node("li", i)));
      heading.append(list);
    }
    $("comparison-result").append(heading);
    const wrap = node("div", undefined, "table-wrap"),
      table = node("table"),
      head = node("thead"),
      tr = node("tr");
    ["Metric", "Baseline", "Candidate", "Delta"].forEach((v) =>
      tr.append(node("th", v)),
    );
    head.append(tr);
    const body = node("tbody");
    for (const [name, values] of Object.entries(result.metrics)) {
      const row = node("tr"),
        label = node("td");
      label.append(
        node("div", name),
        node("div", values.interpretation, "subtle"),
      );
      row.append(
        label,
        ...["baseline", "candidate", "delta"].map((k) =>
          node("td", metric(values[k])),
        ),
      );
      body.append(row);
    }
    table.append(head, body);
    wrap.append(table);
    $("comparison-result").append(
      wrap,
      node("h2", "Question transitions", "section-heading"),
    );
    for (const pair of result.questions) {
      const el = node("details", undefined, "question"),
        summary = node("summary");
      summary.append(
        node("span", pair.id),
        node("span", pair.transition),
        node(
          "span",
          pair.matched ? "Matched question" : "Unmatched question",
          "subtle",
        ),
      );
      const grid = node("div", undefined, "pair-grid");
      for (const side of ["baseline", "candidate"]) {
        const answer = node("div", undefined, "pair-answer");
        answer.append(
          node("div", side.toUpperCase(), "answer-label"),
          node("div", pair[`${side}_answer`], "answer"),
        );
        if (pair[`${side}_error`])
          answer.append(node("p", pair[`${side}_error`], "notice danger"));
        answer.append(evidence("Missing slots", pair[`${side}_missing_slots`]));
        grid.append(answer);
      }
      el.append(summary, grid);
      $("comparison-result").append(el);
    }
    const limits = node("ul", undefined, "footnote");
    result.limitations.forEach((l) => limits.append(node("li", l)));
    $("comparison-result").append(limits);
  } catch (e) {
    if (version === state.comparing) error(e.message);
  } finally {
    $("compare-button").disabled = false;
  }
}
$("refresh").addEventListener("click", () => load(true));
for (const id of [
  "search",
  "status-filter",
  "workspace-filter",
  "model-filter",
])
  $(id).addEventListener("input", renderRuns);
$("clear-filters").addEventListener("click", () => {
  ["search", "status-filter", "workspace-filter", "model-filter"].forEach(
    (id) => ($(id).value = ""),
  );
  renderRuns();
});
for (const id of ["question-search", "question-filter"])
  $(id).addEventListener("input", questions);
for (const id of ["baseline-select", "candidate-select", "hypothesis"])
  $(id).addEventListener("input", invalidateComparison);
$("compare-button").addEventListener("click", compare);
$("use-baseline").addEventListener("click", () => baseline(state.selected));
const tabs = [...document.querySelectorAll("[data-tab]")];
tabs.forEach((button, index) => {
  button.addEventListener("click", () => tab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    const offset =
      event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (offset) {
      event.preventDefault();
      const next = tabs[(index + offset + tabs.length) % tabs.length];
      tab(next.dataset.tab);
      next.focus();
    }
  });
});
$("show-sources").addEventListener("click", () => {
  location.hash = "#runs";
  $("scan-panel").open = true;
  setTimeout(() => $("scan-panel").scrollIntoView({ behavior: "smooth" }), 0);
});
window.addEventListener("hashchange", route);
load();

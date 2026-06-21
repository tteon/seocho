import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';

const SITE_DIR = process.cwd();
const REPO_ROOT = path.resolve(SITE_DIR, '..');
const TARGET_DOCS_DIR = path.join(SITE_DIR, 'src', 'content', 'docs', 'docs');
const TARGET_BLOG_DIR = path.join(SITE_DIR, 'src', 'content', 'docs', 'blog');
const SOURCE_BLOB_BASE = 'https://github.com/tteon/seocho/blob/main/';
const SOURCE_TREE_BASE = 'https://github.com/tteon/seocho/tree/main/';

function sourceDateFor(relPath) {
  try {
    const output = execSync(
      `git -C "${REPO_ROOT}" log -1 --format=%cs -- "${relPath}"`,
      { stdio: ['ignore', 'pipe', 'ignore'] }
    ).toString().trim();
    return output || new Date().toISOString().split('T')[0];
  } catch {
    return new Date().toISOString().split('T')[0];
  }
}

const fileMappings = [
  {
    src: 'docs/WHY_SEOCHO.md',
    dest: 'why_seocho.md',
    frontmatter:
      '---\n' +
      'title: Why SEOCHO\n' +
      'description: Why SEOCHO is ontology-first and graph-native instead of generic memory-first.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/WHY_SEOCHO.md`*\n\n',
  },
  {
    src: 'docs/README.md',
    dest: 'index.md',
    frontmatter:
      '---\n' +
      'title: Docs Home\n' +
      'description: Central Documentation Index for SEOCHO\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/README.md`*\n\n',
  },
  {
    src: 'QUICKSTART.md',
    dest: 'quickstart.md',
    frontmatter:
      '---\n' +
      'title: Quickstart\n' +
      'description: Get SEOCHO up and running in 5 minutes.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/QUICKSTART.md`*\n\n',
  },
  {
    src: 'docs/RUNTIME_DEPLOYMENT.md',
    dest: 'runtime_deployment.md',
    frontmatter:
      '---\n' +
      'title: Runtime Deployment\n' +
      'description: Full local runtime deployment guide for the Docker stack, services, and environment setup.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/RUNTIME_DEPLOYMENT.md`*\n\n',
  },
  {
    src: 'docs/APPLY_YOUR_DATA.md',
    dest: 'apply_your_data.md',
    frontmatter:
      '---\n' +
      'title: Bring Your Data\n' +
      'description: How to load your own records into SEOCHO and query them safely.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/APPLY_YOUR_DATA.md`*\n\n',
  },
  {
    src: 'docs/PYTHON_INTERFACE_QUICKSTART.md',
    dest: 'python_sdk.md',
    frontmatter:
      '---\n' +
      'title: Python SDK\n' +
      'description: Developer-first guide to ingest data and query SEOCHO through the Python SDK.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/PYTHON_INTERFACE_QUICKSTART.md`*\n\n',
  },
  {
    src: 'docs/FILES_AND_ARTIFACTS.md',
    dest: 'files_and_artifacts.md',
    frontmatter:
      '---\n' +
      'title: Files and Artifacts\n' +
      'description: Where ontology files, graph state, rule profiles, semantic artifacts, and traces live.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/FILES_AND_ARTIFACTS.md`*\n\n',
  },
  {
    src: 'docs/ARCHITECTURE.md',
    dest: 'architecture.md',
    frontmatter:
      '---\n' +
      'title: Architecture\n' +
      'description: System Architecture and Module Map.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/ARCHITECTURE.md`*\n\n',
  },
  {
    src: 'docs/WORKFLOW.md',
    dest: 'workflow.md',
    frontmatter:
      '---\n' +
      'title: Workflow\n' +
      'description: End-to-end Operational Workflow.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/WORKFLOW.md`*\n\n',
  },
  {
    src: 'docs/TUTORIAL_FIRST_RUN.md',
    dest: 'tutorial.md',
    frontmatter:
      '---\n' +
      'title: First Run Tutorial\n' +
      'description: End-to-end tutorial to start services, verify APIs, and run agent chat.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/TUTORIAL_FIRST_RUN.md`*\n\n',
  },
  {
    src: 'docs/OPEN_SOURCE_PLAYBOOK.md',
    dest: 'open_source_playbook.md',
    frontmatter:
      '---\n' +
      'title: Open Source Playbook\n' +
      'description: Extension guide for ontology, data, agent, and runtime integration.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/OPEN_SOURCE_PLAYBOOK.md`*\n\n',
  },
  {
    src: 'docs/PHILOSOPHY.md',
    dest: 'philosophy.md',
    frontmatter:
      '---\n' +
      'title: Philosophy\n' +
      'description: Core Design Philosophy Charter and Operating Principles.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/PHILOSOPHY.md`*\n\n',
  },
];

const blogMappings = [
  {
    src: 'docs/PHILOSOPHY.md',
    dest: 'philosophy.md',
    frontmatter:
      '---\n' +
      'title: "SEOCHO Design Philosophy & Operating Principles"\n' +
      `date: ${sourceDateFor('docs/PHILOSOPHY.md')}\n` +
      'authors:\n' +
      '  - seocho\n' +
      'excerpt: Extract domain rules and high-value semantics from heterogeneous data into a SHACL-like semantic layer.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/PHILOSOPHY.md`*\n\n',
  },
  {
    src: 'docs/internal/PHILOSOPHY_FEASIBILITY_REVIEW.md',
    dest: 'feasibility-review-framework.md',
    frontmatter:
      '---\n' +
      'title: "Feasibility Review Framework & Rubrics"\n' +
      `date: ${sourceDateFor('docs/internal/PHILOSOPHY_FEASIBILITY_REVIEW.md')}\n` +
      'authors:\n' +
      '  - seocho\n' +
      'excerpt: Multi-role feasibility review framework and Go/No-Go rubric for graph data implementations.\n' +
      '---\n\n' +
      '> *Source mirrored from `seocho/docs/internal/PHILOSOPHY_FEASIBILITY_REVIEW.md`*\n\n',
  },
];

const routeReplacements = new Map([
  ['`docs/WHY_SEOCHO.md`', '[`/docs/why_seocho/`](/docs/why_seocho/)'],
  ['`docs/README.md`', '[`/docs/`](/docs/)'],
  ['`QUICKSTART.md`', '[`/docs/quickstart/`](/docs/quickstart/)'],
  ['`docs/RUNTIME_DEPLOYMENT.md`', '[`/docs/runtime_deployment/`](/docs/runtime_deployment/)'],
  ['`docs/APPLY_YOUR_DATA.md`', '[`/docs/apply_your_data/`](/docs/apply_your_data/)'],
  ['`docs/PYTHON_INTERFACE_QUICKSTART.md`', '[`/docs/python_sdk/`](/docs/python_sdk/)'],
  ['`docs/FILES_AND_ARTIFACTS.md`', '[`/docs/files_and_artifacts/`](/docs/files_and_artifacts/)'],
  ['`docs/ARCHITECTURE.md`', '[`/docs/architecture/`](/docs/architecture/)'],
  ['`docs/WORKFLOW.md`', '[`/docs/workflow/`](/docs/workflow/)'],
  ['`docs/TUTORIAL_FIRST_RUN.md`', '[`/docs/tutorial/`](/docs/tutorial/)'],
  ['`docs/OPEN_SOURCE_PLAYBOOK.md`', '[`/docs/open_source_playbook/`](/docs/open_source_playbook/)'],
  ['`docs/PHILOSOPHY.md`', '[`/docs/philosophy/`](/docs/philosophy/)'],
  ['`docs/internal/PHILOSOPHY_FEASIBILITY_REVIEW.md`', '[`/blog/feasibility-review-framework/`](/blog/feasibility-review-framework/)'],
  ['(../README.md#execution-surfaces)', `(${SOURCE_BLOB_BASE}README.md#execution-surfaces)`],
  ['(../QUICKSTART.md)', '(/docs/quickstart/)'],
  ['(WHY_SEOCHO.md)', '(/docs/why_seocho/)'],
  ['(QUICKSTART.md)', '(/docs/quickstart/)'],
  ['(docs/RUNTIME_DEPLOYMENT.md)', '(/docs/runtime_deployment/)'],
  ['(RUNTIME_DEPLOYMENT.md)', '(/docs/runtime_deployment/)'],
  ['(docs/APPLY_YOUR_DATA.md)', '(/docs/apply_your_data/)'],
  ['(docs/PYTHON_INTERFACE_QUICKSTART.md)', '(/docs/python_sdk/)'],
  ['(docs/FILES_AND_ARTIFACTS.md)', '(/docs/files_and_artifacts/)'],
  ['(PYTHON_INTERFACE_QUICKSTART.md)', '(/docs/python_sdk/)'],
  ['(APPLY_YOUR_DATA.md)', '(/docs/apply_your_data/)'],
  ['(FILES_AND_ARTIFACTS.md)', '(/docs/files_and_artifacts/)'],
  ['(ARCHITECTURE.md)', '(/docs/architecture/)'],
  ['(WORKFLOW.md)', '(/docs/workflow/)'],
  ['(TUTORIAL_FIRST_RUN.md)', '(/docs/tutorial/)'],
  ['(OPEN_SOURCE_PLAYBOOK.md)', '(/docs/open_source_playbook/)'],
  ['(PHILOSOPHY.md)', '(/docs/philosophy/)'],
  ['(internal/PHILOSOPHY_FEASIBILITY_REVIEW.md)', '(/blog/feasibility-review-framework/)'],
  ['(BENCHMARKS.md)', `(${SOURCE_BLOB_BASE}docs/BENCHMARKS.md)`],
  ['(AGENT_DESIGN_SPECS.md)', `(${SOURCE_BLOB_BASE}docs/AGENT_DESIGN_SPECS.md)`],
  ['(INDEXING_DESIGN_SPECS.md)', `(${SOURCE_BLOB_BASE}docs/INDEXING_DESIGN_SPECS.md)`],
  ['(BEGINNER_GUIDE.md)', `(${SOURCE_BLOB_BASE}docs/BEGINNER_GUIDE.md)`],
  ['(INTERNAL_CLASS_DESIGN.md)', `(${SOURCE_BLOB_BASE}docs/INTERNAL_CLASS_DESIGN.md)`],
  ['(MODULE_OWNERSHIP_MAP.md)', `(${SOURCE_BLOB_BASE}docs/MODULE_OWNERSHIP_MAP.md)`],
  ['(USECASES.md)', `(${SOURCE_BLOB_BASE}docs/USECASES.md)`],
  ['(presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md)', `(${SOURCE_BLOB_BASE}docs/presentations/SEOCHO_OVERVIEW_DEEP_DIVE.md)`],
  ['(internal/RUNTIME_PACKAGE_MIGRATION.md)', `(${SOURCE_BLOB_BASE}docs/internal/RUNTIME_PACKAGE_MIGRATION.md)`],
  ['(internal/AGENT_SERVER_REFACTOR_PLAN.md)', `(${SOURCE_BLOB_BASE}docs/internal/AGENT_SERVER_REFACTOR_PLAN.md)`],
  ['(internal/ARCHITECTURE_HEALTH.md)', `(${SOURCE_BLOB_BASE}docs/internal/ARCHITECTURE_HEALTH.md)`],
  ['(internal/REPOSITORY_HIERARCHY_REVIEW.md)', `(${SOURCE_BLOB_BASE}docs/internal/REPOSITORY_HIERARCHY_REVIEW.md)`],
  ['(internal/PROMPT_ASSEMBLY_DISCUSSION_MEMO.md)', `(${SOURCE_BLOB_BASE}docs/internal/PROMPT_ASSEMBLY_DISCUSSION_MEMO.md)`],
  ['(internal/BASELINE_INSTRUCTIONS.md)', `(${SOURCE_BLOB_BASE}docs/internal/BASELINE_INSTRUCTIONS.md)`],
  ['(internal/KNOWN_ISSUE.md)', `(${SOURCE_BLOB_BASE}docs/internal/KNOWN_ISSUE.md)`],
  ['(internal/)', `(${SOURCE_TREE_BASE}docs/internal)`],
  ['(GRAPH_RAG_AGENT_HANDOFF_SPEC.md)', `(${SOURCE_BLOB_BASE}docs/GRAPH_RAG_AGENT_HANDOFF_SPEC.md)`],
  ['(ONTOLOGY_RUN_CONTEXT_STRATEGY.md)', `(${SOURCE_BLOB_BASE}docs/ONTOLOGY_RUN_CONTEXT_STRATEGY.md)`],
  ['(PROPERTY_GRAPH_LENS_STRATEGY.md)', `(${SOURCE_BLOB_BASE}docs/PROPERTY_GRAPH_LENS_STRATEGY.md)`],
  ['(ISSUE_TASK_SYSTEM.md)', `(${SOURCE_BLOB_BASE}docs/ISSUE_TASK_SYSTEM.md)`],
  ['(BEADS_OPERATING_MODEL.md)', `(${SOURCE_BLOB_BASE}docs/BEADS_OPERATING_MODEL.md)`],
  ['(decisions/DECISION_LOG.md)', `(${SOURCE_BLOB_BASE}docs/decisions/DECISION_LOG.md)`],
  ['(../CONTRIBUTING.md)', `(${SOURCE_BLOB_BASE}CONTRIBUTING.md)`],
  ['(../examples/agent_designs/)', `(${SOURCE_TREE_BASE}examples/agent_designs)`],
  ['(../examples/indexing_designs/)', `(${SOURCE_TREE_BASE}examples/indexing_designs)`],
  ['(../examples/agent_designs/planning_multi_agent_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/agent_designs/planning_multi_agent_finance.yaml)`],
  ['(../examples/agent_designs/reflection_chain_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/agent_designs/reflection_chain_finance.yaml)`],
  ['(../examples/agent_designs/memory_tool_use_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/agent_designs/memory_tool_use_finance.yaml)`],
  ['(../examples/indexing_designs/lpg_finance_provenance.yaml)', `(${SOURCE_BLOB_BASE}examples/indexing_designs/lpg_finance_provenance.yaml)`],
  ['(../examples/indexing_designs/rdf_deductive_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/indexing_designs/rdf_deductive_finance.yaml)`],
  ['(../examples/indexing_designs/hybrid_inquiry_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/indexing_designs/hybrid_inquiry_finance.yaml)`],
  ['(/tmp/seocho-land-finder-e2e/examples/agent_designs)', `(${SOURCE_TREE_BASE}examples/agent_designs)`],
  ['(/tmp/seocho-land-finder-e2e/examples/indexing_designs)', `(${SOURCE_TREE_BASE}examples/indexing_designs)`],
  ['(/tmp/seocho-land-finder-e2e/examples/agent_designs/planning_multi_agent_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/agent_designs/planning_multi_agent_finance.yaml)`],
  ['(/tmp/seocho-land-finder-e2e/examples/agent_designs/reflection_chain_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/agent_designs/reflection_chain_finance.yaml)`],
  ['(/tmp/seocho-land-finder-e2e/examples/agent_designs/memory_tool_use_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/agent_designs/memory_tool_use_finance.yaml)`],
  ['(/tmp/seocho-land-finder-e2e/examples/indexing_designs/lpg_finance_provenance.yaml)', `(${SOURCE_BLOB_BASE}examples/indexing_designs/lpg_finance_provenance.yaml)`],
  ['(/tmp/seocho-land-finder-e2e/examples/indexing_designs/rdf_deductive_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/indexing_designs/rdf_deductive_finance.yaml)`],
  ['(/tmp/seocho-land-finder-e2e/examples/indexing_designs/hybrid_inquiry_finance.yaml)', `(${SOURCE_BLOB_BASE}examples/indexing_designs/hybrid_inquiry_finance.yaml)`],
]);

function rewriteWebsiteRoutes(content) {
  let rewritten = content;
  for (const [sourceRef, routeLink] of routeReplacements.entries()) {
    rewritten = rewritten.replaceAll(sourceRef, routeLink);
  }
  return rewritten;
}

function renderMirroredContent(mapping) {
  const sourcePath = path.join(REPO_ROOT, mapping.src);
  if (!fs.existsSync(sourcePath)) {
    throw new Error(`Missing source file in repo: ${mapping.src}`);
  }

  let content = fs.readFileSync(sourcePath, 'utf8');
  content = content.replace(/^#\s(.*?)\n/m, '');
  content = rewriteWebsiteRoutes(content);
  return mapping.frontmatter + content;
}

function cleanGeneratedOutputs() {
  fs.rmSync(TARGET_DOCS_DIR, { recursive: true, force: true });
  fs.rmSync(path.join(TARGET_BLOG_DIR, 'philosophy.md'), { force: true });
  fs.rmSync(path.join(TARGET_BLOG_DIR, 'feasibility-review-framework.md'), { force: true });
  fs.mkdirSync(TARGET_DOCS_DIR, { recursive: true });
  fs.mkdirSync(TARGET_BLOG_DIR, { recursive: true });
}

function writeMappings(mappings, targetDir) {
  for (const mapping of mappings) {
    const rendered = renderMirroredContent(mapping);
    const destPath = path.join(targetDir, mapping.dest);
    fs.writeFileSync(destPath, rendered);
    console.log(`Generated ${mapping.src} -> ${path.relative(SITE_DIR, destPath)}`);
  }
}

cleanGeneratedOutputs();
writeMappings(fileMappings, TARGET_DOCS_DIR);
writeMappings(blogMappings, TARGET_BLOG_DIR);
console.log('Mirrored docs generated from repo-root source docs.');

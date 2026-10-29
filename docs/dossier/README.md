# 📦 SDD-Parallel — Core Documentation Dossier (SSOT Package)

**Directory Purpose:**  
This directory contains the **modular package of transferable documentation** for the `sdd-parallel` project.  
The documents are designed following the principle of **orthogonal semantic blocks (LEGO architecture)**: each file is self-contained, contains zero hardcoded line numbers of source code, and can be exported into an isolated environment for external stakeholders or autonomous AI agents.

---

## 🗺️ Modular Package Contents

```text
docs/dossier/
├── 01_CORE_PRODUCT_BRIEF.md              # Tier 1: Business, value, problem, ROI, invariants
├── 02_SYSTEM_CAPABILITIES_WHITE_PAPER.md # Tier 2: Architecture, conflict mathematics, AST/CRG
├── 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md  # Tier 3: Entry points, adapters, .tmp/ and lock formats
├── 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md # Tier 4: 4 market tiers, comparison against 300k+ peers
└── 07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md # Tier 5: Anatomy of `sdd-parallel demo` & hands-on E2E
```

> **Note on Translations:** The author's original Russian documentation package is preserved in full in [`docs/i18n/ru/dossier/`](../i18n/ru/dossier/).

---

## 🎯 Role-Based Context Assembly Matrix

Depending on your objective, assemble and share the recommended combination of files:

| Role / Target Objective | Recommended File Bundle | Recipient Takeaway |
| :--- | :--- | :--- |
| 🔬 **AI Researcher / Benchmarker** *(Primary Use Case)* | `01_CORE_PRODUCT_BRIEF.md`<br>`+ 02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`<br>`+ 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`<br>`+ 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md` | Comprehensive understanding of product novelty, mathematical conflict models, and a ready-to-run benchmark comparing against Superpowers, MetaGPT, Spec Kit, and OpenHands. |
| 💼 **Investor / C-Level / Pitch** | `01_CORE_PRODUCT_BRIEF.md`<br>`+ 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md` | Business validation, 2x Time-to-Market acceleration, 45% inference token cost reduction, and market positioning against single-task and prompt frameworks. |
| 🛠️ **External Integrator / Core Dev** | `02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`<br>`+ 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md` | Technical architecture, Git Worktree isolation invariants, runtime lock manifests, and the extension protocol for custom SDD adapters. |
| 📐 **Product Owner / SDD Specifier** | `01_CORE_PRODUCT_BRIEF.md`<br>`+ 02_SYSTEM_CAPABILITIES_WHITE_PAPER.md` | In-depth understanding of Spine Locks, Additive Registries, and roadmap structuring rules for zero-conflict parallelization. |

---

## 🧭 Diátaxis-lite Navigation Matrix (Reading Scenarios)

This technical documentation package adheres to the **Diátaxis-lite** framework, structuring information across 4 core engineering needs:

| Reading Mode (Diátaxis) | Target Document | Content & Learning Focus |
| :--- | :--- | :--- |
| 🎓 **Tutorials** *(Hands-on Learning)* | [`07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md`](07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md) | Step-by-step walkthrough: 4-stage output anatomy of `sdd-parallel demo`, Scenario A (`--keep-temp` demo sandbox) and Scenario B (Greenfield scratch repo, OS Lock crash test, and LIFO rollback). |
| 🧠 **Explanation / Concepts** *(Theory & Architectural Concepts)* | [`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`](02_SYSTEM_CAPABILITIES_WHITE_PAPER.md) | Theoretical foundation: Type-1 and Type-2 conflict models ($W_A \cap I_B$), 3-pass algorithmic pipeline of `ASTHotspotScanner`, CRG impact decay formulation, Three-Tier Scope Guard, and status semi-lattices. |
| 📖 **Reference & How-To** *(Specifications, Formats & Protocols)* | [`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`](03_INTEGRATION_AND_ECOSYSTEM_SPEC.md) | CLI command reference, artifact schema specifications (`.tmp/track-context.md`, `.parallel-lock.json`), MCP JSON-RPC protocol, 5 SDD adapter contracts, and `SDDAdapterProtocol`. |
| 📊 **Executive Overview & Market Taxonomy** *(Business Value & Benchmarking)* | [`01_CORE_PRODUCT_BRIEF.md`](01_CORE_PRODUCT_BRIEF.md)<br>[`04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md`](04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md) | Business justification (ROI, 2x Time-to-Market, 45% token savings), 4-tier taxonomy of AI agent systems, and comparative benchmarks against 300k+ star alternatives. |

---

## 🤖 Ready-to-Use Prompt for External Research Agents

When transferring this dossier to an independent AI agent in an external environment (where it **lacks access to the `sdd-parallel` codebase**), provide the following prompt:

```markdown
<TASK>
You are a Principal Architect and Distributed Agent Systems Analyst.
You have been provided with the documentation dossier of the `sdd-parallel` framework (files: 01_CORE_PRODUCT_BRIEF, 02_SYSTEM_CAPABILITIES_WHITE_PAPER, 03_INTEGRATION_AND_ECOSYSTEM_SPEC, 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY).

Your objective:
1. Examine the architectural core and core innovations of the framework (Zero Worktree Pollution, Type-2 Conflicts via AST/CRG Blast Radius, Monotonic 3-Way Merger, Model Effort Routing).
2. Perform a comprehensive comparative analysis of `sdd-parallel` against popular agentic coding frameworks (Superpowers, MetaGPT, GitHub Spec Kit, BMAD Method, OpenHands, SWE-agent, Aider, and top multi-agent systems with 10k–300k+ stars).
3. Evaluate:
   - Specific scenarios where `sdd-parallel` outperforms existing approaches;
   - Potential synergistic integrations where `sdd-parallel` provides the safe execution harness for worker agents;
   - Missing mechanisms in `sdd-parallel` that have proven successful in established open-source production projects.
</TASK>
```

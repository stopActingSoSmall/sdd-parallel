# ⚡ `sdd-parallel demo` 实战演示指南：4秒见证多智能体安全治理

<p align="center">
  <b>🌐 语言 / Language:</b>
  <a href="../../dossier/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">English</a> |
  <b>简体中文</b> |
  <a href="../es/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Español</a> |
  <a href="../pt-BR/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Português (BR)</a> |
  <a href="../ja/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">日本語</a> |
  <a href="../ko/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">한국어</a> |
  <a href="../ru/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Русский</a>
</p>

**版本:** 0.1.0  

在同一个代码仓库中并发运行多个 AI 编程智能体（Claude Code, Google Antigravity, Codex）能够将开发速度提升 2–3 倍。然而，若缺乏确定性的安全编排，几乎必然会导致以下三种事故：

1. **静默契约破坏（二类语义合并炸弹 `Merge-Bomb`）：** 智能体 A 在服务层修改了函数签名，而智能体 B 在相邻分支中同时编写了对旧签名的调用。因为修改位于不同文件，`git merge` 毫无冲突地完成，但系统在运行时直接崩溃。
2. **范围幻觉（Scope Creep）：** 智能体在处理局部任务时，顺手去“优化”了数据库模型或旁路模块，破坏了并发任务的执行前提。
3. **状态文件写入战争：** 两个智能体分别完成任务，并发修改了共享迭代文件（`sprint-status.yaml` 或 `STATE.md`）中的相邻行，导致常规 `git merge` 因 `<<<<<<< HEAD` 冲突而中断。

**`sdd-parallel demo`** 命令专为此设计：**无需 API 密钥、无需网络连接、无需配置任何项目，约 4 秒内即可在真实 Git 仓库中**验证 `sdd-parallel` 如何拦截上述全部三种隐患。

---

## 1. 演示机制：真机真实执行，非模拟文本

`uv run sdd-parallel demo` **绝非终端文本的模拟输出**。每次执行时，引擎都在磁盘上执行 100% 真实操作：
- 在系统临时目录中初始化真实的 Git 仓库 (`Workspace: .../sdd-parallel-demo-<id>`)；
- 通过抽象语法树 (`AST`) 解析实际 Python 文件；
- 检出真实的隔离 `git worktree` 槽位并施加操作系统级只读保护；
- 模拟两个并行分支的真实提交并执行 `git merge --no-ff`。

该命令支持对 5 大 SDD 框架中的任意一种进行针对性验证，或执行全量矩阵验证：

```bash
# 模式 1：快速验证默认框架（BMAD）
uv run sdd-parallel demo

# 模式 2：指定 SDD 框架验证（bmad, spec-kit, gsd, openspec, beads）
uv run sdd-parallel demo --kit spec-kit

# 模式 3：全量矩阵验证（一键跑通 5 大框架并输出计分板）
uv run sdd-parallel demo --kit all

# 模式 4：交互式沙箱（在磁盘上保留仓库目录，供手动探索）
uv run sdd-parallel demo --keep-temp

# 模式 5：极速生成干净沙箱（供现场实战与手动端到端验证）
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir "$env:TEMP/eval-speckit"
```

### 全量 5 框架矩阵验证输出 (`--kit all`)

运行 `uv run sdd-parallel demo --kit all` 时，引擎会依次对全部 5 个适配器进行端到端验证，并输出统一的综合计分板：

```text
============================================================================
5/5 SDD KITS VERIFIED (BMAD, SPEC KIT, GSD, OPENSPEC, BEADS)
============================================================================
Kit          | Merge-Bomb   | Worktrees    | Scope Guard  | 3-Way Merge 
----------------------------------------------------------------------------
bmad         | PASSED       | PASSED       | PASSED       | PASSED      
spec-kit     | PASSED       | PASSED       | PASSED       | PASSED      
gsd          | PASSED       | PASSED       | PASSED       | PASSED      
openspec     | PASSED       | PASSED       | PASSED       | PASSED      
beads        | PASSED       | PASSED       | PASSED       | PASSED      
============================================================================
```

### 5 大 SDD 框架沙箱制品与治理映射矩阵

| 框架 (`--kit`) | 生成的规范文件路径 | 受保护的核心契约 (`forbidden_files`) | 3 路单调合并目标文件 |
| :--- | :--- | :--- | :--- |
| `spec-kit` | `specs/001-telemetry/{spec.md, plan.md, tasks.md}` | `.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md` | `specs/001-telemetry/tasks.md` (`- [x]`) |
| `gsd` | `.planning/phases/01-core/*-PLAN.md` (XML `<files>`) | `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md` | `.planning/STATE.md` (Post-Merge Hook) |
| `openspec` | `openspec/changes/<id>/{proposal.md, tasks.md}` | `openspec/config.yaml`, `openspec/specs/**`, 邻居 `changes/<sibling>/**` | `openspec/changes/<id>/tasks.md` (`- [x]`) |
| `beads` | `.beads/beads.jsonl` (类型化 `dependencies`) | `.beads/config.yaml` | `.beads/beads.jsonl` (JSONL `max(rank)`) |
| `bmad` | `_bmad-output/planning-artifacts/epics.md`, `spec-*.md` | `_bmad-output/**/epic-*-context.md` | `sprint-status.yaml` (`max(rank)`) |

> **多次运行会有何差异？**  
> 仅 `Workspace:` 中的随机后缀以及总用时 `VERIFIED IN X.XXs`（取决于磁盘性能，通常在 2–4.5 秒）。角色分类、检测到的冲突、波次调度与合并结果**100% 完全确定**，因为调度器基于严格的图论数学计算，而非大模型的随机生成。

---

## 2. 逐步拆解：演示的各个执行阶段

### 阶段 0. 初始状态（磁盘初始化）

临时目录生成初始提交 (`chore: initial demo repository`)，包含 6 个微型遥测模块及 **BMAD** 结构的需求规范：

```text
sdd-parallel-demo-<id>/
├── src/demo_app/
│   ├── __init__.py          # 模块导出 (__all__)
│   ├── schema.py            # 数据库模型与迁移 (SCHEMA_VERSION = 1, auto_migrate())
│   ├── cli.py               # CLI 注册中心 (@app.command)
│   ├── normalizer.py        # 数据规整：normalize_payload(raw)
│   ├── api.py               # HTTP 接口：从 normalizer.py 导入了 normalize_payload！
│   └── exporter.py          # 批量导出：export_events(events)
└── _bmad-output/
    ├── planning-artifacts/          # 需求规划与预编译上下文
    └── implementation-artifacts/    # sprint-status.yaml + 5 个任务规范 (spec-*.md)
```

**待办队列中的 5 个任务 (`ready-for-dev`) 与其中暗藏的 3 个陷阱：**
- **`1-1`** *(Upgrade schema to v2)* $\to$ 修改 `schema.py`（依赖 `1-2`）。
- **`1-2`** *(Normalize payload)* $\to$ 修改 `normalizer.py` 并在 `cli.py` 注册命令。
- **`1-3`** *(CLI batch export)* $\to$ 修改 `exporter.py` 并同样在 `cli.py` 注册命令 *(陷阱 1：争夺共享 `cli.py`)*。
- **`2-1`** *(Expose ingestion API)* $\to$ 仅修改 `api.py` *(陷阱 2 — 隐藏的语义 Merge-Bomb：`2-1` 规范未提及 `1-2`，但 `api.py` 导入了正在被 `1-2` 重构的 `normalizer.py`！)*。
- **`2-2`** *(Add audit log schema)* $\to$ 修改 `schema.py`（依赖 `1-1`）*(陷阱 3：多次数据库迁移)*。

---

### 阶段 1. AST 架构角色分类 (`[Step 1/4]`)

```text
[Step 1/4] AST & Architectural Role Classification
----------------------------------------------------------------------------
File Path                              | Detected Role          | Importers
----------------------------------------------------------------------------
src/demo_app/__init__.py               | additive_registry      | -
src/demo_app/api.py                    | standard               | -
src/demo_app/cli.py                    | additive_registry      | -
src/demo_app/exporter.py               | standard               | -
src/demo_app/normalizer.py             | standard               | src/demo_app/api.py
src/demo_app/schema.py                 | spine                  | -
```

**处理逻辑：**  
引擎静态扫描各模块的 AST 结构，识别其在并行开发中的**架构角色**：
1. **`spine`（架构脊柱 - 强制排队）：** 决定核心数据结构或高扇入的关键文件（含 `SCHEMA_VERSION` 的数据库模型，或入度 $\ge 5$ 的节点）。同一波次中**绝对禁止**同时安排两个修改 `spine` 文件的任务。
2. **`additive_registry`（累加注册表 - 安全并发追加）：** 仅用于注册命令、路由或导出的扩展点（`@app.command`, `@router.get`, `__all__`）。在不同分支中并发追加互不干扰，支持安全合并。
3. **`standard`（业务逻辑模块 - 互斥修改 + 依赖追踪）：** 常规应用逻辑。禁止两个任务并发修改同一标准模块，并严格追踪导入关系（**`Importers`** 列）。扫描器成功识别出 `api.py` 导入了 `normalizer.py`！

---

### 阶段 2. 化解语义 Merge-Bomb 与波次调度 (`[Step 2/4]`)

```text
[Step 2/4] Type-2 Semantic Merge-Bomb Detection & BSP Wave Schedule
----------------------------------------------------------------------------
  Collision Analysis:
    - Naive Scheduler : W(1-2)={normalizer.py, cli.py} vs W(2-1)={api.py}
                        -> DISJOINT WRITES (False Safe!)
    - BSPWaveScheduler: Detected semantic_collision between '1-2' and '2-1'
                        -> Overlap W(1-2) ^ I(2-1) on: src/demo_app/api.py
    - Additive Registry: Tasks '1-2' and '1-3' both touch 'src/demo_app/cli.py'
                        -> Role: ADDITIVE_REGISTRY (Parallel allowed in Wave 1)

  Wave Schedule:
    * Wave 1
        - track-a: Story 1-2 -> writes: src/demo_app/normalizer.py (+ shared: src/demo_app/cli.py)
        - track-b: Story 1-3 -> writes: src/demo_app/exporter.py (+ shared: src/demo_app/cli.py)
    * Wave 2
        - track-a: Story 1-1 -> writes: src/demo_app/schema.py
        - track-b: Story 2-1 -> writes: src/demo_app/api.py
    * Wave 3
        - track-a: Story 2-2 -> writes: src/demo_app/schema.py
```

**核心价值体现：**
1. **捕获潜在语义合并炸弹（`1-2` 与 `2-1`）：**
   - 传统调度器仅核对修改路径：`1-2` 写入 `normalizer.py`，`2-1` 写入 `api.py`，路径无交集（`DISJOINT WRITES -> False Safe!`），并发执行必将导致上线事故。
   - `BSPWaveScheduler` 构建**爆炸半径 ($I$)**：由于 `api.py` 导入了 `normalizer.py`，它落入了 `1-2` 的影响范围中。检测到 `semantic_collision` 后，调度器将 `1-2` 编入 **Wave 1**，而将 `2-1` 顺延至 **Wave 2**（确保新契约已合并入主干）。
2. **充分保留安全并发：** `1-2` 与 `1-3` 均修改 `cli.py`，因其属于 `ADDITIVE_REGISTRY`，允许在 **Wave 1** 中并发运行（分别分配至 `track-a` 与 `track-b`）。
3. **规范数据库迁移顺序：** `1-1` 与 `2-2` 均修改 `schema.py` (`spine`)，调度器将其严格分排在 **Wave 2** 与 **Wave 3**。

---

### 阶段 3. 准备固定槽位与范围守卫验证 (`[Step 3/4]`)

```text
[Step 3/4] Fixed-Slot Worktree Provisioning & PreToolUse Scope Guard
----------------------------------------------------------------------------
  Provisioned Fixed Slots (100% Clean git status: True):
    - track-a (.worktrees/track-a)
    - track-b (.worktrees/track-b)

  PreToolUse Scope Guard Check (track-a -> 'src/demo_app/schema.py'):
    - Decision : DENY
    - Violation: File 'src/demo_app/schema.py' cannot be modified by Wave 1 / track-a (stories ['1-2'])
    - Reason   : Locked by Spine Chain invariant.
```

**磁盘变化与防御措施：**
- 初始化固定工作区槽位：**`.worktrees/track-a`**（分支 `parallel/track-a`）与 **`.worktrees/track-b`**（分支 `parallel/track-b`）。槽位永久复用，切换波次时原地重置，避免生成冗余文件夹。
- 注入安全边界配置：
  - `.parallel-lock.json` — 机器可读的权限清单；
  - `.tmp/track-context.md` 与 `ACTIVE_TRACK.md` — 面向智能体的上下文边界；
  - 操作系统只读锁（`chmod a-w`）保护越界文件与 `spine` 核心。
- **`100% Clean git status: True`：** 配置文件通过 `.git/worktrees/<slot>/info/exclude` 屏蔽，`core.fileMode = false` 屏蔽权限变动。智能体读取到的始终是纯净的 Git 状态。
- **防御触发验证：** 演示调用 `evaluate_tool_access()` 模拟 `track-a` 中的智能体尝试写入 `src/demo_app/schema.py`。钩子拦截请求并直接返回 **`DENY`**。

---

### 阶段 4. 波次栅障与单调三方合并 (`[Step 4/4]`)

```text
[Step 4/4] Barrier Checkpoint & Monotonic 3-Way YAML Merge
----------------------------------------------------------------------------
  Merged Branches:
    - parallel/track-a
    - parallel/track-b

  Resolved sprint-status.yaml (0 conflict markers):
    - 1-2-normalize-telemetry-payload: done
    - 1-3-cli-batch-export: done
============================================================================
ALL 5 CORE INVARIANTS VERIFIED IN 3.21s
============================================================================
```

**单调状态解析原理：**
1. 并发执行前，`1-2` 与 `1-3` 在 `sprint-status.yaml` 中为相邻两行 `ready-for-dev`。
   - `track-a` 智能体将第一行更新为 `done`；
   - `track-b` 智能体并发将第二行更新为 `done`。
2. 常规 `git merge` 按 3 行文本块比对，误判定为冲突并写入 `<<<<<<< HEAD`。
3. **`sdd-parallel` 解决方案：** **单调 3-Way 合并器 (`status_merger.py`)** 依据进度半格 (`backlog (0) < ready-for-dev (1) < in-progress (2) < review (3) < done (4)`)，针对每个任务取三方最大值 $\max(\text{base}, \text{track-a}, \text{track-b})$。合并顺利达成且**零冲突标记**。
4. **槽位清理：** 解除只读锁定，清除旧波次 `.parallel-lock.json`，并将合并事务持久化至 **`.parallel/state.json`**。

---

## 3. 交互式实战任务（保留沙箱验证）

运行带有 `--keep-temp` 参数的演示：

```powershell
uv run sdd-parallel demo --keep-temp
```

复制输出中 `Workspace: ...` 路径（下文简称为 `<DEMO_DIR>`）。

---

### 步骤 1. 查看 Git 历史与事务日志
```powershell
git -C "<DEMO_DIR>" log --graph --oneline --all
```
*验证要点：* 可查看到初始提交及 Wave 1 的两次并行分支合并提交。`<DEMO_DIR>/.parallel/state.json` 记录了回滚锚点 `pre_merge_commit`。

---

### 步骤 2. 规划后续任务 (`plan`)
```powershell
# 1. 预览全量 5 项任务路线图：
uv run sdd-parallel plan --repo "<DEMO_DIR>" --include-done --dry-run

# 2. 规划剩余 3 项任务 (1-1, 2-1, 2-2)：
uv run sdd-parallel plan --repo "<DEMO_DIR>"
```
*磁盘变更：*
- `_bmad-output/planning-artifacts/` 保持完全未修改。
- `<DEMO_DIR>/_bmad-output/parallel-artifacts/` 生成 `execution-plan-parallel.md` 与 `wave-plan.json`。
- 任务 `1-1` 与 `2-1` 成为新的 Wave 1。

---

### 步骤 3. 为下一波次就绪固定槽位 (`prepare` 与 `run-guide`)
```powershell
uv run sdd-parallel prepare --repo "<DEMO_DIR>" --wave 1
uv run sdd-parallel run-guide --repo "<DEMO_DIR>" --wave 1
```
*检查 `.worktrees/track-b/`：*
- 现有槽位原地重置至最新 `master`。
- 查看 `.worktrees/track-b/.parallel-lock.json`：仅允许 `src/demo_app/api.py`，而 `schema.py` 明确锁定。
- `run-guide` 打印出面向各终端的操作指南。

---

### 步骤 4. 压力测试拦截守卫 (`check-lock`)
```powershell
# 测试 1：访问自身授权文件 (api.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/api.py"

# 测试 2：越界尝试修改邻近轨道的 schema.py
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/schema.py"
```
*预期输出：* 测试 1 返回 `{"decision": "allow"}`；测试 2 严正拦截并返回 `{"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}`。

---

### 步骤 5. 紧急中止未合并波次 (`abort-wave`)
```powershell
uv run sdd-parallel abort-wave --repo "<DEMO_DIR>" --wave 1
```
*结果：* 槽位完全复原并清空临时文件，主干无任何残留。

---

### 步骤 6. 事务性 LIFO 回滚已合并波次 (`rollback-wave`)
```powershell
uv run sdd-parallel rollback-wave --repo "<DEMO_DIR>" --wave 1 --save-backup
```
*效果核验：*
1. 自动生成备份分支 `backup/wave-1-before-rollback-...`。
2. `master` 回退至 Wave 1 合并前的提交。
3. `sprint-status.yaml` 中任务状态自动恢复为 `ready-for-dev`。

---

## 4. 场景 B：从零搭建 Greenfield 仓库实战

在第 3 节中我们体验了预设的演示环境，而本节将展示工程师如何在全新的纯净项目中进行端到端验证，无需手动编写数十行样板配置代码。

借助 **`sdd-parallel demo --scaffold-only --output-dir <路径>`**，引擎可以在 1 秒内为任意选定的 SDD 框架搭建具备真实规范、源代码与 Git 历史的干净沙箱。

---

### 路线 A：基于 GitHub Spec Kit 的全流程演练 (`--kit spec-kit`)

#### 第 1 步：快速生成 Spec Kit 沙箱并检查初始状态
```powershell
# 1. 在临时目录中极速搭建干净的 Spec Kit 沙箱
$SANDBOX = "$env:TEMP\eval-speckit"
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir $SANDBOX
Set-Location $SANDBOX

# 2. 检查生成的环境
git status
git log -1 --oneline
```
*生成的仓库结构：*
- `specs/001-telemetry/{spec.md, plan.md, tasks.md}` — 纯正的 GitHub Spec Kit 任务清单 (`- [ ] T001`, `- [ ] T002 [P]`, `- [ ] T003 [P]`, `- [ ] T004`, `- [ ] T005`)。
- `.specify/memory/constitution.md` 与 `.specify/feature.json` — 项目不可变的核心宪章与特征契约。
- `src/demo_app/` — 6 个包含二类语义冲突隐患的遥测应用代码模块。

#### 第 2 步：计算并行波次执行计划 (`plan`)
```powershell
uv run sdd-parallel plan
```
*执行过程：*
- 自动检测器通过 `specs/**/tasks.md` 和 `.specify/` 识别出 GitHub Spec Kit。
- AST 静态分析将 `schema.py` 归类为 `SPINE`，`cli.py` 归类为 `ADDITIVE_REGISTRY`，并识别出 `api.py` 与 `normalizer.py` 之间的跨模块调用依赖。
- 任务 `1-2` 和 `1-3` 被分配至 **Wave 1**，而任务 `2-1` 被隔离至 **Wave 2**，有效阻止了语义合并炸弹。

#### 第 3 步：准备固定轨道工作树槽位 (`prepare`)
```powershell
uv run sdd-parallel prepare --wave 1
```
*磁盘变化：*
- 分配并绑定固定槽位 `.worktrees/track-a` 与 `.worktrees/track-b`。
- 在各自工作树中生成 `.parallel-lock.json` 范围锁定清单。
- 将保护契约（`.specify/memory/constitution.md`、`spec.md` 等）施加操作系统级只读保护 (`chmod a-w`)。

#### 第 4 步：测试三层纵深防御范围守卫与只读拦截
```powershell
# 1. track-a 内合法修改授权文件：
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/normalizer.py
# -> {"decision": "allow"}

# 2. track-a 内尝试越权修改邻居任务锁定的 schema.py：
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/schema.py
# -> {"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}

# 3. 操作系统底层文件拦截测试（尝试写入受保护的 constitution.md）：
Add-Content -Path .worktrees\track-a\.specify\memory\constitution.md -Value "unauthorized"
# -> 预期阻断：System.UnauthorizedAccessException（OS 只读保护生效）
```

#### 第 5 步：模拟并行轨道独立提交
```powershell
# track-a 智能体完成任务 T002：
Set-Content -Path .worktrees\track-a\src\demo_app\normalizer.py -Value "def normalize_payload(raw): return {k.strip(): v.strip() for k, v in raw.items()}"
(Get-Content .worktrees\track-a\specs\001-telemetry\tasks.md) -replace '\[ \] T002', '[x] T002' | Set-Content .worktrees\track-a\specs\001-telemetry\tasks.md
git -C .worktrees\track-a commit -am "feat: complete task T002"

# track-b 智能体完成任务 T003：
Set-Content -Path .worktrees\track-b\src\demo_app\exporter.py -Value "def export_events(events): return len(events)"
(Get-Content .worktrees\track-b\specs\001-telemetry\tasks.md) -replace '\[ \] T003', '[x] T003' | Set-Content .worktrees\track-b\specs\001-telemetry\tasks.md
git -C .worktrees\track-b commit -am "feat: complete task T003"
```

#### 第 6 步：审计差异并执行屏障单调合并
```powershell
# 1. 针对 .parallel-lock.json 审计多提交差异：
uv run sdd-parallel verify-wave --wave 1
# -> {"wave": 1, "compliant": true, "violations": []}

# 2. 执行屏障合并与 3 路单调状态集成：
uv run sdd-parallel merge --wave 1
```
*结果核验：* `specs/001-telemetry/tasks.md` 中两个任务均被安全勾选为 `- [x] T002` 与 `- [x] T003`，且产生 **0 个 Git 冲突标记**。

#### 第 7 步：事务性 LIFO 波次回滚
```powershell
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```
*回滚验证：*
- 自动创建备份分支 `backup/wave-1-before-rollback-...`。
- `master` 干净还原到合并前的初始提交。
- `specs/001-telemetry/tasks.md` 中的任务状态多态恢复为未完成 `[ ]`。

---

### 路线 B：基于 BMAD 规范的演练 (`--kit bmad`)

对于使用 BMAD (`_bmad-output/`) 的项目，执行流程完全一致，系统自动完成协议适配：

```powershell
# 1. 搭建 BMAD 沙箱：
$BMAD_SANDBOX = "$env:TEMP\eval-bmad"
uv run sdd-parallel demo --kit bmad --scaffold-only --output-dir $BMAD_SANDBOX
Set-Location $BMAD_SANDBOX

# 2. 规划与就地准备：
uv run sdd-parallel plan
uv run sdd-parallel prepare --wave 1

# 3. 模拟各轨道提交（更新 sprint-status.yaml）：
(Get-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-2-normalize-telemetry-payload: ready-for-dev', '1-2-normalize-telemetry-payload: done' | Set-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-a commit -am "feat: finish story 1-2"

(Get-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-3-cli-batch-export: ready-for-dev', '1-3-cli-batch-export: done' | Set-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-b commit -am "feat: finish story 1-3"

# 4. 屏障校验与合并：
uv run sdd-parallel verify-wave --wave 1
uv run sdd-parallel merge --wave 1

# 5. 事务性 LIFO 回滚：
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```

---

### 5 大 SDD 框架关键差异速查表

| 执行步骤 / 关键机制 | `spec-kit` | `gsd` | `openspec` | `beads` | `bmad` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **沙箱构建命令** | `--kit spec-kit` | `--kit gsd` | `--kit openspec` | `--kit beads` | `--kit bmad` |
| **任务清单格式** | `specs/**/tasks.md` | `.planning/phases/*-PLAN.md` | `openspec/changes/*/tasks.md` | `.beads/beads.jsonl` | `sprint-status.yaml` |
| **智能体启动命令** | `/speckit.implement <id>` | `/gsd-execute-phase <num>` | `/opsx:apply <id>` | `bd update <id>` | `/bmad-build <id>` |
| **任务状态流转** | `- [ ]` $\to$ `- [x]` | 阶段完成 $\to$ `STATE.md` | `- [ ]` $\to$ `- [x]` | `open` $\to$ `closed` | `ready-for-dev` $\to$ `done` |
| **3 路合并目标文件** | `tasks.md` (Markdown) | `.planning/STATE.md` | `tasks.md` (Markdown) | `beads.jsonl` (JSONL) | `sprint-status.yaml` (YAML) |
| **只读保护契约** | `constitution.md`, `spec.md` | `STATE.md`, `ROADMAP.md` | `config.yaml`, `specs/**` | `config.yaml`, `metadata.json` | `epic-*-context.md` |

---

## 5. 高级架构师指南：Diátaxis-lite 导航

- 📐 **[架构白皮书 (`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`)](../../dossier/02_SYSTEM_CAPABILITIES_WHITE_PAPER.md)** — 衰减爆炸半径 $I(T)$ 理论推导、Kahn 算法无环验证、三层防御体系与状态半格代数。
- 🔌 **[生态与集成规范 (`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`)](../../dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md)** — 面向 **BMAD**、**GitHub Spec Kit (`.specify/`)**、**GSD (`.planning/`)**、**OpenSpec** 和 **Beads** 的适配器规格说明与 MCP 协议。
- 📊 **棕地验证与实战复盘 (Brownfield Verification)** — 在复杂多史诗生产仓库中落地 Two-Stage Assisted 模式的实战复盘与验证记录。
- 🛡️ **[核心不变量总结 (`README.md`)](ARCHITECTURE.md)** — 架构安全保障总览。

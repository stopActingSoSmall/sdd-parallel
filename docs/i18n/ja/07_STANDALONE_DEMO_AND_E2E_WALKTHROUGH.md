# ⚡ `sdd-parallel demo` 実践ガイド：4秒で体感する並行エージェントガバナンス

<p align="center">
  <b>🌐 言語 / Language:</b>
  <a href="../../dossier/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">English</a> |
  <a href="../zh-CN/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">简体中文</a> |
  <a href="../es/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Español</a> |
  <a href="../pt-BR/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Português (BR)</a> |
  <b>日本語</b> |
  <a href="../ko/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">한국어</a> |
  <a href="../ru/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Русский</a>
</p>

**Version:** 0.1.0

単一のリポジトリ内で複数の AI コーディングエージェント（Claude Code、Google Antigravity、Codex）を並行実行すると、開発速度を 2〜3 倍に加速できます。しかし、決定論的な調整なしでは、以下の3つの重大な事故がほぼ確実に発生します：

1. **暗黙の契約破壊（意味的 `Merge-Bomb`）：** エージェントAがサービスの関数シグネチャを変更し、隣接ブランチのエージェントBがコントローラー内で旧シグネチャの呼び出しを同時に記述します。ファイルが異なるため `git merge` は衝突なしで成功しますが、本番稼働時にランタイムで破綻します。
2. **スコープの幻覚（Scope Creep）：** 個別タスクを担当するエージェントが「ついでに」データベーススキーマや隣接モジュールに手を加え、並行タスクの前提条件を破壊します。
3. **ステータスファイルの書き込み競合：** 2つのエージェントがタスクを完了し、共有スプリントファイル（`sprint-status.yaml` や `STATE.md`）の隣接行を同時に変更するため、通常の `git merge` が `<<<<<<< HEAD` 衝突で失敗します。

**`sdd-parallel demo`** コマンドは、**APIキー不要・ネットワーク接続不要・既存プロジェクト設定不要で、約4秒で実 Git リポジトリ上にて**、`sdd-parallel` がこれら3つの事故をどのように未然防止するかを検証できるように設計されています。

---

## 1. デモの動作原理：固定テキストではなく、本物のディスク処理

`uv run sdd-parallel demo` は**コンソール出力の模擬再現ではありません**。実行ごとに、エンジンはディスク上で100%本物の処理を実行します：
- OSの一時ディレクトリに本物の Git リポジトリを初期化 (`Workspace: .../sdd-parallel-demo-<id>`)；
- 抽象構文木（`AST`）を用いて実際の Python ソースを解析；
- 独立した `git worktree` スロットをチェックアウトし、OS レベルの読み取り専用ロックを適用；
- 2つの並行ブランチから本物のコミットを作成し、`git merge --no-ff` を実行。

本コマンドは、対応する 5 つの SDD フレームワークの個別検証、または全体のマトリクス検証をサポートしています：

```bash
# モード 1: デフォルトフレームワーク（BMAD）のクイック検証
uv run sdd-parallel demo

# モード 2: 特定の SDD フレームワーク検証（bmad, spec-kit, gsd, openspec, beads）
uv run sdd-parallel demo --kit spec-kit

# モード 3: 全マトリクス検証（5つの全フレームワークを一括検証しスコアカードを出力）
uv run sdd-parallel demo --kit all

# モード 4: インタラクティブサンドボックス（手動操作のためにリポジトリをディスクに残す）
uv run sdd-parallel demo --keep-temp

# モード 5: クリーンサンドボックスの高速生成（手動 E2E 検証向け）
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir "$env:TEMP/eval-speckit"
```

### 5大フレームワークの一括マトリクス検証結果 (`--kit all`)

`uv run sdd-parallel demo --kit all` を実行すると、全 5 つのアダプターを順次実行し、統合スコアカードを出力します：

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

### 5大 SDD フレームワークのサンドボックス成果物およびガバナンスマトリクス

| フレームワーク (`--kit`) | 生成される仕様ファイル | 保護対象の不変契約 (`forbidden_files`) | 単調 3-Way マージ対象 |
| :--- | :--- | :--- | :--- |
| `spec-kit` | `specs/001-telemetry/{spec.md, plan.md, tasks.md}` | `.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md` | `specs/001-telemetry/tasks.md` (`- [x]`) |
| `gsd` | `.planning/phases/01-core/*-PLAN.md` (XML `<files>`) | `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md` | `.planning/STATE.md` (Post-Merge Hook) |
| `openspec` | `openspec/changes/<id>/{proposal.md, tasks.md}` | `openspec/config.yaml`, `openspec/specs/**`, 隣接 `changes/<sibling>/**` | `openspec/changes/<id>/tasks.md` (`- [x]`) |
| `beads` | `.beads/beads.jsonl` (型付き `dependencies`) | `.beads/config.yaml` | `.beads/beads.jsonl` (JSONL `max(rank)`) |
| `bmad` | `_bmad-output/planning-artifacts/epics.md`, `spec-*.md` | `_bmad-output/**/epic-*-context.md` | `sprint-status.yaml` (`max(rank)`) |

> **実行ごとに何が変わるか？**  
> `Workspace:` 行のランダムサフィックスと全体の検証時間 `VERIFIED IN X.XXs`（ディスク速度依存、通常2〜4.5秒）のみです。ロール分類表、検出された衝突、ウェーブスケジュール、マージ結果は**100%同一**です。スケジューラは非決定論的な LLM ではなく、グラフ数学に基づいているためです。

---

## 2. ステップ解説：各フェーズで何が起きているか

### フェーズ 0. 出発点（ディスク上の初期状態）

初期コミット（`chore: initial demo repository`）で一時ディレクトリが生成され、6つのモジュールから成るテレメトリアプリと **BMAD** 構造の仕様が配置されます：

```text
sdd-parallel-demo-<id>/
├── src/demo_app/
│   ├── __init__.py          # パッケージエクスポート (__all__)
│   ├── schema.py            # DBスキーマとマイグレーション (SCHEMA_VERSION = 1, auto_migrate())
│   ├── cli.py               # CLI 登録ハブ (@app.command)
│   ├── normalizer.py        # データ正規化：normalize_payload(raw)
│   ├── api.py               # HTTPエンドポイント：normalizer.py から normalize_payload をインポート！
│   └── exporter.py          # イベント一括出力：export_events(events)
└── _bmad-output/
    ├── planning-artifacts/          # エピックのロードマップとコンパイル済みコンテキスト
    └── implementation-artifacts/    # sprint-status.yaml + 5つのタスク仕様書 (spec-*.md)
```

**待機キュー内の5つのストーリー (`ready-for-dev`) と潜む3つの罠：**
- **`1-1`** *(スキーマを v2 に更新)* $\to$ `schema.py` を変更（`1-2` に依存）。
- **`1-2`** *(ペイロードの正規化)* $\to$ `normalizer.py` を変更し、`cli.py` にコマンドを追加。
- **`1-3`** *(CLI一括エクスポート)* $\to$ `exporter.py` を変更し、同じく `cli.py` にコマンドを追加 *(罠1：共通の `cli.py` の競合)*。
- **`2-1`** *(取り込みAPIの公開)* $\to$ `api.py` のみを変更 *(罠2 — 隠れたセマンティック Merge-Bomb：`api.py` は、現在 `1-2` で書き換え中の `normalizer.py` から関数をインポートしている！)*。
- **`2-2`** *(監査ログスキーマの追加)* $\to$ `schema.py` を変更（`1-1` に依存）*(罠3：二重DBマイグレーション)*。

---

### フェーズ 1. AST によるアーキテクチャロールの判定 (`[Step 1/4]`)

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

**処理内容：**  
エンジンはファイル名の固定リストに頼らず、モジュールの AST を解析して並行開発時の**並行性ロール**を判定します：
1. **`spine`（背骨・中核 — 厳格な順序付け）：** データ構造を決定づけるファイルや多くの依存を集約するファイル（`SCHEMA_VERSION`、`models.py`、または入次数 $\ge 5$）。同一ウェーブ内に `spine` を変更するタスクが複数配置されることは**絶対にありません**。
2. **`additive_registry`（追記型レジストリ — 安全な並行追加）：** コマンド、ルート、エクスポートの登録のみを行うハブ（`@app.command`, `@router.get`, `__all__`）。並行ブランチで同時に追記しても安全にマージ可能です。
3. **`standard`（標準業務モジュール — 相互排他 ＋ 依存追跡）：** 一般的なビジネスロジック。同一ファイルを同時に変更することは禁止され、逆方向のインポート関係（**`Importers`** 列）が監視されます。スキャナは `api.py` が `normalizer.py` をインポートしていることを捉えています！

---

### フェーズ 2. セマンティック Merge-Bomb の無効化とウェーブ生成 (`[Step 2/4]`)

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

**重要なポイント：**
1. **`1-2` と `2-1` の潜在的衝突の回避：**
   - 単純なスケジューラは書き込み先のみを比較します：`1-2` は `normalizer.py`、`2-1` は `api.py`。書き込み先が重複しないため安全と誤認します（`DISJOINT WRITES -> False Safe!`）。
   - `BSPWaveScheduler` は**影響半径 ($I$)** を計算します：`api.py` が `normalizer.py` を参照しているため、`api.py` は `1-2` の影響圏に入ります。セマンティック衝突（`semantic_collision`）を検出し、`1-2` を **Wave 1** に配置し、`2-1` を **Wave 2** へ延期します（新契約が統合された後に実行）。
2. **有益な並行性の維持：** `1-2` と `1-3` は共に `cli.py` を変更しますが、それが `ADDITIVE_REGISTRY` であるため、**Wave 1** 内で安全に並行実行されます（`track-a` と `track-b`）。
3. **スキーママイグレーションの整列：** `1-1` と `2-2` は `schema.py` (`spine`) を触るため、**Wave 2** と **Wave 3** に順序正しく直列化されます。

---

### フェーズ 3. 固定スロットの準備と境界ガード検証 (`[Step 3/4]`)

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

**ディスク上の設定と縦深防御：**
- 恒久スロットを配置：**`.worktrees/track-a`**（ブランチ `parallel/track-a`）および **`.worktrees/track-b`**（ブランチ `parallel/track-b`）。ウェーブ移行時もディレクトリを再作成せず、その場でリセットされます。
- ガードファイルの注入：
  - `.parallel-lock.json` — 許可および禁止ファイルを記載した機械可読マニフェスト；
  - `.tmp/track-context.md` と `ACTIVE_TRACK.md` — エージェント向けの明示的コンテキスト境界；
  - 他トラックのファイルおよび `spine` ファイルに対する OS の読み取り専用ロック（`chmod a-w`）。
- **`100% Clean git status: True`：** 管理ファイルは `.git/worktrees/<slot>/info/exclude` で除外され、権限変更は `core.fileMode = false` で無視されます。エージェントから見える作業ツリーは常に完璧にクリーンです。
- **迎撃テスト：** デモは `evaluate_tool_access()` を呼び出し、`track-a` のエージェントが `src/demo_app/schema.py` に書き込もうとする状況を再現します。フックがマニフェストと照合し、即座に **`DENY`** を返します。

---

### フェーズ 4. ウェーブの統合と単調3-Wayマージ (`[Step 4/4]`)

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

**単調状態マージの仕組み：**
1. 実行前、`1-2` と `1-3` は `sprint-status.yaml` の隣接行で `ready-for-dev` でした。
   - `track-a` のエージェントが 1 行目を `done` に更新；
   - `track-b` のエージェントが並行して 2 行目を `done` に更新。
2. 通常の `git merge` は 3 行単位でテキスト比較を行うため、衝突と見なして `<<<<<<< HEAD` を書き込みます。
3. **`sdd-parallel` の解決策：** **単調3-Wayマージャー (`status_merger.py`)** がステータス進行半束（`backlog (0) < ready-for-dev (1) < in-progress (2) < review (3) < done (4)`）に従い、各タスクについて3者の最大値 $\max(\text{base}, \text{track-a}, \text{track-b})$ を採用します。これにより**衝突マーカーゼロ**で両タスクが `done` に遷移します。
4. **マージ後処理：** 読み取り専用ロックを解除し、Wave 1 の `.parallel-lock.json` を削除し、マージトランザクションを **`.parallel/state.json`** に記録します。

---

## 3. 実践サンドボックス体験（`--keep-temp`）

ディレクトリを保持してデモを実行します：

```powershell
uv run sdd-parallel demo --keep-temp
```

出力された `Workspace: ...` のパス（以下 `<DEMO_DIR>`）をコピーします。

---

### ステップ 1. Git 履歴とトランザクションログを確認
```powershell
git -C "<DEMO_DIR>" log --graph --oneline --all
```
*確認ポイント：* 初期コミットと Wave 1 の2つの統合コミットが表示されます。`<DEMO_DIR>/.parallel/state.json` にはロールバック用アンカー `pre_merge_commit` が記録されています。

---

### ステップ 2. 残りタスクの計画を策定 (`plan`)
```powershell
# 1. 完了済みを含む全5タスクの構成を確認：
uv run sdd-parallel plan --repo "<DEMO_DIR>" --include-done --dry-run

# 2. 残り3タスク (1-1, 2-1, 2-2) のウェーブ計画を作成：
uv run sdd-parallel plan --repo "<DEMO_DIR>"
```
*ディスクの変化：*
- `_bmad-output/planning-artifacts/` 配下は一切変更されません。
- `<DEMO_DIR>/_bmad-output/parallel-artifacts/` に `execution-plan-parallel.md` と `wave-plan.json` が生成されます。
- `1-1` と `2-1` が次期 Wave 1 に割り当てられます。

---

### ステップ 3. 次期ウェーブ用固定スロットの初期化 (`prepare` と `run-guide`)
```powershell
uv run sdd-parallel prepare --repo "<DEMO_DIR>" --wave 1
uv run sdd-parallel run-guide --repo "<DEMO_DIR>" --wave 1
```
*`.worktrees/track-b/` の確認：*
- 既存スロットが最新の `master` に原地リセットされます。
- `.parallel-lock.json` を開くと、`src/demo_app/api.py` のみが許可され、`schema.py` はロックされています。
- `run-guide` により各ターミナル向けの実行コマンドが表示されます。

---

### ステップ 4. 実行時インターセプトの検証 (`check-lock`)
```powershell
# テスト 1: 担当ファイルへのアクセス (api.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/api.py"

# テスト 2: 範囲外ファイルへのアクセス試行 (schema.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/schema.py"
```
*結果：* テスト1は `{"decision": "allow"}` を返し、テスト2は `{"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}` を返して確実に阻止します。

---

### ステップ 5. 未マージウェーブの中止 (`abort-wave`)
```powershell
uv run sdd-parallel abort-wave --repo "<DEMO_DIR>" --wave 1
```
*結果：* 一時ブランチとロックが消去され、メインブランチを一切汚さずに復元されます。

---

### ステップ 6. マージ済みウェーブの LIFO ロールバック (`rollback-wave`)
Wave 1（`1-2` と `1-3`）を安全に取り消します：

```powershell
uv run sdd-parallel rollback-wave --repo "<DEMO_DIR>" --wave 1 --save-backup
```
*確認事項：*
1. 安全バックアップブランチ `backup/wave-1-before-rollback-...` が保存されます。
2. `master` が Wave 1 マージ直前のコミットに戻ります。
3. `sprint-status.yaml` のタスク状態が `done` から `ready-for-dev` に復元されます。

---

## 4. シナリオ B：ゼロからの Greenfield リポジトリ検証

第3章では事前構築されたリポジトリを探索しましたが、本シナリオでは新規プロジェクトにおいて設定ファイルを何十行も手書きすることなく、`sdd-parallel` の全サイクルを検証します。

**`sdd-parallel demo --scaffold-only --output-dir <パス>`** を利用することで、エンジンは選択したフレームワークの実仕様、ソースコード、Git 履歴を備えたクリーンなサンドボックスを 1 秒以内に生成します。

---

### パス A：GitHub Spec Kit による実践演習 (`--kit spec-kit`)

#### ステップ 1. Spec Kit サンドボックスの生成と初期状態確認
```powershell
# 1. 一時ディレクトリにクリーンな Spec Kit サンドボックスを生成
$SANDBOX = "$env:TEMP\eval-speckit"
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir $SANDBOX
Set-Location $SANDBOX

# 2. 生成されたファイル構成を確認
git status
git log -1 --oneline
```
*生成されるリポジトリ構成：*
- `specs/001-telemetry/{spec.md, plan.md, tasks.md}` — GitHub Spec Kit 準拠のタスクリスト (`- [ ] 1-1`, `- [ ] 1-2`, `- [ ] 1-3`, `- [ ] 2-1`, `- [ ] 2-2`)。
- `.specify/memory/constitution.md`, `.specify/feature.json` — プロジェクトの不変契約。
- `src/demo_app/` — 隠れた第2種意味的衝突を含む 6 つの Python モジュール。

#### ステップ 2. 並行ウェーブ計画の策定 (`plan`)
```powershell
uv run sdd-parallel plan
```
*内部動作：*
- `specs/**/tasks.md` および `.specify/` から GitHub Spec Kit を自動検出。
- AST 静的解析が `schema.py` を `SPINE`、`cli.py` を `ADDITIVE_REGISTRY` に分類し、`api.py` と `normalizer.py` 間の依存を検出。
- タスク `1-2` と `1-3` を **Wave 1** にまとめ、`2-1` を **Wave 2** に分離してマージボムを防止。

#### ステップ 3. 固定トラックスロットの準備 (`prepare`)
```powershell
uv run sdd-parallel prepare --wave 1
```
*ディスク上の変化：*
- 固定スロット `.worktrees/track-a` と `.worktrees/track-b` が初期化・リンク。
- 各スロットに `.parallel-lock.json` スコープロックマニフェストが生成。
- 不変契約（`.specify/memory/constitution.md`, `spec.md` など）に OS 読み取り専用権限 (`chmod a-w`) を設定。

#### ステップ 4. 3層防御スコープガードと OS レベル耐性テスト
```powershell
# 1. track-a の許可ファイルに対する正規変更：
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/normalizer.py
# -> {"decision": "allow"}

# 2. track-a による範囲外アクセス試行（schema.py への変更）：
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/schema.py
# -> {"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}

# 3. OS レベルの直接書き込みテスト（保護された constitution.md への追記）：
Add-Content -Path .worktrees\track-a\.specify\memory\constitution.md -Value "unauthorized"
# -> 期待される結果: System.UnauthorizedAccessException（OS Read-Only ロック作動）
```

#### ステップ 5. 各トラックからの並行コミットシミュレーション
```powershell
# track-a のエージェントがタスク 1-2 を完了：
Set-Content -Path .worktrees\track-a\src\demo_app\normalizer.py -Value "def normalize_payload(raw): return {k.strip(): v.strip() for k, v in raw.items()}"
(Get-Content .worktrees\track-a\specs\001-telemetry\tasks.md) -replace '\[ \] 1-2', '[x] 1-2' | Set-Content .worktrees\track-a\specs\001-telemetry\tasks.md
git -C .worktrees\track-a commit -am "feat: complete task 1-2"

# track-b のエージェントがタスク 1-3 を完了：
Set-Content -Path .worktrees\track-b\src\demo_app\exporter.py -Value "def export_events(events): return len(events)"
(Get-Content .worktrees\track-b\specs\001-telemetry\tasks.md) -replace '\[ \] 1-3', '[x] 1-3' | Set-Content .worktrees\track-b\specs\001-telemetry\tasks.md
git -C .worktrees\track-b commit -am "feat: complete task 1-3"
```

#### ステップ 6. 差分監査およびバリア単調マージ
```powershell
# 1. .parallel-lock.json に基づく差分監査：
uv run sdd-parallel verify-wave --wave 1
# -> {"wave": 1, "compliant": true, "violations": []}

# 2. バリア統合と 3-way 単調状態マージ：
uv run sdd-parallel merge --wave 1
```
*確認事項：* `specs/001-telemetry/tasks.md` において `- [x] 1-2` と `- [x] 1-3` が **Git 競合マーカーゼロ** で統合されます。

#### ステップ 7. トランザクション LIFO ロールバック
```powershell
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```
*ロールバック確認：*
- バックアップブランチ `backup/wave-1-before-rollback-...` が自動作成。
- `master` がマージ前の初期コミットへクリーンに戻る。
- `specs/001-telemetry/tasks.md` 内のタスク状態が未完了 `[ ]` に復元。

---

### パス B：BMAD による実践演習 (`--kit bmad`)

BMAD (`_bmad-output/`) を採用しているプロジェクトでも、全く同じコマンド体系で動作します：

```powershell
# 1. BMAD サンドボックスの生成：
$BMAD_SANDBOX = "$env:TEMP\eval-bmad"
uv run sdd-parallel demo --kit bmad --scaffold-only --output-dir $BMAD_SANDBOX
Set-Location $BMAD_SANDBOX

# 2. 計画策定と準備：
uv run sdd-parallel plan
uv run sdd-parallel prepare --wave 1

# 3. タスク進捗のシミュレーション（sprint-status.yaml の更新）：
(Get-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-2-normalize-telemetry-payload: ready-for-dev', '1-2-normalize-telemetry-payload: done' | Set-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-a commit -am "feat: finish story 1-2"

(Get-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-3-cli-batch-export: ready-for-dev', '1-3-cli-batch-export: done' | Set-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-b commit -am "feat: finish story 1-3"

# 4. バリア検証とマージ：
uv run sdd-parallel verify-wave --wave 1
uv run sdd-parallel merge --wave 1

# 5. トランザクションロールバック：
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```

---

### 5大フレームワーク比較早見表

| 項目 / メカニズム | `spec-kit` | `gsd` | `openspec` | `beads` | `bmad` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **サンドボックス生成** | `--kit spec-kit` | `--kit gsd` | `--kit openspec` | `--kit beads` | `--kit bmad` |
| **タスク形式** | `specs/**/tasks.md` | `.planning/phases/*-PLAN.md` | `openspec/changes/*/tasks.md` | `.beads/beads.jsonl` | `sprint-status.yaml` |
| **エージェント実行コマンド** | `/speckit.implement <id>` | `/gsd-execute-phase <num>` | `/opsx:apply <id>` | `bd update <id>` | `/bmad-build <id>` |
| **ステータス遷移** | `- [ ]` $\to$ `- [x]` | フェーズ完了 $\to$ `STATE.md` | `- [ ]` $\to$ `- [x]` | `open` $\to$ `closed` | `ready-for-dev` $\to$ `done` |
| **3-way マージ対象** | `tasks.md` (Markdown) | `.planning/STATE.md` | `tasks.md` (Markdown) | `beads.jsonl` (JSONL) | `sprint-status.yaml` (YAML) |
| **保護対象の契約** | `constitution.md`, `spec.md` | `STATE.md`, `ROADMAP.md` | `config.yaml`, `specs/**` | `config.yaml`, `metadata.json` | `epic-*-context.md` |

---

## 5. シニア／スタッフエンジニア向け Diátaxis-lite ナビゲーション

- 📐 **[アーキテクチャホワイトペーパー (`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`)](../../dossier/02_SYSTEM_CAPABILITIES_WHITE_PAPER.md)** — 減衰影響半径 $I(T)$ の数理モデル、Kahn の非巡回性証明、三層防御アーキテクチャ、ステータス半束の代数構造。
- 🔌 **[エコシステムおよび統合仕様書 (`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`)](../../dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md)** — **BMAD**、**GitHub Spec Kit (`.specify/`)**、**GSD (`.planning/`)**、**OpenSpec**、**Beads** との接続規約と MCP 定義。
- 📊 **実環境における検証と事例 (Brownfield Verification)** — 複雑な複数エピック本番リポジトリにおける Two-Stage Assisted ワークフローの実証記録。
- 🛡️ **[基本不変条件の一覧 (`README.md`)](../../../README.md#-core-invariants)** — システムの安全保証チェックリスト。

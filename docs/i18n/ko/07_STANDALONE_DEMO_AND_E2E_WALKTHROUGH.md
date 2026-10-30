# ⚡ `sdd-parallel demo` 실전 가이드: 4초 만에 확인하는 병렬 에이전트 거버넌스

<p align="center">
  <b>🌐 언어 / Language:</b>
  <a href="../../dossier/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">English</a> |
  <a href="../zh-CN/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">简体中文</a> |
  <a href="../es/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Español</a> |
  <a href="../pt-BR/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Português (BR)</a> |
  <a href="../ja/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">日本語</a> |
  <b>한국어</b> |
  <a href="../ru/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Русский</a>
</p>

**Version:** 0.1.0  

동일한 저장소 내에서 여러 AI 코딩 에이전트(Claude Code, Google Antigravity, Codex)를 병렬 실행하면 개발 속도를 2~3배 끌어올릴 수 있습니다. 하지만 엄격한 거버넌스가 결여되면 거의 필연적으로 다음과 같은 3가지 치명적 장애를 겪게 됩니다:

1. **조용한 인터페이스 파괴(제2종 시맨틱 `Merge-Bomb`):** 에이전트 A가 서비스 계층의 함수 시그니처를 수정하는 동안, 인접 브랜치의 에이전트 B가 컨트롤러에서 이전 시그니처를 호출하도록 작성합니다. 서로 다른 파일이므로 `git merge`는 텍스트 충돌 없이 완료되지만 배포 후 런타임에서 폭발합니다.
2. **범위 환각(Scope Creep):** 개별 작업을 맡은 에이전트가 "개선"을 핑계로 데이터베이스 스키마나 다른 모듈을 건드려 동시 작업의 전제를 무너뜨립니다.
3. **상태 파일 동시 수정 충돌:** 두 에이전트가 각각 작업을 마치고 공유 스프린트 파일(`sprint-status.yaml` 또는 `STATE.md`)의 인접 행을 수정하여 표준 `git merge`가 `<<<<<<< HEAD` 충돌로 중단됩니다.

**`sdd-parallel demo`** 명령어는 **API 키나 인터넷 연결, 복잡한 프로젝트 설정 없이 실제 Git 저장소에서 약 4초 만에** `sdd-parallel`이 이 3가지 장애를 어떻게 원천 차단하는지 검증하도록 설계되었습니다.

---

## 1. 데모 동작 원리: 텍스트 모의 출력이 아닌 실제 디스크 작업

`uv run sdd-parallel demo`는 **단순 콘솔 흉내내기가 아닙니다**. 실행할 때마다 엔진은 디스크 상에서 100% 실제 작업을 수행합니다:
- OS 임시 디렉터리에 실제 Git 저장소를 초기화합니다 (`Workspace: .../sdd-parallel-demo-<id>`);
- 추상 구문 트리(`AST`)를 통해 실제 Python 코드를 파싱합니다;
- 격리된 `git worktree` 슬롯을 체크아웃하고 OS 레벨의 읽기 전용 잠금을 적용합니다;
- 두 개의 병렬 브랜치에서 실제 커밋을 생성하고 `git merge --no-ff`를 수행합니다.

이 명령어는 지원되는 5개 SDD 프레임워크 각각에 대한 대상 검증 또는 전체 매트릭스 검증을 지원합니다:

```bash
# 모드 1: 기본 프레임워크(BMAD) 빠른 검증
uv run sdd-parallel demo

# 모드 2: 특정 SDD 프레임워크 검증 (bmad, spec-kit, gsd, openspec, beads)
uv run sdd-parallel demo --kit spec-kit

# 모드 3: 전체 매트릭스 검증 (5개 프레임워크 일괄 검증 및 스코어카드 출력)
uv run sdd-parallel demo --kit all

# 모드 4: 대화형 샌드박스 (수동 실험을 위해 저장소를 디스크에 보존)
uv run sdd-parallel demo --keep-temp

# 모드 5: 클린 샌드박스 초고속 프로비저닝 (수동 E2E 검증용)
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir "$env:TEMP/eval-speckit"
```

### 5대 프레임워크 전체 매트릭스 검증 결과 (`--kit all`)

`uv run sdd-parallel demo --kit all`을 실행하면 엔진이 5개 어댑터를 순차 검증하고 종합 스코어카드를 출력합니다:

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

### 5대 SDD 프레임워크 샌드박스 산출물 및 거버넌스 매트릭스

| 프레임워크 (`--kit`) | 생성되는 명세 파일 경로 | 보호 대상 불변 계약 (`forbidden_files`) | 단조 3-Way 머지 대상 |
| :--- | :--- | :--- | :--- |
| `spec-kit` | `specs/001-telemetry/{spec.md, plan.md, tasks.md}` | `.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md` | `specs/001-telemetry/tasks.md` (`- [x]`) |
| `gsd` | `.planning/phases/01-core/*-PLAN.md` (XML `<files>`) | `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md` | `.planning/STATE.md` (Post-Merge Hook) |
| `openspec` | `openspec/changes/<id>/{proposal.md, tasks.md}` | `openspec/config.yaml`, `openspec/specs/**`, 형제 `changes/<sibling>/**` | `openspec/changes/<id>/tasks.md` (`- [x]`) |
| `beads` | `.beads/beads.jsonl` (타입화된 `dependencies`) | `.beads/config.yaml` | `.beads/beads.jsonl` (JSONL `max(rank)`) |
| `bmad` | `_bmad-output/planning-artifacts/epics.md`, `spec-*.md` | `_bmad-output/**/epic-*-context.md` | `sprint-status.yaml` (`max(rank)`) |

> **반복 실행 시 무엇이 달라지나요?**  
> `Workspace:` 줄의 임의 접미사와 총 소요 시간 `VERIFIED IN X.XXs`(디스크 속도에 따라 보통 2~4.5초)만 달라집니다. 역할 분류표, 검출된 충돌, 웨이브 일정, 병합 결과는 **100% 동일**합니다. 스케줄러가 확률적인 LLM이 아닌 결정론적 그래프 수학에 기반하기 때문입니다.

---

## 2. 단계별 분석: 각 실행 단계에서 일어나는 일

### 프레임 0. 출발점 (디스크 상의 초기 상태)

초기 커밋(`chore: initial demo repository`)으로 임시 디렉터리가 생성되며, 6개의 텔레메트리 모듈과 **BMAD** 구조의 명세 파일이 배치됩니다:

```text
sdd-parallel-demo-<id>/
├── src/demo_app/
│   ├── __init__.py          # 패키지 내보내기 (__all__)
│   ├── schema.py            # DB 스키마 및 마이그레이션 (SCHEMA_VERSION = 1, auto_migrate())
│   ├── cli.py               # CLI 등록 허브 (@app.command)
│   ├── normalizer.py        # 데이터 정규화: normalize_payload(raw)
│   ├── api.py               # HTTP 엔드포인트: normalizer.py에서 normalize_payload를 임포트!
│   └── exporter.py          # 이벤트 일괄 내보내기: export_events(events)
└── _bmad-output/
    ├── planning-artifacts/          # 에픽 로드맵 및 사전 컴파일된 컨텍스트
    └── implementation-artifacts/    # sprint-status.yaml + 5개 작업 명세서 (spec-*.md)
```

**대기 큐의 5가지 스토리 (`ready-for-dev`)와 그에 숨겨진 3가지 함정:**
- **`1-1`** *(스키마 v2 업그레이드)* $\to$ `schema.py` 수정 (`1-2`에 의존).
- **`1-2`** *(페이로드 정규화)* $\to$ `normalizer.py`를 수정하고 `cli.py`에 명령 등록.
- **`1-3`** *(CLI 일괄 내보내기)* $\to$ `exporter.py`를 수정하고 마찬가지로 `cli.py`에 명령 등록 *(함정 1: 공유 `cli.py` 경합)*.
- **`2-1`** *(수신 API 노출)* $\to$ `api.py`만 수정 *(함정 2 — 숨겨진 시맨틱 Merge-Bomb: `api.py`는 `1-2`에서 현재 재작성 중인 `normalizer.py`의 함수를 임포트함!)*.
- **`2-2`** *(감사 로그 스키마 추가)* $\to$ `schema.py` 수정 (`1-1`에 의존) *(함정 3: 이중 DB 마이그레이션)*.

---

### 프레임 1. AST 아키텍처 역할 분류 (`[Step 1/4]`)

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

**동작 원리:**  
엔진은 고정된 파일명 목록에 의존하지 않고 각 모듈의 AST 구조를 분석하여 병렬 실행 시의 **동시성 프로파일**을 판별합니다:
1. **`spine` (아키텍처 척추 — 엄격한 직렬 큐):** 데이터 구조를 결정하거나 높은 팬인을 갖는 핵심 파일(`SCHEMA_VERSION`, `models.py` 또는 진입 차수 $\ge 5$). 동일 웨이브에 `spine`을 수정하는 두 작업이 동시에 배치되는 일은 **절대 없습니다**.
2. **`additive_registry` (추가형 레지스트리 — 안전한 병렬 추가):** 명령어, 라우트, 내보내기를 등록하기만 하는 확장 허브(`@app.command`, `@router.get`, `__all__`). 병렬 브랜치에서 동시에 추가해도 충돌 없이 병합 가능합니다.
3. **`standard` (표준 비즈니스 모듈 — 상호 배타 수정 + 의존성 추적):** 일반 비즈니스 로직. 동일 표준 모듈에 대한 동시 수정은 차단되며 역방향 임포트 관계(**`Importers`** 열)가 감시됩니다. 스캐너가 `api.py`가 `normalizer.py`를 임포트함을 정확히 포착했습니다!

---

### 프레임 2. 시맨틱 Merge-Bomb 무력화 및 웨이브 스케줄링 (`[Step 2/4]`)

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

**핵심 가치 요약:**
1. **숨겨진 잠재적 충돌(`1-2`와 `2-1`)의 회피:**
   - 단순 스케줄러는 쓰기 경로만 대조합니다: `1-2`는 `normalizer.py`, `2-1`은 `api.py`. 파일이 달라 안전하다고 오판합니다(`DISJOINT WRITES -> False Safe!`).
   - `BSPWaveScheduler`는 **영향 반경($I$)**을 계산합니다: `api.py`가 `normalizer.py`를 참조하므로 `api.py`는 `1-2`의 영향권에 들어옵니다. 시맨틱 충돌(`semantic_collision`)을 감지하여 `1-2`를 **Wave 1**에 배치하고 `2-1`을 **Wave 2**로 이연합니다.
2. **안전한 유효 병렬성 보존:** `1-2`와 `1-3`은 모두 `cli.py`를 수정하지만, 이것이 `ADDITIVE_REGISTRY`이므로 **Wave 1**에서 안전하게 병렬 실행됩니다(`track-a` 및 `track-b`).
3. **스키마 마이그레이션 순서화:** `1-1`과 `2-2`는 `schema.py`(`spine`)를 건드리므로 **Wave 2**와 **Wave 3**으로 자연스럽게 직렬화됩니다.

---

### 프레임 3. 고정 슬롯 준비 및 스코프 가드 검증 (`[Step 3/4]`)

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

**디스크 변경 사항 및 심층 방어:**
- 영구 슬롯 배치: **`.worktrees/track-a`**(브랜치 `parallel/track-a`) 및 **`.worktrees/track-b`**(브랜치 `parallel/track-b`). 새 웨이브 전환 시에도 에디터 폴더를 삭제하지 않고 제자리에서 리셋됩니다.
- 가드 설정 파일 주입:
  - `.parallel-lock.json` — 허용 및 잠금 파일의 기계 판독 매니페스트;
  - `.tmp/track-context.md` 및 `ACTIVE_TRACK.md` — 에이전트를 위한 명시적 경계 안내서;
  - 타 트랙 및 spine 파일에 대한 OS 레벨 읽기 전용 잠금(`chmod a-w`).
- **`100% Clean git status: True`:** 제어 파일은 `.git/worktrees/<slot>/info/exclude`로 마스킹되고, 권한 변경은 `core.fileMode = false`로 무시됩니다. 에이전트가 마주하는 작업 공간은 언제나 무결합니다.
- **차단 테스트:** 데모는 `evaluate_tool_access()`를 호출하여 `track-a`의 에이전트가 `src/demo_app/schema.py`를 수정하려는 상황을 재현합니다. 훅이 매니페스트를 검사하고 즉시 **`DENY`**를 반환합니다.

---

### 프레임 4. 웨이브 통합 및 단조 3-Way 병합 (`[Step 4/4]`)

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

**단조 상태 병합 원리:**
1. 실행 전 `sprint-status.yaml`에서 `1-2`와 `1-3`은 인접한 두 줄의 `ready-for-dev`였습니다.
   - `track-a` 에이전트가 첫 줄을 `done`으로 변경;
   - `track-b` 에이전트가 병렬로 둘째 줄을 `done`으로 변경.
2. 표준 `git merge`는 3줄 단위 비교로 인해 텍스트 충돌로 판단하여 `<<<<<<< HEAD`를 기록합니다.
3. **`sdd-parallel`의 해결책:** **단조 3-Way 병합기(`status_merger.py`)**가 상태 반격자 순서(`backlog (0) < ready-for-dev (1) < in-progress (2) < review (3) < done (4)`)에 따라 각 작업의 최대값 $\max(\text{base}, \text{track-a}, \text{track-b})$을 산출합니다. 두 작업 모두 **충돌 마커 없이** `done`으로 정상 병합됩니다.
4. **마지 후처리:** 읽기 전용 잠금을 해제하고 Wave 1의 `.parallel-lock.json`을 정리하며 트랜잭션을 **`.parallel/state.json`**에 기록합니다.

---

## 3. 대화형 실습 퀘스트 (`--keep-temp`)

저장소를 보존하며 데모를 실행하세요:

```powershell
uv run sdd-parallel demo --keep-temp
```

출력된 `Workspace: ...` 경로(이하 `<DEMO_DIR>`)를 복사합니다.

---

### 단계 1. Git 히스토리 및 트랜잭션 저널 검사
```powershell
git -C "<DEMO_DIR>" log --graph --oneline --all
```
*확인 사항:* 초기 커밋과 Wave 1의 두 통합 커밋이 표시됩니다. `<DEMO_DIR>/.parallel/state.json`에 롤백 앵커 `pre_merge_commit`이 기록되어 있습니다.

---

### 단계 2. 잔여 작업 계획 수립 (`plan`)
```powershell
# 1. 완료 작업을 포함한 전체 5개 작업 계획 확인:
uv run sdd-parallel plan --repo "<DEMO_DIR>" --include-done --dry-run

# 2. 남은 3개 작업(1-1, 2-1, 2-2)에 대한 활성 계획 생성:
uv run sdd-parallel plan --repo "<DEMO_DIR>"
```
*디스크 변경 사항:*
- `_bmad-output/planning-artifacts/` 내 원본 파일은 그대로 유지됩니다.
- `<DEMO_DIR>/_bmad-output/parallel-artifacts/`에 `execution-plan-parallel.md`와 `wave-plan.json`이 생성됩니다.
- `1-1`과 `2-1`이 다음 Wave 1의 최우선 작업으로 편성됩니다.

---

### 단계 3. 다음 웨이브를 위한 고정 슬롯 초기화 (`prepare` 및 `run-guide`)
```powershell
uv run sdd-parallel prepare --repo "<DEMO_DIR>" --wave 1
uv run sdd-parallel run-guide --repo "<DEMO_DIR>" --wave 1
```
*`.worktrees/track-b/` 점검:*
- 기존 슬롯이 최신 `master`로 제자리 재설정됩니다.
- `.parallel-lock.json`에서 `src/demo_app/api.py`만 허용되고 `schema.py`는 잠깁니다.
- `run-guide`가 각 터미널 창별 시작 명령어를 출력합니다.

---

### 단계 4. 런타임 차단기 실시간 시험 (`check-lock`)
```powershell
# 시험 1: 인가된 파일 수정 시도 (api.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/api.py"

# 시험 2: 타 트랙 파일 수정 시도 (schema.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/schema.py"
```
*결과:* 시험 1은 `{"decision": "allow"}`를 반환하고, 시험 2는 `{"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}`을 반환하여 차단합니다.

---

### 단계 5. 미병합 웨이브 중단 (`abort-wave`)
```powershell
uv run sdd-parallel abort-wave --repo "<DEMO_DIR>" --wave 1
```
*결과:* 슬롯이 초기화되고 임시 파일이 깨끗하게 제거됩니다.

---

### 단계 6. 병합된 웨이브의 트랜잭션 LIFO 롤백 (`rollback-wave`)
Wave 1(`1-2`와 `1-3`)을 안전하게 되돌립니다:

```powershell
uv run sdd-parallel rollback-wave --repo "<DEMO_DIR>" --wave 1 --save-backup
```
*검증 항목:*
1. 안전 백업 브랜치 `backup/wave-1-before-rollback-...` 생성 확인.
2. `master`가 Wave 1 병합 이전 커밋으로 복귀.
3. `sprint-status.yaml`의 작업 상태가 `ready-for-dev`로 복구.

---

## 4. 시나리오 B: 처음부터 구축하는 Greenfield 저장소 실습

3장에서는 사전 준비된 데모 환경을 검토했으나, 본 시나리오에서는 보일러플레이트 코드를 수십 줄씩 수작업으로 작성할 필요 없이 순수한 샌드박스에서 전체 라이프사이클을 검증합니다.

**`sdd-parallel demo --scaffold-only --output-dir <경로>`** 명령어를 통해 엔진은 실제 명세, 소스 코드 및 Git 이력이 포함된 클린 샌드박스를 1초 만에 구성합니다.

---

### 트랙 A: GitHub Spec Kit 실전 튜토리얼 (`--kit spec-kit`)

#### 단계 1. Spec Kit 샌드박스 생성 및 초기 상태 확인
```powershell
# 1. 임시 디렉터리에 클린한 Spec Kit 샌드박스 배포
$SANDBOX = "$env:TEMP\eval-speckit"
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir $SANDBOX
Set-Location $SANDBOX

# 2. 생성된 저장소 상태 확인
git status
git log -1 --oneline
```
*생성된 저장소 구조:*
- `specs/001-telemetry/{spec.md, plan.md, tasks.md}` — GitHub Spec Kit 표준 작업 체크리스트 (`- [ ] T001`, `- [ ] T002 [P]`, `- [ ] T003 [P]`, `- [ ] T004`, `- [ ] T005`).
- `.specify/memory/constitution.md`, `.specify/feature.json` — 프로젝트의 불변 헌장 및 명세 계약.
- `src/demo_app/` — 숨겨진 제2종 시맨틱 충돌을 포함하는 6개의 텔레메트리 파이썬 모듈.

#### 단계 2. 병렬 웨이브 실행 계획 산출 (`plan`)
```powershell
uv run sdd-parallel plan
```
*실행 분석:*
- `specs/**/tasks.md` 및 `.specify/`를 통해 GitHub Spec Kit 자동 인식.
- AST 정적 분석기가 `schema.py`를 `SPINE`, `cli.py`를 `ADDITIVE_REGISTRY`로 분류하고, `api.py`와 `normalizer.py` 간의 호출 종속성을 탐지.
- 작업 `T002`와 `T003`은 **Wave 1**로 묶이고, `T004`는 **Wave 2**로 격리되어 머지 폭탄을 차단.

#### 단계 3. 고정 트랙 작업 트리 슬롯 준비 (`prepare`)
```powershell
uv run sdd-parallel prepare --wave 1
```
*디스크 변경 사항:*
- 고정 슬롯 `.worktrees/track-a` 및 `.worktrees/track-b` 초기화 및 연결.
- 각 작업 트리에 `.parallel-lock.json` 스코프 락 매니페스트 생성.
- 불변 계약(`.specify/memory/constitution.md`, `spec.md` 등)에 OS 읽기 전용 권한(`chmod a-w`) 강제 적용.

#### 단계 4. 3단계 심층 방어 스코프 가드 및 OS 크래시 테스트
```powershell
# 1. track-a 내 정상적인 허용 파일 수정:
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/normalizer.py
# -> {"decision": "allow"}

# 2. track-a 내 범위 밖 파일 수정 시도 (다른 트랙 소유의 schema.py):
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/schema.py
# -> {"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}

# 3. OS 레벨 직접 파일 쓰기 차단 테스트 (보호된 constitution.md 수정 시도):
Add-Content -Path .worktrees\track-a\.specify\memory\constitution.md -Value "unauthorized"
# -> 예상 결과: System.UnauthorizedAccessException (OS Read-Only 보호 발동)
```

#### 단계 5. 각 트랙에서 병렬 커밋 시뮬레이션
```powershell
# track-a 에이전트가 작업 T002 완료:
Set-Content -Path .worktrees\track-a\src\demo_app\normalizer.py -Value "def normalize_payload(raw): return {k.strip(): v.strip() for k, v in raw.items()}"
(Get-Content .worktrees\track-a\specs\001-telemetry\tasks.md) -replace '\[ \] T002', '[x] T002' | Set-Content .worktrees\track-a\specs\001-telemetry\tasks.md
git -C .worktrees\track-a commit -am "feat: complete task T002"

# track-b 에이전트가 작업 T003 완료:
Set-Content -Path .worktrees\track-b\src\demo_app\exporter.py -Value "def export_events(events): return len(events)"
(Get-Content .worktrees\track-b\specs\001-telemetry\tasks.md) -replace '\[ \] T003', '[x] T003' | Set-Content .worktrees\track-b\specs\001-telemetry\tasks.md
git -C .worktrees\track-b commit -am "feat: complete task T003"
```

#### 단계 6. diff 감사 및 장벽 단조 머지 수행
```powershell
# 1. .parallel-lock.json 기준 diff 감사:
uv run sdd-parallel verify-wave --wave 1
# -> {"wave": 1, "compliant": true, "violations": []}

# 2. 장벽 머지 및 3-way 단조 상태 통합:
uv run sdd-parallel merge --wave 1
```
*검증:* `specs/001-telemetry/tasks.md`에서 두 작업 모두 `- [x] T002`와 `- [x] T003`으로 **Git 충돌 마커 없이 100% 깔끔하게 병합**됩니다.

#### 단계 7. 트랜잭션 LIFO 웨이브 롤백
```powershell
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```
*롤백 결과:*
- 안전 백업 브랜치 `backup/wave-1-before-rollback-...` 자동 생성.
- `master` 브랜치가 병합 전 초기 커밋으로 깨끗하게 복구.
- `specs/001-telemetry/tasks.md`의 작업 상태가 미완료 `[ ]`로 안전하게 복원.

---

### 트랙 B: BMAD 프레임워크 실전 튜토리얼 (`--kit bmad`)

BMAD (`_bmad-output/`)를 사용하는 팀의 경우에도 동일한 명령어 인터페이스로 완벽히 지원됩니다:

```powershell
# 1. BMAD 샌드박스 생성:
$BMAD_SANDBOX = "$env:TEMP\eval-bmad"
uv run sdd-parallel demo --kit bmad --scaffold-only --output-dir $BMAD_SANDBOX
Set-Location $BMAD_SANDBOX

# 2. 계획 및 슬롯 준비:
uv run sdd-parallel plan
uv run sdd-parallel prepare --wave 1

# 3. 트랙별 작업 완료 시뮬레이션 (sprint-status.yaml 갱신):
(Get-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-2-normalize-telemetry-payload: ready-for-dev', '1-2-normalize-telemetry-payload: done' | Set-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-a commit -am "feat: complete task T002"

(Get-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-3-cli-batch-export: ready-for-dev', '1-3-cli-batch-export: done' | Set-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-b commit -am "feat: complete task T003"

# 4. 장벽 검증 및 머지:
uv run sdd-parallel verify-wave --wave 1
uv run sdd-parallel merge --wave 1

# 5. 트랜잭션 롤백:
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```

---

### 5대 SDD 프레임워크 핵심 차이점 비교 요약표

| 단계 / 메커니즘 | `spec-kit` | `gsd` | `openspec` | `beads` | `bmad` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **샌드박스 생성 명령** | `--kit spec-kit` | `--kit gsd` | `--kit openspec` | `--kit beads` | `--kit bmad` |
| **작업 목록 형식** | `specs/**/tasks.md` | `.planning/phases/*-PLAN.md` | `openspec/changes/*/tasks.md` | `.beads/beads.jsonl` | `sprint-status.yaml` |
| **에이전트 실행 명령** | `/speckit.implement <id>` | `/gsd-execute-phase <num>` | `/opsx:apply <id>` | `bd update <id>` | `/bmad-build <id>` |
| **상태 전이 흐름** | `- [ ]` $\to$ `- [x]` | 페이즈 완료 $\to$ `STATE.md` | `- [ ]` $\to$ `- [x]` | `open` $\to$ `closed` | `ready-for-dev` $\to$ `done` |
| **3-way 머지 대상** | `tasks.md` (Markdown) | `.planning/STATE.md` | `tasks.md` (Markdown) | `beads.jsonl` (JSONL) | `sprint-status.yaml` (YAML) |
| **보호 대상 불변 계약** | `constitution.md`, `spec.md` | `STATE.md`, `ROADMAP.md` | `config.yaml`, `specs/**` | `config.yaml`, `metadata.json` | `epic-*-context.md` |

---

## 5. 시니어 / 스태프 엔지니어를 위한 Diátaxis-lite 가이드

- 📐 **[아키텍처 화이트페이퍼 (`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`)](../../dossier/02_SYSTEM_CAPABILITIES_WHITE_PAPER.md)** — 감쇠 영향 반경 $I(T)$ 수식 증명, Kahn 비순환성 검증, 3계층 방어 체계 및 상태 반격자 대수학.
- 🔌 **[생태계 및 통합 명세서 (`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`)](../../dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md)** — **BMAD**, **GitHub Spec Kit (`.specify/`)**, **GSD (`.planning/`)**, **OpenSpec**, **Beads** 어댑터 명세 및 MCP 프로토콜.
- 📊 **실제 프로젝트 환경 검증 (Brownfield Verification)** — 대규모 다중 에픽 프로덕션 저장소에서 Two-Stage Assisted 워크플로를 적용한 실전 검증 분석.
- 🛡️ **[핵심 불변 조건 요약 (`README.md`)](ARCHITECTURE.md)** — 시스템 안전성 보증 총괄 체크리스트.

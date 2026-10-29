# ⚡ Guia Prático do `sdd-parallel demo`: Governança de Agentes Paralelos em 4 Segundos

<p align="center">
  <b>🌐 Idioma / Language:</b>
  <a href="../../dossier/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">English</a> |
  <a href="../zh-CN/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">简体中文</a> |
  <a href="../es/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Español</a> |
  <b>Português (BR)</b> |
  <a href="../ja/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">日本語</a> |
  <a href="../ko/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">한국어</a> |
  <a href="../ru/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Русский</a>
</p>

**Versão:** 0.1.0  

Executar múltiplos agentes de codificação baseados em IA (Claude Code, Google Antigravity, Codex) de forma concorrente no mesmo repositório pode acelerar o ritmo de engenharia em 2x a 3x. No entanto, sem orquestração rigorosa, essa abordagem quase invariavelmente resulta em três falhas críticas:

1. **Quebra silenciosa de contratos (A `Merge-Bomb` semântica de Tipo 2):** O Agente A altera a assinatura de uma função em um serviço, enquanto o Agente B em uma branch paralela escreve uma invocação para a assinatura antiga em um controller. São arquivos diferentes — o `git merge` conclui sem conflitos de texto, mas a aplicação quebra em produção.
2. **Alucinação de escopo (Scope Creep):** Um agente encarregado de uma tarefa pontual decide "otimizar" o schema do banco de dados ou um módulo adjacente, quebrando premissas de tarefas concorrentes.
3. **Guerra de escrita em arquivos de status:** Dois agentes concluem suas tarefas e editam linhas adjacentes no arquivo central do sprint (`sprint-status.yaml` ou `STATE.md`), fazendo o `git merge` padrão falhar com conflitos `<<<<<<< HEAD`.

O comando **`sdd-parallel demo`** foi concebido para que você possa **comprovar em ~4 segundos em um repositório Git real — sem chaves de API, sem internet e sem configurar projetos —** como o `sdd-parallel` previne essas três falhas.

---

## 1. Como a demonstração opera: Execução real em disco, não texto simulado

`uv run sdd-parallel demo` **não é uma saída simulada de terminal**. A cada execução, o motor realiza operações 100% autênticas em disco:
- Inicializa um repositório Git real no diretório temporário do sistema operacional (`Workspace: .../sdd-parallel-demo-<id>`);
- Analisa arquivos Python reais utilizando Árvores de Sintaxe Abstrata (`AST`);
- Provisiona slots isolados de `git worktree` e aplica travas de somente leitura no sistema de arquivos;
- Registra commits autênticos em duas branches concorrentes e executa um `git merge --no-ff`.

O comando oferece suporte à verificação direcionada para qualquer um dos 5 frameworks SDD ou à verificação matricial completa:

```bash
# Modo 1: Verificação rápida do framework padrão (BMAD)
uv run sdd-parallel demo

# Modo 2: Framework SDD específico (bmad, spec-kit, gsd, openspec, beads)
uv run sdd-parallel demo --kit spec-kit

# Modo 3: Verificação matricial completa (todos os 5 frameworks com painel de pontuação)
uv run sdd-parallel demo --kit all

# Modo 4: Sandbox interativo (mantém o repositório em disco para experimentação prática)
uv run sdd-parallel demo --keep-temp

# Modo 5: Provisionamento ágil de sandbox limpo (para validação E2E manual)
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir "$env:TEMP/eval-speckit"
```

### Saída da Verificação Matricial dos 5 Frameworks (`--kit all`)

Ao executar `uv run sdd-parallel demo --kit all`, o motor avalia os 5 adaptadores em sequência e emite um painel consolidado:

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

### Matriz de Artefatos de Sandbox e Governança nos 5 Frameworks

| Framework (`--kit`) | Arquivos de especificação gerados | Contratos protegidos (`forbidden_files`) | Alvo de Merge Monotônico 3-Way |
| :--- | :--- | :--- | :--- |
| `spec-kit` | `specs/001-telemetry/{spec.md, plan.md, tasks.md}` | `.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md` | `specs/001-telemetry/tasks.md` (`- [x]`) |
| `gsd` | `.planning/phases/01-core/*-PLAN.md` (XML `<files>`) | `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md` | `.planning/STATE.md` (Post-Merge Hook) |
| `openspec` | `openspec/changes/<id>/{proposal.md, tasks.md}` | `openspec/config.yaml`, `openspec/specs/**`, branches irmãs `changes/<sibling>/**` | `openspec/changes/<id>/tasks.md` (`- [x]`) |
| `beads` | `.beads/beads.jsonl` (`dependencies` tipadas) | `.beads/config.yaml` | `.beads/beads.jsonl` (JSONL `max(rank)`) |
| `bmad` | `_bmad-output/planning-artifacts/epics.md`, `spec-*.md` | `_bmad-output/**/epic-*-context.md` | `sprint-status.yaml` (`max(rank)`) |

> **O que muda entre execuções?**  
> Apenas o sufixo aleatório na linha `Workspace:` e o tempo decorrido `VERIFIED IN X.XXs` (conforme a velocidade de I/O do disco, normalmente entre 2 e 4.5 s). As tabelas de papéis, colisões detectadas, cronograma de ondas e resultados de merge são **100% determinísticos**, pois o agendador apoia-se em matemática de grafos e não em gerações não determinísticas de LLMs.

---

## 2. Anatomia passo a passo: O que ocorre em cada etapa

### Quadro 0. Ponto de partida (Estado inicial no disco)

O diretório temporário inicializa com o commit base (`chore: initial demo repository`), contendo uma aplicação de telemetria com 6 módulos e especificações de backlog no padrão **BMAD**:

```text
sdd-parallel-demo-<id>/
├── src/demo_app/
│   ├── __init__.py          # Exportações de pacote (__all__)
│   ├── schema.py            # Schema de banco e migrações (SCHEMA_VERSION = 1, auto_migrate())
│   ├── cli.py               # Ponto de registro CLI (@app.command)
│   ├── normalizer.py        # Limpeza de dados: normalize_payload(raw)
│   ├── api.py               # Endpoint HTTP: importa normalize_payload de normalizer.py!
│   └── exporter.py          # Exportação de eventos: export_events(events)
└── _bmad-output/
    ├── planning-artifacts/          # Roadmap de epics e contextos compilados
    └── implementation-artifacts/    # sprint-status.yaml + 5 especificações (spec-*.md)
```

**Cinco tarefas em fila (`ready-for-dev`) e três armadilhas arquiteturais embutidas:**
- **`1-1`** *(Upgrade schema to v2)* $\to$ altera `schema.py` (depende de `1-2`).
- **`1-2`** *(Normalize payload)* $\to$ altera `normalizer.py` e registra comando em `cli.py`.
- **`1-3`** *(CLI batch export)* $\to$ altera `exporter.py` e também registra comando em `cli.py` *(Armadilha 1: concorrência sobre o `cli.py` compartilhado)*.
- **`2-1`** *(Expose ingestion API)* $\to$ altera apenas `api.py` *(Armadilha 2 — Merge-Bomb semântica oculta: `api.py` importa uma função de `normalizer.py`, que está sendo reescrito concorrentemente em `1-2`)*.
- **`2-2`** *(Add audit log schema)* $\to$ altera `schema.py` (depende de `1-1`) *(Armadilha 3: dupla migração de banco de dados)*.

---

### Quadro 1. Classificação de papéis arquiteturais via AST (`[Step 1/4]`)

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

**O que aconteceu:**  
O motor inspeciona a AST de cada arquivo sem depender de listas estáticas de nomes, atribuindo o **perfil de concorrência** adequado:
1. **`spine` (Espinha dorsal — Fila estrita):** Arquivos fundamentais que regem estruturas de dados ou acumulam muitas dependências (`SCHEMA_VERSION`, `models.py` ou in-degree $\ge 5$). Duas tarefas que alteram um arquivo `spine` **nunca** são alocadas na mesma onda.
2. **`additive_registry` (Registro aditivo — Adição segura):** Pontos de extensão onde tarefas apenas adicionam registros (`@app.command`, `@router.get`, `__all__`). Alterações concorrentes em branches isoladas são permitidas e unificadas no merge.
3. **`standard` (Módulo de negócio — Exclusão mútua + Rastreamento de impacto):** Serviços e controladores comuns. Tarefas concorrentes não podem editar o mesmo arquivo padrão, e o grafo reverso de importações (**`Importers`**) é estritamente monitorado. Observe que o scanner detectou que `api.py` importa `normalizer.py`!

---

### Quadro 2. Desarmando a Merge-Bomb semântica e agendamento de ondas (`[Step 2/4]`)

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

**Principais conclusões:**
1. **Interceptação da Merge-Bomb oculta entre `1-2` e `2-1`:**
   - Um agendador simplório compara apenas caminhos de escrita: `1-2` escreve em `normalizer.py`, `2-1` escreve em `api.py`. As escritas parecem disjuntas (`DISJOINT WRITES -> False Safe!`).
   - O `BSPWaveScheduler` calcula o **blast radius ($I$)**: como `api.py` importa `normalizer.py`, ele entra na zona de impacto de `1-2`. Identificada a `semantic_collision`, o agendador aloca `1-2` na **Onda 1** e adia `2-1` para a **Onda 2** (após a integração do novo contrato no branch principal).
2. **Preservação de paralelismo seguro entre `1-2` e `1-3`:** Ambas tocam `cli.py`, mas como ele é um `ADDITIVE_REGISTRY`, executam concorrentemente na **Onda 1** (`track-a` e `track-b`).
3. **Pipeline ordenado de migrações:** `1-1` e `2-2` alteram `schema.py` (`spine`), sendo sequenciadas entre a **Onda 2** e a **Onda 3**.

---

### Quadro 3. Provisionamento de Fixed Slots e verificação do Scope Guard (`[Step 3/4]`)

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

**Alterações em disco e proteção em profundidade:**
- Slots permanentes criados: **`.worktrees/track-a`** (branch `parallel/track-a`) e **`.worktrees/track-b`** (branch `parallel/track-b`). São resetados in-place a cada nova onda sem recriação de diretórios na IDE.
- Injeção de contratos de contorno:
  - `.parallel-lock.json` — manifesto estruturado com arquivos liberados e bloqueados;
  - `.tmp/track-context.md` e `ACTIVE_TRACK.md` — instruções claras para o agente;
  - Trava de somente leitura do SO (`chmod a-w`) aplicada aos arquivos fora de escopo e spine.
- **`100% Clean git status: True`:** Metadados são ignorados em `.git/worktrees/<slot>/info/exclude` e o Git ignora alterações de permissão via `core.fileMode = false`. O agente opera em um working tree perfeitamente limpo.
- **Teste de interceptação:** O script executa `evaluate_tool_access()` simulando uma tentativa de escrita em `schema.py` dentro de `track-a`. O hook consulta o manifesto e devolve imediatamente **`DENY`**.

---

### Quadro 4. Barreira de integração e Monotonic 3-Way Merge (`[Step 4/4]`)

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

**Resolução monótona de status:**
1. Antes do início da onda, `1-2` e `1-3` figuravam como linhas adjacentes com status `ready-for-dev`.
   - O agente em `track-a` marca `1-2` como `done`;
   - O agente em `track-b` marca concorrentemente `1-3` como `done`.
2. O `git merge` padrão avalia blocos de 3 linhas e aborta com conflitos `<<<<<<< HEAD`.
3. **Como o `sdd-parallel` resolve:** O **Monotonic 3-Way Merger (`status_merger.py`)** avalia o progresso por ordem de semirreticulado (`backlog (0) < ready-for-dev (1) < in-progress (2) < review (3) < done (4)`), escolhendo $\max(\text{base}, \text{track-a}, \text{track-b})$. Ambas as histórias tornam-se `done` com **zero marcadores de conflito**.
4. **Higiene pós-merge:** Restabelece permissões de escrita, remove os bloqueios da Onda 1 e registra a transação em **`.parallel/state.json`**.

---

## 3. Prática interativa no Sandbox (`--keep-temp`)

Execute a demonstração preservando os arquivos em disco:

```powershell
uv run sdd-parallel demo --keep-temp
```

Copie o caminho impresso na linha `Workspace: ...` (doravante `<DEMO_DIR>`).

---

### Passo 1. Inspecionar o histórico Git e o registro de transações
```powershell
git -C "<DEMO_DIR>" log --graph --oneline --all
```
*O que observar:* Verá o commit inicial e dois commits de integração da Onda 1. O arquivo `<DEMO_DIR>/.parallel/state.json` armazena o ponto de ancoragem `pre_merge_commit`.

---

### Passo 2. Planejar o restante das tarefas (`plan`)
```powershell
# 1. Visualizar o planejamento completo das 5 tarefas:
uv run sdd-parallel plan --repo "<DEMO_DIR>" --include-done --dry-run

# 2. Gerar o plano ativo para as 3 tarefas restantes (1-1, 2-1, 2-2):
uv run sdd-parallel plan --repo "<DEMO_DIR>"
```
*Mudanças no disco:*
- Os arquivos BMAD em `_bmad-output/planning-artifacts/` permanecem intocados.
- `<DEMO_DIR>/_bmad-output/parallel-artifacts/` recebe `execution-plan-parallel.md` e `wave-plan.json`.
- As tarefas `1-1` e `2-1` passam a compor a nova Onda 1.

---

### Passo 3. Preparar os slots fixos para a próxima onda (`prepare` e `run-guide`)
```powershell
uv run sdd-parallel prepare --repo "<DEMO_DIR>" --wave 1
uv run sdd-parallel run-guide --repo "<DEMO_DIR>" --wave 1
```
*Inspecione `.worktrees/track-b/`:*
- Os slots existentes foram resetados in-place para o `master` atualizado.
- Em `.parallel-lock.json`: `src/demo_app/api.py` está liberado e `schema.py` categoricamente bloqueado.
- O `run-guide` exibe os comandos de inicialização para cada janela de terminal.

---

### Passo 4. Testar a proteção em tempo de execução (`check-lock`)
```powershell
# Tentativa 1: Acesso a arquivo autorizado (api.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/api.py"

# Tentativa 2: Acesso não autorizado a schema.py
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/schema.py"
```
*Saída esperada:* A tentativa 1 retorna `{"decision": "allow"}`; a tentativa 2 retorna `{"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}`.

---

### Passo 5. Cancelar uma onda antes do merge (`abort-wave`)
```powershell
uv run sdd-parallel abort-wave --repo "<DEMO_DIR>" --wave 1
```
*Resultado:* Os slots são limpos e liberados sem alterações no branch principal.

---

### Passo 6. Rollback transacional LIFO de onda já integrada (`rollback-wave`)
Para desfazer a Onda 1 (`1-2` e `1-3`):

```powershell
uv run sdd-parallel rollback-wave --repo "<DEMO_DIR>" --wave 1 --save-backup
```
*Validação:*
1. Cria a branch imutável de segurança `backup/wave-1-before-rollback-...`.
2. Restaura o `master` para o commit anterior ao merge da Onda 1.
3. Restabelece no `sprint-status.yaml` as tarefas de `done` para `ready-for-dev`.

---

## 4. Cenário B: Walkthrough E2E em um repositório Greenfield do zero

Enquanto a Seção 3 utilizou um repositório pré-configurado, este cenário demonstra como validar o ciclo completo do `sdd-parallel` em um sandbox limpo sem a necessidade de escrever dezenas de linhas de configuração manual.

Com o comando **`sdd-parallel demo --scaffold-only --output-dir <caminho>`**, o motor gera em <1 segundo um repositório Git autêntico com especificações reais, código-fonte e histórico.

---

### Trilha A: Validação completa com GitHub Spec Kit (`--kit spec-kit`)

#### Passo 1. Provisionar sandbox de Spec Kit e inspecionar estado inicial
```powershell
# 1. Criar um sandbox limpo do Spec Kit em diretório temporário
$SANDBOX = "$env:TEMP\eval-speckit"
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir $SANDBOX
Set-Location $SANDBOX

# 2. Inspecionar o repositório criado
git status
git log -1 --oneline
```
*Estrutura provisionada:*
- `specs/001-telemetry/{spec.md, plan.md, tasks.md}` — checklist de tarefas no padrão GitHub Spec Kit (`- [ ] 1-1`, `- [ ] 1-2`, `- [ ] 1-3`, `- [ ] 2-1`, `- [ ] 2-2`).
- `.specify/memory/constitution.md` e `.specify/feature.json` — contratos imutáveis do projeto.
- `src/demo_app/` — 6 módulos Python com colisão semântica oculta de Tipo 2.

#### Passo 2. Calcular o plano de ondas paralelas (`plan`)
```powershell
uv run sdd-parallel plan
```
*O que acontece:*
- O detector reconhece automaticamente o GitHub Spec Kit através de `specs/**/tasks.md` e `.specify/`.
- A análise estática AST classifica `schema.py` como `SPINE`, `cli.py` como `ADDITIVE_REGISTRY` e detecta a dependência semântica entre `api.py` e `normalizer.py`.
- As tarefas `1-2` e `1-3` são agrupadas na **Onda 1**, enquanto a tarefa `2-1` é isolada na **Onda 2** para prevenir o Semantic Merge-Bomb.

#### Passo 3. Provisionar slots fixos de trabalho (`prepare`)
```powershell
uv run sdd-parallel prepare --wave 1
```
*Resultado no disco:*
- Os slots `.worktrees/track-a` e `.worktrees/track-b` são vinculados e preparados.
- Os manifestos `.parallel-lock.json` são gerados em cada worktree.
- Os contratos imutáveis (`.specify/memory/constitution.md`, `spec.md`, etc.) recebem permissões de somente leitura no SO (`chmod a-w`).

#### Passo 4. Testar a guarda de escopo em três camadas e crash-test
```powershell
# 1. Modificação válida dentro do escopo de track-a:
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/normalizer.py
# -> {"decision": "allow"}

# 2. Violação de escopo (tentativa de alterar schema.py protegido):
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/schema.py
# -> {"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}

# 3. Crash-test em nível de SO (tentativa de escrita em constitution.md protegido):
Add-Content -Path .worktrees\track-a\.specify\memory\constitution.md -Value "invasão"
# -> Esperado: System.UnauthorizedAccessException (bloqueio OS Read-Only ativo)
```

#### Passo 5. Simular commits concorrentes em cada trilha
```powershell
# Agente em track-a implementa a tarefa 1-2:
Set-Content -Path .worktrees\track-a\src\demo_app\normalizer.py -Value "def normalize_payload(raw): return {k.strip(): v.strip() for k, v in raw.items()}"
(Get-Content .worktrees\track-a\specs\001-telemetry\tasks.md) -replace '\[ \] 1-2', '[x] 1-2' | Set-Content .worktrees\track-a\specs\001-telemetry\tasks.md
git -C .worktrees\track-a commit -am "feat: complete task 1-2"

# Agente em track-b implementa a tarefa 1-3:
Set-Content -Path .worktrees\track-b\src\demo_app\exporter.py -Value "def export_events(events): return len(events)"
(Get-Content .worktrees\track-b\specs\001-telemetry\tasks.md) -replace '\[ \] 1-3', '[x] 1-3' | Set-Content .worktrees\track-b\specs\001-telemetry\tasks.md
git -C .worktrees\track-b commit -am "feat: complete task 1-3"
```

#### Passo 6. Auditar diferenças e integrar com merge monotônico 3-way
```powershell
# 1. Auditar diffs em relação ao merge-base conforme .parallel-lock.json:
uv run sdd-parallel verify-wave --wave 1
# -> {"wave": 1, "compliant": true, "violations": []}

# 2. Realizar o merge em barreira com reconciliação 3-way:
uv run sdd-parallel merge --wave 1
```
*Observação:* Ambas as tarefas concluem com `- [x] 1-2` e `- [x] 1-3` em `specs/001-telemetry/tasks.md` com **0 marcadores de conflito do Git**.

#### Passo 7. Rollback transacional LIFO
```powershell
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```
*Verificação:*
- A branch de backup `backup/wave-1-before-rollback-...` é preservada.
- O `master` retorna limpamente para o commit anterior ao merge.
- Em `specs/001-telemetry/tasks.md`, as tarefas `1-2` e `1-3` são restauradas para pendentes `[ ]`.

---

### Trilha B: Validação com BMAD (`--kit bmad`)

Para equipes que utilizam BMAD (`_bmad-output/`), o processo segue a mesma interface com adaptação automática:

```powershell
# 1. Criar sandbox BMAD:
$BMAD_SANDBOX = "$env:TEMP\eval-bmad"
uv run sdd-parallel demo --kit bmad --scaffold-only --output-dir $BMAD_SANDBOX
Set-Location $BMAD_SANDBOX

# 2. Planejar e preparar:
uv run sdd-parallel plan
uv run sdd-parallel prepare --wave 1

# 3. Simular avanço de tarefas (atualiza sprint-status.yaml):
(Get-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-2-normalize-telemetry-payload: ready-for-dev', '1-2-normalize-telemetry-payload: done' | Set-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-a commit -am "feat: finish story 1-2"

(Get-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-3-cli-batch-export: ready-for-dev', '1-3-cli-batch-export: done' | Set-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-b commit -am "feat: finish story 1-3"

# 4. Verificar e integrar:
uv run sdd-parallel verify-wave --wave 1
uv run sdd-parallel merge --wave 1

# 5. Rollback transacional:
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```

---

### Tabela de Comparação Rápida dos 5 Kits

| Etapa / Mecanismo | `spec-kit` | `gsd` | `openspec` | `beads` | `bmad` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Comando de criação** | `--kit spec-kit` | `--kit gsd` | `--kit openspec` | `--kit beads` | `--kit bmad` |
| **Formato de tarefas** | `specs/**/tasks.md` | `.planning/phases/*-PLAN.md` | `openspec/changes/*/tasks.md` | `.beads/beads.jsonl` | `sprint-status.yaml` |
| **Comando do agente** | `/speckit.implement <id>` | `/gsd-execute-phase <num>` | `/opsx:apply <id>` | `bd update <id>` | `/bmad-build <id>` |
| **Evolução de status** | `- [ ]` $\to$ `- [x]` | Fase completa $\to$ `STATE.md` | `- [ ]` $\to$ `- [x]` | `open` $\to$ `closed` | `ready-for-dev` $\to$ `done` |
| **Alvo de merge 3-way** | `tasks.md` (Markdown) | `.planning/STATE.md` | `tasks.md` (Markdown) | `beads.jsonl` (JSONL) | `sprint-status.yaml` (YAML) |
| **Contratos protegidos** | `constitution.md`, `spec.md` | `STATE.md`, `ROADMAP.md` | `config.yaml`, `specs/**` | `config.yaml`, `metadata.json` | `epic-*-context.md` |

---

## 5. Navegação Diátaxis-lite para Engenheiros Senior / Staff

- 📐 **[White Paper de Capacidades e Arquitetura (`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`)](../../dossier/02_SYSTEM_CAPABILITIES_WHITE_PAPER.md)** — Atenuação matemática do raio de impacto $I(T)$, demonstração de aciclicidade de Kahn, Three-Tier Scope Guard e álgebra de semirreticulados.
- 🔌 **[Especificação de Integração e Ecossistema (`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`)](../../dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md)** — Especificações para **BMAD**, **GitHub Spec Kit (`.specify/`)**, **GSD (`.planning/`)**, **OpenSpec** e **Beads**, com esquemas `.parallel-lock.json` e MCP.
- 📊 **Validação em Ambientes Reais (Brownfield)** — Validação empírica do fluxo de trabalho Two-Stage Assisted em bases de código complexas e multi-épicos.
- 🛡️ **[Resumo de Invariantes Fundamentais (`README.md`)](../../../README.md#-core-invariants)** — Checklist consolidado de garantias do sistema.

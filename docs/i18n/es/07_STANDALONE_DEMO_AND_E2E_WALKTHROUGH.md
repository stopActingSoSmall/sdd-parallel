# ⚡ Guía Práctica de `sdd-parallel demo`: Gobernanza de Agentes Paralelos en 4 Segundos

<p align="center">
  <b>🌐 Idioma / Language:</b>
  <a href="../../dossier/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">English</a> |
  <a href="../zh-CN/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">简体中文</a> |
  <b>Español</b> |
  <a href="../pt-BR/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Português (BR)</a> |
  <a href="../ja/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">日本語</a> |
  <a href="../ko/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">한국어</a> |
  <a href="../ru/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Русский</a>
</p>

**Versión:** 0.1.0  

Ejecutar múltiples agentes de codificación con IA (Claude Code, Google Antigravity, Codex) de forma concurrente en el mismo repositorio puede acelerar el desarrollo entre 2x y 3x. Sin embargo, sin una orquestación estricta, casi inevitablemente desemboca en tres accidentes críticos:

1. **Ruptura silenciosa de contratos (La `Merge-Bomb` semántica de Tipo 2):** El Agente A modifica la firma de una función en un servicio, mientras el Agente B en una rama adyacente invoca la firma anterior en un controlador. Son archivos distintos: `git merge` se completa sin conflictos textuales, pero el despliegue falla en tiempo de ejecución.
2. **Alucinación de alcance (Scope Creep):** Un agente que trabaja en una tarea local decide «mejorar» el esquema de la base de datos o un módulo vecino, rompiendo los supuestos de las tareas paralelas.
3. **Guerra de estados en el roadmap:** Dos agentes finalizan sus tareas y modifican líneas contiguas en el archivo compartido del sprint (`sprint-status.yaml` o `STATE.md`), provocando que el `git merge` estándar falle con marcadores `<<<<<<< HEAD`.

El comando **`sdd-parallel demo`** está diseñado para que puedas **comprobar en ~4 segundos en un repositorio Git real —sin claves API, sin internet y sin configurar proyectos—** cómo `sdd-parallel` previene estos tres accidentes.

---

## 1. Cómo opera la demo: Ejecución real en disco, no texto simulado

`uv run sdd-parallel demo` **no es una simulación de consola**. En cada ejecución, el motor lleva a cabo operaciones 100% reales en disco:
- Inicializa un repositorio Git auténtico en el directorio temporal de tu sistema (`Workspace: .../sdd-parallel-demo-<id>`);
- Analiza archivos Python reales mediante árboles de sintaxis abstracta (`AST`);
- Despliega ranuras `git worktree` aisladas y aplica bloqueos de solo lectura del sistema operativo;
- Genera commits reales en dos ramas paralelas y ejecuta un `git merge --no-ff`.

El comando admite la verificación específica de cualquiera de los 5 frameworks SDD soportados o una verificación matricial completa:

```bash
# Modo 1: Verificación rápida del framework por defecto (BMAD)
uv run sdd-parallel demo

# Modo 2: Framework SDD específico (bmad, spec-kit, gsd, openspec, beads)
uv run sdd-parallel demo --kit spec-kit

# Modo 3: Verificación matricial completa (los 5 frameworks con tabla de resultados)
uv run sdd-parallel demo --kit all

# Modo 4: Sandbox interactivo (conserva el repositorio en disco para experimentación manual)
uv run sdd-parallel demo --keep-temp

# Modo 5: Andamiaje ultrarrápido de sandbox limpio (para validación E2E manual)
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir "$env:TEMP/eval-speckit"
```

### Salida de Verificación Matricial de los 5 Frameworks (`--kit all`)

Al ejecutar `uv run sdd-parallel demo --kit all`, el motor evalúa los 5 adaptadores secuencialmente y emite una tabla de puntuación unificada:

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

### Matriz de Artefactos de Sandbox y Gobernanza en los 5 Frameworks

| Framework (`--kit`) | Archivos de especificación generados | Contratos protegidos (`forbidden_files`) | Objetivo de 3-Way Merge monótono |
| :--- | :--- | :--- | :--- |
| `spec-kit` | `specs/001-telemetry/{spec.md, plan.md, tasks.md}` | `.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md` | `specs/001-telemetry/tasks.md` (`- [x]`) |
| `gsd` | `.planning/phases/01-core/*-PLAN.md` (XML `<files>`) | `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md` | `.planning/STATE.md` (Post-Merge Hook) |
| `openspec` | `openspec/changes/<id>/{proposal.md, tasks.md}` | `openspec/config.yaml`, `openspec/specs/**`, ramas hermanas `changes/<sibling>/**` | `openspec/changes/<id>/tasks.md` (`- [x]`) |
| `beads` | `.beads/beads.jsonl` (`dependencies` tipadas) | `.beads/config.yaml` | `.beads/beads.jsonl` (JSONL `max(rank)`) |
| `bmad` | `_bmad-output/planning-artifacts/epics.md`, `spec-*.md` | `_bmad-output/**/epic-*-context.md` | `sprint-status.yaml` (`max(rank)`) |

> **¿Qué cambia entre ejecuciones?**  
> Únicamente el sufijo aleatorio en la línea `Workspace:` y el tiempo total `VERIFIED IN X.XXs` (dependiente de la velocidad de disco de tu equipo, habitualmente 2–4.5 s). Las tablas de roles, los conflictos detectados, el cronograma de olas y los resultados de integración son **100% deterministas**, ya que el planificador se basa en matemáticas de grafos y no en generación no determinista de LLMs.

---

## 2. Anatomía paso a paso: Qué ocurre en cada etapa

### Fotograma 0. Estado inicial en disco

El directorio temporal se inicializa con el commit base (`chore: initial demo repository`), conteniendo una aplicación de telemetría de 6 módulos y especificaciones bajo la estructura **BMAD**:

```text
sdd-parallel-demo-<id>/
├── src/demo_app/
│   ├── __init__.py          # Exportaciones de paquete (__all__)
│   ├── schema.py            # Esquema BD y migraciones (SCHEMA_VERSION = 1, auto_migrate())
│   ├── cli.py               # Punto de registro CLI (@app.command)
│   ├── normalizer.py        # Limpieza de datos: normalize_payload(raw)
│   ├── api.py               # Endpoint HTTP: ¡importa normalize_payload de normalizer.py!
│   └── exporter.py          # Exportación de eventos: export_events(events)
└── _bmad-output/
    ├── planning-artifacts/          # Roadmap de epics y contextos precompilados
    └── implementation-artifacts/    # sprint-status.yaml + 5 especificaciones (spec-*.md)
```

**Cinco historias en cola (`ready-for-dev`) y tres trampas arquitectónicas ocultas:**
- **`1-1`** *(Upgrade schema to v2)* $\to$ modifica `schema.py` (depende de `1-2`).
- **`1-2`** *(Normalize payload)* $\to$ modifica `normalizer.py` y registra comando en `cli.py`.
- **`1-3`** *(CLI batch export)* $\to$ modifica `exporter.py` y también registra comando en `cli.py` *(Trampa 1: contención sobre el archivo compartido `cli.py`)*.
- **`2-1`** *(Expose ingestion API)* $\to$ modifica únicamente `api.py` *(Trampa 2 — Merge-Bomb semántica oculta: `api.py` importa una función de `normalizer.py`, el cual está siendo reescrito concurrentemente en `1-2`)*.
- **`2-2`** *(Add audit log schema)* $\to$ modifica `schema.py` (depende de `1-1`) *(Trampa 3: doble migración de base de datos)*.

---

### Fotograma 1. Clasificación de roles arquitectónicos por AST (`[Step 1/4]`)

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

**Qué ocurrió:**  
El motor analiza la estructura AST sin listas de archivos preconcebidas y determina el comportamiento de concurrencia de cada archivo:
1. **`spine` (Columna vertebral — Cola estricta):** Archivos que definen contratos de datos o concentran dependencias (`SCHEMA_VERSION`, `models.py`, o in-degree $\ge 5$). Dos tareas sobre un archivo `spine` **nunca** se ejecutan en la misma ola.
2. **`additive_registry` (Registro aditivo — Adición segura):** Puntos de extensión donde las tareas solo agregan registros (`@app.command`, `@router.get`, `__all__`). Las modificaciones paralelas en distintas ramas están explícitamente permitidas.
3. **`standard` (Módulo estándar — Exclusión mutua + Análisis de impacto):** Servicios habituales. Se prohíbe que dos tareas concurrentes escriban en el mismo archivo estándar y se audita el grafo de llamadas (columna **`Importers`**). ¡El escáner detectó que `api.py` importa `normalizer.py`!

---

### Fotograma 2. Desactivación de la Merge-Bomb semántica y programación de olas (`[Step 2/4]`)

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

**Puntos clave:**
1. **Detección de la Merge-Bomb oculta entre `1-2` y `2-1`:**
   - Un planificador simple solo compara rutas de escritura: `1-2` modifica `normalizer.py`, `2-1` modifica `api.py`. No hay choque de escritura directa (`DISJOINT WRITES -> False Safe!`).
   - `BSPWaveScheduler` calcula el **blast radius ($I$)**: dado que `api.py` importa `normalizer.py`, queda dentro del área de impacto de `1-2`. Al detectar la colisión semántica, programa `1-2` en la **Ola 1** y pospone `2-1` a la **Ola 2** (cuando el nuevo contrato ya está integrado).
2. **Preservación del paralelismo seguro para `1-2` y `1-3`:** Ambas tareas tocan `cli.py`, pero al ser un `ADDITIVE_REGISTRY`, se ejecutan concurrentemente en la **Ola 1** (`track-a` y `track-b`).
3. **Pipeline ordenado de migraciones:** `1-1` y `2-2` modifican `schema.py` (`spine`), secuenciándose en la **Ola 2** y la **Ola 3**.

---

### Fotograma 3. Disposición de Fixed Slots y verificación de Scope Guard (`[Step 3/4]`)

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

**Modificaciones en disco y defensa en profundidad:**
- Ranuras permanentes aprovisionadas: **`.worktrees/track-a`** (rama `parallel/track-a`) y **`.worktrees/track-b`** (rama `parallel/track-b`). Se reinician en el lugar sin recrear carpetas en el editor.
- Inyección de artefactos de contención:
  - `.parallel-lock.json` — pasaporte de archivos autorizados y bloqueados;
  - `.tmp/track-context.md` y `ACTIVE_TRACK.md` — contexto explícito para el agente;
  - Bloqueo de solo lectura del SO (`chmod a-w`) sobre archivos de otros tracks y núcleos spine.
- **`100% Clean git status: True`:** Los archivos de control se ocultan en `.git/worktrees/<slot>/info/exclude` y `core.fileMode = false` ignora cambios de permisos. El agente percibe un árbol de trabajo impecable.
- **Prueba del interceptor:** La demo invoca `evaluate_tool_access()` simulando que un agente en `track-a` intenta editar `schema.py`. El hook evalúa el manifiesto y responde de inmediato con **`DENY`**.

---

### Fotograma 4. Barrera de integración y Monotonic 3-Way Merge (`[Step 4/4]`)

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

**Resolución monótona de estados:**
1. Antes de la ola, `1-2` y `1-3` estaban en líneas adyacentes con estado `ready-for-dev`.
   - El agente de `track-a` marca `1-2` como `done`;
   - El agente de `track-b` marca concurrentemente `1-3` como `done`.
2. Git estándar compara bloques de 3 líneas y genera conflicto con marcas `<<<<<<< HEAD`.
3. **Solución:** El **Monotonic 3-Way Merger (`status_merger.py`)** evalúa la progresión en un semirretículo (`backlog (0) < ready-for-dev (1) < in-progress (2) < review (3) < done (4)`), seleccionando el máximo $\max(\text{base}, \text{track-a}, \text{track-b})$. Ambas historias pasan a `done` con **cero marcadores de conflicto**.
4. **Limpieza posterior:** Se restablecen permisos de escritura, se eliminan los `.parallel-lock.json` de la Ola 1 y la transacción se registra en **`.parallel/state.json`**.

---

## 3. Práctica interactiva en Sandbox (`--keep-temp`)

Ejecuta la demo conservando los archivos:

```powershell
uv run sdd-parallel demo --keep-temp
```

Copia la ruta de la línea `Workspace: ...` (denominada en adelante `<DEMO_DIR>`).

---

### Paso 1. Inspeccionar historial de Git y registro de transacciones
```powershell
git -C "<DEMO_DIR>" log --graph --oneline --all
```
*Observación:* Verás el commit inicial y los dos commits de integración de la Ola 1. En `<DEMO_DIR>/.parallel/state.json` se encuentra registrado el punto de retorno `pre_merge_commit`.

---

### Paso 2. Programar las tareas restantes (`plan`)
```powershell
# 1. Revisar roadmap completo de 5 tareas:
uv run sdd-parallel plan --repo "<DEMO_DIR>" --include-done --dry-run

# 2. Planificar las 3 tareas restantes (1-1, 2-1, 2-2):
uv run sdd-parallel plan --repo "<DEMO_DIR>"
```
*Resultado en disco:*
- Los artefactos de BMAD en `_bmad-output/planning-artifacts/` permanecen intactos.
- `<DEMO_DIR>/_bmad-output/parallel-artifacts/` contiene `execution-plan-parallel.md` y `wave-plan.json`.
- Las historias `1-1` y `2-1` encabezan la nueva Ola 1.

---

### Paso 3. Preparar las ranuras fijas para la siguiente ola (`prepare` y `run-guide`)
```powershell
uv run sdd-parallel prepare --repo "<DEMO_DIR>" --wave 1
uv run sdd-parallel run-guide --repo "<DEMO_DIR>" --wave 1
```
*Inspección de `.worktrees/track-b/`:*
- Las ranuras existentes se actualizan al último `master`.
- En `.parallel-lock.json`: se autoriza `src/demo_app/api.py` y se bloquea explícitamente `schema.py`.
- `run-guide` muestra la guía de comandos para cada ventana de terminal.

---

### Paso 4. Probar la intercepción en vivo (`check-lock`)
```powershell
# Intento 1: Acceso a archivo propio (api.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/api.py"

# Intento 2: Acceso no autorizado a schema.py
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/schema.py"
```
*Resultado:* La prueba 1 devuelve `{"decision": "allow"}`; la prueba 2 devuelve `{"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}`.

---

### Paso 5. Abortar una ola antes del merge (`abort-wave`)
```powershell
uv run sdd-parallel abort-wave --repo "<DEMO_DIR>" --wave 1
```
*Resultado:* Las ranuras se limpian de manifiestos temporales sin afectar a la rama principal.

---

### Paso 6. Reversión transaccional LIFO de una ola integrada (`rollback-wave`)
Para revertir la Ola 1 (`1-2` y `1-3`):

```powershell
uv run sdd-parallel rollback-wave --repo "<DEMO_DIR>" --wave 1 --save-backup
```
*Verificación:*
1. Preserva la rama de seguridad `backup/wave-1-before-rollback-...`.
2. Restablece `master` al commit previo a la integración.
3. Restaura en `sprint-status.yaml` las tareas a `ready-for-dev`.

---

## 4. Escenario B: Prueba E2E en un repositorio Greenfield desde cero

Mientras que la Sección 3 analizó un repositorio previamente preparado, este escenario demuestra cómo validar el flujo completo de `sdd-parallel` en un sandbox limpio sin redactar manualmente decenas de líneas de configuración.

Mediante **`sdd-parallel demo --scaffold-only --output-dir <ruta>`**, el motor genera en <1 segundo un repositorio Git funcional con especificaciones reales, código fuente e historial.

---

### Ruta A: Validación completa con GitHub Spec Kit (`--kit spec-kit`)

#### Paso 1. Generar sandbox de Spec Kit e inspeccionar el estado inicial
```powershell
# 1. Crear un sandbox limpio de Spec Kit en un directorio temporal
$SANDBOX = "$env:TEMP\eval-speckit"
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir $SANDBOX
Set-Location $SANDBOX

# 2. Inspeccionar el repositorio creado
git status
git log -1 --oneline
```
*Estructura generada:*
- `specs/001-telemetry/{spec.md, plan.md, tasks.md}` — lista de tareas según el estándar de GitHub Spec Kit (`- [ ] 1-1`, `- [ ] 1-2`, `- [ ] 1-3`, `- [ ] 2-1`, `- [ ] 2-2`).
- `.specify/memory/constitution.md` y `.specify/feature.json` — contratos inmutables del proyecto.
- `src/demo_app/` — 6 módulos Python con colisión semántica oculta de tipo 2.

#### Paso 2. Calcular el plan de olas paralelas (`plan`)
```powershell
uv run sdd-parallel plan
```
*Qué sucede:*
- El detector identifica automáticamente GitHub Spec Kit a través de `specs/**/tasks.md` y `.specify/`.
- El análisis AST clasifica `schema.py` como `SPINE`, `cli.py` como `ADDITIVE_REGISTRY`, y detecta la dependencia semántica entre `api.py` y `normalizer.py`.
- Las tareas `1-2` y `1-3` se agrupan en la **Ola 1**, mientras que `2-1` queda aislada en la **Ola 2** para evitar la colisión semántica.

#### Paso 3. Aprovisionar ranuras fijas de trabajo (`prepare`)
```powershell
uv run sdd-parallel prepare --wave 1
```
*Efecto en disco:*
- Se crean y vinculan las ranuras `.worktrees/track-a` y `.worktrees/track-b`.
- Se generan los manifiestos `.parallel-lock.json` en cada ranura.
- Los contratos inmutables (`.specify/memory/constitution.md`, `spec.md`, etc.) reciben permisos de solo lectura a nivel de SO (`chmod a-w`).

#### Paso 4. Probar la guarda de ámbito en tres niveles y crash-test
```powershell
# 1. Modificación válida dentro del ámbito de track-a:
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/normalizer.py
# -> {"decision": "allow"}

# 2. Violación de ámbito (intento de tocar schema.py reservado para otra pista):
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/schema.py
# -> {"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}

# 3. Crash-test a nivel de SO (intento de escribir en constitution.md protegido):
Add-Content -Path .worktrees\track-a\.specify\memory\constitution.md -Value "intrusión"
# -> Esperado: System.UnauthorizedAccessException (bloqueo OS Read-Only activo)
```

#### Paso 5. Simular commits concurrentes en cada pista
```powershell
# Agente en track-a completa la tarea 1-2:
Set-Content -Path .worktrees\track-a\src\demo_app\normalizer.py -Value "def normalize_payload(raw): return {k.strip(): v.strip() for k, v in raw.items()}"
(Get-Content .worktrees\track-a\specs\001-telemetry\tasks.md) -replace '\[ \] 1-2', '[x] 1-2' | Set-Content .worktrees\track-a\specs\001-telemetry\tasks.md
git -C .worktrees\track-a commit -am "feat: complete task 1-2"

# Agente en track-b completa la tarea 1-3:
Set-Content -Path .worktrees\track-b\src\demo_app\exporter.py -Value "def export_events(events): return len(events)"
(Get-Content .worktrees\track-b\specs\001-telemetry\tasks.md) -replace '\[ \] 1-3', '[x] 1-3' | Set-Content .worktrees\track-b\specs\001-telemetry\tasks.md
git -C .worktrees\track-b commit -am "feat: complete task 1-3"
```

#### Paso 6. Auditar diferencias e integrar con 3-way merge
```powershell
# 1. Auditar diffs respecto a merge-base según .parallel-lock.json:
uv run sdd-parallel verify-wave --wave 1
# -> {"wave": 1, "compliant": true, "violations": []}

# 2. Integrar mediante barrera con reconciliación monótona de 3 vías:
uv run sdd-parallel merge --wave 1
```
*Resultado:* Ambas tareas aparecen marcadas como `- [x] 1-2` y `- [x] 1-3` en `specs/001-telemetry/tasks.md` con **0 marcadores de conflicto Git**.

#### Paso 7. Rollback transaccional LIFO
```powershell
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```
*Comprobación:*
- Se crea la rama de seguridad `backup/wave-1-before-rollback-...`.
- `master` vuelve de forma limpia al commit anterior a la integración.
- En `specs/001-telemetry/tasks.md`, las tareas `1-2` y `1-3` se restablecen a pendientes `[ ]`.

---

### Ruta B: Validación con BMAD (`--kit bmad`)

Para equipos que utilizan BMAD (`_bmad-output/`), el proceso es completamente idéntico:

```powershell
# 1. Crear sandbox BMAD:
$BMAD_SANDBOX = "$env:TEMP\eval-bmad"
uv run sdd-parallel demo --kit bmad --scaffold-only --output-dir $BMAD_SANDBOX
Set-Location $BMAD_SANDBOX

# 2. Planificar y preparar:
uv run sdd-parallel plan
uv run sdd-parallel prepare --wave 1

# 3. Simular avance de tareas (modifica sprint-status.yaml):
(Get-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-2-normalize-telemetry-payload: ready-for-dev', '1-2-normalize-telemetry-payload: done' | Set-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-a commit -am "feat: finish story 1-2"

(Get-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-3-cli-batch-export: ready-for-dev', '1-3-cli-batch-export: done' | Set-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-b commit -am "feat: finish story 1-3"

# 4. Verificar e integrar:
uv run sdd-parallel verify-wave --wave 1
uv run sdd-parallel merge --wave 1

# 5. Rollback transaccional:
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```

---

### Tabla Comparativa de Diferencias Clave en los 5 Kits

| Paso / Mecanismo | `spec-kit` | `gsd` | `openspec` | `beads` | `bmad` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Comando de creación** | `--kit spec-kit` | `--kit gsd` | `--kit openspec` | `--kit beads` | `--kit bmad` |
| **Formato de tareas** | `specs/**/tasks.md` | `.planning/phases/*-PLAN.md` | `openspec/changes/*/tasks.md` | `.beads/beads.jsonl` | `sprint-status.yaml` |
| **Comando del agente** | `/speckit.implement <id>` | `/gsd-execute-phase <num>` | `/opsx:apply <id>` | `bd update <id>` | `/bmad-build <id>` |
| **Progresión de estado** | `- [ ]` $\to$ `- [x]` | Fase completa $\to$ `STATE.md` | `- [ ]` $\to$ `- [x]` | `open` $\to$ `closed` | `ready-for-dev` $\to$ `done` |
| **Destino de 3-way merge** | `tasks.md` (Markdown) | `.planning/STATE.md` | `tasks.md` (Markdown) | `beads.jsonl` (JSONL) | `sprint-status.yaml` (YAML) |
| **Contratos protegidos** | `constitution.md`, `spec.md` | `STATE.md`, `ROADMAP.md` | `config.yaml`, `specs/**` | `config.yaml`, `metadata.json` | `epic-*-context.md` |

---

## 5. Navegación Diátaxis-lite para Ingenieros Senior / Staff

- 📐 **[White Paper de Capacidades y Arquitectura (`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`)](../../dossier/02_SYSTEM_CAPABILITIES_WHITE_PAPER.md)** — Formulación matemática de atenuación del radio de impacto $I(T)$, verificación acíclica de Kahn, Three-Tier Scope Guard y álgebra de semirretículos.
- 🔌 **[Especificación de Integraciones y Ecosistema (`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`)](../../dossier/03_INTEGRATION_AND_ECOSYSTEM_SPEC.md)** — Protocolos de conexión para **BMAD**, **GitHub Spec Kit (`.specify/`)**, **GSD (`.planning/`)**, **OpenSpec** y **Beads**, con esquemas de bloqueo y MCP.
- 📊 **Verificación en Repositorios Reales (Brownfield)** — Validación empírica del flujo de trabajo Two-Stage Assisted en bases de código complejas y multi-épica.
- 🛡️ **[Resumen de Invariantes Fundamentales (`README.md`)](../../../README.md#-core-invariants)** — Lista de garantías de seguridad del sistema.

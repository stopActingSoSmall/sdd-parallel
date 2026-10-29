# ⚡ Демо-стенд `sdd-parallel demo`: как за 4 секунды увидеть защиту параллельных агентов в действии

<p align="center">
  <b>🌐 Язык / Language:</b>
  <a href="../../../dossier/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">English</a> |
  <a href="../../zh-CN/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">简体中文</a> |
  <a href="../../es/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Español</a> |
  <a href="../../pt-BR/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">Português (BR)</a> |
  <a href="../../ja/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">日本語</a> |
  <a href="../../ko/07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md">한국어</a> |
  <b>Русский</b>
</p>

**Версия:** 0.1.0  

Параллельный запуск нескольких ИИ-агентов (Claude Code, Google Antigravity, Codex) в одном репозитории ускоряет разработку в 2–3 раза, но без жёсткой оркестрации почти неизбежно заканчивается тремя авариями:

1. **Тихая поломка контрактов (Семантическая `Merge-Bomb`):** Агент А меняет сигнатуру функции в сервисе, а Агент Б в соседней ветке одновременно пишет вызов старой сигнатуры в контроллере. Файлы разные — `git merge` проходит без единого конфликта, но сборка взрывается в рантайме.
2. **Галлюцинация скоупа (Scope Creep):** Агент во время выполнения локальной задачи решает «заодно подправить» схему базы данных или чужой модуль, ломая работу параллельных потоков.
3. **Война статусов в дорожной карте:** Два агента закрывают свои задачи и одновременно меняют соседние строки в общем файле спринта (`sprint-status.yaml` или `STATE.md`), из-за чего обычный `git merge` падает с маркерами конфликта `<<<<<<< HEAD`.

Команда **`sdd-parallel demo`** создана для того, чтобы вы могли **без API-ключей, без интернета и без настройки своего проекта за ~4 секунды проверить на реальном Git-репозитории**, как `sdd-parallel` предотвращает все три аварии.

---

## 1. Как устроено демо: реальное исполнение, а не заготовленный текст

Важно понимать с первой секунды: `uv run sdd-parallel demo` — **это не имитация терминального вывода**. При каждом запуске движок проделывает 100% честную работу на диске:
- инициализирует настоящий Git-репозиторий во временной папке вашей ОС (`Workspace: .../sdd-parallel-demo-<id>`);
- парсит реальные Python-файлы через синтаксическое дерево (`AST`);
- разворачивает настоящие изолированные рабочие деревья `git worktree` и выставляет системные блокировки ОС;
- делает реальные коммиты от лица двух параллельных треков и выполняет настоящее слияние `git merge --no-ff`.

Команда поддерживает как целевую проверку любого из 5 SDD-фреймворков, так и сквозную матричную валидацию:

```bash
# Режим 1: Быстрая проверка фреймворка по умолчанию (BMAD)
uv run sdd-parallel demo

# Режим 2: Проверка конкретного SDD-кита (bmad, spec-kit, gsd, openspec, beads)
uv run sdd-parallel demo --kit spec-kit

# Режим 3: Сквозная матричная верификация всех 5 китов со сводной таблицей
uv run sdd-parallel demo --kit all

# Режим 4: Песочница (оставляет созданный репозиторий на диске для ваших ручных экспериментов)
uv run sdd-parallel demo --keep-temp

# Режим 5: Мгновенная генерация чистой песочницы для ручного сквозного E2E-тестирования
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir "$env:TEMP/eval-speckit"
```

### Сводная матрица верификации 5 китов (`--kit all`)

При выполнении команды `uv run sdd-parallel demo --kit all` движок последовательно прогоняет сценарий на всех 5 адаптерах и выводит итоговую таблицу:

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

### Артефакты песочницы по 5 SDD-фреймворкам

| Фреймворк (`--kit`) | Генерируемые файлы спецификаций | Защищённые контракты (`forbidden_files`) | Файл 3-Way Monotonic Merge |
| :--- | :--- | :--- | :--- |
| `spec-kit` | `specs/001-telemetry/{spec.md, plan.md, tasks.md}` | `.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md` | `specs/001-telemetry/tasks.md` (`- [x]`) |
| `gsd` | `.planning/phases/01-core/*-PLAN.md` (XML `<files>`) | `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/PROJECT.md` | `.planning/STATE.md` (Post-Merge Hook) |
| `openspec` | `openspec/changes/<id>/{proposal.md, tasks.md}` | `openspec/config.yaml`, `openspec/specs/**`, чужие `changes/<sibling>/**` | `openspec/changes/<id>/tasks.md` (`- [x]`) |
| `beads` | `.beads/beads.jsonl` (typed `dependencies`) | `.beads/config.yaml` | `.beads/beads.jsonl` (JSONL `max(rank)`) |
| `bmad` | `_bmad-output/planning-artifacts/epics.md`, `spec-*.md` | `_bmad-output/**/epic-*-context.md` | `sprint-status.yaml` (`max(rank)`) |

> **Что будет меняться в выводе при повторных запусках?**  
> Только случайный суффикс временной папки в строке `Workspace:` и итоговое время `VERIFIED IN X.XXs` (зависит от скорости диска вашей ОС, обычно `2–4.5` с). Сами таблицы ролей, найденные конфликты, расписание волн и результаты слияния **всегда 100% идентичны**, так как планировщик опирается на строгую математику графов, а не на генерацию LLM.

---

## 2. Пошаговая раскадровка: что происходит на каждом этапе демо

Представьте работу `sdd-parallel demo` как 5 последовательных кадров состояния репозитория — от исходного бэклога до чистого слияния первой волны.

---

### Кадр 0. Исходная точка (Что создаётся на диске до старта планировщика)

Временная папка инициализируется первым коммитом (`chore: initial demo repository`). Внутри лежат миниатюрное приложение телеметрии на 6 модулей и бэклог в структуре **BMAD**:

```text
sdd-parallel-demo-<id>/
├── src/demo_app/
│   ├── __init__.py          # Пакетный экспорт (__all__)
│   ├── schema.py            # Схема БД и миграции (SCHEMA_VERSION = 1, auto_migrate())
│   ├── cli.py               # Точка регистрации CLI-команд (@app.command)
│   ├── normalizer.py        # Очистка данных: normalize_payload(raw)
│   ├── api.py               # HTTP-ручка: импортирует normalize_payload из normalizer.py!
│   └── exporter.py          # Выгрузка пачек событий: export_events(events)
└── _bmad-output/
    ├── planning-artifacts/          # Дорожная карта эпиков и скомпилированные контексты
    └── implementation-artifacts/    # sprint-status.yaml + 5 спецификаций задач (spec-*.md)
```

> 💡 **Примечание для опытных пользователей BMAD:**  
> Файлы внутри `_bmad-output/` в демо намеренно сделаны **минимальными структурными фикстурами** (по 4–10 строк вместо полновесных BMAD-документов с YAML-фронтматтером, `As a / I want / So that` и Gherkin-сценариями). Иерархия папок, ключи `sprint-status.yaml` и секции `## Code Map` полностью соответствуют тому, что парсит боевой адаптер, но убрана текстовая «вода», чтобы вы могли открыть любой файл и за 5 секунд глазами увидеть всю картину связей. *(В боевых репозиториях поддерживаются полновесные BMAD-истории любого объёма).*

**Пять задач в очереди (`ready-for-dev`) и три скрытые ловушки в них:**
- **`1-1`** *(Upgrade schema to v2)* $\to$ меняет `schema.py` (зависит от `1-2`).
- **`1-2`** *(Normalize payload)* $\to$ меняет `normalizer.py` и добавляет команду в `cli.py`.
- **`1-3`** *(CLI batch export)* $\to$ меняет `exporter.py` и тоже добавляет команду в `cli.py`. *(Ловушка №1: спор за общий `cli.py`)*.
- **`2-1`** *(Expose ingestion API)* $\to$ меняет только `api.py`. *(Ловушка №2 — скрытая `Merge-Bomb`: в спеке `2-1` ни слова про `1-2`, но `api.py` импортирует функцию из `normalizer.py`, который прямо сейчас переписывается в `1-2`!)*.
- **`2-2`** *(Add audit log schema)* $\to$ меняет `schema.py` (зависит от `1-1`). *(Ловушка №3: двойная миграция схемы БД)*.

---

### Кадр 1. Архитектурное зрение AST (`[Step 1/4]`)

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

**Что произошло:**  
Движок ничего не меняет в файлах проекта — он строит синтаксическое дерево (`AST`) каждого модуля и без каких-либо зашитых списков имен файлов определяет их **архитектурное поведение при параллельной работе**.

**Откуда взялись эти роли и есть ли другие?**  
В классических системах контроля версий все файлы равны, из-за чего оркестраторы либо разрешают лишнее (получая поломки), либо блокируют всё подряд (убивая параллелизм). На стыке теории графов зависимостей (*Core-Periphery*), условий параллельной независимости Бернштейна и коммутативных структур данных кодовая база делится на **3 статические роли модулей** (плюс **2 рантайм-зоны для артефактов SDD**):

1. **`spine` («Позвоночник» — строгая очередь):** Файлы, от которых зависит структура данных или слишком много модулей сразу (схемы БД с маркерами миграций `SCHEMA_VERSION` / `auto_migrate`, корневые `models.py`/`schema.py`, либо узлы графа с $\ge 5$ входящими импортами). Две задачи над `spine`-файлом **никогда** не запускаются в одной волне.
2. **`additive_registry` («Аддитивный реестр» — безопасное пополнение):** Точки расширения, куда разные задачи лишь дописывают регистрацию своей команды, роута или экспорта (декораторы `@app.command`, `@router.get`, `@tool`, списки `__all__` в `__init__.py`). Блокировать из-за них параллельную разработку бессмысленно — их безопасно менять в параллельных `git worktree` с последующим слиянием.
3. **`standard` («Обычный бизнес-модуль» — взаимное исключение + учёт связей):** Обычные сервисы и контроллеры. В одну волну нельзя ставить две задачи, меняющие один `standard`-файл (если они не разведены по изолированным функциям), и для них обязательно проверяется граф импортов (колонка **`Importers`**). Обратите внимание: сканер заметил, что `normalizer.py` импортируется внутри `src/demo_app/api.py`!

*(За кулисами во время исполнения волны работают также 2 служебные зоны для не-кодовых файлов: **монотонно сливаемые статусы** вроде `sprint-status.yaml` и **замороженные контексты эпиков** `epic-*-context.md`, запись в которые закрыта вообще всем воркерам волны, чтобы не сбрасывать кэш).*

---

### Кадр 2. Обезвреживание семантической `Merge-Bomb` и расписание волн (`[Step 2/4]`)

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

**Что произошло (ключевой момент всего демо):**
1. **Поймана скрытая `Merge-Bomb` между `1-2` и `2-1`:**
   - Простой планировщик (`Naive Scheduler`) сравнивает только списки файлов из спецификаций: `1-2` пишет в `normalizer.py`, а `2-1` — в `api.py`. Прямого пересечения нет (`DISJOINT WRITES -> False Safe!`). Если выпустить их вместе, ветки сольются без конфликта Git, но `api.py` упадёт при вызове изменённого `normalizer.py`.
   - `BSPWaveScheduler` строит **радиус поражения ($I$)**: поскольку на Шаге 1 мы узнали, что `api.py` импортирует `normalizer.py`, файл `api.py` входит в зону поражения задачи `1-2`. Обнаружено семантическое пересечение (`semantic_collision`), поэтому планировщик оставляет `1-2` в **Wave 1**, а `2-1` откладывает во **Wave 2** (когда новый контракт `normalizer.py` уже будет влит в основную ветку!).
2. **Сохранён полезный параллелизм между `1-2` и `1-3`:** Обе задачи затрагивают `cli.py`, но так как это `ADDITIVE_REGISTRY`, они спокойно встают бок о бок в **Wave 1** (`track-a` и `track-b`).
3. **Выстроена цепочка миграций БД:** `1-1` и `2-2` меняют `spine`-файл `schema.py`, поэтому разведены по **Wave 2** и **Wave 3**, при этом все 5 задач бэклога гарантированно вошли в план без петель и потерь.

---

### Кадр 3. Подготовка постоянных слотов и перехват нарушения границ (`[Step 3/4]`)

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

**Какие изменения произошли на диске в этот момент:**
- Появилась папка `.worktrees/` с двумя постоянными слотами: **`.worktrees/track-a`** (ветка `parallel/track-a` под задачу `1-2`) и **`.worktrees/track-b`** (ветка `parallel/track-b` под задачу `1-3`). Слоты постоянные: на следующих волнах они не пересоздаются заново, а мгновенно переключаются на месте, не плодя десятки папок в IDE.
- Внутрь каждого слота записаны правила игры для агента:
  - `.parallel-lock.json` — машиночитаемый паспорт разрешённых и заблокированных файлов этого трека;
  - `.tmp/track-context.md` и `ACTIVE_TRACK.md` — чёткая памятка границ для самого ИИ-агента;
  - на чужие и `spine`-файлы внутри слота выставлен системный замок ОС «только чтение» (`chmod a-w`).
- **Почему `100% Clean git status: True` критически важно?** Все служебные файлы оркестратора автоматически прописаны в локальном `.git/worktrees/<slot>/info/exclude`, а смена прав `chmod` скрыта настройкой `core.fileMode = false`. Когда агент заходит в слот и запускает `git status`, он видит **идеально чистый репозиторий** и не пытается закоммитить или удалить служебные файлы.
- **Краш-тест защиты:** Поскольку демо работает офлайн без запуска LLM, оно напрямую вызывает боевую функцию перехватчика `evaluate_tool_access()`, передавая ей запрос на запись в чужой файл схемы БД `src/demo_app/schema.py` внутри реального слота `track-a` (точно такой же JSON-пакет, какой присылает IDE при попытке живого агента отредактировать файл). Перехватчик считывает созданный `.parallel-lock.json` и мгновенно возвращает **`DENY`**.

---

### Кадр 4. Слияние волны и бесконфликтный мердж статусов (`[Step 4/4]`)

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

**Что произошло и почему здесь обычно возникает «война статусов»:**
1. До старта волны в общем файле `sprint-status.yaml` две задачи стояли на соседних строках в статусе `ready-for-dev`.
   - Агент в `track-a` завершил задачу `1-2` и поменял первую строку на `done` (оставив `1-3` как `ready-for-dev`).
   - Агент в `track-b` параллельно завершил задачу `1-3` и поменял вторую строку на `done` (оставив `1-2` как `ready-for-dev`).
2. **Почему обычный `git merge` здесь ломается:** Алгоритм слияния Git сравнивает текст блоками по 3 соседние строки (hunks). Поскольку изменённые строки `1-2` и `1-3` стоят вплотную друг к другу, обычный Git считает это конфликтом одного текстового блока и вставляет в YAML-файл маркеры аварии:
   ```yaml
   <<<<<<< HEAD (после влития track-a)
     1-2-normalize-telemetry-payload: done
     1-3-cli-batch-export: ready-for-dev
   =======
     1-2-normalize-telemetry-payload: ready-for-dev
     1-3-cli-batch-export: done
   >>>>>>> parallel/track-b
   ```
3. **Как это решает `sdd-parallel`:** Перед коммитом слияния контролёр `verify_wave` проверяет диффы веток, а **монотонный 3-сторонний мерджер (`status_merger.py`)** разбирает статусы построчно по рангу прогресса (`backlog (0) < ready-for-dev (1) < in-progress (2) < review (3) < done (4)`) и для каждой задачи выбирает максимум из трёх веток ($\max(\text{base}, \text{track-a}, \text{track-b})$). В результате обе задачи получают `done` с **0 маркеров конфликта `<<<<<<<`**.
4. **Пост-мерджевая уборка слотов:** Поскольку Волна 1 завершена и слита, оркестратор снял замки «только чтение» и убрал отработавшие `.parallel-lock.json` Волны 1 из `track-a` и `track-b` (оставив слоты чистыми в ожидании Волны 2), а запись о слиянии сохранил в журнал транзакций **`.parallel/state.json`**.

---

## 3. Практический квест: проверяем все команды своими руками

Чтобы лично пощупать живые файлы, блокировки, переиспользование слотов и откаты, запустите демо с сохранением папки:

```powershell
uv run sdd-parallel demo --keep-temp
```

Скопируйте путь из верхней строки `Workspace: ...` (далее в командах — `<DEMO_DIR>`) и пройдите 6 коротких шагов прямо в терминале.

> **Важная точка отсчёта:** Помните, что демо остановилось на **Кадре 4** — то есть Волна 1 (`1-2` и `1-3`) в этой папке **уже выполнена и влита в `master`**, а в очереди остались 3 задачи (`1-1`, `2-1`, `2-2`).

---

### Шаг 1. Осматриваем историю слияния и журнал транзакций
Убедимся глазами, что в папке лежит реальный Git-репозиторий с двумя параллельными влитыми ветками:

```powershell
git -C "<DEMO_DIR>" log --graph --oneline --all
```
*Куда смотреть:* Вы увидите начальный коммит и два мердж-коммита Волны 1 (`integrate track-a (1-2)` и `integrate track-b (1-3)`). А если открыть файл `<DEMO_DIR>/.parallel/state.json`, внутри будет записана точка возврата `pre_merge_commit` на случай отката.

---

### Шаг 2. Строим план волн (`plan`): полная картина vs оставшийся бэклог
Посмотрим, как планировщик строит дорожную карту:

```powershell
# 1. Посмотреть весь исходный расклад из 5 задач (включая уже закрытые 1-2 и 1-3):
uv run sdd-parallel plan --repo "<DEMO_DIR>" --include-done --dry-run

# 2. Сформировать рабочий план для 3 оставшихся задач (1-1, 2-1, 2-2):
uv run sdd-parallel plan --repo "<DEMO_DIR>"
```
*Что изменилось на диске:*
- Файлы исходного BMAD в `_bmad-output/planning-artifacts/` **не тронуты** (кэши эпиков сохранены).
- Рядом появилась новая папка **`<DEMO_DIR>/_bmad-output/parallel-artifacts/`** с двумя файлами: читаемой таблицей **`execution-plan-parallel.md`** (где для каждой задачи также рассчитан рекомендуемый уровень модели `Effort: low / medium / high`) и машинной схемой **`wave-plan.json`**.
- Задачи `1-1` (`schema.py`) и `2-1` (`api.py`) теперь стоят первыми на очереди — для текущего состояния репозитория они стали новой **Wave 1**.

---

### Шаг 3. Заряжаем постоянные слоты под следующую волну (`prepare` и `run-guide`)
Подготовим рабочие места для двух агентов под задачи `1-1` и `2-1`:

```powershell
uv run sdd-parallel prepare --repo "<DEMO_DIR>" --wave 1
uv run sdd-parallel run-guide --repo "<DEMO_DIR>" --wave 1
```
*Что изменилось на диске (загляните внутрь `<DEMO_DIR>/.worktrees/track-b/`):*
1. Новые папки не плодились — существующие слоты `track-a` и `track-b` за долю секунды переключились на свежий `master`.
2. Внутри `.worktrees/track-b/` появились живые файлы защиты текущей волны:
   - откройте **`.parallel-lock.json`** — в `allowed_write_globs` разрешён `src/demo_app/api.py`, а в `forbidden_files` жёстко заблокирован `src/demo_app/schema.py` с пометкой `"Locked by sibling track-a (story 1-1)"` и закрыты все `epic-*-context.md`;
   - откройте **`.tmp/track-context.md`** и **`ACTIVE_TRACK.md`** — это готовый контекстный коридор для ИИ-агента.
3. Команда `run-guide` вывела готовую шпаргалку оператора: какие папки открыть в окнах IDE и какую команду запустить в каждом треке.

---

### Шаг 4. Испытываем блокировщик на прочность (`check-lock`)
Проверим, как защитный хук отреагирует на действия агента в слоте `track-b` (которому поручен только `api.py`):

```powershell
# Попытка №1: агент меняет свой законный файл api.py
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/api.py"

# Попытка №2: агент пытается залезть в файл схемы БД соседа (schema.py)
uv run sdd-parallel check-lock --file "<DEMO_DIR>/.worktrees/track-b/src/demo_app/schema.py"
```
*Что вы увидите:* Первая команда мгновенно вернёт `{"decision": "allow"}` (код выхода `0`), а вторая — жёсткий отказ `{"decision": "deny", "reason": "BLAST RADIUS VIOLATION... Locked by sibling track-a (story 1-1)"}` с кодом блокировки `2`.

---

### Шаг 5. Экстренный сброс ещё не слитой волны (`abort-wave`)
Допустим, вы подготовили волну на Шаге 3, но решили поменять приоритеты до слияния:

```powershell
uv run sdd-parallel abort-wave --repo "<DEMO_DIR>" --wave 1
```
*Что изменилось:* Слоты `track-a` и `track-b` разблокированы и очищены от временных манифестов без единого следа в основной ветке.

---

### Шаг 6. Машина времени: транзакционный откат уже влитой волны (`rollback-wave`)
Вспомним, что в самом начале демо (на Кадре 4) в ветку `master` уже была влита первая волна с задачами `1-2` и `1-3`. Представим, что после слияния мы нашли архитектурную ошибку и хотим откатить всю волну целиком:

```powershell
uv run sdd-parallel rollback-wave --repo "<DEMO_DIR>" --wave 1 --save-backup
```
*Куда посмотреть и что изменилось:*
1. **Никакой потери данных:** Перед откатом оркестратор автоматически сохранил резервную ветку `"backup_branch": "backup/wave-1-before-rollback-..."` (её видно через `git -C "<DEMO_DIR>" branch -a`).
2. **Чистый откат Git в строгом порядке LIFO (от последней слитой волны к предыдущим):** Команда `git -C "<DEMO_DIR>" log --oneline` покажет, что `master` вернулся к самому первому коммиту `chore: initial demo repository`.
3. **Синхронный откат статусов задач:** Откройте `<DEMO_DIR>/_bmad-output/implementation-artifacts/sprint-status.yaml` — задачи `1-2` и `1-3` автоматически вернулись из `done` обратно в `ready-for-dev`, а сгенерированные спецификации откаченной волны вычищены как артефакты незавершённой реализации.

---

## 4. Сценарий Б: Полный E2E-прогон на собственном Greenfield-репозитории с нуля (От `git init` до краш-теста ядра ОС и отката)

Если в Разделе 3 мы исследовали уже готовый демо-репозиторий, то здесь вы пройдёте путь валидации полного жизненного цикла на чистой песочнице без необходимости вручную писать десятки строк бойлерплейта.

Благодаря флагу **`sdd-parallel demo --scaffold-only --output-dir <путь>`**, движок за 1 секунду разворачивает полноценный проект с реальными спецификациями выбранного фреймворка, исходным кодом и Git-историей.

---

### Направление А: Сквозное прохождение на GitHub Spec Kit (`--kit spec-kit`)

#### Шаг 1. Создание песочницы Spec Kit и инспекция окружения
```powershell
# 1. Разворачиваем чистую песочницу Spec Kit во временном каталоге
$SANDBOX = "$env:TEMP\eval-speckit"
uv run sdd-parallel demo --kit spec-kit --scaffold-only --output-dir $SANDBOX
Set-Location $SANDBOX

# 2. Проверяем созданное состояние
git status
git log -1 --oneline
```
*Что создано:*
- `specs/001-telemetry/{spec.md, plan.md, tasks.md}` — чек-лист задач по стандарту GitHub Spec Kit (`- [ ] 1-1`, `- [ ] 1-2`, `- [ ] 1-3`, `- [ ] 2-1`, `- [ ] 2-2`).
- `.specify/memory/constitution.md` и `.specify/feature.json` — неизменяемые контракты проекта.
- `src/demo_app/` — 6 модулей телеметрии с заложенной скрытой коллизией 2-го рода.

#### Шаг 2. Расчёт параллельного плана волн (`plan`)
```powershell
uv run sdd-parallel plan
```
*Что происходит:*
- Авто-детектор распознаёт GitHub Spec Kit по спецификациям `specs/**/tasks.md` и `.specify/`.
- AST-анализатор присваивает роли (`schema.py` $\to$ `SPINE`, `cli.py` $\to$ `ADDITIVE_REGISTRY`), находит семантический импорт `api.py` из `normalizer.py`.
- Задачи `1-2` и `1-3` объединяются в **Wave 1**, а задача `2-1` изолируется в **Wave 2** во избежание Merge-Bomb.

#### Шаг 3. Подготовка фиксированных слотов треков (`prepare`)
```powershell
uv run sdd-parallel prepare --wave 1
```
*Результат на диске:*
- Инициализированы постоянные слоты `.worktrees/track-a` и `.worktrees/track-b`.
- Сформированы манифесты `.parallel-lock.json` с контекстными границами.
- Защищённые системные файлы (`.specify/memory/constitution.md`, `.specify/feature.json`, `spec.md`, `plan.md`) закрыты системным замком ОС (`chmod a-w`).

#### Шаг 4. Краш-тест защиты (PreToolUse и блокировка ядра ОС)
```powershell
# 1. Легитимный доступ в треке track-a:
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/normalizer.py
# -> {"decision": "allow"}

# 2. Попытка взлома границы (доступ к schema.py соседа):
uv run sdd-parallel check-lock --file .worktrees/track-a/src/demo_app/schema.py
# -> {"decision": "deny", "reason": "BLAST RADIUS VIOLATION..."}

# 3. Краш-тест на уровне ОС (попытка прямой записи в constitution.md):
Add-Content -Path .worktrees\track-a\.specify\memory\constitution.md -Value "hacked"
# -> Ожидается: System.UnauthorizedAccessException (сработал OS Read-Only Lock)
```

#### Шаг 5. Параллельные коммиты в треках
```powershell
# Агент в track-a реализует задачу 1-2:
Set-Content -Path .worktrees\track-a\src\demo_app\normalizer.py -Value "def normalize_payload(raw): return {k.strip(): v.strip() for k, v in raw.items()}"
(Get-Content .worktrees\track-a\specs\001-telemetry\tasks.md) -replace '\[ \] 1-2', '[x] 1-2' | Set-Content .worktrees\track-a\specs\001-telemetry\tasks.md
git -C .worktrees\track-a commit -am "feat: complete task 1-2"

# Агент в track-b реализует задачу 1-3:
Set-Content -Path .worktrees\track-b\src\demo_app\exporter.py -Value "def export_events(events): return len(events)"
(Get-Content .worktrees\track-b\specs\001-telemetry\tasks.md) -replace '\[ \] 1-3', '[x] 1-3' | Set-Content .worktrees\track-b\specs\001-telemetry\tasks.md
git -C .worktrees\track-b commit -am "feat: complete task 1-3"
```

#### Шаг 6. Проверка диффов и монотонное барьерное слияние
```powershell
# 1. Аудит диффов относительно merge-base:
uv run sdd-parallel verify-wave --wave 1
# -> {"wave": 1, "compliant": true, "violations": []}

# 2. Барьерное слияние с 3-way merge чек-листа:
uv run sdd-parallel merge --wave 1
```
*Результат:* Обе задачи `- [x] 1-2` и `- [x] 1-3` монотонно закрыты в `specs/001-telemetry/tasks.md` с **0 маркеров конфликтов**.

#### Шаг 7. Транзакционный LIFO-откат волны
```powershell
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```
*Итог:* Создана резервная ветка `backup/wave-1-before-rollback-...`, ветка `master` возвращена на исходный коммит, а в `tasks.md` статусы задач синхронно возвращены в незавершённые `[ ]`.

---

### Направление Б: Прохождение на BMAD (`--kit bmad`)

Для проектов, использующих BMAD (`_bmad-output/`), процесс полностью аналогичен:

```powershell
# 1. Разворачиваем песочницу BMAD:
$BMAD_SANDBOX = "$env:TEMP\eval-bmad"
uv run sdd-parallel demo --kit bmad --scaffold-only --output-dir $BMAD_SANDBOX
Set-Location $BMAD_SANDBOX

# 2. Расчёт и подготовка слотов:
uv run sdd-parallel plan
uv run sdd-parallel prepare --wave 1

# 3. Имитация коммитов (обновляет sprint-status.yaml):
(Get-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-2-normalize-telemetry-payload: ready-for-dev', '1-2-normalize-telemetry-payload: done' | Set-Content .worktrees\track-a\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-a commit -am "feat: finish story 1-2"

(Get-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml) -replace '1-3-cli-batch-export: ready-for-dev', '1-3-cli-batch-export: done' | Set-Content .worktrees\track-b\_bmad-output\implementation-artifacts\sprint-status.yaml
git -C .worktrees\track-b commit -am "feat: finish story 1-3"

# 4. Барьерная проверка и слияние:
uv run sdd-parallel verify-wave --wave 1
uv run sdd-parallel merge --wave 1

# 5. Транзакционный откат:
uv run sdd-parallel rollback-wave --wave 1 --save-backup
```

---

### Сравнительная таблица отличий по всем 5 китам

| Шаг / Механизм | `spec-kit` | `gsd` | `openspec` | `beads` | `bmad` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Команда скаффолда** | `--kit spec-kit` | `--kit gsd` | `--kit openspec` | `--kit beads` | `--kit bmad` |
| **Формат задач** | `specs/**/tasks.md` | `.planning/phases/*-PLAN.md` | `openspec/changes/*/tasks.md` | `.beads/beads.jsonl` | `sprint-status.yaml` |
| **Команда воркера** | `/speckit.implement <id>` | `/gsd-execute-phase <num>` | `/opsx:apply <id>` | `bd update <id>` | `/bmad-build <id>` |
| **Прогресс статуса** | `- [ ]` $\to$ `- [x]` | Фаза закрыта $\to$ `STATE.md` | `- [ ]` $\to$ `- [x]` | `open` $\to$ `closed` | `ready-for-dev` $\to$ `done` |
| **Цель 3-way слияния** | `tasks.md` (Markdown 3-way) | `.planning/STATE.md` | `tasks.md` (Markdown 3-way) | `beads.jsonl` (JSONL 3-way) | `sprint-status.yaml` (YAML 3-way) |
| **Защищённые файлы** | `constitution.md`, `spec.md` | `STATE.md`, `ROADMAP.md` | `config.yaml`, `specs/**` | `config.yaml`, `metadata.json` | `epic-*-context.md` |

---

## 5. Для Senior / Staff инженеров: навигация по документации (Diátaxis-lite)

Если после прохождения демо вы хотите изучить математическую модель, граничные случаи и работу с другими SDD-фреймворками (GitHub Spec Kit, GSD, OpenSpec, Beads):

- 📐 **[Архитектурный White Paper (`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`)](02_SYSTEM_CAPABILITIES_WHITE_PAPER.md)** — формула взвешенного радиуса поражения $I(T)$ с затуханием по графу вызовов (`code-review-graph`), условия ацикличности Кана, трёхуровневый перехват шелл-команд (`Terminal Guard` против обхода через `>` / `sed -i` / `python -c`) и алгебра полурешёток статусов.
- 🔌 **[Спецификация интеграций и 5 SDD-адаптеров (`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`)](03_INTEGRATION_AND_ECOSYSTEM_SPEC.md)** — как `sdd-parallel` подключается к **BMAD**, **GitHub Spec Kit (`.specify/`)**, **GSD (`.planning/`)**, **OpenSpec** и **Beads**, а также схема `.parallel-lock.json` и протоколы хуков Antigravity / Claude Code.
- 📊 **Верификация на существующих проектах (Brownfield)** — Практический опыт применения двухфазной модели исполнения (`Two-Stage Assisted`) на сложных многомодульных репозиториях.
- 🛡️ **[Сводка 9 фундаментальных инвариантов в `README.md`](../README.md#-фундаментальные-инварианты)** — краткий чек-лист гарантий безопасности движка.

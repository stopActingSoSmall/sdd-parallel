# 📦 SDD-Parallel — Core Documentation Dossier (SSOT Package)

**Назначение директории:**  
Этот каталог содержит **модульный пакет отчуждаемой документации** по проекту `sdd-parallel`.  
Документы спроектированы по принципу **ортогональных смысловых блоков (LEGO-архитектура)**: каждый файл является самодостаточным, не содержит привязки к номерам строк исходного кода и может быть экспортирован в изолированную среду для передачи внешним стейкхолдерам или автономным ИИ-агентам.

---

## 🗺️ Состав модульного пакета

```
docs/dossier/
├── 01_CORE_PRODUCT_BRIEF.md              # Уровень 1: Бизнес, ценность, проблема, ROI, инварианты
├── 02_SYSTEM_CAPABILITIES_WHITE_PAPER.md # Уровень 2: Архитектура, математика конфликтов, AST/CRG
├── 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md  # Уровень 3: Точки входа, адаптеры, форматы .tmp/ и lock
├── 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md # Уровень 4: 4 эшелона рынка, сравнение с 300k+ аналогами
└── 07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md # Уровень 5: Анатомия `sdd-parallel demo` и ручной E2E-прогон
```

---

## 🎯 Сборки пакета под конкретные роли (Context Assembly Matrix)

В зависимости от задачи скопируйте соответствующую комбинацию файлов:

| Роль / Задача | Рекомендуемый набор файлов | Что получит адресат |
| :--- | :--- | :--- |
| 🔬 **AI-Исследователь / Benchmarker** *(Ваш основной кейс)* | `01_CORE_PRODUCT_BRIEF.md`<br>`+ 02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`<br>`+ 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`<br>`+ 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md` | Полное понимание уникальности продукта, алгоритмической модели и готовый бенчмарк для сравнения с аналогами (Superpowers, MetaGPT, Spec Kit, OpenHands). |
| 💼 **Инвестор / C-Level / Pitch** | `01_CORE_PRODUCT_BRIEF.md`<br>`+ 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md` | Бизнес-обоснование, сокращение Time-to-Market в 2x, экономия токенов на 45% и рыночное отстраивание от «шумных» аналогов. |
| 🛠️ **Внешний интегратор / Core Dev** | `02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`<br>`+ 03_INTEGRATION_AND_ECOSYSTEM_SPEC.md` | Техническая архитектура, правила изоляции в Git Worktree, манифесты блокировок и протокол создания нового адаптера. |
| 📐 **Product Owner / SDD Specifier** | `01_CORE_PRODUCT_BRIEF.md`<br>`+ 02_SYSTEM_CAPABILITIES_WHITE_PAPER.md` | Понимание принципов Spine Locks, Additive Registries и правил подготовки бэклога под бесконфликтную параллелизацию. |

---

## 🧭 Навигационная матрица Diátaxis-lite (Сценарии чтения)

Пакет технической документации спроектирован по стандарту **Diátaxis-lite**, чётко разделяющему информацию по 4 ключевым потребностям инженера:

| Режим чтения (Diátaxis) | Целевой документ | Содержание и фокус изучения |
| :--- | :--- | :--- |
| 🎓 **Tutorials** *(Обучение и живой прогон)* | [`07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md`](07_STANDALONE_DEMO_AND_E2E_WALKTHROUGH.md) | Пошаговый сквозной практикум: анатомия вывода 4 шагов `sdd-parallel demo`, Сценарий А (демо-песочница) и Сценарий Б (Greenfield-репозиторий с нуля, краш-тест OS Lock и LIFO-откат). |
| 🧠 **Explanation / Concepts** *(Теория и архитектурные концепции)* | [`02_SYSTEM_CAPABILITIES_WHITE_PAPER.md`](02_SYSTEM_CAPABILITIES_WHITE_PAPER.md) | Теоретический фундамент: классификация конфликтов 1-го и 2-го рода ($W_A \cap I_B$), 3-проходный алгоритмический конвейер `ASTHotspotScanner`, формула затухания радиуса поражения CRG, 3 эшелона защиты границ и полурешётки статусов. |
| 📖 **Reference & How-To** *(Спецификации, форматы и протоколы)* | [`03_INTEGRATION_AND_ECOSYSTEM_SPEC.md`](03_INTEGRATION_AND_ECOSYSTEM_SPEC.md) | Справочник CLI-команд, форматы выходных манифестов (`.tmp/track-context.md`, `.parallel-lock.json`), протокол MCP JSON-RPC, спецификация 5 SDD-адаптеров и протокол `SDDAdapterProtocol`. |
| 📊 **Executive Overview & Market Taxonomy** *(Бизнес-ценность и сравнение)* | [`01_CORE_PRODUCT_BRIEF.md`](01_CORE_PRODUCT_BRIEF.md)<br>[`04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md`](04_COMPETITIVE_AND_BENCHMARK_TAXONOMY.md) | Бизнес-обоснование (ROI, 2x Time-to-Market, экономия токенов на 45%), таксономия 4 эшелонов рынка мульти-агентных систем и сравнительный бенчмарк против 300k+ аналогов. |

---

## 🤖 Готовый промпт для запуска внешнего агента-исследователя

Если вы передаете эти документы независимому агенту в другой среде (где у него **нет доступа к кодовой базе `sdd-parallel`**), используйте следующий вводный промпт:

```markdown
<TASK>
Ты — старший архитектор и аналитик распределенных агентных систем.
Тебе предоставлен пакет документации фреймворка `sdd-parallel` (файлы 01_CORE_PRODUCT_BRIEF, 02_SYSTEM_CAPABILITIES_WHITE_PAPER, 03_INTEGRATION_AND_ECOSYSTEM_SPEC, 04_COMPETITIVE_AND_BENCHMARK_TAXONOMY).

Твоя задача:
1. Изучить архитектурное ядро и ключевые инновации фреймворка (Zero Worktree Pollution, Конфликты 2-го рода через AST/CRG Blast Radius, монотонный 3-way merger, Model Effort Routing).
2. Провести глубокий сравнительный анализ `sdd-parallel` с популярными фреймворками агентного кодинга (Superpowers, MetaGPT, GitHub Spec Kit, BMAD Method, OpenHands, SWE-agent, Aider и популярными мульти-агентными системами на десятки и сотни тысяч звёзд).
3. Определить:
   - В каких сценариях `sdd-parallel` превосходит эти системы;
   - Где возможно взаимовыгодное сопряжение (симбиоз), когда `sdd-parallel` выступает слоем безопасного исполнения для их воркеров;
   - Каких механизмов не хватает в `sdd-parallel`, которые уже доказали свою состоятельность в топовых open-source проектах.
</TASK>
```

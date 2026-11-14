# Реализация ревью 1.4.4 → Runtime 1.5 / Workspace 0.1

Основание — переданное текстовое резюме ревью коммита `b3a0623`: F01–F10, два независимых приложения, DSH и общий набор приёмки. Полный DOCX по ссылке ChatGPT Library в этой среде не был доступен. Таблица ниже — рабочая матрица, составленная по резюме; она не выдаётся за дословные 22 задачи из недоступного документа.

## Подтверждённые дефекты

| ID | Изменение | Регрессия |
|---|---|---|
| F01 | При первой публикации сохраняется содержимое кандидата; обязательные файлы проверяются до/после переключения; rollback сохраняет прежнюю программу | `test_build_transactions`, `test_runtime_review_m0` + чистая EXE-сборка |
| F02 | Ошибка уборки старых весов больше не отменяет фиксацию нового файла и реестра | Инъекция ошибки GC после commit |
| F03 | Общий lifespan восстанавливает pending предыдущего владельца и владеет InstanceLock | Startup headless/backend и вложенная desktop-lease |
| F04 | DiagnosticChatService использует общий ModelRegistry / DATA_DIR resolver | Внешний каталог данных |
| F05 | Параметры восстанавливаются по разделам, политика доступа сохраняется; неизвестная политика закрывает `/v1` с 503; частичный repair не расширяет доступ | Recovery/auth/cold-start + независимая проверка кандидата |
| F06 | UI посылает изменённые поля PATCH с If-Match; конфликт одного поля не перезаписывается; секреты не возвращаются форме | CAS, stale snapshot, disjoint merge |
| F07 | Явный отсутствующий ID возвращает ошибку; fallback только без выбора | Diagnostic model selection |
| F08 | Модельные и request overrides проверяются до worker; итоговый контекст — с реальным max_tokens | Invalid runtime / no WorkerClient allocation |
| F09 | Описаны серверная история чата, stateless inference, разделение reasoning/content и recovery policy | README + API + Workspace guide |
| F10 | Draft сравнивается с исходным отправленным текстом; пробелы не мешают очистке; новый ввод сохраняется | Frontend draft helper |

## Матрица поставки

| Задача | Реализация | Приёмка |
|---|---|---|
| W01 | Транзакционная чистая поставка Runtime | Preflight, первая установка, rollback |
| W02 | Модельные commit/GC границы | Новые веса остаются при ошибке уборки |
| W03 | Восстановление общего чата | Перезапуск pending без вечного busy |
| W04 | Единый путь и ID модели | DATA_DIR, explicit missing ID |
| W05 | Access policy и согласованные настройки | Ключ/disabled не сбрасываются; PATCH/CAS |
| W06 | Общие сервисы вне launcher | Backend запускается без desktop/tools |
| W07 | ModelRegistry и RuntimeManager | Единый worker, явная admission capacity |
| W08 | Полная схема инструментального диалога | assistant calls → tool results → answer |
| W09 | IPC и шаблон GGUF | Qwen3.5 native XML, typed arguments, no incomplete action |
| W10 | SSE и stable errors | Call IDs/arguments, tool_calls finish, busy до SSE |
| W11 | Capabilities, readiness, профиль | Пределы и фактический адаптер доступны по HTTP |
| W12 | Адресная отмена | Owner token не отменяет соседний запрос |
| W13 | Pinned DSH adapter | SDK/native version + digest, local profile, no telemetry/retry |
| W14 | Управляемое дерево процессов | Windows Job, Linux process group, blocked-stdin stop |
| W15 | Сквозная задача | Реальная 4B читает проект, исправляет файл, запускает тесты |
| W16 | Независимый Workspace | Отдельная версия, SQLite, data root, EXE/build |
| W17 | UI задач и событий | Проекты, действия, вывод, diff, независимый выбор окна |
| W18 | Разрешения и вопросы | Одноразовое решение с журналом |
| W19 | История, timeout, resume | Canonical JSONL v4 → UI projection; без replay |
| W20 | Снимки и совместимость данных | Consistent SQLite + DSH; future schema refusal; rollback в копии |
| W21 | Повторяемые измерения | 20 injected regressions из реальных модулей; тест до/после, diff, инструменты, время/RSS |
| W22 | Первый app connector и выпуск | Read + конкретно одобренная write, receipt, MCP; Docker CPU/CUDA, Windows, GitHub |

Матрица описывает критерии, а не заменяет доказательство их прохождения. Фактические запуски, результаты, ограничения модели и непроведённые проверки указаны отдельно в [VALIDATION.md](VALIDATION.md). Runtime и Workspace не имеют общего релизного номера или общего каталога данных.

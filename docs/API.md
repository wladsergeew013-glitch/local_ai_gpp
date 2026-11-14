# Local AI API

EXE запускает сервер на **127.0.0.1:8765**. Веб-чат: http://127.0.0.1:8765,
интерактивная схема: http://127.0.0.1:8765/docs.
Порт задаётся через LOCAL_AI_GPP_PORT или server.port в settings.json.
Запускайте один экземпляр: EXE, мини-помощник и API используют общий worker.

Docker: base URL `http://127.0.0.1:8080/v1`, Swagger `http://127.0.0.1:8080/docs`;
backend напрямую — `http://127.0.0.1:18765`. Пути GGUF внутри контейнера начинаются с `/models/`.
[Запуск и хранение Docker](DOCKER.md).

## Проверить подключение отдельным скриптом

Запустите `LocalAIGPP.exe`, затем откройте лежащий рядом `CHECK_API.bat`.
Скрипт `check_api.py` использует только HTTP и стандартную библиотеку Python — его можно
скопировать в другое приложение. Из исходников запуск выглядит так:

```powershell
python examples/check_api.py --base-url http://127.0.0.1:8765/v1
# Явно выбрать модель:
python examples/check_api.py --model "Qwen3.5 4B:Qwen3.5-4B-Q4_K_M.gguf"
```

Без `--model` используется первая модель из `/v1/models`, её ID печатается в начале.
Проверка запоминает имя `ORBIT`, запрашивает его в следующем сообщении через SSE,
а затем делает независимый запрос без истории. `"ok": true` и код завершения `0`
подтверждают успешную проверку; рассуждения Qwen отключены параметром
`chat_template_kwargs.enable_thinking=false`. Это обращение к локальному сервису:
доступ из локальной сети или интернета не требуется.

## Подключение модели

- `GET /api/health` — доступность сервера; не подтверждает загрузку модели.
- `GET /v1/models` — доступные LLM. Используйте возвращённый id в запросах.
- `GET /api/runtime/status` — модель, PID worker, CPU/CPU + CUDA/CUDA, фактические gpu_offloaded_layers/model_layers, причина fallback и время загрузки.
- `POST /api/models/register-path` — форма model_name, model_type=LLM, model_path, необязательный runtime_json={}. Это путь на машине сервера.
- `POST /api/models/{model_id}/start` — загрузить, необязательное тело `{"runtime":{"n_gpu_layers":-1}}`.
- `POST /api/models/{model_id}/unload` — выгрузить.
- `POST /api/runtime/unload-all` — освободить память всех моделей.
- `POST /api/runtime/cancel` — прекратить загрузку/разогрев или генерацию, завершив worker.

При первом inference модель загружается автоматически. Загружена одна LLM одновременно.
Sampler настройки не требуют перезагрузки; изменение n_ctx/GPU layers требует её.
`keep_hot` (или `manual`) сохраняет модель, `unload_after_idle` выгружает после idle_unload_sec.
model_id в URL кодируйте через percent encoding.

Три режима задаются `runtime.n_gpu_layers`: `0` — только CPU (KV и операции тоже остаются CPU),
положительное число — столько слоёв на GPU, остальные на CPU, `-1` — все поддерживаемые слои на GPU.
`gpu_fallback_to_cpu=false` позволяет проверить выбранный режим без скрытого перехода на CPU.
Статус отражает наблюдаемое число слоёв из llama.cpp, а не только запрошенную настройку.

## OpenAI-compatible

Текущий контракт — **API 1.1**. Клиенту Workspace не нужно читать файлы Runtime:

| Запрос | Результат |
|---|---|
| `GET /api/health` | Процесс сервера жив; не означает готовность модели |
| `GET /v1/runtime/readiness` | `model_not_loaded`, `ready` или `busy`; capacity=1, queue=0 |
| `GET /v1/models/{id}/capabilities` | Реальный контекст выбранного профиля, лимит вывода, streaming, поддержка tools и адаптер |
| `POST /v1/models/{id}/load` | Загрузить; необязательное `{"runtime":{"n_ctx":16384,"max_tokens":2048,"n_gpu_layers":12}}` сохраняет явно выбранный профиль модели |
| `POST /v1/requests/{id}/cancel` | Адресная отмена с `X-Local-AI-Cancel-Token` |
| `POST /v1/owners/{owner}/cancel` | Отмена запроса владельца с `X-Local-AI-Owner-Token` |

`tools.supported` подтверждается после загрузки фактического GGUF-шаблона, а не по имени модели. Проверены Qwen3.5 XML и протокольный цикл; другие модели не объявляются поддержанными автоматически. Контекст/GPU-профиль уже загруженной модели используется другими API-клиентами; явные значения native-запроса имеют приоритет. После выгрузки используется сохранённый профиль модели. Формы Runtime сохраняют выбранный профиль при загрузке.

POST `/v1/chat/completions`, Content-Type: application/json:

```json
{
  "model": "ID из /v1/models",
  "messages": [
    {"role":"system","content":"Answer briefly."},
    {"role":"user","content":"My name is Alex."},
    {"role":"assistant","content":"Hello, Alex."},
    {"role":"user","content":"What is my name?"}
  ],
  "temperature": 0.2,
  "max_tokens": 256,
  "memory": true,
  "stream": false
}
```

Ответ: choices[0].message.content, choices[0].finish_reason, usage.
При stream=true: SSE `data: {"object":"chat.completion.chunk",...}`,
текст в choices[0].delta.content, затем finish_reason и `data: [DONE]`.
`stream_options: {"include_usage":true}` добавляет chunk с usage и пустым choices.
Потоковый usage.estimated=true обозначает приблизительный подсчёт без полного chat template.
Ошибка после начала SSE: `data: {"error":{"message":...,"code":...}}`.
Клиент обязан проверить её и [DONE]; комментарии `: keepalive` можно игнорировать.

Реализованы текстовый chat completions, model listing и streaming.
Tools/function calling, изображения, embeddings и Responses API не реализованы.

## Qwen: прямой ответ и рассуждения

Адаптер определяется по `general.architecture` GGUF и наличию `enable_thinking` в chat template.
Для таких Qwen-моделей рассуждения **выключены по умолчанию**. Адаптер связывает переменную штатного шаблона,
а не удаляет уже сгенерированный текст. Другие модели и Qwen-шаблоны без этой возможности сохраняют свой формат.
Проверена Qwen3.5 4B; поведение иных GGUF зависит от их собственного шаблона.

### Python-клиент: обычное общение без рассуждений

```python
from examples.client import LocalAI

ai = LocalAI(
    base_url="http://127.0.0.1:8765/v1",
    model="Qwen3.5 4B:Qwen3.5-4B-Q4_K_M.gguf",  # Точный ID из /v1/models
    memory=True,
    enable_thinking=False,  # Отключить рассуждения Qwen
)
print(ai.ask("Привет! Ответь одним коротким предложением."))
```

`enable_thinking=False` отключает рассуждения, `True` включает их, `None` или отсутствие параметра использует настройки сервера/модели. Значение передаётся при каждом `ask()`, включая `stream=True`.

```bash
python examples/client.py --model "Qwen3.5 4B:Qwen3.5-4B-Q4_K_M.gguf" --no-thinking --prompt "Привет!"
```

### OpenAI-compatible HTTP / curl

Запрос без рассуждений:

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"Qwen3.5 4B:Qwen3.5-4B-Q4_K_M.gguf","messages":[{"role":"user","content":"Привет! Ответь кратко."}],"max_tokens":512,"chat_template_kwargs":{"enable_thinking":false}}'
```

Чтобы включить рассуждения, передайте `true` вместо `false`. Тело запроса:

```json
{"model":"ID","messages":[{"role":"user","content":"What is two plus two?"}],"max_tokens":512,"chat_template_kwargs":{"enable_thinking":true}}
```

В Python OpenAI SDK передайте `extra_body={"chat_template_kwargs":{"enable_thinking": False}}`
в `client.chat.completions.create(...)`. В Python используются `False`/`True`, в JSON — `false`/`true`.

### Native API

`POST /api/chat` (для SSE — тот же JSON на `/api/chat/stream`):

```json
{
  "model_id": "Qwen3.5 4B:Qwen3.5-4B-Q4_K_M.gguf",
  "message": "Привет! Ответь кратко.",
  "memory": true,
  "max_tokens": 512,
  "runtime": {"enable_thinking": false}
}
```

Постоянная настройка: `settings.runtime.enable_thinking`; в интерфейсе — «Рассуждения Qwen».
Изменение флага не перегружает веса. `/api/runtime/status` возвращает `model_adapter` и `enable_thinking`;
у неподдерживаемого шаблона это `default` и `null`.

При включённых рассуждениях `/v1` возвращает финальный ответ в `message.content`, а рассуждения — отдельно в `message.reasoning_content` и `_local_ai_gpp.reasoning`. SSE не смешивает рассуждения с ответом. Native чат также отделяет `reasoning` от `answer`. В прежних assistant messages передавайте только финальный ответ (и tool_calls, если они были).
Токены рассуждения входят в лимит `max_tokens`; `finish_reason=length` означает обрезанный ответ.
Qwen3.5 не поддерживает `/think` и `/nothink` как официальный переключатель.
[Описание режимов от Qwen](https://huggingface.co/Qwen/Qwen3.5-4B#instruct-or-non-thinking-mode).

## Вызовы инструментов

Готовый безопасный пример: `python examples/tools_client.py`. Он выбирает первую доступную модель как обычный `LocalAI`, предлагает одну функцию `project_name`, проверяет имя и аргументы, возвращает результат и получает итог. Произвольный Python или shell из ответа модели не исполняется.

```python
import json
import requests

base = "http://127.0.0.1:8765/v1"
model = requests.get(f"{base}/models").json()["data"][0]["id"]
tools = [{"type": "function", "function": {
    "name": "project_name",
    "description": "Return this example project's name.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}}]
messages = [{"role": "user", "content": "Use project_name to tell me my project name."}]

def ask():
    response = requests.post(f"{base}/chat/completions", json={
        "model": model, "messages": messages, "tools": tools,
        "max_tokens": 512, "chat_template_kwargs": {"enable_thinking": False},
    }, timeout=240)
    response.raise_for_status()
    return response.json()["choices"][0]

first = ask()
assert first["finish_reason"] == "tool_calls"  # length/error не разрешают действие
messages.append(first["message"])
for call in first["message"]["tool_calls"]:
    assert call["function"]["name"] == "project_name"
    assert json.loads(call["function"]["arguments"]) == {}
    messages.append({"role": "tool", "tool_call_id": call["id"],
                     "content": json.dumps({"name": "ORBIT"})})
print(ask()["message"]["content"])
```

`assistant.content` может быть `null`, если есть `tool_calls`. Каждый вызов имеет уникальный ID и JSON-строку `function.arguments`; каждый результат — роль `tool` с соответствующим `tool_call_id`. Допускаются несколько вызовов и результаты в любом порядке. Нельзя пропустить результат, ответить дважды на один ID или продолжить обычным сообщением до всех результатов. `memory=false` с инструментальным обменом отклоняется, чтобы не потерять эту связь.

В SSE вызовы передаются как `choices[].delta.tool_calls` с `index`, ID, именем и аргументами; конец хода — `finish_reason="tool_calls"`. Собирайте фрагменты по `index`. Для Qwen инструментальный ход буферизуется до полного проверенного envelope: неполный/обрезанный/неизвестный вызов возвращает ошибку, а не исполнимое действие. Обычный текстовый чат продолжает выдавать токены сразу.

`tool_choice`: `auto`, `none`, `required` или `{"type":"function","function":{"name":"project_name"}}`. `none` отключает предложения инструментов. Невыполненный forced/required выбор возвращает `TOOL_CALL_REQUIRED`/`TOOL_CHOICE_MISMATCH`. Runtime не выполняет инструменты и не подтверждает разрешения за приложение.

### Занятость, отмена и ошибки

Один активный запрос; второй получает HTTP `409`, `error.code="RUNTIME_BUSY"` и `Retry-After: 1` до открытия SSE. Это не разрешение автоматически повторять уже выполненную команду агента.

Для адресной отмены сохраните `X-Local-AI-Request-ID` и `X-Local-AI-Cancel-Token` из ответа и отправьте POST `/v1/requests/{id}/cancel` с тем же токеном. Если приложению нужен ID ещё до получения ответа, передайте собственные `X-Local-AI-Owner-ID` и случайный `X-Local-AI-Owner-Token`, затем отменяйте `/v1/owners/{owner}/cancel`. При настроенном ключе дополнительно нужен обычный Bearer/X-API-Key. Неизвестный owner возвращает cancelled=0; неверный токен активного запроса — 403. Завершённый запрос не затрагивает следующую генерацию.

HTTP и SSE используют устойчивые `error.code`: `RUNTIME_BUSY`, `MODEL_NOT_FOUND`, `CONTEXT_OVERFLOW`, `REQUEST_CANCELLED`, `WORKER_FAILED`, `WORKER_TIMEOUT`, `TOOLS_UNSUPPORTED`, `TOOL_CALL_INVALID`, `TOOL_CALL_INCOMPLETE`, `SETTINGS_POLICY_UNAVAILABLE`, `AUTH_REQUIRED`, `API_DISABLED`. Ошибка после начала SSE остаётся error-frame; клиент обязан проверить её и терминальный finish_reason, а не только HTTP 200.

## Память и отдельные диалоги

Модельные API `/v1/chat/completions`, `/api/chat` и `/api/chat/stream` не хранят сессии сообщений. Клиент передаёт историю нужного диалога при каждом запросе. Общий диагностический чат `/api/desktop/chat-*` сохраняет беседы на сервере и восстанавливает прерванные pending-сообщения при любом запуске backend, включая Docker.
memory=true (по умолчанию) использует все переданные messages.
memory=false сохраняет системные сообщения и последний вопрос пользователя, исключая прежнюю историю.
Для нового диалога используйте новую коллекцию messages. Общий worker не переносит историю между ними.
Качество запоминания зависит от модели и доступного контекста.

Native API: POST `/api/chat` или `/api/chat/stream`:

```json
{"model_id":"ID","message":"What is my name?","history":[{"role":"user","content":"My name is Alex."},{"role":"assistant","content":"Hello, Alex."}],"memory":true,"temperature":0.2,"max_tokens":256,"runtime":{}}
```

Native SSE содержит worker_status, runtime, heartbeat, delta, done, error.
done содержит answer, reasoning, usage и history_dropped.
При переполнении `/api/chat` с memory=true удаляет старые завершённые пары, сохраняя системный prompt и последний вопрос.
OpenAI-compatible API не обрезает историю автоматически: при переполнении возвращает ошибку.
max_tokens должен оставлять место для prompt в n_ctx; длинный последний вопрос не обрезается молча.
Браузер, EXE и мини-помощник используют общий серверный `assistant_state/shared_chat_v67.json`; браузерная локальная копия служит кэшем. Удаление кэша браузера не удаляет серверные беседы.
Сообщения desktop store и браузера содержат createdAt (ISO 8601), который сохраняется при обновлениях ответа.
Дата/время отображаются в локальном часовом поясе. Для старой истории используется сохранённый syncUpdatedAt;
если временных меток нет, интерфейс показывает «Время не сохранено».

## Ошибки и время ожидания

- 400: prompt/контекст/параметры модели; 404: неизвестная модель или выключенный compatibility API.
- 409: inference занят. Неограниченной очереди нет, повторите после завершения запроса.
- 502: worker завершился; 504: истёк timeout, worker остановлен.
- 503: повреждённые настройки/реестр, исходный файл сохранён; 422: параметры не соответствуют схеме.

После отправки SSE заголовков ошибки 409/504 приходят внутри потока с HTTP 200.
settings.runtime: request_timeout_sec=180, load_timeout_sec=120 (явный прогрев), worker_idle_timeout_sec=60.
Автоматическая загрузка входит в общий request_timeout.
Worker сообщает heartbeat во время загрузки и расчёта, включая запросы без stream, чтобы долгий ответ не вызвал ложный idle timeout.
Обрыв SSE или «Остановить» прекращает worker; следующий запрос создаст новый.

server.api_key защищает `/v1/*`: Authorization: Bearer KEY или X-API-Key.
Пустой ключ допустим для локального подключения.
Служебные `/api/*` этим ключом не защищены и предназначены для собственного локального приложения.
По умолчанию используется loopback; внешний доступ требует отдельного решения о защите сервиса.

## Пример приложения и VetConsult

### Python: явный выбор модели

Local AI должен уже работать, а GGUF — быть зарегистрирован. Получите точный ID из `GET /v1/models` и передайте его клиенту:

```python
from examples.client import LocalAI

ai = LocalAI(
    base_url="http://127.0.0.1:8765/v1",
    model="Qwen3.5 4B:Qwen3.5-4B-Q4_K_M.gguf",  # ID из /v1/models
    memory=True,
    enable_thinking=False,  # Обычный диалог Qwen без рассуждений
)

# Одна модель, продолжение одного диалога:
print(ai.ask("Запомни: мой проект называется ORBIT."))
print(ai.ask("Как называется мой проект?"))

# Та же модель, отдельный клиент без памяти:
other = LocalAI(base_url=ai.base_url, model=ai.model, memory=False, enable_thinking=False)
print(other.ask("Сколько будет два плюс два?"))
```

Клиент передаёт `model=ai.model` при каждом `ask()`. Если веса ещё не загружены, сервер загружает их при первом запросе.
Без аргумента `model` клиент получает `/v1/models` и выбирает **первую модель в списке**.
Выбор модели в интерфейсе не меняет модель Python-клиента. Проверить выбранный ID можно через `print(ai.model)`.
Для Docker замените base_url на `http://127.0.0.1:8080/v1` и возьмите ID из списка этого сервера.

`python examples/client.py --model "ID из /v1/models" --stream` — интерактивный чат с памятью, без сторонних библиотек.
--no-memory отключает историю; --base-url и --model выбирают подключение.
--max-tokens задаёт лимит ответа (по умолчанию 512); клиент сообщает об обрезке и исключает рассуждения из истории.
--no-thinking отключает рассуждения Qwen, --thinking включает; если флаг не указан, используются настройки сервера/модели.
Для защищённого API задайте LOCAL_AI_API_KEY.

### VetConsult

VetConsult: provider **openai_compat**, base_url **http://127.0.0.1:8765/v1**,
model — точный ID, timeout_sec=200, max_tokens=256.
`examples/vetconsult_connection.json` — готовое тело POST `/llm/connections`;
сохранение требует авторизованной администраторской сессии VetConsult.
Пример TinyLlama проверяет транспорт; для рабочих ответов выберите подходящую модель.
Активный коннектор и профили VetConsult выбираются отдельно.

Проверка штатного клиента без изменения VetConsult:

```powershell
& C:\Gavno\VetConsult\RAG_assistant_\venv\Scripts\python.exe tools/check_vetconsult_client.py --vetconsult-root C:\Gavno\VetConsult\RAG_assistant_ --model 'ID из /v1/models'
```


## История и целостность ответа (1.4.4)

Браузер, Docker web и мини-помощник используют общий серверный файл диалогов.
`version` монотонно возрастает; клиент не применяет более старый ответ HTTP после нового poll.
Очистка устанавливает барьер версии: старый snapshot или поздний worker не восстанавливает удалённые реплики.
Python `LocalAI` хранит свою память в экземпляре клиента; на `/v1` сервер не создаёт диалоги автоматически.
Лимит запроса — 8192 сообщения, затем действует реальный лимит контекста модели; клиент не обрезает историю скрыто.

У Qwen с включённым адаптером финальный ответ находится в `choices[0].message.content`,
рассуждение — в `reasoning_content`. `_local_ai_gpp.answer_state` и `reasoning_truncated`
показывают отсутствие финального ответа. В SSE эти поля приходят в `_local_ai_gpp` завершающего chunk.
Незавершённый ответ не добавляется в память Python-клиента. В обычном тексте XML-теги `<think>` сохраняются.

Сервис предназначен для loopback. Браузерные запросы проверяют Host и Origin до выполнения действий;
обычный Python/curl без Origin продолжает работать. Дополнительные браузерные origins задаются явно
через `server.cors_origins` или `LOCAL_AI_GPP_CORS_ORIGINS`; после изменения нужен перезапуск.


## Восстановление и согласованное изменение настроек

Если испорчены параметры генерации, восстанавливаются только соответствующие секции. Действующие `server.api_key`
и `openai_compat_enabled` из того же файла сохраняют силу. Когда политику доступа невозможно определить,
`/v1` возвращает `503 SETTINGS_POLICY_UNAVAILABLE`; health и диагностика продолжают работать. Исходный файл не переписывается.
При полном повреждении политики для ремонта необходимо явно передать корректные `server.api_key` и `server.openai_compat_enabled`.
Первый запуск без настроек и корректные частичные конфигурации поддерживаются.

`GET /api/settings` возвращает `_revision` и ETag. Секретные значения не возвращаются: вместо них есть `api_key_configured` и `token_configured`.
`PATCH /api/settings` принимает только изменённые поля и требует `If-Match: "<revision>"`. Конфликт версии — `409 SETTINGS_CONFLICT`,
отсутствие версии — 428. Неизменённые параметры и ключи остаются на сервере. Старый PUT поддерживается, но новые клиенты должны использовать PATCH.
Основная форма Runtime отправляет только изменённые runtime-поля и сохраняет параллельные правки других полей.

Общий backend, включая Docker, при старте восстанавливает незавершённые сообщения как прерванные, сохраняя частичный текст.
Повторный владелец того же каталога данных отклоняется до восстановления. Явный неизвестный ID модели возвращает 404 вместо выбора другой модели.

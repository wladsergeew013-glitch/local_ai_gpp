# Local AI API

EXE запускает сервер на **127.0.0.1:8765**. Веб-чат: http://127.0.0.1:8765,
интерактивная схема: http://127.0.0.1:8765/docs.
Порт задаётся через LOCAL_AI_GPP_PORT или server.port в settings.json.
Запускайте один экземпляр: EXE, мини-помощник и API используют общий worker.

Docker: base URL `http://127.0.0.1:8080/v1`, Swagger `http://127.0.0.1:8080/docs`;
backend напрямую — `http://127.0.0.1:18765`. Пути GGUF внутри контейнера начинаются с `/models/`.
[Запуск и хранение Docker](DOCKER.md).

## Подключение модели

- `GET /api/health` — доступность сервера; не подтверждает загрузку модели.
- `GET /v1/models` — доступные LLM. Используйте возвращённый id в запросах.
- `GET /api/runtime/status` — модель, PID worker, CPU/CPU + CUDA/CUDA, фактические gpu_offloaded_layers/model_layers, причина fallback и время загрузки.
- `POST /api/models/register-path` — форма model_name, model_type=LLM, model_path, необязательный runtime_json={}. Это путь на машине сервера.
- `POST /api/models/{model_id}/start` — загрузить, необязательное тело `{"runtime":{"n_gpu_layers":-1}}`.
- `POST /api/models/{model_id}/unload` — выгрузить.
- `POST /api/runtime/unload-all` — освободить память всех моделей.
- `POST /api/runtime/cancel` — прекратить активную генерацию, завершив worker.

При первом inference модель загружается автоматически. Загружена одна LLM одновременно.
Sampler настройки не требуют перезагрузки; изменение n_ctx/GPU layers требует её.
`keep_loaded` сохраняет модель, `unload_after_idle` выгружает после idle_unload_sec.
model_id в URL кодируйте через percent encoding.

Три режима задаются `runtime.n_gpu_layers`: `0` — только CPU (KV и операции тоже остаются CPU),
положительное число — столько слоёв на GPU, остальные на CPU, `-1` — все поддерживаемые слои на GPU.
`gpu_fallback_to_cpu=false` позволяет проверить выбранный режим без скрытого перехода на CPU.
Статус отражает наблюдаемое число слоёв из llama.cpp, а не только запрошенную настройку.

## OpenAI-compatible

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

OpenAI-compatible API позволяет включить рассуждения для одного запроса:

```json
{"model":"ID","messages":[{"role":"user","content":"What is two plus two?"}],"max_tokens":512,"chat_template_kwargs":{"enable_thinking":true}}
```

В OpenAI SDK передайте `extra_body={"chat_template_kwargs":{"enable_thinking": false}}`.
Native API принимает `runtime: {"enable_thinking": false}`.
Постоянная настройка: `settings.runtime.enable_thinking`; в интерфейсе — «Рассуждения Qwen».
Изменение флага не перегружает веса. `/api/runtime/status` возвращает `model_adapter` и `enable_thinking`;
у неподдерживаемого шаблона это `default` и `null`.

При включённых рассуждениях `/v1` возвращает исходный think-блок в content/SSE;
native чат отделяет `reasoning` от `answer`. В прежних assistant messages передавайте только финальный ответ.
Токены рассуждения входят в лимит `max_tokens`; `finish_reason=length` означает обрезанный ответ.
Qwen3.5 не поддерживает `/think` и `/nothink` как официальный переключатель.
[Описание режимов от Qwen](https://huggingface.co/Qwen/Qwen3.5-4B#instruct-or-non-thinking-mode).

## Память и отдельные диалоги

API не хранит сессии сообщений. Клиент передаёт историю нужного диалога при каждом запросе.
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
Браузер сохраняет историю локально; EXE и мини-помощник — в общем assistant_state/shared_chat_v67.json.
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

`python examples/client.py --stream` — интерактивный чат с памятью, без сторонних библиотек.
--no-memory отключает историю; --base-url и --model выбирают подключение.
--max-tokens задаёт лимит ответа (по умолчанию 512); клиент сообщает об обрезке и исключает рассуждения из истории.
Для защищённого API задайте LOCAL_AI_API_KEY.

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

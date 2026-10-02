# Docker · Linux CPU

Compose собирает React/Vite в Nginx и FastAPI с `llama-cpp-python 0.3.36` в отдельном Python 3.12 контейнере. Desktop-зависимости и EXE в образ не включаются. Эта конфигурация использует CPU; Windows CUDA проверяется отдельно.

## Запуск

```bash
export LOCAL_AI_MODELS_DIR=/absolute/path/to/gguf-folder
docker compose up -d --build --wait
```

В PowerShell:

```powershell
$env:LOCAL_AI_MODELS_DIR='C:\models\Qwen3.5-4B-GGUF'
docker compose up -d --build --wait
```

Откройте http://127.0.0.1:8080. В настройках подключите `/models/имя-файла.gguf` и выберите CPU. Полный путь Windows в контейнере использовать нельзя: `/models` отображает выбранную папку только для чтения. Без переменной подключается папка `models_storage` проекта.

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `LOCAL_AI_MODELS_DIR` | `./models_storage` | Папка GGUF на хосте |
| `LOCAL_AI_WEB_PORT` | `8080` | Чат, `/v1`, `/docs` через Nginx |
| `LOCAL_AI_API_PORT` | `18765` | API backend напрямую |

Порты публикуются на `127.0.0.1`. Порт backend 8000 остаётся внутренним, поэтому запуск не конфликтует с VetConsult на хосте.

## Регистрация и вызов API

```bash
curl http://127.0.0.1:8080/api/models/register-path \
  --data-urlencode 'model_name=Qwen3.5 4B' \
  --data-urlencode 'model_type=LLM' \
  --data-urlencode 'model_path=/models/Qwen3.5-4B-Q4_K_M.gguf' \
  --data-urlencode 'runtime_json={"n_gpu_layers":0,"n_ctx":4096,"gpu_fallback_to_cpu":false,"enable_thinking":false}'
curl http://127.0.0.1:8080/v1/models
python examples/client.py --base-url http://127.0.0.1:8080/v1 --stream
```

Ответ регистрации содержит `id`; при нескольких моделях используйте `--model 'ID'`. Первый запрос загружает модель, последующие используют тот же worker. Для Qwen4B с контекстом 1024 оставляйте `max_tokens <= 512`.

Nginx передаёт `/api`, `/v1`, Swagger и OpenAPI в backend. Буферизация SSE отключена. `/ui-assets` обслуживается статически, `/assets` позволяет получать branding из backend. `proxy_read_timeout=210s` превышает стандартный общий timeout генерации 180s.

## Хранение и остановка

Настройки, реестр, branding и логи сохраняются в именованном volume `local_ai_data` в `/data`. Веса модели читаются из `/models`, не копируются в образ и не изменяются контейнером. Файлы, загруженные через интерфейс, сохраняются в volume. История веб-чата хранится в браузере этого origin; desktop store относится к Windows EXE.

```bash
docker compose logs --tail 100
docker compose down
```

`down` сохраняет volume. `down -v` удаляет данные этой Compose-поставки; используйте для удаления своей тестовой установки. При смене портов сохраняйте значения переменных для запуска и остановки.

## Проверка

```bash
# Без модели: страница, JS/CSS, health, bootstrap, models, Swagger, branding.
python tools/check_docker.py
# С существующим GGUF: дополнительно реальный CPU inference.
python tools/check_docker.py --model-path /models/Qwen3.5-4B-Q4_K_M.gguf
```

Проверено 2026-10-02: Linux/amd64 образ на Docker Desktop, Qwen3.5 4B Q4_K_M, ответ `Four`, `finish_reason=stop`, адаптер `qwen`, рассуждения выключены. Двухходовый Python-клиент с SSE возвращает `ORBIT`. Холодная загрузка через Windows bind mount заняла 83.25s; это проверка работоспособности, не benchmark нативного Linux-диска.

Linux GPU/NVIDIA Container Toolkit в эту проверку не входят. GPU-запрос к CPU-образу требует fallback или вернёт ошибку при `gpu_fallback_to_cpu=false`. Для Windows проверены CPU, смешанный режим и все слои на CUDA — [результаты](VALIDATION.md).

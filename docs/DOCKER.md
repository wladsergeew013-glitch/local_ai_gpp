# Docker · Linux CPU и NVIDIA CUDA

Compose собирает React/Vite в Nginx и FastAPI с `llama-cpp-python 0.3.36`. По умолчанию backend использует Python 3.12 и CPU. Отдельный GPU-вариант использует NVIDIA CUDA 12.4.1, Ubuntu 22.04, Python 3.10 и точный официальный CUDA wheel `0.3.36/cu124`. Desktop-зависимости и EXE в образы не включаются.

## Запуск на CPU

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

## Запуск с NVIDIA GPU

GPU-образ предназначен для Linux **x86_64/amd64** с NVIDIA CUDA GPU. На хосте нужны Docker Engine с Compose v2, совместимый драйвер NVIDIA и [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). Для CUDA 12.4.1 используйте драйвер 550.54.15 или новее; [совместимость CUDA](https://docs.nvidia.com/cuda/archive/12.4.1/cuda-toolkit-release-notes/index.html#cuda-driver). CUDA Toolkit на хосте не нужен — runtime включён в образ.

После установки NVIDIA Container Toolkit настройте Docker по инструкции NVIDIA:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
nvidia-smi
docker run --rm --gpus all ubuntu:24.04 nvidia-smi
```

Последняя команда должна показать видеокарту внутри контейнера. При ошибке доступа к GPU сначала исправьте драйвер/Container Toolkit.

```bash
export LOCAL_AI_MODELS_DIR=/absolute/path/to/gguf-folder
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build --wait
```

`docker-compose.gpu.yml` меняет только backend: CUDA-образ и GPU reservation. Frontend, порты, read-only `/models` и постоянный `/data` сохраняются. [Как Compose передаёт GPU](https://docs.docker.com/compose/how-tos/gpu-support/).

В интерфейсе выберите CUDA или CPU + CUDA; при вызове API используйте `runtime.n_gpu_layers`:

| Значение | Распределение модели |
|---|---|
| `0` | CPU; KV и операции также остаются на CPU |
| `16` | 16 слоёв на GPU, остальные на CPU (пример для Qwen4B с 33 слоями) |
| `-1` | Все поддерживаемые слои на GPU |

GPU-образ поддерживает все три режима. При проверке задавайте `gpu_fallback_to_cpu=false`, чтобы нехватка VRAM или недоступная CUDA вернули ошибку. Проверяйте фактические `mode`, `gpu_offloaded_layers`, `model_layers` и `fallback_reason` в `GET /api/runtime/status`; служебные операции приложения выполняются на CPU даже при всех слоях модели на GPU.

Регистрация Qwen для CUDA без рассуждений:

```bash
curl http://127.0.0.1:8080/api/models/register-path \
  --data-urlencode 'model_name=Qwen3.5 4B' \
  --data-urlencode 'model_type=LLM' \
  --data-urlencode 'model_path=/models/Qwen3.5-4B-Q4_K_M.gguf' \
  --data-urlencode 'runtime_json={"n_gpu_layers":-1,"n_ctx":4096,"gpu_fallback_to_cpu":false,"enable_thinking":false}'
```

Дальше используйте тот же [Python-клиент и API](API.md); адрес `http://127.0.0.1:8080/v1`. Если модель уже зарегистрирована, выберите режим в настройках или передайте runtime при native запросе.

При обновлении существующей CPU-установки запустите GPU-команду с тем же Compose project name и папкой моделей: контейнер backend пересоздаётся, данные `/data` сохраняются. Смена образа сама по себе не меняет настройки GPU-слоёв. Для переключения обратно на CPU: `docker compose up -d --build --wait`, затем выберите CPU в интерфейсе.

На Windows GPU-контейнер также требует Docker Desktop с WSL2 backend и поддержкой NVIDIA GPU в WSL; GPU-вариант запускается той же Compose-командой.

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

Для GPU-варианта используйте те же Compose-файлы при просмотре логов и остановке:

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml logs --tail 100
docker compose -f docker-compose.yml -f docker-compose.gpu.yml down
```

`down` сохраняет volume. `down -v` удаляет данные этой Compose-поставки; используйте для удаления своей тестовой установки. При смене портов сохраняйте значения переменных для запуска и остановки.

## Проверка

```bash
# Без модели: страница, JS/CSS, health, bootstrap, models, Swagger, branding.
python tools/check_docker.py
# С существующим GGUF: дополнительно реальный CPU inference.
python tools/check_docker.py --model-path /models/Qwen3.5-4B-Q4_K_M.gguf
# На запущенном GPU-образе: смешанный и полностью GPU режимы без fallback.
python tools/check_docker.py --model-path /models/Qwen3.5-4B-Q4_K_M.gguf --gpu-layers 16
python tools/check_docker.py --model-path /models/Qwen3.5-4B-Q4_K_M.gguf --gpu-layers -1
```

Проверено 2026-10-02: Linux/amd64 образ на Docker Desktop, Qwen3.5 4B Q4_K_M, ответ `Four`, `finish_reason=stop`, адаптер `qwen`, рассуждения выключены. Двухходовый Python-клиент с SSE возвращает `ORBIT`. Холодная загрузка через Windows bind mount заняла 83.25s; это проверка работоспособности, не benchmark нативного Linux-диска.

GPU-запрос к обычному CPU-образу требует fallback или вернёт ошибку при `gpu_fallback_to_cpu=false`. GPU-образ выбирайте через overlay выше. Проверочные запросы и фактические GPU-слои — [результаты](VALIDATION.md).

# Local AI GPP Hub

Локальный AI Hub на FastAPI с разделённой структурой **backend/frontend**, поддержкой загрузки моделей, чата и базовой зоны тренировки.

## Требования
- **Python 3.11** (рекомендуется `3.11.x`)
- (опционально) Node.js 20+ для сборки TypeScript

## Что доступно
- Загрузка модели с диска/сервера через веб-интерфейс.
- Сохранение модели в `models_storage/`.
- Запуск модели (статус `running`, как точка интеграции рантайма).
- Зона тренировки (placeholder endpoint для подключения пайплайна обучения).
- Поддержка типов: LLM, Embedding, Classical ML, Other.

## Структура проекта

```text
.
├── backend/
│   └── app/
│       ├── main.py              # FastAPI backend
│       ├── templates/
│       │   └── index.html       # HTML шаблон
│       └── static/
│           ├── styles.css
│           ├── logo.svg
│           └── app.js           # собранный frontend JS
├── frontend/
│   ├── src/
│   │   └── app.ts              # frontend на TypeScript
│   ├── package.json
│   └── tsconfig.json
├── models_storage/             # создаётся автоматически
├── requirements.txt
├── Dockerfile
└── app.py                      # совместимость (экспорт app)
```

## Быстрый старт (venv)

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
```

Открыть: `http://127.0.0.1:8000`

> Также поддерживается совместимый entrypoint: `uvicorn app:app --reload`.

## Сборка TypeScript фронтенда

```bash
cd frontend
npm install
npm run build
```

После сборки обновится `backend/app/static/app.js`.

## Docker

```bash
docker build -t local-ai-gpp .
docker run --rm -p 8000:8000 local-ai-gpp
```

Открыть: `http://127.0.0.1:8000`

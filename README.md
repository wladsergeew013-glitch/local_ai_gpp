# local_ai_gpp

Базовый корпоративный форк локального AI-движка в стиле синий/белый.

## Что оставлено
- Загрузка модели с диска/сервера через веб-интерфейс.
- Сохранение модели в `models_storage/`.
- Запуск модели (статус `running`, как точка интеграции вашего рантайма).
- Зона тренировки (placeholder endpoint для подключения пайплайна обучения).
- Поддержка типов: LLM, Embedding, Classical ML, Other.

## Запуск
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Откройте: `http://127.0.0.1:8000`

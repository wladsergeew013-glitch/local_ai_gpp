"""One model-selection boundary for HTTP and diagnostic clients."""
from fastapi import HTTPException
from backend.app import core


class ModelRegistry:
    def list(self):
        return core.load_models()

    def select(self, model_id=None):
        models=[m for m in self.list() if m.get('type')=='LLM' and m.get('file_exists')]
        if model_id:
            selected=next((m for m in models if m.get('id')==model_id),None)
            if selected is None:raise HTTPException(404,'MODEL_NOT_FOUND: '+model_id)
            return selected
        selected=next((m for m in models if m.get('status') in {'running','warm'}),models[0] if models else None)
        if selected is None:raise HTTPException(404,'MODEL_NOT_FOUND: register a local GGUF model')
        return selected

    def resolve(self, path):
        return core.resolve_model_file_path(path)

    def register(self, **fields):
        return core.register_model_path(**fields)


model_registry=ModelRegistry()

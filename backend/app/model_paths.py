"""Portable paths share one base, independent of the process working directory."""
from pathlib import Path
import os


def data_root(project_root):
    raw = Path(os.environ.get('LOCAL_AI_GPP_DATA_DIR') or project_root)
    return (raw if raw.is_absolute() else Path(project_root) / raw).resolve()


def model_path(value, models_dir, project_root):
    path = Path(str(value or '').strip().strip('"').replace('\\', '/'))
    if path.is_absolute():
        return path
    candidates = ([Path(models_dir).parent / path] if path.parts[:1] == ('models_storage',)
                  else [Path(models_dir) / path, Path(project_root) / path])
    return next((item for item in candidates if item.is_file()), candidates[0])

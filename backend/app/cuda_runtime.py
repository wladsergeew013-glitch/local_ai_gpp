"""Register CUDA DLL paths without changing the machine's PATH/toolkit."""
import os
import sys
from pathlib import Path

_handles = []
_registered = set()


def configure_cuda_dlls() -> list[str]:
    if os.name != 'nt':
        return []
    configured = os.environ.get('LOCAL_AI_GPP_CUDA_DLL_DIR', '')
    candidates = [Path(configured)] if configured else []
    candidates += [Path(sys.executable).parent / 'cuda',
                   Path(sys.prefix) / 'cuda', Path(sys.prefix) / 'Lib/site-packages/nvidia/cuda_runtime/bin',
                   Path(sys.prefix) / 'Lib/site-packages/nvidia/cublas/bin']
    if os.environ.get('CUDA_PATH'):
        candidates.append(Path(os.environ['CUDA_PATH']) / 'bin')
    for candidate in candidates:
        if candidate.is_dir() and str(candidate.resolve()) not in _registered:
            _handles.append(os.add_dll_directory(str(candidate.resolve())))
            _registered.add(str(candidate.resolve()))
            # Older llama-cpp-python wheels load with winmode=0 and consult PATH.
            os.environ['PATH'] = str(candidate.resolve()) + os.pathsep + os.environ.get('PATH', '')
    return sorted(_registered)

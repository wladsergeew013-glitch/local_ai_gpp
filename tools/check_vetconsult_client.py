"""Exercise the actual VetConsult client without changing its state."""
import argparse
import json
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--vetconsult-root', type=Path, required=True)
parser.add_argument('--base-url', default='http://127.0.0.1:8765/v1')
parser.add_argument('--model', required=True)
parser.add_argument('--max-tokens', type=int, default=512)
args = parser.parse_args()
sys.dont_write_bytecode = True
sys.path.insert(0, str(args.vetconsult_root / 'backend/main'))
from app.services.llm.llm_client import openai_compat_complete

answer = openai_compat_complete('What is two plus two? Answer briefly.',
    base_url=args.base_url, model=args.model, api_key=os.getenv('LOCAL_AI_API_KEY', ''),
    temperature=0, max_tokens=args.max_tokens, timeout_sec=200)
print(json.dumps({'client':'VetConsult app.services.llm.llm_client.openai_compat_complete',
                  'base_url':args.base_url, 'model':args.model, 'answer':answer}, ensure_ascii=False, indent=2))
raise SystemExit(1 if answer.startswith('OA_') or not answer else 0)

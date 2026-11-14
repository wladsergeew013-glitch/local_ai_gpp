"""Pinned native SDK profile: local inference, plaintext durable v4 logs, no telemetry."""
import json
from pathlib import Path

SDK_VERSION='0.2.2rc2'
DSH_SOURCE='39163dd30f703a79079e2d2446545cff3c453738'
SESSION_FORMAT=4


def profile(path: Path, provider, capabilities, owner_id, owner_token):
    if not capabilities.get('tools',{}).get('supported'):raise ValueError('MODEL_TOOLS_UNAVAILABLE: load a model with a supported tool template.')
    context=int(capabilities['context_window']);output=int(capabilities['max_output_tokens'])
    if context < 8192:raise ValueError('MODEL_CONTEXT_TOO_SMALL: agent tasks require at least 8192 configured context tokens.')
    rows=[{'id':'llm-pi-ai','config':{'providers':{'local-ai':{
        'api':'openai-completions','baseURL':provider['base_url'],'apiKeyEnv':'LOCAL_AI_API_KEY',
        'models':[{'id':provider['model'],'contextWindow':context,'maxTokens':output,'reasoning':False}],
        'headers':{'X-Local-AI-Owner-ID':owner_id,'X-Local-AI-Owner-Token':owner_token},
        'retryPolicy':{'mode':'normal','maxRetries':0},
        'compat':{'supportsStore':False,'supportsDeveloperRole':False,'supportsReasoningEffort':False,
                  'supportsUsageInStreaming':True,'supportsFinishReason':True,'maxTokensField':'max_tokens',
                  'requiresToolResultName':False}}}}},
        # Loader overlays replace a plugin config object; retain an explicit root.
        {'id':'session-persistence-jsonl','config':{'compression':'none','root':str(path.parent/'sessions')}},
        {'id':'session-telemetry-otel','disabled':True},{'id':'session-title-llm','disabled':True},
        {'id':'llm-retry','disabled':True}]
    # These optional capabilities are intentionally outside the first local coding profile.
    for name in ('llm-deepseek','llm-deepseek-account','tool-web','tool-office','tool-schedule',
                 'tool-agent-team','tool-subagent-control','tool-subagent','tool-subagent-fork','tool-workflow',
                 'tool-goal','tool-todo','tool-plugin-manager','tool-skill','tool-executor-ptc'):
        rows.append({'id':name,'disabled':True})
    path.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    return path

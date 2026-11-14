"""Validate tool transcripts and decode the Qwen template's JSON tool envelope.

Runtime proposes calls; executing them remains the caller's responsibility.
"""
from __future__ import annotations

import json
import uuid
import re


def _xml_function(body: str, definitions: dict) -> dict:
    match = re.fullmatch(r'\s*<function=([a-zA-Z0-9_.-]+)>\s*(.*?)\s*</function>\s*', body, re.S)
    if not match or match[1] not in definitions:
        raise ValueError('TOOL_CALL_INVALID: unknown function or invalid Qwen XML envelope.')
    definition = definitions[match[1]]
    schema = definition.get('parameters') or {}
    properties = schema.get('properties') or {}
    arguments, cursor = {}, 0
    parameters = match[2]
    for parameter in re.finditer(r'<parameter=([a-zA-Z0-9_.-]+)>\n?(.*?)\n?</parameter>', parameters, re.S):
        if parameters[cursor:parameter.start()].strip() or parameter[1] in arguments:
            raise ValueError('TOOL_CALL_INVALID: duplicate parameter or unexpected text.')
        name, raw = parameter[1], parameter[2]
        kind = (properties.get(name) or {}).get('type')
        if kind == 'string':
            value = raw
        else:
            try:
                value = json.loads(raw)
            except ValueError:
                if kind in ('object', 'array', 'integer', 'number', 'boolean', 'null'):
                    raise ValueError('TOOL_CALL_INVALID: parameter has the wrong JSON type.') from None
                value = raw
        valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
                 'integer': isinstance(value, int) and not isinstance(value, bool),
                 'number': isinstance(value, (int, float)) and not isinstance(value, bool),
                 'boolean': isinstance(value, bool), 'null': value is None}
        if kind in valid and not valid[kind]:
            raise ValueError('TOOL_CALL_INVALID: parameter has the wrong JSON type.')
        arguments[name] = value
        cursor = parameter.end()
    if parameters[cursor:].strip() or not set(schema.get('required') or []).issubset(arguments):
        raise ValueError('TOOL_CALL_INVALID: malformed or missing parameters.')
    if schema.get('additionalProperties') is False and set(arguments) - set(properties):
        raise ValueError('TOOL_CALL_INVALID: unexpected parameters.')
    return {'name': match[1], 'arguments': arguments}


def validate_transcript(messages: list[dict], tools: list[dict]) -> None:
    names = [tool['function']['name'] for tool in tools]
    if len(set(names)) != len(names):
        raise ValueError('Tool names must be unique.')
    pending: set[str] = set()
    seen: set[str] = set()
    for message in messages:
        role = message['role']
        calls = message.get('tool_calls') or []
        if role == 'tool':
            call_id = message.get('tool_call_id')
            if call_id not in pending:
                raise ValueError('A tool result must match one outstanding tool_call_id exactly once.')
            pending.remove(call_id)
        else:
            if pending:
                raise ValueError('Return every tool result before the next user or assistant message.')
            if role == 'assistant':
                for call in calls:
                    if call['id'] in seen:
                        raise ValueError('Tool call IDs must be unique within the transcript.')
                    seen.add(call['id'])
                    pending.add(call['id'])
            elif calls:
                raise ValueError('Only assistant messages may contain tool_calls.')
    if pending:
        raise ValueError('The transcript ends with unanswered tool calls.')


def decode_qwen_tools(text: str, tools: list[dict], choice='auto', finish='stop') -> tuple[str | None, list[dict]]:
    """Only active for a verified Qwen tools template and explicitly offered tools."""
    if not tools or choice == 'none':
        return text, []
    definitions = {tool['function']['name']: tool['function'] for tool in tools}
    allowed = set(definitions)
    calls, prose, cursor = [], [], 0
    while True:
        start = text.find('<tool_call>', cursor)
        if start < 0:
            prose.append(text[cursor:])
            break
        prose.append(text[cursor:start])
        end = text.find('</tool_call>', start)
        if end < 0:
            raise ValueError('TOOL_CALL_INCOMPLETE: model did not finish a tool call; no action was emitted.')
        try:
            body = text[start + len('<tool_call>'):end].strip()
            value = _xml_function(body, definitions) if body.startswith('<function=') else json.loads(body)
        except (ValueError, TypeError) as exc:
            raise ValueError('TOOL_CALL_INVALID: model returned invalid tool JSON; no action was emitted.') from exc
        if not isinstance(value, dict) or value.get('name') not in allowed or not isinstance(value.get('arguments'), dict):
            raise ValueError('TOOL_CALL_INVALID: unknown tool name or non-object arguments.')
        if isinstance(choice, dict) and value['name'] != choice['function']['name']:
            raise ValueError('TOOL_CHOICE_MISMATCH: model selected a different tool.')
        calls.append({'id': 'call_' + uuid.uuid4().hex, 'type': 'function', 'function': {
            'name': value['name'], 'arguments': json.dumps(value['arguments'], ensure_ascii=False, separators=(',', ':'))}})
        cursor = end + len('</tool_call>')
    if calls and finish == 'length':
        raise ValueError('TOOL_CALL_INCOMPLETE: truncated completion; no action was emitted.')
    if not calls and (choice == 'required' or isinstance(choice, dict)):
        raise ValueError('TOOL_CALL_REQUIRED: model did not produce the requested tool call.')
    return ''.join(prose).strip() or None, calls


def merge_tool_delta(calls: dict[int, dict], delta: dict) -> None:
    for part in delta.get('tool_calls') or []:
        index = int(part.get('index', 0))
        call = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
        if part.get('id'):
            call['id'] += part['id']
        for key in ('name', 'arguments'):
            call['function'][key] += str((part.get('function') or {}).get(key) or '')

"""Choose from the chat's enabled skills and its own document handles using the active model."""

import json
import re

from httpx import HTTPStatusError

from workspace.app.runtime_client import decision_content, decision_request
from workspace.app.skills import ChatSkill


PLANNER = """Ты диспетчер текущей реплики. Реши по её смыслу, что необходимо.
1. Для приветствия или общего вопроса: {"use":[],"read":[]}.
2. Для чтения обычного документа, получения кода или выписки текста: {"use":[],"read":"all"}.
3. Если назначение конкретного навыка прямо соответствует запрошенной операции, добавь в use
объект с полями skill (номер этого навыка из каталога) и files (массив номеров нужных
документов или "all"). Для текстовой инструкции или сохранённого отчёта files пуст.
Для навыка workflow укажи в use только skill. Документы для операции выберет следующий шаг.
Выбирай только операции, которые запросил пользователь или которые нужны для указанного результата.
В use указывай навыки в порядке работы, включая операции с файлами, которые создаст предыдущий навык.
В read перечисли номера документов для отдельного чтения. Если отдельного чтения нет, укажи read: [].
Старые ответы относятся к предыдущим запросам. Верни один объект JSON."""


def eligible_documents(ctx, docs):
    extensions = ctx.definition.get("accepts")
    return [d for d in docs if not extensions or d["name"].lower().endswith(tuple(extensions))]


def parse_plan(text):
    if text.startswith("```"):
        text = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Some small models finish the action list but omit the outer closing brace.
        if exc.pos != len(text) or not text.startswith("{"):
            raise
        return json.loads(text + "}")


def choose(client, headers, messages, contexts, docs, cancelled, max_tokens, fresh, *, native=False):
    available = []
    catalog = []
    for ctx in contexts:
        skill = ctx.definition
        link = skill["connection"] or {}
        inputs = "workflow" if "product" in skill else "files" if link.get("files") else "file" if link.get("file") else "none"
        eligible = eligible_documents(ctx, docs)
        if inputs in {"files", "file"} and not eligible and ctx.cached is None:
            continue
        available.append(ctx)
        catalog.append({"id": len(available), "name": skill.get("title", skill["name"]),
                        "description": skill["description"] or ctx.instructions[:400],
                        "input": inputs, "formats": skill.get("accepts", []), "saved_report": ctx.cached is not None})
    if (not available and not docs) or cancelled.is_set():
        return [], []
    if not available and fresh:
        return [], list(docs)
    query = {"skills": catalog, "documents": [{"id": n, "name": d["name"]} for n, d in enumerate(docs, 1)],
             "previous": [m["content"][:800] for m in messages[-3:-1]],
             "request": messages[-1]["content"]}
    prompt = [{"role": "system", "content": PLANNER},
              {"role": "user", "content": json.dumps(query, ensure_ascii=False)}]
    structured = native and any(item["input"] == "workflow" for item in catalog)
    index_list = {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"const": "all"}]}
    decision = decision_request("workspace_select", "Choose relevant Workspace skills and documents in order.",
                                {"type": "object", "properties": {
                                    "use": {"type": "array", "items": {"type": "object", "properties": {
                                        "skill": {"type": "integer"}, "files": index_list,
                                    }, "required": ["skill"], "additionalProperties": False}},
                                    "read": index_list,
                                }, "required": ["use"], "additionalProperties": False}) if structured else {}
    for attempt in range(2):
        if cancelled.is_set():
            return [], []
        text = ""
        try:
            response = client.request("POST", "chat/completions", headers=headers, timeout=300, cancelled=cancelled,
                                      json=client.chat_payload({
                "model": client.model, "messages": prompt, "temperature": 0,
                "max_tokens": min(max_tokens, 320 if decision else 768), "memory": True,
                "chat_template_kwargs": {"enable_thinking": False},
                **decision,
            }))
            choice = response["choices"][0]
            text = decision_content(choice, "workspace_select" if decision else None).strip()
            plan = parse_plan(text)
            selected = selections(plan, available, docs) if available else []
            plain = plan.get("read", "all" if not selected and fresh else [])
            return selected, document_subset(plain, docs)
        except HTTPStatusError as exc:
            if not decision or attempt or exc.response.status_code not in {400, 415, 422, 501}:
                raise
            decision = {}
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            if cancelled.is_set():
                return [], []
            if attempt:
                if not available:
                    return [], list(docs)
                raise ValueError("Модель не смогла выбрать навыки и документы. Уточните запрос.") from exc
            prompt += [{"role": "assistant", "content": text[:1500]},
                       {"role": "user", "content": "Исправь формат: объект use, только номера из каталога. Первый документ имеет номер 1."}]
            decision = {}
    return [], []


def document_subset(indices, docs):
    if indices in ("all", ["all"]):
        return list(docs)
    if not isinstance(indices, list) or any(type(i) is not int or not 1 <= i <= len(docs) for i in indices):
        raise ValueError("invalid document")
    return [docs[i - 1] for i in dict.fromkeys(indices)]


def selections(plan, contexts, docs):
    if not isinstance(plan, dict) or not isinstance(plan.get("use"), list):
        raise ValueError("invalid plan")
    result, seen = [], set()
    for item in plan["use"]:
        number = item["skill"]
        if type(number) is not int or not 1 <= number <= len(contexts) or number in seen:
            raise ValueError("invalid skill")
        seen.add(number)
        ctx = contexts[number - 1]
        if "product" in ctx.definition:
            result.append((ctx, eligible_documents(ctx, docs)))
            continue
        connection = ctx.definition["connection"] or {}
        needs_files = bool(connection.get("file") or connection.get("files"))
        indices = item.get("files", [])
        allowed = eligible_documents(ctx, docs)
        subset = allowed if indices in ("all", ["all"]) else document_subset(indices, docs)
        if any(d not in allowed for d in subset):
            raise ValueError("document format not accepted by the selected skill")
        if needs_files and not subset and ctx.cached is None:
            raise ValueError("missing document")
        if not needs_files:
            subset = []
        batches = [[d] for d in subset] if connection.get("file") and subset else [subset]
        for batch in batches:
            result.append((ChatSkill(ctx.definition, ctx.instructions, None, ctx.cached if not batch else None), batch))
    return result

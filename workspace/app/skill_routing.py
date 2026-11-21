"""Choose from the chat's enabled skills and its own document handles using the active model."""

import json
import re

from workspace.app.skills import ChatSkill


PLANNER = """Ты диспетчер текущей реплики. Реши по её смыслу, что необходимо.
1. Для приветствия или общего вопроса: {"use":[],"read":[]}.
2. Для чтения обычного документа, получения кода или выписки текста: {"use":[],"read":"all"}.
3. Если назначение конкретного навыка прямо соответствует запрошенной операции, добавь в use
объект с полями skill (номер этого навыка из каталога) и files (массив номеров нужных
документов или "all"). Для текстовой инструкции или сохранённого отчёта files пуст.
Запрос может требовать нескольких навыков. В read перечисляй только документы для
обычного чтения. Старые ответы относятся к предыдущим запросам. Верни один объект JSON."""


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


def choose(client, headers, messages, contexts, docs, cancelled, max_tokens, fresh):
    available = []
    catalog = []
    for ctx in contexts:
        skill = ctx.definition
        link = skill["connection"] or {}
        inputs = "files" if link.get("files") else "file" if link.get("file") else "none"
        eligible = eligible_documents(ctx, docs)
        if inputs != "none" and not eligible and ctx.cached is None:
            continue
        available.append(ctx)
        catalog.append({"id": len(available), "name": skill["name"],
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
    for attempt in range(2):
        if cancelled.is_set():
            return [], []
        response = client.request("POST", "chat/completions", headers=headers, timeout=300, json={
            "model": client.model, "messages": prompt, "temperature": 0,
            "max_tokens": min(max_tokens, 768), "memory": True,
            "chat_template_kwargs": {"enable_thinking": False},
        })
        choice = response["choices"][0]
        text = (choice["message"].get("content") or "").strip()
        try:
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete decision")
            plan = parse_plan(text)
            selected = selections(plan, available, docs) if available else []
            plain = plan.get("read", "all" if not selected and fresh else [])
            return selected, document_subset(plain, docs)
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            if attempt:
                if not available:
                    return [], list(docs)
                raise ValueError("Модель не смогла выбрать навыки и документы. Уточните запрос.") from exc
            prompt += [{"role": "assistant", "content": text[:1500]},
                       {"role": "user", "content": "Исправь формат: объект use, только номера из каталога. Первый документ имеет номер 1."}]
    return [], []


def document_subset(indices, docs):
    if indices == "all":
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
        connection = ctx.definition["connection"] or {}
        needs_files = bool(connection.get("file") or connection.get("files"))
        indices = item.get("files", [])
        allowed = eligible_documents(ctx, docs)
        subset = allowed if indices == "all" else document_subset(indices, docs)
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

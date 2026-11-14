"""Twenty repeatable regression-repair tasks copied from this real repository.

Each fixture injects ONE known bug into a public, pure Runtime module. This is
a local integration/regression benchmark, not a broad coding-model ranking.
Existing task results are never automatically resent, even after a timeout.
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import psutil
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workspace.app.store import Store, identifier
from workspace.app.task_service import TaskService


CASES = [
    (
        "context-minimum",
        "context_budget.py",
        "512 <= n_ctx",
        "1024 <= n_ctx",
        "validate_context(512,16)",
    ),
    (
        "context-maximum",
        "context_budget.py",
        "n_ctx <= 131072",
        "n_ctx <= 65536",
        "validate_context(131072,16)",
    ),
    (
        "output-maximum",
        "context_budget.py",
        "min(32768,",
        "min(8192,",
        "validate_context(65536,16384)",
    ),
    (
        "output-reserve",
        "context_budget.py",
        "n_ctx - 257",
        "n_ctx - 511",
        "validate_context(512,128)",
    ),
    (
        "context-lower-edge",
        "context_budget.py",
        "512 <= n_ctx",
        "512 < n_ctx",
        "validate_context(512,32)",
    ),
    (
        "utf8-estimate",
        "context_budget.py",
        ".encode('utf-8')",
        ".encode('ascii',errors='ignore')",
        "self.assertEqual(estimate_context([{'content':'Привет'}],8),172)",
    ),
    (
        "template-overhead",
        "context_budget.py",
        "+ 128 + max_tokens",
        "+ 0 + max_tokens",
        "self.assertEqual(estimate_context([],8),136)",
    ),
    (
        "message-overhead",
        "context_budget.py",
        "+ 32 for m in messages",
        "+ 0 for m in messages",
        "self.assertEqual(estimate_context([{'content':'abc'}],8),169)",
    ),
    (
        "plain-answer-trim",
        "reasoning.py",
        "answer=text.strip()",
        "answer=text",
        "self.assertEqual(split_reasoning('  Hello  ',False)['answer'],'Hello')",
    ),
    (
        "closing-delimiter-case",
        "reasoning.py",
        "re.search(r'</think\\s*>', text, re.I)",
        "re.search(r'</think\\s*>', text)",
        "self.assertEqual(split_reasoning('reason</THINK>Answer',True)['answer'],'Answer')",
    ),
    (
        "reasoning-answer-phase",
        "reasoning.py",
        "text[closing.end():]",
        "text[closing.start():]",
        "self.assertEqual(split_reasoning('reason</think>Answer',True)['answer'],'Answer')",
    ),
    (
        "reasoning-private-phase",
        "reasoning.py",
        "text[:closing.start()]",
        "text[:closing.end()]",
        "self.assertEqual(split_reasoning('reason</think>Answer',True)['reasoning'],'reason')",
    ),
    (
        "truncated-reasoning",
        "reasoning.py",
        "reasoning_truncated=closing is None",
        "reasoning_truncated=closing is not None",
        "self.assertTrue(split_reasoning('unfinished',True)['reasoning_truncated'])",
    ),
    (
        "adapter-selection",
        "reasoning.py",
        "== 'qwen'",
        "== 'llama'",
        "self.assertTrue(uses_reasoning({'model_adapter':'qwen','enable_thinking':True}))",
    ),
    (
        "thinking-flag",
        "reasoning.py",
        "get('enable_thinking') is True",
        "get('enable_thinking') is False",
        "self.assertTrue(uses_reasoning({'model_adapter':'qwen','enable_thinking':True}))",
    ),
    (
        "plain-answer-status",
        "reasoning.py",
        "'final_answer' if text.strip() else 'empty'",
        "'empty' if text.strip() else 'final_answer'",
        "self.assertEqual(split_reasoning('Answer',False)['answer_state'],'final_answer')",
    ),
    (
        "external-data-env",
        "model_paths.py",
        "'LOCAL_AI_GPP_DATA_DIR'",
        "'LOCAL_AI_GPP_DATA_WRONG'",
        "import tempfile,os\n        from unittest.mock import patch\n        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'LOCAL_AI_GPP_DATA_DIR':tmp}):\n            self.assertEqual(data_root(Path(tmp)/'app'),Path(tmp).resolve())",
    ),
    (
        "relative-data-root",
        "model_paths.py",
        "Path(project_root) / raw",
        "Path.cwd() / raw",
        "import os\n        from unittest.mock import patch\n        with patch.dict(os.environ,{'LOCAL_AI_GPP_DATA_DIR':'state'}):\n            self.assertEqual(data_root(Path.cwd()/'app'),(Path.cwd()/'app/state').resolve())",
    ),
    (
        "portable-backslashes",
        "model_paths.py",
        ".replace('\\\\', '/')",
        "",
        "from pathlib import PurePosixPath\n        from unittest.mock import patch\n        class PortablePath(PurePosixPath):\n            def is_file(self): return False\n        with patch('candidate.Path',PortablePath):\n            self.assertEqual(model_path('models_storage\\\\weights.gguf',PortablePath('/data/models_storage'),PortablePath('/app')),PortablePath('/data/models_storage/weights.gguf'))",
    ),
    (
        "external-model-prefix",
        "model_paths.py",
        "('models_storage',)",
        "('wrong_storage',)",
        "self.assertEqual(model_path('models_storage/weights.gguf',Path.cwd()/'external/models_storage',Path.cwd()/'app'),Path.cwd()/'external/models_storage/weights.gguf')",
    ),
]


def prepare(root, source_root, case):
    name, module, before, after, assertion = case
    root.mkdir(parents=True, exist_ok=False)
    original = (source_root / "backend/app" / module).read_text(encoding="utf-8")
    if original.count(before) < 1:
        raise ValueError("Benchmark mutation no longer matches source: " + name)
    (root / "candidate.py").write_text(
        original.replace(before, after, 1), encoding="utf-8"
    )
    (root / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    test = (
        "import unittest\nfrom pathlib import Path\nfrom candidate import *\nclass Regression(unittest.TestCase):\n    def test_regression(self):\n        "
        + assertion
        + "\n"
    )
    (root / "test_regression.py").write_text(test, encoding="utf-8", newline="\n")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    for command in (
        ["git", "init", "-q"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Local Acceptance",
            "-c",
            "user.email=acceptance@example.invalid",
            "commit",
            "-qm",
            "Injected regression " + name,
        ],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True, **flags)
    return (
        hashlib.sha256(original.encode()).hexdigest(),
        hashlib.sha256(test.encode()).hexdigest(),
    )


def tests(project):
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-B", "-m", "unittest", "discover", "-v"],
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        **flags,
    )
    return {"exit_code": result.returncode, "output": result.stdout + result.stderr}


def run(args):
    root = Path(args.output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root / "workspace")
    service = TaskService(store)
    source = Path(__file__).resolve().parents[1]
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(args.base_url + "/models")
        response.raise_for_status()
        model = response.json()["data"][0]["id"]
    if not store.rows("SELECT id FROM providers WHERE id='benchmark'"):
        with store.transaction() as db:
            db.execute(
                "INSERT INTO providers VALUES(?,?,?,?,?)",
                ("benchmark", "Benchmark local Runtime", args.base_url, model, ""),
            )
    all_results = []
    try:
        for index, case in enumerate(CASES[: args.limit], 1):
            if index < args.start_case:
                continue
            case_root = root / f"{index:02d}-{case[0]}"
            result_path = case_root / "result.json"
            if result_path.exists():
                all_results.append(json.loads(result_path.read_text(encoding="utf-8")))
                continue
            if case_root.exists():
                # An interrupted observer is not permission to resend a task.
                all_results.append(
                    {
                        "case": case[0],
                        "success": False,
                        "status": "interrupted_observer",
                        "automatic_resend": False,
                    }
                )
                continue
            project = case_root / "project"
            source_sha, test_sha = prepare(project, source, case)
            initial = tests(project)
            if initial["exit_code"] == 0:
                raise AssertionError(
                    "Invalid benchmark: injected regression did not fail " + case[0]
                )
            project_id = identifier()
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO projects VALUES(?,?,?,?)",
                    (project_id, case[0], str(project), "benchmark"),
                )
            prompt = "Repair the failing test in this isolated repository. Run python -B -m unittest discover -v, inspect candidate.py and test_regression.py, fix the regression in candidate.py, and run the tests again. Preserve test_regression.py unchanged. Report the actual test result. Do not access anything outside this project."
            task = service.create(
                project_id,
                "benchmark",
                prompt,
                identifier(),
                "workspace-write",
                case[0],
            )
            started = time.monotonic()
            interventions = 0
            peak_rss = 0
            try:
                service.start(task["id"], task["revision"])
                while time.monotonic() - started < args.timeout:
                    current = store.one("tasks", task["id"])
                    process = service.processes.get(task["id"])
                    if process:
                        total = 0
                        for pid in list(process.known):
                            try:
                                total += psutil.Process(pid).memory_info().rss
                            except psutil.Error:
                                pass
                        peak_rss = max(peak_rss, total)
                    for approval in store.rows(
                        "SELECT id FROM approvals WHERE task_id=? AND status='pending'",
                        (task["id"],),
                    ):
                        interventions += 1
                        # Do not grant unexpected out-of-project privileges in an unattended benchmark.
                        service.decide(approval["id"], {"outcome": "rejected"})
                    if current["status"] not in {
                        "starting",
                        "running",
                        "awaiting_permission",
                        "stopping",
                    }:
                        break
                    time.sleep(0.5)
                else:
                    service.stop(task["id"])
            except Exception as exc:
                store.update_task(task["id"], "failed", {"benchmark_error": str(exc)})
            final = tests(project)
            current = store.one("tasks", task["id"])
            unchanged = (
                hashlib.sha256(
                    (project / "test_regression.py").read_bytes()
                ).hexdigest()
                == test_sha
            )
            events = [
                json.loads(row["payload"])
                for row in store.rows(
                    "SELECT payload FROM events WHERE task_id=? AND kind='dsh'",
                    (task["id"],),
                )
            ]
            diff = service.diff(task["id"])
            success = (
                final["exit_code"] == 0
                and unchanged
                and current["status"] == "completed"
                and any(f["path"] == "candidate.py" for f in diff["files"])
            )
            result = {
                "case": case[0],
                "source_module": "backend/app/" + case[1],
                "source_sha256": source_sha,
                "model": model,
                "task_id": task["id"],
                "success": success,
                "status": current["status"],
                "seconds": round(time.monotonic() - started, 2),
                "permission_interventions": interventions,
                "tool_calls": sum(e["type"] == "tool/call" for e in events),
                "tool_errors": sum(
                    e["type"] == "tool/result"
                    and e.get("data", {}).get("message", {}).get("isError", False)
                    for e in events
                ),
                "adapter_peak_rss_bytes": peak_rss,
                "tests_unchanged": unchanged,
                "before": initial,
                "after": final,
                "diff": diff,
                "details": current["details"],
                "automatic_resend": False,
            }
            result_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            all_results.append(result)
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in (
                            "case",
                            "success",
                            "status",
                            "seconds",
                            "tool_calls",
                            "tool_errors",
                        )
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            summary = {
                "dataset": "20 injected regression repairs from three real Local AI Runtime modules; narrow integration evidence, not a general coding benchmark",
                "model": model,
                "attempted": len(all_results),
                "passed": sum(r.get("success", False) for r in all_results),
                "results": all_results,
            }
            (root / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        service.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8765/v1")
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=360)
    parser.add_argument("--start-case", type=int, default=1)
    run(parser.parse_args())

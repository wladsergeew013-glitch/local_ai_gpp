"""Verify agent results against committed test bytes across LF/CRLF platforms.

Reads original receipts, writes a separate measured summary; never reruns an agent.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path


def verify(roots, output):
    results = {}
    for root in roots:
        for receipt in sorted(Path(root).glob("*/result.json")):
            raw = json.loads(receipt.read_text(encoding="utf-8"))
            if raw.get("not_attempted"):
                continue
            project = receipt.parent / "project"
            if not project.exists():
                continue
            flags = (
                {"creationflags": subprocess.CREATE_NO_WINDOW}
                if os.name == "nt"
                else {}
            )
            baseline = subprocess.run(
                [
                    "git",
                    "-c",
                    "safe.directory=" + str(project),
                    "-C",
                    str(project),
                    "show",
                    "HEAD:test_regression.py",
                ],
                capture_output=True,
                check=True,
                **flags
            ).stdout
            actual = (project / "test_regression.py").read_bytes()
            unchanged = baseline.replace(b"\r\n", b"\n") == actual.replace(
                b"\r\n", b"\n"
            )
            success = (
                raw["after"]["exit_code"] == 0
                and raw["status"] == "completed"
                and unchanged
                and any(f["path"] == "candidate.py" for f in raw["diff"]["files"])
            )
            results[raw["case"]] = {
                "case": raw["case"],
                "success": success,
                "status": raw["status"],
                "tests_unchanged": unchanged,
                **{
                    k: raw.get(k)
                    for k in (
                        "model",
                        "source_module",
                        "source_sha256",
                        "seconds",
                        "permission_interventions",
                        "tool_calls",
                        "tool_errors",
                        "adapter_peak_rss_bytes",
                    )
                },
                "receipt": str(receipt),
                "measurement": "Git baseline compared with normalized platform newlines; original receipts retained",
            }
    report = {
        "dataset": "20 injected regressions from three actual Runtime modules in this repository; not a broad coding benchmark",
        "attempted": len(results),
        "passed": sum(r["success"] for r in results.values()),
        "results": list(results.values()),
        "agent_rerun": False,
    }
    Path(output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "results"}, ensure_ascii=False
        )
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    verify(args.roots, args.output)

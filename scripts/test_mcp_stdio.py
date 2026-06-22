"""Smoke-test the SuperFoodie MCP stdio server.

This script is intentionally small and deterministic enough for coursework evidence:
it starts the MCP server, sends initialize/tools/list/tools/call JSON-RPC messages,
and prints the responses as JSON lines.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "src" / "mcp_server.py"


def send(proc: subprocess.Popen, payload: dict) -> dict:
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("MCP server returned no response")
    return json.loads(line)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(SERVER)],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        responses = [
            send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
            send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "query_diet_safety",
                        "arguments": {"disease": "gout", "food": "seafood"},
                    },
                },
            ),
        ]
        for response in responses:
            print(json.dumps(response, ensure_ascii=False))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())

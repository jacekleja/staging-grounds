#!/usr/bin/env python3
"""Long-lived JSON-RPC worker for dispatch-agent.ts hook invocations.

Reads newline-delimited JSON requests from stdin (one per line) and writes
JSON responses to stdout (one per line, flushed after each response).

Protocol:
  Request:  {"id": <int>, "hook": "<name>", "payload": <object>}
  Response: {"id": <int>, "result": <object>}
         or {"id": <int>, "error": "<message>"}

Supported hook names:
  "delegation-prompt-schema-gate"  — schema validation + path-discipline
  "build-pass-gate"                — advisory build-pass signal check
  "post-stop-verify-runner"        — recipe verification after task completion

Each hook module is loaded once via importlib and cached for the worker's
lifetime. The worker runs until stdin is closed (parent closed the pipe or
exited). Any exception in the dispatch loop is serialized as an error
response so the caller can fail-open — the loop never crashes.
"""
import json
import os
import sys
import importlib.util

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_SCRIPT_DIR)
_HOOKS_DIR = os.path.join(_REPO_ROOT, '.claude', 'hooks')

# Ensure hook siblings are importable (e.g. delegation_prompt_parser, _dispatch_child_guard).
sys.path.insert(0, _HOOKS_DIR)
sys.path.insert(0, _SCRIPT_DIR)


def _load_module(file_path: str, module_name: str):
    """Load a Python module from an absolute file path; return the module object."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {file_path!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# Module cache: each hook is imported at most once per worker lifetime.
_module_cache: dict = {}


def _get_run_gate(hook: str):
    """Return the run_gate() callable for the named hook (cached after first load)."""
    if hook not in _module_cache:
        if hook == 'delegation-prompt-schema-gate':
            path = os.path.join(_HOOKS_DIR, 'delegation-prompt-schema-gate.py')
            mod = _load_module(path, '_caa_schema_gate')
        elif hook == 'build-pass-gate':
            path = os.path.join(_HOOKS_DIR, 'build-pass-gate.py')
            mod = _load_module(path, '_caa_build_pass_gate')
        elif hook == 'post-stop-verify-runner':
            path = os.path.join(_SCRIPT_DIR, 'post-stop-verify-runner.py')
            mod = _load_module(path, '_caa_post_stop_runner')
        else:
            raise ValueError(f"Unknown hook: {hook!r}")
        if not hasattr(mod, 'run_gate'):
            raise AttributeError(f"Hook module for {hook!r} does not expose run_gate()")
        _module_cache[hook] = mod
    return _module_cache[hook].run_gate


def _dispatch(hook: str, payload: dict) -> dict:
    """Invoke run_gate() for the named hook and return its result dict."""
    run_gate = _get_run_gate(hook)
    result = run_gate(payload)
    return result if isinstance(result, dict) else {}


def main():
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request_id = None
        try:
            req = json.loads(line)
            request_id = req.get('id')
            result = _dispatch(req.get('hook', ''), req.get('payload', {}))
            sys.stdout.write(json.dumps({'id': request_id, 'result': result}) + '\n')
        except Exception as e:  # noqa: BLE001 — must not crash the worker loop
            sys.stdout.write(json.dumps({'id': request_id, 'error': str(e)}) + '\n')
        sys.stdout.flush()


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
import argparse
import json
import os
import signal
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _dispatch_completion_lib import _pre_create_wake_stream
except ImportError:
    pass

def find_project_root() -> str:
    import subprocess
    script_path = os.path.join(os.path.dirname(__file__), "..", ".claude", "hooks", "find-project-root.sh")
    try:
        root = subprocess.check_output(["bash", script_path], stderr=subprocess.DEVNULL).decode().strip()
        if root:
            return root
    except Exception:
        pass
    # Fallback to git
    try:
        return subprocess.check_output(["git", "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def _resolve_session_dir(args) -> str:
    if args.session_dir:
        return args.session_dir
    session_id = os.environ.get("CLAUDE_SESSION_ID")
    if not session_id:
        print("Error: CLAUDE_SESSION_ID not in environment and --session-dir not provided.", file=sys.stderr)
        sys.exit(1)
    return os.path.join(find_project_root(), ".agent_context", "sessions", session_id)

def merge_settings():
    root = find_project_root()
    settings_path = os.path.join(root, ".claude", "settings.json")
    try:
        with open(settings_path, "r") as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
        
    monitor_cmd_1 = {
        "command": 'tail -F -n 0 "{session_dir}/dispatch-wake-stream.jsonl" 2>/dev/null | grep --line-buffered -E \'"event":"(dispatch_completion|daemon_stale_respawn)"\'',
        "persistent": True
    }
    monitor_cmd_2 = {
        "command": 'python3 "$(bash .claude/hooks/find-project-root.sh 2>/dev/null || git rev-parse --show-toplevel)/.claude/hooks/dispatch-daemon-watchdog.py" --session-dir "{session_dir}" >/dev/null 2>&1',
        "persistent": True
    }
    
    if "Monitor" not in settings:
        settings["Monitor"] = [{
            "matcher": "",
            "monitors": [monitor_cmd_1, monitor_cmd_2]
        }]
    else:
        monitors = settings["Monitor"][0].setdefault("monitors", [])
        existing_cmds = [m.get("command") for m in monitors]
        if monitor_cmd_1["command"] not in existing_cmds:
            monitors.append(monitor_cmd_1)
        if monitor_cmd_2["command"] not in existing_cmds:
            monitors.append(monitor_cmd_2)

    if "hooks" not in settings:
        settings["hooks"] = {}
        
    if "Stop" not in settings["hooks"]:
        settings["hooks"]["Stop"] = []
    
    stop_cmd = {
        "type": "command",
        "command": 'python3 "$(bash .claude/hooks/find-project-root.sh 2>/dev/null || git rev-parse --show-toplevel)/bin/dispatch-daemon-supervisor.py" --stop',
        "asyncRewake": False,
        "timeout": 5
    }
    
    has_stop = False
    for hook_block in settings["hooks"]["Stop"]:
        for h in hook_block.get("hooks", []):
            cmd = h.get("command", "")
            if "dispatch-daemon-supervisor.py" in cmd and "--stop" in cmd:
                has_stop = True
                break
    if not has_stop:
        settings["hooks"]["Stop"].append({
            "matcher": "",
            "hooks": [stop_cmd]
        })

    if "SessionStart" not in settings["hooks"]:
        settings["hooks"]["SessionStart"] = []
        
    start_cmd = {
        "type": "command",
        "command": 'CAA_ORCHESTRATOR_PID="$PPID" python3 "$(bash .claude/hooks/find-project-root.sh 2>/dev/null || git rev-parse --show-toplevel)/bin/dispatch-daemon-supervisor.py" --bootstrap'
    }
    
    has_start = False
    for hook_block in settings["hooks"]["SessionStart"]:
        for h in hook_block.get("hooks", []):
            cmd = h.get("command", "")
            if "dispatch-daemon-supervisor.py" in cmd and "--bootstrap" in cmd and "CAA_ORCHESTRATOR_PID" in cmd:
                has_start = True
                break
    if not has_start:
        settings["hooks"]["SessionStart"].append({
            "matcher": "",
            "hooks": [start_cmd]
        })

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

def do_bootstrap(session_dir):
    # R6/R7 ordering invariant: wake stream must exist before tail -F can attach, and before daemon spawn can emit the first wake line.
    # _pre_create_wake_stream MUST be the FIRST on-disk side effect; settings registration is moved to the END of bootstrap
    # because it is idempotent and not part of the load-bearing ordering chain (D-SUP-2 violation fixed in V-R2).
    _pre_create_wake_stream(session_dir)
    
    env_pid = os.environ.get("CAA_ORCHESTRATOR_PID")
    if not env_pid or not env_pid.isdigit() or int(env_pid) <= 0:
        print("Error: CAA_ORCHESTRATOR_PID missing or invalid", file=sys.stderr)
        sys.exit(1)
        
    pid = int(env_pid)
    pid_path = os.path.join(session_dir, "orchestrator.pid")
    fd = os.open(pid_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
    os.write(fd, f"{pid}\n".encode())
    os.close(fd)
    
    daemon_path = os.path.join(find_project_root(), "bin", "dispatch-completion-daemon.py")
    env = os.environ.copy()
    env["CAA_SESSION_DIR"] = session_dir
    subprocess.Popen(["python3", daemon_path], env=env, start_new_session=True, close_fds=True)
    
    # Settings registration runs LAST: idempotent, not part of the R6/R7 bootstrap ordering invariant.
    # Moved here from start-of-function to satisfy D-SUP-2 (wake-stream MUST be the first on-disk side effect). V-R2 fix.
    merge_settings()

def do_stop(session_dir):
    pid_path = os.path.join(session_dir, "dispatch-completion-daemon.pid")
    if not os.path.exists(pid_path):
        sys.exit(0)
    try:
        with open(pid_path, "r") as f:
            content = f.read().strip()
            if not content:
                sys.exit(0)
            pid = int(content)
            if pid <= 0:
                sys.exit(0)
    except Exception:
        sys.exit(0)
        
    try:
        os.kill(pid, 0)
    except OSError:
        sys.exit(0)
        
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    sys.exit(0)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--session-dir")
    args = parser.parse_args()
    
    session_dir = _resolve_session_dir(args)
    if args.bootstrap:
        do_bootstrap(session_dir)
    elif args.stop:
        do_stop(session_dir)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Opt-in real Codex restart test; uses only a disposable offline profile."""
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
import time

from codex_profiles import REX, ROOT, wait_for


class OfflineEndpoint(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        payload = b'{"error":{"message":"offline restore fixture","type":"invalid_request_error"}}'
        self.send_response(400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def main():
    codex = shutil.which("codex")
    assert codex, "Codex CLI must be installed"
    endpoint = http.server.ThreadingHTTPServer(("127.0.0.1", 0), OfflineEndpoint)
    threading.Thread(target=endpoint.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="rex-real-codex-") as tmp:
        root = Path(tmp).resolve()
        profile, state, project = [root / s for s in ("profile", "durable", "project")]
        for p in (profile, state, project, root / "rex-state"):
            p.mkdir(mode=0o700)
        (profile / "config.toml").write_text(f'''model = "gpt-6.1-sol"
model_provider = "offline_test"
approval_policy = "never"
sandbox_mode = "read-only"
[features]
daemon_auto_start = true
[model_providers.offline_test]
name = "Offline restore fixture"
base_url = "http://127.0.0.1:{endpoint.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
[tui]
terminal_title = ["thread-title", "thread-id", "project"]
screen_reader_detection_done = true
[projects.{json.dumps(str(project))}]
trust_level = "trusted"
''')
        # No auth files, work config, or work conversations are copied.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("REX_", "CODEX_", "OPENAI_"))}
        env["CODEX_HOME"] = str(profile)
        ids = []
        server = None
        log = (root / "rex.log").open("w+")
        socket = "unix://" + str(root / "rex.sock")
        init = root / "init.lua"
        init.write_text("")

        def rex(*args):
            r = subprocess.run([REX, "--autostart=false", "-S", socket, *args], env=env,
                               text=True, capture_output=True, timeout=60)
            if r.returncode:
                raise AssertionError(r.stderr or r.stdout)
            return r.stdout

        def start():
            nonlocal server
            server = subprocess.Popen([REX, "server", "run", "--listen", socket,
                                       "--state-dir", str(root / "rex-state"), "--config", str(init)],
                                      env=env, stdout=log, stderr=log)
            def ready():
                try:
                    rex("ls")
                    return True
                except AssertionError:
                    return False
            wait_for(ready, "isolated server")

        def stop():
            nonlocal server
            if server is not None:
                server.terminate()
                server.wait(timeout=15)
                server = None

        def durable(mode):
            return rex("do", str(ROOT / "durable.lua"), "mode=" + mode,
                       "state_dir=" + str(state), "notify=false")

        def saved():
            durable("save")
            p = max((state / "snapshots").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
            snap = json.loads(p.read_text())
            return [w["layout"]["pane"] for s in snap["sessions"]
                    if s["label"] == "offline-codex-test" for w in s["windows"]]

        def process(block):
            return json.loads(rex("block", "call", "-s", sid, "-b", block, "process"))["foreground"]

        def daemon_pid():
            data = json.loads((profile / "app-server-daemon/daemon.pid").read_text())
            return data["pid"] if isinstance(data, dict) else int(data)

        def stop_daemon():
            pidfile = profile / "app-server-daemon/daemon.pid"
            if not pidfile.exists():
                return
            pid = daemon_pid()
            opened = subprocess.run(["lsof", "-nP", "-w", "-Fn", "-p", str(pid)],
                                    text=True, capture_output=True).stdout
            if str(profile) + "/" not in opened:
                return
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            def stopped():
                r = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], text=True, capture_output=True)
                return not r.stdout.strip() or r.stdout.strip().startswith("Z")
            wait_for(stopped, "disposable Codex daemon shutdown")

        try:
            for n in range(2):
                r = subprocess.run([codex, "exec", "--json", "--skip-git-repo-check", "--cd", str(project),
                                    f"Offline restore fixture {n}. No action requested."],
                                   env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
                events = [json.loads(line) for line in r.stdout.splitlines() if line.startswith("{")]
                ids.append(next(e["thread_id"] for e in events if e.get("type") == "thread.started"))
                assert "offline restore fixture" in r.stdout + r.stderr, r.stdout + r.stderr
            print("PASS: two real Codex conversations persisted using only the local offline endpoint", flush=True)
            launcher = root / "work-launcher"
            launcher.write_text("#!/bin/sh\nexport CODEX_HOME=" + shlex.quote(str(profile))
                                + "\nexec " + shlex.quote(codex) + ' "$@"\n')
            launcher.chmod(0o700)
            (state / "config.json").write_text(json.dumps({"programs": False, "codex_profiles": [
                {"home": str(profile), "launcher": str(launcher)}]}))
            start()
            windows = [{"layout": {"block": {"flavor": "com.superlogical.terminal.shell",
                       "options": {"cwd": str(project), "initial_input":
                                   shlex.join([str(launcher), "resume", tid]) + "\n"}}}} for tid in ids]
            created = json.loads(rex("api", "call", "session.create", json.dumps(
                {"label": "offline-codex-test", "initial_windows": windows})))
            sid = created["session_id"]
            blocks = [w["block_ids"][0] for w in created["initial_windows"]]
            wait_for(lambda: [p.get("id") for p in saved()] == ids, "actual Codex titles", seconds=30)
            initial_daemon = daemon_pid()
            os.kill(initial_daemon, 0)
            before = [process(b)["pid"] for b in blocks]
            durable("startup")
            stop()
            os.kill(initial_daemon, 0)
            start()
            result = durable("startup")
            assert "reopened 2 chats" in result, result
            wait_for(lambda: [p.get("id") for p in saved()] == ids, "actual resumed titles", seconds=30)
            after = [process(b)["pid"] for b in blocks]
            assert all(a != b for a, b in zip(before, after))
            assert daemon_pid() == initial_daemon
            os.kill(initial_daemon, 0)
            print("PASS: real Codex TUIs resumed both exact chats after Rex restart; shared daemon stayed running", flush=True)
            durable("startup")
            assert [process(b)["pid"] for b in blocks] == after
            print("PASS: repeated startup left the same real Codex clients running", flush=True)
            stop()
            stop_daemon()
            start()
            result = durable("startup")
            assert "reopened 2 chats" in result, result
            wait_for(lambda: [p.get("id") for p in saved()] == ids, "cold-daemon restored titles", seconds=30)
            assert daemon_pid() != initial_daemon
            print("PASS: both chats also restored after stopping Rex and the isolated Codex daemon", flush=True)
        except Exception:
            if server is not None and "blocks" in locals():
                for b in blocks:
                    print("title", rex("block", "call", "-s", sid, "-b", b, "title"), flush=True)
                    print(rex("capture", "-s", sid, "-b", b, "--trim")[-5000:], flush=True)
                print("snapshot", saved(), flush=True)
            raise
        finally:
            stop()
            # Only the dedicated test profile's daemon is eligible for cleanup.
            stop_daemon()
            endpoint.shutdown()
            endpoint.server_close()
            log.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Restart an isolated Rex server with simulated shared-daemon Codex TUIs."""
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parent.parent
REX = os.environ.get("REX") or shutil.which("rex")
assert REX, "set REX to the Rex executable"
IDS = [f"{n:08x}-2222-4333-8444-555555555555" for n in range(1, 5)]


def wait_for(check, label, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError("timed out: " + label)


def main():
    with tempfile.TemporaryDirectory(prefix="rex-profile-test-") as tmp:
        root = Path(tmp).resolve()
        profile, state, project = [root / p for p in ("work profile", "durable", "project")]
        for p in (profile, state, project, root / "rex-state"):
            p.mkdir()
        init = root / "init.lua"
        init.write_text("")
        exe = root / "codex"
        subprocess.run(["cc", "-O0", "-o", str(exe), '-DAPP="codex"',
                        "-DFAKE_HOME=" + json.dumps(tmp), str(ROOT / "test/fake.c")], check=True)
        launcher = root / "work launcher"
        launcher.write_text("#!/bin/sh\nexport CODEX_HOME=" + shlex.quote(str(profile))
                            + "\nexport REX_DURABLE_FAKE_TITLES=1\nexec "
                            + shlex.quote(str(exe)) + ' "$@"\n')
        launcher.chmod(0o700)
        config = {"programs": False, "codex_profiles": [{"home": str(profile), "launcher": str(launcher)}]}
        (state / "config.json").write_text(json.dumps(config))
        db = sqlite3.connect(profile / "state_5.sqlite")
        db.execute("CREATE TABLE threads(id TEXT PRIMARY KEY, cwd TEXT, rollout_path TEXT, archived INTEGER)")
        rollouts = []
        for tid in IDS:
            path = profile / "sessions" / ("rollout-" + tid + ".jsonl")
            path.parent.mkdir(exist_ok=True)
            path.write_text("fixture only\n")
            db.execute("INSERT INTO threads VALUES(?,?,?,0)", (tid, str(project), str(path)))
            rollouts.append(path)
        db.commit()
        db.close()
        # The test process holds every rollout across the Rex restart, as a
        # persistent Codex daemon does even after a terminal disconnects.
        holders = [p.open() for p in rollouts]
        env = {k: v for k, v in os.environ.items() if not k.startswith("REX_")}
        endpoint = "unix://" + str(root / "rex.sock")
        server = None
        server_log = (root / "server.log").open("w+")

        def rex(*args, check=True):
            r = subprocess.run([REX, "--autostart=false", "-S", endpoint, *args],
                               env=env, capture_output=True, text=True, timeout=60)
            if check and r.returncode:
                raise AssertionError(r.stderr or r.stdout)
            return r

        def start():
            nonlocal server
            server = subprocess.Popen([REX, "server", "run", "--listen", endpoint,
                                       "--state-dir", str(root / "rex-state"), "--config", str(init)],
                                      env=env, stdout=server_log, stderr=server_log)
            wait_for(lambda: rex("ls", check=False).returncode == 0, "test server")

        def stop():
            nonlocal server
            if server is not None:
                server.terminate()
                server.wait(timeout=15)
                server = None

        def durable(mode, *extra, check=True):
            return rex("do", str(ROOT / "durable.lua"), "mode=" + mode,
                       "state_dir=" + str(state), "notify=false", *extra, check=check)

        def snapshot():
            durable("save")
            path = max((state / "snapshots").glob("*.json"), key=lambda p: p.stat().st_mtime_ns)
            data = json.loads(path.read_text())
            sessions = [s for s in data["sessions"] if s["label"] == "fixtures"]
            return [w["layout"]["pane"] for w in sessions[0]["windows"]]

        def call(block, method, args=None):
            words = ["block", "call", "-s", sid, "-b", block, method]
            if args is not None:
                words.append(json.dumps(args))
            return json.loads(rex(*words).stdout)

        try:
            start()
            windows = []
            for i, tid in enumerate(IDS[:3]):
                command = ["env", "CODEX_HOME=" + str(profile), "REX_DURABLE_FAKE_TITLES=1",
                           "REX_DURABLE_FAKE_ID=" + tid, str(exe)]
                if i == 1:
                    command += ["resume", tid]
                windows.append({"layout": {"block": {"flavor": "com.superlogical.terminal.shell",
                                "options": {"cwd": str(project), "initial_input": shlex.join(command) + "\n"}}}})
            created = json.loads(rex("api", "call", "session.create",
                                     json.dumps({"label": "fixtures", "initial_windows": windows})).stdout)
            sid = created["session_id"]
            blocks = [w["block_ids"][0] for w in created["initial_windows"]]
            panes = wait_for(lambda: (p if [x.get("id") for x in p] == IDS[:3] else None)
                            if (p := snapshot()) else None, "three exact chat identities")
            assert all(p["codex_home"] == str(profile) for p in panes)
            print("PASS: three chats in one folder identified through their custom profile", flush=True)

            call(blocks[1], "write", {"data": "title " + IDS[3][:26] + "...\n"})
            wait_for(lambda: snapshot()[1].get("id") == IDS[3], "chat switch")
            call(blocks[2], "write", {"data": "title unavailable\n"})
            wait_for(lambda: snapshot()[2]["kind"] == "unknown", "unknown identity")
            print("PASS: title follows chat switch; missing identity does not reuse launch ID", flush=True)
            durable("startup")
            before = [call(b, "process")["foreground"]["pid"] for b in blocks]
            stop()
            start()
            restored = durable("startup").stdout
            assert "reopened 2 chats" in restored, restored
            panes = wait_for(lambda: (p if [x.get("id") for x in p[:2]] == [IDS[0], IDS[3]] else None)
                            if (p := snapshot()) else None, "resumed identities")
            assert panes[2]["kind"] == "shell", panes[2]
            after = [call(b, "process")["foreground"]["pid"] for b in blocks]
            assert all(a != b for a, b in zip(before, after))
            assert all(p["codex_launcher"] == str(launcher) for p in panes[:2])
            print("PASS: restart restored both exact chats with work launcher while daemon held rollouts", flush=True)
            durable("startup")
            assert [call(b, "process")["foreground"]["pid"] for b in blocks] == after
            print("PASS: repeated startup does not open duplicates; unknown pane remains idle", flush=True)
            assert state.stat().st_mode & 0o077 == 0
            assert all(p.stat().st_mode & 0o077 == 0 for p in (state / "snapshots").glob("*.json"))
            print("PASS: saved state is private to its owner", flush=True)
        except Exception:
            if server is not None:
                for b in blocks:
                    print("fixture process", call(b, "process"), flush=True)
                    print("fixture title", call(b, "title"), flush=True)
                print("fixture snapshot", snapshot(), flush=True)
            raise
        finally:
            stop()
            for f in holders:
                f.close()
            server_log.close()


if __name__ == "__main__":
    main()

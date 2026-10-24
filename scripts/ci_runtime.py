"""Streaming subprocess logs and incremental, fail-closed CI evidence."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time


class EvidenceRunner:
    def __init__(self, directory, metadata):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.results = dict(metadata, success=False, checks=[])
        self.checkpoint()

    def checkpoint(self):
        temporary = self.directory / "results.tmp"
        temporary.write_text(json.dumps(self.results, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.directory / "results.json")

    def execute(self, name, args, cwd, env, timeout=600):
        started = time.monotonic()
        check = dict(name=name, command=args, state="running",
                     started_at=datetime.now(timezone.utc).isoformat())
        self.results["checks"].append(check)
        self.checkpoint()
        print(f"START {name}", flush=True)
        code, chunks, process = 1, [], None
        try:
            with (self.directory / (name + ".log")).open("w", encoding="utf-8") as log:
                process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                           errors="replace", start_new_session=os.name != "nt")
                lines = queue.Queue()

                def read():
                    try:
                        for line in process.stdout:
                            lines.put(line)
                    finally:
                        lines.put(None)

                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                while True:
                    if time.monotonic() - started > timeout:
                        raise subprocess.TimeoutExpired(args, timeout)
                    try:
                        line = lines.get(timeout=.2)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    log.write(line)
                    log.flush()
                    chunks.append(line)
                    print(line, end="", flush=True)
                code = process.wait(timeout=max(.1, timeout - (time.monotonic() - started)))
                reader.join(1)
        except (OSError, subprocess.SubprocessError) as error:
            message = str(error) + "\n"
            chunks.append(message)
            with (self.directory / (name + ".log")).open("a", encoding="utf-8") as log:
                log.write(message)
            print(message, end="", flush=True)
        finally:
            if process is not None:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait()
                process.stdout.close()
            check.update(state="passed" if code == 0 else "failed", exit_code=code,
                         duration_seconds=round(time.monotonic() - started, 3))
            self.checkpoint()
        print(f"END {name}: {check['state']} ({check['duration_seconds']}s)", flush=True)
        if code:
            raise RuntimeError(f"{name} failed; evidence: {self.directory}")
        return "".join(chunks)

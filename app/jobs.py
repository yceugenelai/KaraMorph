import json
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal


class JobManager(QObject):
    event = Signal(dict)
    finished = Signal(dict, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue = []
        self.current = None
        self.process = None
        self.buffer = ""
        self.last_result = None
        self.last_error = None

    def enqueue(self, kind: str, python: Path, worker: Path, args: list[str], env: dict[str, str], song_id: str | None = None, metadata: dict | None = None) -> str:
        job = {"id": uuid.uuid4().hex, "kind": kind, "python": str(python), "worker": str(worker), "args": args, "env": env, "song_id": song_id, "metadata": metadata or {}}
        self.queue.append(job)
        self.event.emit({"type": "queued", "job_id": job["id"], "message": kind})
        self._start_next()
        return job["id"]

    def _start_next(self):
        if self.current or not self.queue:
            return
        self.current = self.queue.pop(0)
        self.last_result = None
        self.last_error = None
        self.buffer = ""
        self.process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        for key, value in self.current["env"].items():
            environment.insert(key, value)
        self.process.setProcessEnvironment(environment)
        self.process.setProgram(self.current["python"])
        self.process.setArguments([self.current["worker"], *self.current["args"]])
        self.process.readyReadStandardOutput.connect(self._stdout)
        self.process.readyReadStandardError.connect(self._stderr)
        self.process.finished.connect(self._done)
        self.process.errorOccurred.connect(self._process_error)
        self.event.emit({"type": "running", "job_id": self.current["id"], "message": self.current["kind"]})
        self.process.start()

    def _stdout(self):
        self.buffer += bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            item["job_id"] = self.current["id"]
            if item.get("type") == "result":
                self.last_result = item
            elif item.get("type") == "error":
                self.last_error = item.get("message")
            self.event.emit(item)

    def _stderr(self):
        data = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace")
        log = Path(self.current["env"]["AIK_LOG_DIR"]) / f"{self.current['id']}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(data)

    def _process_error(self, error):
        self.last_error = self.process.errorString()
        self.event.emit({"type": "error", "message": self.last_error})
        if error == QProcess.ProcessError.FailedToStart:
            job = self.current
            self.finished.emit({**job, "result": None, "error": self.last_error,
                                "log_path": str(Path(job["env"]["AIK_LOG_DIR"]) / f"{job['id']}.log")}, False)
            self.process.deleteLater()
            self.process = None
            self.current = None
            self._start_next()

    def _done(self, code, status):
        job = self.current
        self._stdout()
        self._stderr()
        ok = (not job.get("cancelled") and code == 0 and self.last_result is not None
              and self.last_result.get("ok") is True)
        if job.get("cancelled"):
            self.event.emit({"type": "cancelled", "job_id": job["id"], "message": f"{job['kind']} cancelled"})
        elif not ok:
            self.event.emit({"type": "error", "job_id": job["id"], "message": f"{job['kind']} failed (exit {code}); see job log"})
        self.finished.emit({**job, "result": self.last_result, "error": self.last_error,
                            "exit_code": code, "log_path": str(Path(job["env"]["AIK_LOG_DIR"]) / f"{job['id']}.log")}, ok)
        self.process.deleteLater()
        self.process = None
        self.current = None
        self._start_next()

    def cancel(self):
        if self.process:
            self.current["cancelled"] = True
            pid = self.process.processId()
            if sys.platform == "win32" and pid:
                target = self.process
                killer = QProcess(self)
                killer.setProgram("taskkill")
                killer.setArguments(["/PID", str(pid), "/T", "/F"])
                killer.finished.connect(lambda code, _, target=target: target.kill() if code and target.state() != QProcess.ProcessState.NotRunning else None)
                killer.finished.connect(killer.deleteLater)
                killer.start()
            else:
                self.process.kill()
        else:
            self.queue.clear()

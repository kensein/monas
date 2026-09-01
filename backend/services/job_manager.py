"""Background processing status for large NC files."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class Job:
    id: str
    type: str
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def create_job(job_type: str) -> Job:
    job = Job(id=str(uuid.uuid4())[:8], type=job_type)
    with _lock:
        _jobs[job.id] = job
    return job


def update_job(job_id: str, **kwargs) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)


def get_job(job_id: str) -> Job | None:
    return _jobs.get(job_id)


def run_in_background(job_id: str, func, *args, **kwargs) -> None:
    def wrapper():
        update_job(job_id, status=JobStatus.RUNNING, message="Memproses...")
        try:
            result = func(*args, progress_cb=lambda p, m: update_job(job_id, progress=p, message=m), **kwargs)
            update_job(job_id, status=JobStatus.DONE, progress=100, result=result or {}, message="Selesai")
        except Exception as e:
            update_job(job_id, status=JobStatus.ERROR, message=str(e))

    threading.Thread(target=wrapper, daemon=True).start()

"""Background scheduler for auto pipeline on litbangweb."""
from __future__ import annotations

import os
import threading
import time

from backend.config import PIPELINE_INTERVAL_SEC
from backend.services.obs_fetcher import sync_observations_recent
from backend.services.pipeline import init_pipeline_db, run_full_pipeline


_scheduler_thread: threading.Thread | None = None
_running = False
_obs_sync_enabled = os.getenv("AUTO_SYNC_OBS", "true").lower() in ("1", "true", "yes")


def _scheduler_loop() -> None:
    global _running
    while _running:
        try:
            if _obs_sync_enabled:
                sync_observations_recent(days=10)
            run_full_pipeline()
        except Exception as e:
            print(f"[pipeline] error: {e}")
        time.sleep(PIPELINE_INTERVAL_SEC)


def start_scheduler() -> None:
    global _scheduler_thread, _running
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    init_pipeline_db()
    _running = True
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()
    print(f"[pipeline] scheduler started (interval={PIPELINE_INTERVAL_SEC}s)")


def stop_scheduler() -> None:
    global _running
    _running = False

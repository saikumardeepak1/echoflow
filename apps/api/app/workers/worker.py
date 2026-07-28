"""RQ worker entrypoint.

Runs the ``default`` queue (currently just ``notify_appointment``, see
app/workers/jobs.py) on the main thread via RQ's blocking ``Worker.work()``,
and the reminder sweep (app/workers/scheduler.py) on a background thread
alongside it, so both "process notification jobs" and "periodically enqueue
reminder jobs" run for as long as this process is up, per docs/TDD.md
section 3.5.
"""

import threading
import uuid

from redis import Redis
from rq import Queue, Worker
from rq.job import Job

from app.core.config import settings
from app.core.logging import configure_logging, correlation_id_var, set_correlation_id
from app.workers.scheduler import run_reminder_sweep_loop


class CorrelationIdWorker(Worker):
    """RQ worker that tags every job it runs with a ``job-<hex>`` correlation
    id (see app/core/logging.py and docs/TDD.md section 8).

    Wraps ``perform_job`` rather than setting the id inside each job
    function, so any job this worker executes, including
    ``notify_appointment`` (see app/workers/jobs.py), gets a correlation id
    on every log line it emits for free, the worker-side equivalent of
    ``CorrelationIdMiddleware`` on the API side. Resets the contextvar once
    the job finishes, whether it succeeded or raised, so the id never leaks
    into whichever job this worker picks up next.
    """

    def perform_job(self, job: Job, queue: Queue) -> bool:
        token = set_correlation_id(f"job-{uuid.uuid4().hex}")
        try:
            return super().perform_job(job, queue)
        finally:
            correlation_id_var.reset(token)


def main() -> None:
    # Configured before the worker starts pulling jobs so every log line this
    # process emits, RQ's own included, is JSON (see app/core/logging.py and
    # docs/TDD.md section 8).
    configure_logging()

    connection = Redis.from_url(settings.redis_url)
    queue = Queue("default", connection=connection)

    # Runs the reminder sweep on its own thread since Worker.work() below
    # blocks the main thread for as long as the process is up. Daemonized so
    # it never keeps the process alive on its own; stop_event is still set
    # in `finally` for a clean shutdown of the thread itself when work()
    # returns (SIGINT/SIGTERM).
    stop_sweep = threading.Event()
    sweep_thread = threading.Thread(
        target=run_reminder_sweep_loop,
        args=(stop_sweep,),
        name="reminder-sweep",
        daemon=True,
    )
    sweep_thread.start()

    worker = CorrelationIdWorker([queue], connection=connection)
    try:
        worker.work()
    finally:
        stop_sweep.set()


if __name__ == "__main__":
    main()

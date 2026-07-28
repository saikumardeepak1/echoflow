"""RQ worker entrypoint.

No jobs are registered yet, that lands with the outbound notification
worker (see docs/ROADMAP.md, Milestone 3). This process just needs to boot
and stay connected to Redis so the worker container in docker-compose comes
up cleanly ahead of that work.
"""

import uuid

from redis import Redis
from rq import Queue, Worker
from rq.job import Job

from app.core.config import settings
from app.core.logging import configure_logging, correlation_id_var, set_correlation_id


class CorrelationIdWorker(Worker):
    """RQ worker that tags every job it runs with a ``job-<hex>`` correlation
    id (see app/core/logging.py and docs/TDD.md section 8).

    Wraps ``perform_job`` rather than setting the id inside each job
    function, so any job this worker executes, including the outbound
    notification job planned for a later milestone, gets a correlation id
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
    worker = CorrelationIdWorker([queue], connection=connection)
    worker.work()


if __name__ == "__main__":
    main()

"""The reminder sweep's interval loop.

This project has no dedicated Redis-backed job scheduler dependency yet (see
docs/TDD.md section 3.5), so the periodic reminder sweep is a plain loop on
its own thread rather than something like ``rq-scheduler``: call
``enqueue_due_reminders`` (see app/workers/jobs.py), wait, repeat, until told
to stop. Split out of ``app/workers/worker.py`` so it is a small, pure
function this test suite can exercise directly (with a mocked
``enqueue_due_reminders`` and a ``stop_event`` set after the first
iteration) rather than only reachable via ``Worker.work()``'s blocking loop.
"""

import logging
import threading

from app.core.logging import correlation_id_var, set_correlation_id
from app.workers.jobs import enqueue_due_reminders

logger = logging.getLogger(__name__)

# How often the sweep re-runs once the worker is up. A few minutes, per
# docs/TDD.md section 3.5 ("checked on worker startup and every few
# minutes"); well under REMINDER_LEAD_TIME (see app/workers/jobs.py) so a
# newly-due appointment is never more than a few minutes late getting its
# reminder enqueued.
DEFAULT_SWEEP_INTERVAL_SECONDS = 300.0


def run_reminder_sweep_loop(
    stop_event: threading.Event,
    interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS,
) -> None:
    """Run ``enqueue_due_reminders`` immediately, then again every
    ``interval_seconds`` until ``stop_event`` is set.

    Runs the sweep before the first wait so "on worker startup" (the first
    half of docs/TDD.md section 3.5's requirement) holds regardless of
    ``interval_seconds``. Exceptions from a single sweep are logged and
    swallowed rather than left to kill the thread, since one failed sweep
    (e.g. a transient database blip) shouldn't stop every later one from
    running.
    """
    while True:
        token = set_correlation_id(f"reminder-sweep-{threading.get_ident():x}")
        try:
            enqueued = enqueue_due_reminders()
            if enqueued:
                logger.info(
                    "Reminder sweep enqueued jobs",
                    extra={"enqueued_count": enqueued},
                )
        except Exception:
            logger.exception("Reminder sweep failed")
        finally:
            correlation_id_var.reset(token)

        if stop_event.wait(interval_seconds):
            return

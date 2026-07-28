"""Tests for app.workers.scheduler.run_reminder_sweep_loop.

No real worker process or Redis needed here: enqueue_due_reminders (see
app/workers/jobs.py, tested separately in tests/test_jobs.py) is mocked out
so these only exercise the loop's own behavior -- run once immediately, wait
between iterations, stop when told to, and keep going if a single sweep
raises.
"""

import threading
from unittest.mock import patch

from app.workers.scheduler import run_reminder_sweep_loop


def test_run_reminder_sweep_loop_runs_before_first_wait_then_stops() -> None:
    """The loop must run enqueue_due_reminders once immediately (the "on
    worker startup" half of docs/TDD.md section 3.5's requirement) rather
    than only after the first interval elapses.
    """
    stop_event = threading.Event()
    calls: list[int] = []

    def fake_enqueue_due_reminders() -> int:
        calls.append(1)
        stop_event.set()
        return 0

    with patch(
        "app.workers.scheduler.enqueue_due_reminders", side_effect=fake_enqueue_due_reminders
    ):
        run_reminder_sweep_loop(stop_event, interval_seconds=999)

    assert len(calls) == 1


def test_run_reminder_sweep_loop_repeats_on_interval() -> None:
    stop_event = threading.Event()
    calls: list[int] = []

    def fake_enqueue_due_reminders() -> int:
        calls.append(1)
        if len(calls) >= 3:
            stop_event.set()
        return 0

    with patch(
        "app.workers.scheduler.enqueue_due_reminders", side_effect=fake_enqueue_due_reminders
    ):
        run_reminder_sweep_loop(stop_event, interval_seconds=0.01)

    assert len(calls) == 3


def test_run_reminder_sweep_loop_survives_a_failed_sweep() -> None:
    """A single sweep raising (e.g. a transient database error) must not
    kill the loop; the next iteration should still run.
    """
    stop_event = threading.Event()
    calls: list[int] = []

    def fake_enqueue_due_reminders() -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient database error")
        stop_event.set()
        return 0

    with patch(
        "app.workers.scheduler.enqueue_due_reminders", side_effect=fake_enqueue_due_reminders
    ):
        run_reminder_sweep_loop(stop_event, interval_seconds=0.01)

    assert len(calls) == 2


def test_run_reminder_sweep_loop_stops_promptly_without_running_again() -> None:
    """If stop_event is already set before the loop is entered, it should
    still run the sweep exactly once (startup guarantee) and then return
    without waiting out a long interval.
    """
    stop_event = threading.Event()
    stop_event.set()
    calls: list[int] = []

    def fake_enqueue_due_reminders() -> int:
        calls.append(1)
        return 0

    with patch(
        "app.workers.scheduler.enqueue_due_reminders", side_effect=fake_enqueue_due_reminders
    ):
        run_reminder_sweep_loop(stop_event, interval_seconds=999)

    assert len(calls) == 1

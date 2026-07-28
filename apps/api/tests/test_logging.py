"""Tests for app.core.logging / app.core.middleware: JSON log formatting and
correlation-id propagation via contextvars, for both the API process and the
RQ worker (see docs/TDD.md section 8, issue #21).
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from redis import Redis
from rq import Queue, Worker

from app.core.config import settings
from app.core.logging import (
    JSONFormatter,
    configure_logging,
    correlation_id_var,
    set_correlation_id,
)
from app.main import app
from app.workers.worker import CorrelationIdWorker

_REQUEST_ID_PATTERN = re.compile(r"^req-[0-9a-f]{32}$")
_JOB_ID_PATTERN = re.compile(r"^job-[0-9a-f]{32}$")


@pytest.fixture(autouse=True)
def _reset_correlation_id_after_test() -> Iterator[None]:
    """Safety net: guarantees the contextvar is unset by the time the next
    test runs even if a test fails before its own cleanup executes.
    """
    yield
    correlation_id_var.set(None)


# --- configure_logging / JSONFormatter --------------------------------------


def test_configure_logging_emits_one_json_line_per_record_with_expected_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    logging.getLogger("tests.logging.plain").info("hello world")

    assert len(caplog.records) == 1
    line = JSONFormatter().format(caplog.records[0])
    payload = json.loads(line)  # raises ValueError if this isn't valid JSON

    assert payload["level"] == "INFO"
    assert payload["logger"] == "tests.logging.plain"
    assert payload["message"] == "hello world"
    assert "timestamp" in payload
    # No correlation id was set for this record, so the field is omitted
    # entirely rather than present-but-null.
    assert "correlation_id" not in payload


def test_configure_logging_includes_correlation_id_on_the_record_when_set(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    token = set_correlation_id("req-test-manual-id")
    try:
        logging.getLogger("tests.logging.plain").warning("with id")
    finally:
        correlation_id_var.reset(token)

    payload = json.loads(JSONFormatter().format(caplog.records[0]))
    assert payload["level"] == "WARNING"
    assert payload["correlation_id"] == "req-test-manual-id"


def test_configure_logging_includes_extra_fields_passed_by_the_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    logging.getLogger("tests.logging.plain").info("with extra", extra={"document_id": "abc123"})

    payload = json.loads(JSONFormatter().format(caplog.records[0]))
    assert payload["document_id"] == "abc123"


def test_configure_logging_is_idempotent_and_does_not_duplicate_handlers() -> None:
    configure_logging()
    configure_logging()
    root_logger = logging.getLogger()
    assert len(root_logger.handlers) == 1


# --- CorrelationIdMiddleware -------------------------------------------------


async def test_middleware_generates_a_correlation_id_when_none_is_sent() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert _REQUEST_ID_PATTERN.match(response.headers["x-request-id"])


async def test_middleware_reuses_and_echoes_an_inbound_correlation_id() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": "caller-supplied-id-123"})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "caller-supplied-id-123"


async def test_request_correlation_id_appears_on_every_log_line_emitted_during_handling(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A single real request through the app, asserting every log line it
    produces (here, just app.main's own "health check requested" line, but
    the assertion holds regardless of how many routes and services a
    request passes through, see app/core/middleware.py) carries the same
    correlation id the middleware generated for that request.
    """
    caplog.set_level(logging.INFO)
    known_id = f"req-{uuid.uuid4().hex}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": known_id})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == known_id

    request_records = [record for record in caplog.records if record.name == "app.main"]
    assert request_records, "expected at least one log line from the health handler"
    for record in request_records:
        assert record.correlation_id == known_id


# --- CorrelationIdWorker ------------------------------------------------------


def test_correlation_id_worker_tags_job_execution_and_resets_after(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """perform_job is wrapped rather than any specific job function, so this
    stubs out the parent class's perform_job (real job execution, tested by
    RQ itself) to assert only what CorrelationIdWorker adds: a job-<hex> id
    on correlation_id_var, visible to any logging the job body does, for the
    duration of the call and cleared once it returns.
    """
    caplog.set_level(logging.INFO)
    seen_correlation_ids: list[str | None] = []

    def fake_perform_job(self: Worker, job: object, queue: object) -> bool:
        logging.getLogger("tests.workers.fake_job").info("job body ran")
        seen_correlation_ids.append(correlation_id_var.get())
        return True

    monkeypatch.setattr(Worker, "perform_job", fake_perform_job)

    connection = Redis.from_url(settings.redis_url)
    queue = Queue("default", connection=connection)
    worker = CorrelationIdWorker([queue], connection=connection)

    assert correlation_id_var.get() is None
    worker.perform_job(job=object(), queue=queue)  # type: ignore[arg-type]
    assert correlation_id_var.get() is None

    assert len(seen_correlation_ids) == 1
    job_correlation_id = seen_correlation_ids[0]
    assert job_correlation_id is not None
    assert _JOB_ID_PATTERN.match(job_correlation_id)

    job_records = [record for record in caplog.records if record.name == "tests.workers.fake_job"]
    assert job_records
    for record in job_records:
        assert record.correlation_id == job_correlation_id

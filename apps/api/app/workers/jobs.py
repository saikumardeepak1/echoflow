"""RQ job definitions.

``notify_appointment`` is enqueued by ``app.services.appointment_service.book``
(see docs/TDD.md section 3.5) whenever an appointment is booked, so the
booking request is not blocked on an outbound Twilio REST call. This is a
stub: nothing consumes the queue yet, and the real implementation (loading
the appointment, sending the SMS via ``twilio_service``, and updating the
matching ``Notification`` row) lands with the notification worker in a later
issue. It exists now, importable and enqueable, purely so
``appointment_service.book`` has a real job function to hand to RQ.
"""


def notify_appointment(appointment_id: str, kind: str) -> None:
    """Send an outbound notification for an appointment.

    ``kind`` is ``"confirmation"`` (enqueued on booking) or ``"reminder"``
    (enqueued by the worker's periodic sweep, added in a later issue).
    Not yet implemented; the worker that runs this job also lands in a
    later issue, so nothing consumes the queue yet.
    """

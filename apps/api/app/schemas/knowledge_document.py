"""Request/response schemas for /v1/knowledge-documents routes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeDocumentCreateRequest(BaseModel):
    """A staff-authored knowledge base entry to create for the caller's
    organization.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Cancellation policy",
                    "content": (
                        "Cancel or reschedule your appointment at least 24 "
                        "hours in advance to avoid a fee."
                    ),
                }
            ]
        }
    )

    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)


class KnowledgeDocumentResponse(BaseModel):
    """The persisted, listable view of a knowledge document.

    ``content_tsv`` is deliberately omitted: it is an internal search index,
    not something a client needs back.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "title": "Cancellation policy",
                    "content": (
                        "Cancel or reschedule your appointment at least 24 "
                        "hours in advance to avoid a fee."
                    ),
                    "created_at": "2026-07-23T09:15:00Z",
                }
            ]
        },
    )

    id: uuid.UUID
    title: str
    content: str
    created_at: datetime

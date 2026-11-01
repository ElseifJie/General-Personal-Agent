"""Stable, human-readable opaque identifiers for GPA resources."""

from __future__ import annotations

import uuid


def _prefixed(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_conversation_id() -> str:
    return _prefixed("c")


def new_turn_id() -> str:
    return _prefixed("turn")


def new_event_id() -> str:
    return _prefixed("evt")


def new_task_id() -> str:
    return _prefixed("task")


def new_artifact_id() -> str:
    return _prefixed("art")


def new_trace_id() -> str:
    return _prefixed("trace")


def new_request_id() -> str:
    return _prefixed("req")


def new_file_id() -> str:
    return _prefixed("f")


def new_conversation_file_id() -> str:
    return _prefixed("cf")


def new_turn_attachment_id() -> str:
    return _prefixed("att")


def new_kb_doc_id() -> str:
    return _prefixed("doc")


def new_file_job_id() -> str:
    return _prefixed("fjob")


def new_tombstone_id() -> str:
    return _prefixed("tomb")


def new_task_input_id() -> str:
    return _prefixed("tin")


def new_attachment_id() -> str:
    return new_turn_attachment_id()

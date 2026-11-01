"""GPA-native domain services (conversation, context, agent transport, tasks).

This package is the v1.0 implementation track. It deliberately does not import
the transition baseline modules (``wb``, ``task``, ``voice``) so the new core
keeps a single dependency direction: GPA core depends on ``db``/``auth``/
``settings`` only.
"""

from general_personal_agent.gpa.ids import (
    new_conversation_id,
    new_event_id,
    new_task_id,
    new_trace_id,
    new_turn_id,
)

__all__ = [
    "new_conversation_id",
    "new_event_id",
    "new_task_id",
    "new_trace_id",
    "new_turn_id",
]

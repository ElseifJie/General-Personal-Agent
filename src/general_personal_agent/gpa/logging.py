"""GPA logging re-export (single logging configuration lives in the baseline)."""

from general_personal_agent.logging import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]

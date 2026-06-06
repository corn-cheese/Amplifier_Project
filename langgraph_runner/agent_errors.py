from __future__ import annotations


AGENT_EXECUTION_FAILED = "agent_execution_failed"
AGENT_TIMEOUT = "agent_timeout"
AGENT_PROCESS_FAILED = "agent_process_failed"

AGENT_EXECUTION_ERROR_CLASSES = frozenset(
    {
        AGENT_EXECUTION_FAILED,
        AGENT_TIMEOUT,
        AGENT_PROCESS_FAILED,
    }
)

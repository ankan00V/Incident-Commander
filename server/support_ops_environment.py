"""Compatibility wrapper for the new environment module."""

from server.environment import IncidentCommanderEnvironment

SupportOpsEnvironment = IncidentCommanderEnvironment

__all__ = ["IncidentCommanderEnvironment", "SupportOpsEnvironment"]

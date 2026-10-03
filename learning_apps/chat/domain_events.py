"""Synchronous domain events emitted inside the owning database transaction."""

from django.dispatch import Signal


conversation_fenced = Signal()


__all__ = ["conversation_fenced"]

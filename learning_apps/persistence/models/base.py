from __future__ import annotations

import os
import uuid

from django.db import models as models



def generate_learning_material_source_key() -> str:
    """Return an opaque, provider-independent identifier for an uploaded source."""
    return uuid.uuid4().hex


def generate_probe_offer_id() -> str:
    return f"apo_{uuid.uuid4().hex}"


def learning_material_upload_path(instance, filename: str) -> str:
    """Keep source bytes owner/goal scoped and outside provider identifiers."""
    safe_name = os.path.basename(filename or "learning-material")[:180]
    return (
        f"learning_materials/{instance.user_id}/{instance.learning_goal_id}/"
        f"{instance.source_key}/{safe_name}"
    )

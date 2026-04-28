"""Compatibility shim for legacy prompt imports.

All authoritative prompt definitions now live in
`app.prompts.goal_driven_templates`.
This module is kept only to avoid breaking older imports during migration.
"""
from app.prompts.goal_driven_templates import *  # noqa: F401,F403

"""Compatibility layer for legacy imports.

The project now keeps the canonical SQLAlchemy session/base definitions in
`app.db.session`. This module re-exports them so older imports keep working.
"""
from app.db.session import Base, SessionLocal, engine, get_db, init_db

__all__ = ["Base", "SessionLocal", "engine", "get_db", "init_db"]

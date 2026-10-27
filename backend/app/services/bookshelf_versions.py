"""Stable shelf ownership independent of editable bookshelf labels."""
from typing import Literal
from sqlalchemy import case
from app.prompts.sheke2610 import CATEGORY, VERSION

ShelfVersion = Literal["1.0", "2.0"]
from app.prompts.social_pain2610 import CATEGORY as HYBRID_CATEGORY, VERSION as HYBRID_VERSION
from app.prompts.astra_agents2610 import AGENTS, VERSIONS
V2_CATEGORIES = frozenset({CATEGORY, HYBRID_CATEGORY, *AGENTS})
V2_VERSIONS = frozenset({VERSION, HYBRID_VERSION, *VERSIONS})


def category_shelf_version(category: str) -> ShelfVersion:
    return "2.0" if category in V2_CATEGORIES else "1.0"


def task_shelf_version(config: dict) -> ShelfVersion:
    explicit = config.get("bookshelf_version")
    if explicit in {"1.0", "2.0"}:
        return explicit
    return "2.0" if config.get("text_workflow_version") in V2_VERSIONS else "1.0"


def shelf_version_expression(config_column):
    explicit = config_column["bookshelf_version"].as_string()
    return case(
        (explicit.in_(["1.0", "2.0"]), explicit),
        (config_column["text_workflow_version"].as_string().in_(list(V2_VERSIONS)), "2.0"),
        else_="1.0",
    )

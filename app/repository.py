"""Public repository boundary; keep existing local imports compatible."""
from app.storage.adapters import ProgressRepository, PostgresRepository, create_repository
from app.storage.repository import SQLProgressRepository

__all__ = ['ProgressRepository', 'PostgresRepository', 'SQLProgressRepository', 'create_repository']

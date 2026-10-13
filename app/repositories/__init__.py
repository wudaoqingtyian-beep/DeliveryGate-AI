"""持久化层：SQLite 初始化与 RunRepository。

对齐 docs/DATA_CONTRACTS.md 第 6 节。使用标准库 sqlite3，无 ORM/迁移框架。
"""

from app.repositories.db import get_connection, get_db_path, init_db
from app.repositories.run_repository import (
    RepositoryError,
    RunRepository,
)

__all__ = [
    "RunRepository",
    "RepositoryError",
    "init_db",
    "get_connection",
    "get_db_path",
]

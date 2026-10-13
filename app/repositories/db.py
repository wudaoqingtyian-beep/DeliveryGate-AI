"""SQLite 连接与初始化（Python 标准库 sqlite3，无 ORM/迁移框架）。

对齐 docs/DATA_CONTRACTS.md 第 6 节。
- 数据库路径：环境变量 DELIVERYGATE_DB_PATH，缺省为项目 data/ 下的默认文件。
- 初始化时创建所需目录与三张表，并开启外键约束。
- 不覆盖或删除已存在的历史数据库（CREATE TABLE IF NOT EXISTS）。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# repositories/ -> app/ -> 项目根
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _PROJECT_ROOT / "data" / "deliverygate.db"


def get_db_path() -> str:
    """返回数据库路径：优先 DELIVERYGATE_DB_PATH，缺省用项目 data/ 默认文件。"""
    return os.environ.get("DELIVERYGATE_DB_PATH") or str(_DEFAULT_DB)


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """打开一个开启外键约束的连接，行工厂为 sqlite3.Row。"""
    path = db_path or get_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str | None = None) -> None:
    """创建目录与三张表（不删除既有数据）。"""
    path = db_path or get_db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).resolve().parent / "schema.sql"
    sql = schema_path.read_text(encoding="utf-8")
    conn = get_connection(path)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()

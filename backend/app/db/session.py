import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "mysql+pymysql://root@127.0.0.1:3306/mirror_dev?charset=utf8mb4",
    )


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True)


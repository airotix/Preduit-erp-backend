"""Declarative base. In production these models are reflected from the live
schema with sqlacodegen (database-first); hand-written here for the skeleton."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass

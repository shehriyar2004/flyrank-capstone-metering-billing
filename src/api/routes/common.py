"""Shared route helpers: per-request DB connection + error envelope."""
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def error_body(code: str, message: str, **details) -> dict:
    return {"error": {"code": code, "message": message, **details}}


def get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.db_factory()

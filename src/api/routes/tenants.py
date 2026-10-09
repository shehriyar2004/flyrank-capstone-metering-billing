from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import repo
from ..schemas import CreateTenantRequest
from .common import error_body, get_conn

router = APIRouter()


@router.post("/tenants", status_code=201)
def create_tenant(payload: CreateTenantRequest, request: Request):
    conn = get_conn(request)
    try:
        tenant = repo.create_tenant(conn, payload.name.strip())
        conn.commit()
        return {
            "id": tenant["id"],
            "name": tenant["name"],
            "plan": tenant["plan"],
            "subscription_status": tenant["subscription_status"],
        }
    finally:
        conn.close()


@router.get("/tenants/{tenant_id}")
def get_tenant(tenant_id: str, request: Request):
    conn = get_conn(request)
    try:
        tenant = repo.get_tenant(conn, tenant_id)
        if tenant is None:
            return JSONResponse(
                status_code=404,
                content=error_body("tenant_not_found",
                                   f"unknown tenant_id: {tenant_id}"),
            )
        return dict(tenant)
    finally:
        conn.close()

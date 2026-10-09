"""GET /usage — the monthly rollup: used, limits, remaining, cost."""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from ...services import BillingService, TenantNotFound
from .common import error_body, get_conn

router = APIRouter()
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@router.get("/usage")
def usage(
    request: Request,
    tenant_id: str = Query(min_length=1, max_length=64),
    month: str | None = Query(default=None, description="YYYY-MM, defaults to current UTC month"),
):
    if month is None:
        now = datetime.now(timezone.utc)
        year, mon = now.year, now.month
    elif _MONTH_RE.match(month):
        year, mon = int(month[:4]), int(month[5:7])
    else:
        return JSONResponse(status_code=422, content=error_body(
            "invalid_month", "month must be YYYY-MM, e.g. 2026-10"))

    conn = get_conn(request)
    try:
        try:
            return BillingService.usage_summary(conn, tenant_id, year, mon)
        except TenantNotFound as exc:
            return JSONResponse(status_code=404, content=error_body(
                "tenant_not_found", str(exc), tenant_id=tenant_id))
    finally:
        conn.close()

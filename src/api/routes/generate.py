"""POST /generate — the dummy billable endpoint.

Meters usage idempotently, enforces quotas, and answers honestly:
  201  usage recorded (or the exact original response on retry)
  404  unknown tenant
  422  invalid input
  429  quota exceeded (with used/limit/reset details)
  402  subscription billing problem
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...services import MeteringService, NothingToMeter, TenantNotFound
from ..schemas import GenerateRequest
from .common import error_body, get_conn

router = APIRouter()


@router.post("/generate")
def generate(payload: GenerateRequest, request: Request):
    conn = get_conn(request)
    try:
        result = MeteringService.record(
            conn,
            tenant_id=payload.tenant_id,
            idempotency_key=payload.idempotency_key,
            api_calls=payload.api_calls,
            input_tokens=payload.input_tokens,
            cached_input_tokens=payload.cached_input_tokens,
            output_tokens=payload.output_tokens,
            reasoning_tokens=payload.reasoning_tokens,
        )
    except TenantNotFound as exc:
        return JSONResponse(status_code=404, content=error_body(
            "tenant_not_found", str(exc), tenant_id=payload.tenant_id))
    except NothingToMeter as exc:
        return JSONResponse(status_code=422, content=error_body(
            "nothing_to_meter", str(exc)))
    finally:
        conn.close()

    headers = {"X-Idempotent-Replayed": "true"} if result.replayed else {}
    return JSONResponse(status_code=result.status_code, content=result.body,
                        headers=headers)

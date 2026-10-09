from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "service": "metering-billing-engine"}

"""Request/response validation at the boundary.

Bad input fails here with a clean 422 — never a 500, never a partial write.
"""
from pydantic import BaseModel, Field, model_validator


class GenerateRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    api_calls: int = Field(default=1, ge=0, le=1_000_000)
    input_tokens: int = Field(default=0, ge=0, le=100_000_000)
    cached_input_tokens: int = Field(default=0, ge=0, le=100_000_000)
    output_tokens: int = Field(default=0, ge=0, le=100_000_000)
    reasoning_tokens: int = Field(default=0, ge=0, le=100_000_000)

    @model_validator(mode="after")
    def _something_to_meter(self):
        total = (
            self.api_calls + self.input_tokens + self.cached_input_tokens
            + self.output_tokens + self.reasoning_tokens
        )
        if total <= 0:
            raise ValueError("request meters nothing: at least one quantity must be > 0")
        return self


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CheckoutRequest(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    success_url: str = Field(
        default="http://127.0.0.1:8000/billing/success?session_id={CHECKOUT_SESSION_ID}"
    )
    cancel_url: str = Field(default="http://127.0.0.1:8000/billing/cancel")

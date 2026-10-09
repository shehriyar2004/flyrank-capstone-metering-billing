"""Pinned configuration: plans, pricing, and environment settings.

Plans and pricing are pinned here (in code, under version control) so that
EVIDENCE.md proofs and GET /usage always agree on the same constants.
"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Plan:
    name: str
    api_call_limit: int      # per calendar month
    token_limit: int         # per calendar month (all token categories summed)
    monthly_price_cents: int


PLANS: dict[str, Plan] = {
    "free": Plan(name="free", api_call_limit=1_000, token_limit=100_000,
                 monthly_price_cents=0),
    "pro": Plan(name="pro", api_call_limit=100_000, token_limit=10_000_000,
                monthly_price_cents=2_000),  # $20.00 / month
}

# Pinned pricing: price per unit as an integer rational (numerator, denominator)
# in USD cents. Integer-only arithmetic; a single round-half-up happens at the
# final total. Never floats.
#
#   api_call           $0.001 / call
#   input_token        $0.50  / 1M tokens
#   cached_input_token $0.125 / 1M tokens  (cheaper: provider already had it cached)
#   output_token       $1.50  / 1M tokens  (reasoning tokens bill as output)
PRICING: dict[str, tuple[int, int]] = {
    "api_call": (1, 10),               # $0.001 / call
    "input_token": (1, 20000),         # $0.50 / 1M tokens
    "cached_input_token": (1, 80000),  # $0.125 / 1M tokens (cheaper cached input)
    "output_token": (3, 20000),        # $1.50 / 1M tokens (reasoning bills here)
}

# Subscription states that block billable actions with 402 Payment Required.
# Quota exhaustion is a different failure: 429 Too Many Requests.
BILLING_BLOCKED_STATUSES = frozenset({"past_due", "unpaid", "canceled"})


@dataclass
class Settings:
    database_path: str = os.environ.get("DATABASE_PATH", "./data/app.db")
    stripe_secret_key: str = os.environ.get("STRIPE_SECRET_KEY", "")
    stripe_webhook_secret: str = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    stripe_pro_price_id: str = os.environ.get("STRIPE_PRO_PRICE_ID", "")
    job_interval_seconds: int = int(os.environ.get("JOB_INTERVAL_SECONDS", "300"))
    app_host: str = os.environ.get("APP_HOST", "127.0.0.1")
    app_port: int = int(os.environ.get("APP_PORT", "8000"))

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_secret_key and self.stripe_secret_key != "sk_test_paste_yours_here")


settings = Settings()

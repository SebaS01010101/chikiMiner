"""Rate-limit parsing and conservative retry delay calculation."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Mapping

from chikiminer.models import RateLimitInfo


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return str(value)
    return None


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    value = _header(headers, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_reset_epoch(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def parse_header_rate_limit(headers: Mapping[str, str], *, cost: int | None = None) -> RateLimitInfo:
    """Parse GitHub's case-insensitive ``x-ratelimit-*`` headers."""

    return RateLimitInfo(
        limit=_int_header(headers, "x-ratelimit-limit"),
        remaining=_int_header(headers, "x-ratelimit-remaining"),
        used=_int_header(headers, "x-ratelimit-used"),
        reset_at=_parse_reset_epoch(_header(headers, "x-ratelimit-reset")),
        resource=_header(headers, "x-ratelimit-resource"),
        cost=cost,
    )


def parse_graphql_rate_limit(
    payload: Mapping[str, object],
    headers: Mapping[str, str],
) -> RateLimitInfo:
    """Prefer the GraphQL body values and fill gaps from response headers."""

    body_rate = None
    data = payload.get("data")
    if isinstance(data, Mapping):
        candidate = data.get("rateLimit")
        if isinstance(candidate, Mapping):
            body_rate = candidate

    cost = body_rate.get("cost") if body_rate else None
    remaining = body_rate.get("remaining") if body_rate else None
    reset_at = body_rate.get("resetAt") if body_rate else None
    if isinstance(reset_at, str):
        try:
            reset_at_value = datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
        except ValueError:
            reset_at_value = None
    else:
        reset_at_value = None

    header_rate = parse_header_rate_limit(headers, cost=int(cost) if isinstance(cost, int) else None)
    return RateLimitInfo(
        limit=body_rate.get("limit", header_rate.limit) if body_rate else header_rate.limit,
        remaining=remaining if isinstance(remaining, int) else header_rate.remaining,
        used=body_rate.get("used", header_rate.used) if body_rate else header_rate.used,
        reset_at=reset_at_value or header_rate.reset_at,
        resource=header_rate.resource or "graphql",
        cost=cost if isinstance(cost, int) else header_rate.cost,
    )


def response_mentions_rate_limit(payload: object) -> bool:
    """Detect the documented rate-limit/abuse messages without false booleans."""

    if not isinstance(payload, Mapping):
        return False
    markers = ("rate limit", "secondary rate", "abuse", "too many requests", "resource exhausted")
    top_level_message = payload.get("message")
    if isinstance(top_level_message, str) and any(marker in top_level_message.lower() for marker in markers):
        return True
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return False
    for error in errors:
        if isinstance(error, Mapping):
            text = " ".join(str(error.get(key, "")) for key in ("message", "type")).lower()
            if any(marker in text for marker in markers):
                return True
    return False


def retry_after_seconds(headers: Mapping[str, str], now: datetime | None = None) -> float | None:
    """Return Retry-After seconds, supporting both seconds and HTTP dates."""

    value = _header(headers, "retry-after")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        return max(0.0, (target - current).total_seconds())


def conservative_retry_delay(
    headers: Mapping[str, str],
    rate_limit: RateLimitInfo | None,
    *,
    attempt: int,
    base_seconds: float = 1.0,
    max_seconds: float = 60.0,
    jitter: float = 0.25,
    random_fn=random.random,
) -> float:
    """Calculate a bounded exponential delay with reset/Retry-After priority."""

    explicit = retry_after_seconds(headers)
    if explicit is not None:
        return min(max_seconds, explicit)

    if rate_limit and rate_limit.remaining == 0 and rate_limit.reset_at:
        until_reset = (rate_limit.reset_at - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, until_reset + 1.0)

    exponential = min(max_seconds, base_seconds * (2 ** max(0, attempt - 1)))
    return min(max_seconds, exponential + max(0.0, jitter) * random_fn())

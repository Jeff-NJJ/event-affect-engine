"""Explicit clock calibration; original client timestamps remain in the event audit."""
from .model import DomainError, iso, timestamp


def calibrated_event(raw, now):
    if "clock_sync" not in raw:
        return raw
    raw=dict(raw)
    sync=raw.pop("clock_sync")
    if not isinstance(sync,dict) or set(sync)!={"client_at_sync","server_at_sync"}:
        raise DomainError("invalid_clock_sync")
    client=timestamp(sync["client_at_sync"])
    server=timestamp(sync["server_at_sync"])
    # A calibration is explicit input, never proof of server identity. Authentication
    # and endpoint verification are the client's responsibility.
    if server>now+300:
        raise DomainError("invalid_clock_sync_server_time")
    original=raw.get("occurred_at")
    effective=timestamp(original)-(client-server)
    payload=raw.get("payload",{})
    if not isinstance(payload,dict) or "clock_audit" in payload:
        raise DomainError("clock_audit_reserved")
    raw["occurred_at"]=iso(effective)
    audit={"original_occurred_at":original,**sync,"offset_seconds":client-server,"method":"explicit_client_calibration"}
    if raw.get('observed_at') is not None:
        audit['original_observed_at']=raw['observed_at']
        raw['observed_at']=iso(timestamp(raw['observed_at'])-(client-server))
    raw["payload"]={**payload,"clock_audit":audit}
    return raw

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


class DomainError(Exception):
    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.code, self.status = code, status


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def finite(value, lo=0.0, hi=1.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise DomainError("invalid_number")
    if not math.isfinite(value) or not lo <= value <= hi:
        raise DomainError("number_out_of_range")
    return float(value)


def timestamp(value: str) -> float:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
        return dt.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise DomainError("invalid_timestamp_with_timezone_required") from None


def iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="milliseconds")


def identity(value: Any, name="id") -> str:
    if not isinstance(value, str) or not value or len(value) > 240 or any(ord(x) < 32 for x in value):
        raise DomainError("invalid_" + name)
    return value


@dataclass(frozen=True)
class Config:
    version: str = "affect-v5.1-research-v2"
    timezone: str = "Asia/Shanghai"
    fast_half_life_h: float = 0.75
    arousal_half_life_h: float = 1.0
    mood_half_life_h: float = 24.0
    mood_arousal_half_life_h: float = 12.0
    mood_coupling_h: float = 0.015
    arousal_coupling_h: float = 0.008
    maximum_event_gain: float = 0.30
    relation_evidence_gate: float = 0.70
    habit_half_life_h: float = 6.0
    expectation_grace_s: float = 300.0
    relation_prior_mass: float = 3.0
    relation_evidence_weight: float = 0.15
    emotion_half_life_h: float = 3.0
    awake_fatigue_tau_h: float = 16.0
    sleep_fatigue_tau_h: float = 4.0
    sleep_start_hour: int = 23
    sleep_end_hour: int = 7
    personality: dict = field(default_factory=lambda: {
        "sensitivity":.5,"resilience":.5,"sociability":.5,"curiosity":.5,"regulation":.5})
    goals: dict = field(default_factory=lambda: {
        "connection": 1.0, "respect": 1.0, "autonomy": 0.8,
        "competence": 0.8, "exploration": 0.7, "rest": 0.7,
    })

    @property
    def signature(self):
        return digest(asdict(self))


SOURCES = {"user_message", "external_event", "self_reflection", "schedule", "time", "system", "agent_message"}
KINDS = {"utterance", "external_experience", "recall", "expect_add", "expect_resolve", "time_advance", "profile_update", "sleep_update", "goal_update", "semantic_resolution"}


@dataclass(frozen=True)
class Event:
    agent_id: str
    event_id: str
    occurred_at: str
    source: str = "user_message"
    event_type: str = "utterance"
    text: str = ""
    actor_id: str | None = None
    target_id: str | None = None
    conversation_id: str | None = None
    provenance: str = "observed_message"
    causation_id: str | None = None
    payload: dict = field(default_factory=dict)
    schema_version: int = 1
    observed_at: str | None = None
    ingestion_mode: str = "live"

    @classmethod
    def parse(cls, raw: dict):
        if not isinstance(raw, dict):
            raise DomainError("object_required")
        fields = cls.__dataclass_fields__
        extras = set(raw) - set(fields) - {"source_message_id"}
        if extras:
            raise DomainError("unknown_event_fields")
        try:
            e = cls(**{k: v for k, v in raw.items() if k in fields})
        except TypeError:
            raise DomainError("missing_event_fields") from None
        for name in ("agent_id", "event_id"):
            identity(getattr(e, name), name)
        for name in ("actor_id", "target_id", "conversation_id", "causation_id"):
            if getattr(e, name) is not None:
                identity(getattr(e, name), name)
        if (type(e.schema_version) is not int or e.schema_version != 1 or not isinstance(e.source, str)
                or not isinstance(e.event_type, str) or e.source not in SOURCES or e.event_type not in KINDS):
            raise DomainError("invalid_event_schema")
        if not isinstance(e.text, str) or len(e.text) > 8000:
            raise DomainError("invalid_text")
        try:
            payload_size = len(canonical(e.payload))
        except (ValueError, TypeError):
            raise DomainError("invalid_payload") from None
        if not isinstance(e.payload, dict) or payload_size > 16000:
            raise DomainError("invalid_payload")
        if not isinstance(e.provenance, str) or e.provenance not in {"observed_message", "observed_event", "self_reflection", "system", "operator_assertion"}:
            raise DomainError("invalid_provenance")
        provenance_by_source = {
            "agent_message": {"observed_message"},
            "user_message": {"observed_message"}, "external_event": {"observed_event"},
            "self_reflection": {"self_reflection"}, "schedule": {"system", "observed_event"},
            "time": {"system"}, "system": {"system", "operator_assertion"},
        }
        if e.provenance not in provenance_by_source[e.source]:
            raise DomainError("source_provenance_conflict")
        if e.ingestion_mode not in ("live","historical_replay"):
            raise DomainError("invalid_ingestion_mode")
        if e.observed_at is not None:
            timestamp(e.observed_at)
        if e.source=="agent_message" and e.actor_id!=e.agent_id:
            raise DomainError("agent_output_actor_mismatch")
        if e.event_type=="semantic_resolution" and (e.source!="system" or e.provenance!="system" or not e.causation_id):
            raise DomainError("resolution_requires_internal_context")
        if e.event_id.startswith('semantic-resolution:') and e.event_type!='semantic_resolution':
            raise DomainError('reserved_internal_event_id',403)
        if e.event_id.startswith(('legacy:v3:','legacy:v4:')):
            raise DomainError('reserved_legacy_memory_id',403)
        if e.event_id.startswith('expect-expiry:') and not (e.source=='time' and e.provenance=='system'
            and e.event_type=='expect_resolve' and e.payload.get('result')=='expired_unknown'):
            raise DomainError('reserved_internal_event_id',403)
        if e.event_type == "recall" and e.source != "self_reflection":
            raise DomainError("invalid_recall_provenance")
        if e.event_type in {"profile_update", "sleep_update", "goal_update"}:
            if e.source != "system" or e.provenance != "operator_assertion":
                raise DomainError("profile_requires_operator",403)
            if e.event_type == "profile_update":
                if not e.payload or set(e.payload)-set(Config().personality):
                    raise DomainError("invalid_personality_fields")
                for value in e.payload.values():
                    finite(value)
            elif e.event_type=="goal_update":
                if not e.payload or set(e.payload)-set(Config().goals):
                    raise DomainError("invalid_goal_fields")
                for value in e.payload.values():
                    finite(value,0,2)
            elif set(e.payload)!={"mode"} or e.payload["mode"] not in ("sleep","wake","automatic"):
                raise DomainError("invalid_sleep_mode")
        if e.event_type in {"expect_add", "expect_resolve"}:
            e.validate_command()
        timestamp(e.occurred_at)
        return e

    def validate_command(self):
        p = self.payload
        operator = self.source == "system" and self.provenance == "operator_assertion"
        timer = self.source == "time" and self.provenance == "system"
        if not operator and not (timer and self.event_type == "expect_resolve"):
            raise DomainError("expectation_requires_operator_or_expiry", 403)
        if self.event_type == "expect_add":
            if set(p) - {"id", "expect", "mutual", "importance", "deadline"}:
                raise DomainError("unknown_expectation_fields")
            identity(p.get("id", self.event_id), "expectation_id")
            text = p.get("expect")
            if not isinstance(text, str) or not text.strip() or len(text) > 240:
                raise DomainError("invalid_expectation")
            if not isinstance(p.get("mutual", False), bool):
                raise DomainError("invalid_mutual_flag")
            finite(p.get("importance", .4))
            if "deadline" in p:
                timestamp(p["deadline"])
        else:
            if set(p) - {"id", "result"}:
                raise DomainError("unknown_expectation_fields")
            identity(p.get("id"), "expectation_id")
            result = p.get("result")
            if not isinstance(result, str) or result not in {"fulfilled", "unmet", "withdrawn", "expired_unknown"}:
                raise DomainError("invalid_expectation_result")
            if timer and result != "expired_unknown":
                raise DomainError("timer_cannot_assert_outcome", 403)

    def to_dict(self):
        return asdict(self)


ASSESSMENT_KINDS = {"support", "affection", "gratitude", "apology", "rejection", "criticism",
                    "loss", "joy", "curiosity", "playful", "neutral", "unknown"}


@dataclass(frozen=True)
class Assessment:
    kind: str = "unknown"
    observed_affect: str = "unknown"
    target: str = "unknown"
    speech_act: str = "unknown"
    agency: str = "unknown"
    certainty: float = 0.0
    relevance: float = 0.0
    intensity: float = 0.0
    controllability: float = 0.5
    expectedness: float = 0.5
    evidence: tuple[str, ...] = ()
    classifier_status: str = "unconfigured"
    model_version: str = "none"
    facts: tuple = ()

    @classmethod
    def parse(cls, raw: dict, text: str):
        if not isinstance(raw, dict) or set(raw) - set(cls.__dataclass_fields__):
            raise DomainError("invalid_assessment")
        raw = dict(raw)
        if not isinstance(raw.get("evidence", ()), (list, tuple)):
            raise DomainError("invalid_assessment_evidence")
        raw["evidence"] = tuple(raw.get("evidence", ()))
        from .facts import Fact
        facts=raw.get("facts",())
        if not isinstance(facts,(list,tuple)) or len(facts)>6:
            raise DomainError("invalid_fact_collection")
        raw["facts"]=tuple(Fact.parse(f.to_dict() if isinstance(f,Fact) else f,text) for f in facts)
        if len({f.fact_id for f in raw["facts"]})!=len(raw["facts"]):
            raise DomainError("duplicate_fact_id")
        try:
            a = cls(**raw)
        except TypeError:
            raise DomainError("invalid_assessment") from None
        if any(not isinstance(getattr(a, field), str) for field in ("kind", "target", "speech_act", "agency", "observed_affect")):
            raise DomainError("invalid_assessment_enum")
        if (a.kind not in ASSESSMENT_KINDS or a.target not in {"agent", "actor", "third_party", "unknown"}
                or a.speech_act not in {"direct", "quote", "hypothetical", "technical", "unknown"}
                or a.agency not in {"actor", "agent", "external", "unknown"}
                or a.observed_affect not in {"sad", "angry", "anxious", "tired", "happy", "neutral", "unknown"}):
            raise DomainError("invalid_assessment_enum")
        for name in ("certainty", "relevance", "intensity", "controllability", "expectedness"):
            finite(getattr(a, name))
        if len(a.evidence) > 4 or any(not isinstance(s, str) or not s or len(s) > 240 or s not in text for s in a.evidence):
            raise DomainError("unsupported_assessment_evidence")
        if (a.kind not in {"neutral", "unknown"} or a.observed_affect not in {"neutral", "unknown"}) and not a.evidence:
            raise DomainError("assessment_evidence_required")
        for name in ("model_version", "classifier_status"):
            identity(getattr(a, name), name)
        return a

    def to_dict(self):
        return asdict(self)


def contextual_assessment(assessment, event):
    a = Assessment.parse(assessment.to_dict(), event.text)
    if (a.target == "agent" or any(f.target=="agent" for f in a.facts)) and event.target_id is not None and event.target_id != event.agent_id:
        return Assessment(classifier_status="explicit_target_conflict", model_version=a.model_version)
    return a

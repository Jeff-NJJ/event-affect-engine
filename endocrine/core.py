"""Pure deterministic transitions. No I/O, current-time calls or random draws."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .model import Assessment, Config, DomainError, Event, canonical, contextual_assessment, finite, identity, iso, timestamp


def clamp(x, low=0.0, high=1.0):
    return max(low, min(high, x))


def initial_state(agent_id, now, config):
    return {"schema_version": 3, "generation": 0, "agent_id": agent_id, "revision": 0, "last_applied_seq": 0,
            "state_time": now, "config_version": config.version, "config_signature": config.signature,
            "fast_valence": 0.0, "fast_arousal": 0.0, "mood_valence": 0.0, "mood_arousal": 0.0,
            "stress": 0.0, "relationships": {}, "habits": {}, "expectations": {},
            "events_processed": 0, "last_appraisal": None, "legacy": {},
            "personality": copy.deepcopy(config.personality), "emotion_tracks": {},
            "appraisal_goals":copy.deepcopy(config.goals), "dialogue":{"disclosures":{}},
            "physiology": {"fatigue": .3, "sleep_debt_h": 0.0, "mode": "automatic",
                "awake_streak_h": 0.0, "sleep_streak_h": 0.0, "total_sleep_h": 0.0,
                "baseline": "configured_initial_condition_not_measured"}}


def validate_state(state, config):
    if (not isinstance(state, dict) or state.get("schema_version") != 3 or state.get("config_signature") != config.signature
            or any(not isinstance(state.get(k), dict) for k in ("relationships", "expectations", "habits", "legacy"))):
        raise DomainError("state_schema_or_config_mismatch", 503)
    for field in ("fast_valence", "mood_valence"):
        finite(state[field], -1.0, 1.0)
    for field in ("fast_arousal", "mood_arousal"):
        finite(state[field], -1.0, 1.0)
    finite(state["stress"])
    for key in config.personality:
        finite(state["personality"][key])
    finite(state["physiology"]["fatigue"])
    finite(state["physiology"]["sleep_debt_h"], 0, 24)
    for track in state["emotion_tracks"].values():
        finite(track["activation"])
    for relation in state["relationships"].values():
        finite(relation["trust"])
        finite(relation["alpha"], .001, 1e12)
        finite(relation["beta"], .001, 1e12)
    canonical(state)


def _coupled_decay(fast, slow, dt, fast_h, slow_h, coupling):
    # Exact solution of fast'=-a*fast, slow'=-b*slow+coupling*fast.
    a, b = math.log(2) / fast_h, math.log(2) / slow_h
    fa, fb = math.exp(-a * dt), math.exp(-b * dt)
    transfer = (fa - fb) / (b - a) if abs(a - b) > 1e-12 else dt * fa
    return fast * fa, slow * fb + coupling * fast * transfer


def _sleeping(s, at, config):
    mode=s["physiology"]["mode"]
    hour=datetime.fromtimestamp(at,_timezone(config.timezone)).hour
    return mode=="sleep" or (mode=="automatic" and (hour>=config.sleep_start_hour or hour<config.sleep_end_hour))


def advance(state, now, config):
    s = copy.deepcopy(state)
    start=s["state_time"]
    if now<=start:
        return s
    if now-start>3650*86400:
        raise DomainError("projection_horizon_exceeds_ten_years")
    while start<now:
        local=datetime.fromtimestamp(start,_timezone(config.timezone))
        boundaries=[(local+timedelta(days=day)).replace(hour=h,minute=0,second=0,microsecond=0).timestamp()
                    for day in (0,1) for h in (config.sleep_end_hour,config.sleep_start_hour)]
        end=min(now,min(t for t in boundaries if t>start+1e-6))
        dt=(end-start)/3600
        sleeping=_sleeping(s,start,config)
        recovery=2**(s["personality"]["resilience"]-.5)*(1.2 if sleeping else 1)
        for fast,slow,fh,sh,c in (("fast_valence","mood_valence",config.fast_half_life_h,config.mood_half_life_h,config.mood_coupling_h),
                                ("fast_arousal","mood_arousal",config.arousal_half_life_h,config.mood_arousal_half_life_h,config.arousal_coupling_h)):
            s[fast],s[slow]=_coupled_decay(s[fast],s[slow],dt,fh/recovery,sh,c)
        s["stress"] *= 2**(-dt/12)
        for track in s["emotion_tracks"].values():
            track["activation"] *= 2**(-dt/config.emotion_half_life_h)
        phys=s["physiology"]
        if sleeping:
            phys["fatigue"] *= math.exp(-dt/config.sleep_fatigue_tau_h)
            phys["sleep_debt_h"]=max(0,phys["sleep_debt_h"]-dt)
            phys["sleep_streak_h"]+=dt;phys["awake_streak_h"]=0.0;phys["total_sleep_h"]+=dt
        else:
            phys["fatigue"]=1-(1-phys["fatigue"])*math.exp(-dt/config.awake_fatigue_tau_h)
            phys["sleep_debt_h"]=min(24,phys["sleep_debt_h"]+dt*.5)
            phys["awake_streak_h"]+=dt;phys["sleep_streak_h"]=0.0
        start=end
    s["state_time"] = now
    return s


def _timezone(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # Fixed modern Shanghai offset when Windows has no IANA database.
        if name == "Asia/Shanghai":
            return timezone(timedelta(hours=8))
        raise DomainError("timezone_database_missing", 503) from None


def _impulse(s, v, a, gain):
    s["fast_valence"] = clamp(s["fast_valence"] + v * gain, -1, 1)
    s["fast_arousal"] = clamp(s["fast_arousal"] + a * gain, -0.2, 0.75)
    if v < 0:
        s["stress"] = clamp(s["stress"] + abs(v) * gain * 0.25)


def _relationship(s, person, config):
    from .social import prepare_relationship
    r=s["relationships"].setdefault(person, {
        "trust": 0.5, "closeness": 0.5, "familiarity": 0.0, "confidence": 0.0,
        "contact_count": 0, "evidence_count": 0, "last_contact_at": None,
        "last_evidence_event_id": None, "positive_evidence": 0, "negative_evidence": 0,
        "alpha": config.relation_prior_mass, "beta": config.relation_prior_mass,
        "budget_day": None, "positive_daily_weight": 0.0, "negative_daily_weight": 0.0,
    })
    return prepare_relationship(r,config)


def _expect(s, event, now, config):
    event.validate_command()
    p = event.payload
    if event.event_type == "expect_add":
        eid = identity(p.get("id", event.event_id), "expectation_id")
        if eid in s["expectations"]:
            raise DomainError("expectation_already_exists", 409)
        text = p.get("expect")
        if not isinstance(text, str) or not text.strip() or len(text) > 240:
            raise DomainError("invalid_expectation")
        if sum(x["status"] == "pending" for x in s["expectations"].values()) >= 50:
            raise DomainError("pending_expectation_limit", 409)
        if not isinstance(p.get("mutual", False), bool):
            raise DomainError("invalid_mutual_flag")
        due = timestamp(p["deadline"]) if "deadline" in p else now + 72 * 3600
        if due < now or due > now + 365 * 86400:
            raise DomainError("invalid_deadline")
        s["expectations"][eid] = {"id": eid, "expect": text, "actor_id": event.actor_id,
            "mutual": p.get("mutual", False), "importance": finite(p.get("importance", 0.4)),
            "deadline": due, "status": "pending", "created_at": now,
            "created_event_id": event.event_id, "settled_event_id": None}
        return {"expectation": eid, "status": "pending", "reason": "explicit_expectation_added"}
    eid = identity(p.get("id"), "expectation_id")
    if eid not in s["expectations"]:
        raise DomainError("expectation_not_found", 404)
    it = s["expectations"][eid]
    result = p.get("result")
    if not isinstance(result, str) or result not in {"fulfilled", "unmet", "withdrawn", "expired_unknown"}:
        raise DomainError("invalid_expectation_result")
    if event.actor_id is not None and it["actor_id"] is not None and event.actor_id != it["actor_id"]:
        raise DomainError("expectation_actor_mismatch", 409)
    if it["status"] not in {"pending", "expired_unknown"} or it["status"] == result:
        return {"expectation": eid, "status": it["status"], "reason": "already_settled_no_effect"}
    if result == "expired_unknown" and now < it["deadline"] + config.expectation_grace_s:
        raise DomainError("expectation_not_due", 409)
    it.update(status=result, settled_at=now, settled_event_id=event.event_id)
    gain = min(config.maximum_event_gain, it["importance"] * 0.12)
    delta = 0.0
    if result == "fulfilled":
        _impulse(s, 0.55, 0.15, gain)
    elif result == "unmet":
        _impulse(s, -0.5, 0.2, gain)
    # Unknown expiry is not proof of rejection. Explicit evidence can refine it later.
    if it["mutual"] and it["actor_id"] and result in {"fulfilled", "unmet"}:
        r = _relationship(s, it["actor_id"], config)
        delta, weight = _relation_evidence(r,1 if result=="fulfilled" else -1,
            it["importance"],event,now,config)
    return {"expectation": eid, "status": result, "trust_delta": delta,
            "gain": gain if result in {"fulfilled", "unmet"} else 0.0,
            "outcome_provenance": event.provenance,
            "reason": "explicit_outcome" if result != "expired_unknown" else "deadline_without_outcome"}


def apply(state,event,assessment,now,seq,config,wall_at=None):
    from .appraisal import transition
    return transition(state,event,assessment,now,seq,config,wall_at)


def snapshot(state, as_of, config):
    validate_state(state, config)
    s = advance(state, as_of, config)
    local = datetime.fromtimestamp(s["state_time"], _timezone(config.timezone))
    hour = local.hour + local.minute / 60
    circadian = 0.05 * math.cos((hour - 13) * math.pi / 12)
    v = clamp(s["fast_valence"] + 0.35 * s["mood_valence"], -1, 1)
    a = clamp(0.25 + circadian + s["fast_arousal"] + 0.25 * s["mood_arousal"])
    last = s.get("last_appraisal") or {}
    # Event labels are transient. Old appraisals must not label a recovered state.
    if abs(v) < 0.03 and abs(s["fast_arousal"]) < 0.03:
        emotions, tone = ["平静"], "自然平稳"
    elif last.get("label") == "concern" and v < -0.02:
        emotions, tone = ["关切"], "温和关切，先理解对方的处境"
    elif v < -0.08:
        emotions, tone = ["受挫" if last.get("target") == "agent" else "失落"], "克制表达感受，说明具体原因"
    elif v > 0.06:
        emotions, tone = ["愉快"], "温暖自然"
    else:
        emotions, tone = ["轻微波动"], "自然回应，避免过度解读"
    names={"warmth":"温暖","joy":"愉快","concern":"关切","hurt":"受伤", "disappointment":"失落",
           "curiosity":"好奇","task_frustration":"任务受挫","relief":"释然","anger":"不满"}
    detail=[{"label":t.get("label",label),"intensity":t["activation"],"causes":t["causes"],"target":t.get("target","unknown"),"actor_id":t.get("actor_id"),"goal":t.get("goal")}
            for label,t in sorted(s["emotion_tracks"].items(),key=lambda kv:(-kv[1]["activation"],kv[0])) if t["activation"]>=.02][:3]
    if detail:
        emotions=[names.get(t["label"],t["label"]) for t in detail]
    sleeping=_sleeping(s,s["state_time"],config)
    a=clamp(a-.15*s["physiology"]["fatigue"]-.1*s["physiology"]["sleep_debt_h"]/24)
    suffix = (f"[模拟情绪状态] {'、'.join(emotions)}；效价{v:.3f}，激活{a:.3f}。"
              f"语气建议：{tone}。归因对象：{last.get('target', 'unknown')}。"
              f"疲劳{ s['physiology']['fatigue']:.2f}；作息模式{ s['physiology']['mode']}。"
              "混合感受的来源分别见emotions_detail；稳定风格见personality。"
              "这是状态参考；事实以事件证据为准，行动另行决定。")
    # Compatibility display only. Never fed back into the core.
    hormones = {"oxytocin": clamp(0.4 + max(0, v) * 0.25), "dopamine": clamp(0.3 + max(0, v) * 0.2),
                "serotonin": clamp(0.5 + s["mood_valence"] * 0.15), "cortisol": clamp(0.2 + s["stress"] * 0.4),
                "melatonin": clamp(0.1 + 0.2 * math.cos((hour - 3) * math.pi / 12))}
    return {"success": True, "engine": "event_affect_v5_1", "schema_version": 3, "agent_id": s["agent_id"],
            "persisted_revision": state["revision"], "state_time": iso(state["state_time"]),
            "as_of": iso(s["state_time"]), "config_version": config.version,
            "generation": s["generation"], "personality": s["personality"], "appraisal_goals":s["appraisal_goals"],
            "physiology": {**s["physiology"],"sleeping":sleeping}, "emotions_detail": detail,
            "core_affect": {"valence": v, "arousal": a},
            "mood": {"valence": s["mood_valence"], "arousal": s["mood_arousal"]},
            "arousal": a, "social_stress": s["stress"],
            "cognitive_mode": "sleeping" if sleeping else "tired" if s["physiology"]["fatigue"]>.8 else "strained" if a>.75 else "resting" if a<.15 else "reflective",
            "rel_temp": s["relationships"].get("example_user", {}).get("closeness", .5),
            "disappointment": None,
            "expects_pending": [x["expect"] for x in s["expectations"].values() if x["status"] == "pending"],
            "legacy_events_processed": s["legacy"].get("events_processed_before_migration", 0),
            "compatibility_notice": "hormones_are_display_only; disappointment_uses_expectation_outcomes",
            "relationships": s["relationships"], "expectations": list(s["expectations"].values()),
            "emotions": emotions, "behaviors": [], "prompt_suffix": suffix,
            "hormones": {k: round(value, 4) for k, value in hormones.items()},
            "hormones_semantics": "synthetic_compatibility_display_only",
            "events_processed": s["events_processed"], "last_appraisal": last}


def _relation_evidence(r,sign,strength,event,now,config):
    from .social import update_evidence
    day=datetime.fromtimestamp(now,_timezone(config.timezone)).date().isoformat()
    r["last_evidence_event_id"]=event.event_id
    return update_evidence(r,"integrity","commitments",sign,strength,day,config)

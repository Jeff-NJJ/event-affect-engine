from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sqlite3
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .time_sync import calibrated_event
from .model import DomainError, canonical, identity, iso, timestamp
from .integration import conclusion_feed
from . import operit_native

LOG = logging.getLogger("endocrine.api")


def legacy_event(service, path, body):
    agent = identity(body.get("agent_id", "example_agent"), "agent_id")
    supplied = body.get("event_id") or body.get("request_id")
    if not supplied and body.get("source_message_id"):
        supplied = "operit:" + str(body.get("conversation_id", "default")) + ":" + str(body["source_message_id"])
    eid = identity(supplied or "legacy:" + str(uuid.uuid4()), "event_id")
    old = service.repo.get_event(agent, eid)
    when = body.get("occurred_at") or (json.loads(old["body"])["occurred_at"] if old else iso(service.clock.now()))
    e = {"agent_id": agent, "event_id": eid, "occurred_at": when,
         "actor_id": body.get("actor_id", "example_user") if path != "/event" else body.get("actor_id"),
         "conversation_id": body.get("conversation_id"), "text": body.get("msg", body.get("message", body.get("text", ""))),
         "source": "user_message", "provenance": "observed_message", "payload": {}}
    if path == "/event":
        e.update(source="external_event", provenance="observed_event", event_type="external_experience")
        # Old stim fields are recorded for audit, never bypass semantic attribution.
        e["payload"] = {"legacy_source": body.get("source", "world"), "legacy_stim_hint": body.get("stim")}
    elif path in {"/expect", "/expect_resolve"}:
        e.update(source="system", provenance="operator_assertion")
        if path == "/expect_resolve" or body.get("resolve"):
            mapping = {"lapsed": "unmet", "fulfilled": "fulfilled", "withdrawn": "withdrawn"}
            result = body.get("result")
            if not isinstance(result, str):
                raise DomainError("invalid_expectation_result")
            e.update(event_type="expect_resolve", payload={"id": body.get("id"), "result": mapping.get(result, result)})
        else:
            ttl = body.get("ttl", 72)
            try:
                ttl = float(ttl)
                if not 0 < ttl <= 8760:
                    raise ValueError()
            except (TypeError, ValueError):
                raise DomainError("invalid_ttl") from None
            e.update(event_type="expect_add", payload={"id": body.get("id") or eid,
                     "expect": body.get("expect"), "mutual": body.get("mutual", False),
                     "importance": body.get("importance", 0.4),
                     "deadline": body.get("deadline") or iso(timestamp(when) + ttl * 3600)})
    return e, bool(supplied)


def submit_legacy(service, path, body):
    body = calibrated_event(body,service.clock.now())
    event, supported = legacy_event(service, path, body)
    if "clock_audit" in body.get("payload",{}):
        event["payload"]["clock_audit"]=body["payload"]["clock_audit"]
    defaults = {"occurred_at": not bool(body.get("occurred_at"))}
    if event.get("event_type") == "expect_add" and not body.get("deadline"):
        defaults["deadline_ttl_h"] = float(body.get("ttl", 72))
    result = service.submit(event, server_defaults=defaults)
    result["dedupe_supported"] = supported
    if not supported:
        result["compatibility_notice"] = "missing_stable_event_id"
    return result


def make_server(service, host="127.0.0.1", port=5003, token="", admin_token=""):
    if not token and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("ENDOCRINE_TOKEN required for non-loopback binding")

    class Handler(BaseHTTPRequestHandler):
        server_version = "EventAffect/5.7"

        def log_message(self, format, *args):
            pass  # access logging below deliberately excludes query text, bodies and headers

        def send(self, code, result, trace_id):
            data = canonical(result).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Trace-ID", trace_id)
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                LOG.warning("response_disconnected trace_id=%s", trace_id)

        def dispatch(self, method):
            trace = str(uuid.uuid4())
            parsed = urlsplit(self.path)
            path = parsed.path.rstrip("/") or "/"
            try:
                if path == "/health" and method == "GET":
                    with service.repo.connect() as c:
                        c.execute("SELECT 1").fetchone()
                    result, status = {"status": "ok", "engine": "event_affect", "release": "5.7.0", "expression": "expression-v1"}, 200
                else:
                    if token and not hmac.compare_digest(self.headers.get("X-Token", "").encode("utf-8"),token.encode("utf-8")):
                        raise DomainError("forbidden", 403)
                    if method == "GET":
                        query = parse_qs(parsed.query)
                        agent = identity(query.get("agent_id", ["example_agent"])[0], "agent_id")
                        if path == "/v2/time":
                            result = {"success": True, "server_time": iso(service.clock.now()),
                                      "max_future_skew_seconds": 300, "time_basis": "UTC"}
                        elif path in {"/", "/snapshot", "/v2/snapshot"}:
                            as_of = timestamp(query["as_of"][0]) if "as_of" in query else None
                            result = service.snapshot(agent, as_of)
                        elif path == "/v2/integration/changes":
                            try:
                                after=int(query.get('after',['0'])[0]);limit=int(query.get('limit',['100'])[0])
                            except ValueError:raise DomainError('invalid_change_cursor') from None
                            result=conclusion_feed(service.repo,agent,after,limit)
                        elif path == '/v2/operit/receipt':
                            result=operit_native.receipt(service,agent,query.get('event_id',[''])[0])
                        elif path == "/v2/deliveries/status":
                            result = service.delivery.view(query.get("operation_id",[""])[0])
                        elif path == "/expect":
                            view = service.snapshot(agent)
                            result = {"pending": [e for e in view["expectations"] if e["status"] == "pending"],
                                      "recent": view["expectations"][-10:], "disappointment": None,
                                      "semantics": "v4_unknown_outcomes_do_not_imply_broken_promises"}
                        elif path == "/v2/memory":
                            try:
                                limit=int(query.get("limit",["10"])[0])
                            except ValueError:
                                raise DomainError("invalid_memory_limit") from None
                            result=service.memories(agent,actor_id=query.get("actor_id",[None])[0],
                                conversation_id=query.get("conversation_id",[None])[0],query=query.get("q",[""])[0],
                                cursor=query.get("cursor",[None])[0],limit=limit,important=query.get("sort",[""])[0]=="salience")
                            result["memories"]=result["items"]
                        else:
                            raise DomainError("not_found", 404)
                    else:
                        try:
                            length = int(self.headers.get("Content-Length", "0"))
                        except ValueError:
                            raise DomainError("invalid_content_length") from None
                        if not 0 < length <= 65536:
                            raise DomainError("invalid_body_size", 413)
                        try:
                            self.connection.settimeout(10)
                            body = json.loads(self.rfile.read(length))
                        except TimeoutError:
                            raise DomainError('request_body_timeout',408) from None
                        except (ValueError, UnicodeError):
                            raise DomainError("bad_json") from None
                        if not isinstance(body, dict):
                            raise DomainError("object_required")
                        if path=="/v2/events" and body.get("event_type")=="semantic_resolution":
                            raise DomainError("internal_event_not_accepted",403)
                        administrative=(path in {"/expect","/expect_resolve"} or
                            (path=="/v2/events" and (body.get("source") in ("system","time","schedule") or
                            body.get("event_type") in ("profile_update","sleep_update","goal_update","expect_add","expect_resolve"))))
                        if administrative:
                            if not admin_token:raise DomainError("admin_auth_not_configured",503)
                            if not hmac.compare_digest(self.headers.get("X-Admin-Token","").encode("utf-8"),admin_token.encode("utf-8")):
                                raise DomainError("admin_required",403)
                        if path=='/v2/memory/reflect':
                            result=service.grounded.reflect(body)
                        elif path=='/v2/expression/review':
                            result=service.grounded.review(body)
                        elif path=='/v2/expression/respond':
                            result=service.grounded.respond(body)
                        elif path=='/v2/expression/context':
                            result=service.expression_context(body)
                        elif path=='/v2/operit/context':
                            result=operit_native.context(service,body)
                        elif path=='/v2/operit/messages':
                            result=operit_native.accept_message(service,body)
                        elif path=='/v2/memory/search':
                            if not isinstance(body.get('query'),str) or not body['query'].strip():raise DomainError('query_required')
                            result=service.memories(identity(body.get('agent_id','example_agent'),'agent_id'),
                                query=body.get('query',''),actor_id=body.get('actor_id'),conversation_id=body.get('conversation_id'),
                                limit=body.get('limit',10))
                        elif path=="/v2/messages":
                            role=body.get("role")
                            if role not in ("user","assistant"):raise DomainError("invalid_message_role")
                            agent=identity(body.get("agent_id","example_agent"),"agent_id")
                            eid=identity(body.get("event_id"),"event_id")
                            event={"agent_id":agent,"event_id":eid,"occurred_at":body.get("occurred_at") or iso(service.clock.now()),
                                "source":"agent_message" if role=="assistant" else "user_message","provenance":"observed_message",
                                "text":body.get("text",""),"actor_id":agent if role=="assistant" else body.get("actor_id","example_user"),
                                "conversation_id":body.get("conversation_id")}
                            result=service.submit(event,server_defaults={"occurred_at":not bool(body.get("occurred_at"))})
                        elif path == "/v2/deliveries":
                            queued=service.delivery.queue(body.get("operation_id"),body.get("payload"))
                            result=service.delivery.view(queued["operation_id"])
                        elif path == "/v2/deliveries/dispatch":
                            result=service.delivery.dispatch(body.get("operation_id"))
                        elif path == "/v2/deliveries/reconcile":
                            result=service.delivery.reconcile(body.get("operation_id"))
                        elif path == "/v2/events":
                            result = service.submit(body)
                        elif path in {"/emotion", "/event", "/expect", "/expect_resolve"}:
                            result = submit_legacy(service, path, body)
                        elif path == "/reset":
                            raise DomainError("reset_requires_explicit_offline_migration_or_new_agent", 409)
                        else:
                            raise DomainError("not_found", 404)
                    status = result.get("http_status", 200)
                self.send(status, result, trace)
                LOG.info("request_completed trace_id=%s method=%s status=%s event_id_hash=%s revision=%s",
                         trace, method, status,
                         hashlib.sha256(str(result.get("event_id", "")).encode()).hexdigest()[:12], result.get("revision"))
            except DomainError as exc:
                self.send(exc.status, {"success": False, "error": exc.code, "trace_id": trace,
                                       **({"server_time": iso(service.clock.now()), "max_future_skew_seconds": 300}
                                          if exc.code in {"event_too_far_in_future","observation_too_far_in_future"} else {})}, trace)
                LOG.warning("request_rejected trace_id=%s code=%s", trace, exc.code)
            except (sqlite3.Error, OSError):
                self.send(503, {"success": False, "error": "storage_unavailable", "trace_id": trace}, trace)
                LOG.error("storage_failed trace_id=%s", trace)
            except Exception:
                self.send(500, {"success": False, "error": "internal_error", "trace_id": trace}, trace)
                LOG.error("internal_failed trace_id=%s", trace)

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

    server = ThreadingHTTPServer((host, port), Handler)
    server.timeout = 1
    return server

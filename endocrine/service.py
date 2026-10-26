import json
import logging
import time
from dataclasses import replace

from .classifier import UnknownClassifier
from .time_sync import calibrated_event
from .clock import Clock
from .core import snapshot, initial_state
from .expression import build_expression, validate_request, validate_profile
from .delivery import DeliveryService
from .semantic import SemanticMemory
from .grounding import GroundedDialogue
from .model import Assessment, DomainError, Event, contextual_assessment, digest, iso, timestamp

LOG = logging.getLogger("endocrine.service")


class Service:
    def __init__(self, repository, classifier=None, clock=None, *, expression_profiles=None):
        self.repo = repository
        self.config = repository.config
        self.delivery = DeliveryService(repository)
        self.semantic = SemanticMemory(repository)
        self.classifier = classifier or UnknownClassifier()
        self.clock = clock or Clock()
        self.before_commit = None  # fault-injection seam, never set by HTTP input
        self.expression_profiles = {k: validate_profile(v) for k, v in (expression_profiles or {}).items()}
        self.grounded = GroundedDialogue(self)

    def submit(self, raw, wait_seconds=75.0, *, server_defaults=None):
        raw = calibrated_event(raw,self.clock.now())
        event = Event.parse(raw)
        now = self.clock.now()
        if timestamp(event.occurred_at) > now + 300 and not self.repo.get_event(event.agent_id, event.event_id):
            raise DomainError("event_too_far_in_future")
        if event.observed_at is not None and timestamp(event.observed_at)>now+300 and not self.repo.get_event(event.agent_id,event.event_id):
            raise DomainError("observation_too_far_in_future")
        if event.event_type == "recall":
            original = self.repo.get_event(event.agent_id, event.causation_id)
            if (not original or original["status"] != "applied") and not self.repo.find_memory(event.agent_id,event.causation_id):
                raise DomainError("recall_requires_applied_original")
            original_text=json.loads(original["body"])["text"] if original and original["status"]=="applied" else self.repo.find_memory(event.agent_id,event.causation_id)["text"]
            if event.text!=original_text:
                raise DomainError("recall_content_mismatch",409)
            if event.source != "self_reflection" or event.provenance != "self_reflection":
                raise DomainError("invalid_recall_provenance")
        row, duplicate = self.repo.reserve(event, now, server_defaults)
        deadline = time.monotonic() + wait_seconds
        while True:
            result = self.repo.get_event(event.agent_id, event.event_id)
            if result["response"]:
                response = json.loads(result["response"])
                return {**response, "deduplicated": duplicate, "dedupe_supported": True}
            if time.monotonic() >= deadline:
                return {"success": True, "status": "pending", "http_status": 202,
                        "event_id": event.event_id, "agent_id": event.agent_id,
                        "dedupe_supported": True, "deduplicated": duplicate}
            # Do not start another provider call beyond this request budget.
            budget = getattr(self.classifier, "timeout", 0.0) + 1.0
            if deadline - time.monotonic() < budget:
                return {"success": True, "status": "pending", "http_status": 202,
                        "event_id": event.event_id, "agent_id": event.agent_id,
                        "dedupe_supported": True, "deduplicated": duplicate}
            claim = self.repo.claim_next(event.agent_id)
            if claim:
                self._process(claim)
            else:
                time.sleep(0.01)

    def _process(self, row):
        try:
            event = Event.parse(json.loads(row["body"]))
            context=None
            if row["assessment"]:
                assessment = Assessment.parse(json.loads(row["assessment"]), event.text)
                if event.event_type!='semantic_resolution':context=self.repo.saved_assessment_context(row)
            elif event.source=="agent_message":
                assessment=Assessment(classifier_status="context_only",model_version="context-log-v1")
            elif event.event_type in {"expect_add", "expect_resolve", "time_advance", "profile_update", "sleep_update", "goal_update"}:
                assessment = Assessment(classifier_status="system_command", model_version="deterministic-v1")
            else:
                try:
                    assessment,context=self._assess(row,event)
                    assessment = contextual_assessment(assessment, event)
                except (DomainError, ValueError, TypeError, AttributeError):
                    assessment = Assessment(classifier_status="invalid_classifier_output")
            context_row=row
            if event.event_type=="semantic_resolution":
                context_row=self.repo.get_event(event.agent_id,event.payload["original_event_id"])
            if context is None:
                context=self.repo.classification_context(context_row,event_ids=event.payload.get('context_event_ids') if event.event_type=='semantic_resolution' else None)
            try:
                assessment=self._bind_context(assessment,event,context)
            except (DomainError,TypeError,ValueError):
                assessment=Assessment(classifier_status="invalid_classifier_output")
            if self.repo.save_assessment(row, assessment, context):
                self.repo.finish(row, assessment, self.before_commit)
        except DomainError as exc:
            if exc.status >= 500:
                raise
            self.repo.reject_claim(row, exc)

    def recover_pending(self, limit=100):
        processed = 0
        while processed < limit:
            row = self.repo.claim_next_any()
            if not row:break
            try:
                self._process(row)
            except Exception as exc:
                LOG.error("recovery_failed agent_hash=%s event_hash=%s error_type=%s code=%s",
                          digest(row['agent_id'])[:12], digest(row["event_id"])[:12], type(exc).__name__,
                          exc.code if isinstance(exc, DomainError) else "internal_or_storage_failure")
            processed += 1
        return processed

    def _assess(self,row,event):
        deadline=time.monotonic()+getattr(self.classifier,'timeout',60)
        context=self.repo.classification_context(row)
        if event.conversation_id and self.semantic.reranker is not None:
            try:
                memories=self.semantic.search(event.agent_id,event.text,purpose='context',
                    query_context={'agent_id':event.agent_id,'current_actor_id':event.actor_id,'occurred_at':event.occurred_at},
                    conversation_id=event.conversation_id,limit=4,before_seq=row['seq'],
                    exclude_ids=[item['event_id'] for item in context],deadline=min(deadline,time.monotonic()+20))
                ids=[m['event_id'] for m in memories['items']]+[item['event_id'] for item in context]
                context=self.repo.classification_context(row,event_ids=list(dict.fromkeys(ids)))
            except DomainError:
                return Assessment(classifier_status='context_retrieval_unavailable'),context
        if hasattr(self.classifier,'classify_with_context'):
            assessment=(self.classifier.classify_with_context(event,context,deadline=deadline)
                if getattr(self.classifier,'supports_deadline',False) else self.classifier.classify_with_context(event,context))
        else:assessment=self.classifier.classify(event)
        return assessment,context

    def retry_classifications(self, limit=1, *, force=False):
        if isinstance(self.classifier,UnknownClassifier) or getattr(self.classifier,"key",None)=="":
            return 0
        completed=0
        for _ in range(limit):
            job=self.repo.claim_classification(force=force)
            if not job:
                break
            row=self.repo.get_event(job["agent_id"],job["event_id"])
            event=Event.parse(json.loads(row["body"]))
            try:
                a,context=self._assess(row,event)
                a=self._bind_context(contextual_assessment(a,event),event,context)
            except (DomainError,ValueError,TypeError,AttributeError):
                a=Assessment(classifier_status="invalid_classifier_output");context=self.repo.classification_context(row)
            completed+=self.repo.complete_classification(job,a,context,resolved_at=self.clock.now())
        if completed:
            self.recover_pending(limit=max(10,completed*2))
        return completed

    def _bind_context(self,assessment,event,context):
        known={row["event_id"]:row for row in context}
        facts=[]
        for fact in assessment.facts:
            if fact.related_event_id is not None and fact.related_event_id not in known:
                raise DomainError("fact_context_reference_not_found")
            for ref in fact.context_evidence:
                if ref["event_id"] not in known or ref["evidence"] not in known[ref["event_id"]]["text"]:
                    raise DomainError("fact_context_evidence_mismatch")
            related_actor=known[fact.related_event_id]["actor_id"] if fact.related_event_id else None
            if fact.interaction=='repair_confirmed' and not (fact.related_event_id and related_actor==event.actor_id
                and any(ref['event_id']==fact.related_event_id and ref['meaning']=='repair_reference' for ref in fact.context_evidence)):
                # An unbound declaration of repair is not evidence that an
                # earlier violation was resolved. Retain the current text.
                fact=replace(fact,interaction='none',trust_evidence=())
            facts.append(replace(fact,related_actor_id=related_actor))
        return replace(assessment,facts=tuple(facts))

    def memories(self,agent_id="example_agent",*,query="",actor_id=None,conversation_id=None,limit=10,cursor=None,important=False):
        if query:
            if cursor:raise DomainError("semantic_cursor_not_supported")
            return self.semantic.search(agent_id,query,actor_id=actor_id,conversation_id=conversation_id,limit=limit)
        result=self.repo.search_memories(agent_id,actor_id=actor_id,conversation_id=conversation_id,limit=limit,cursor=cursor,important=important)
        return {"success":True,**result,"memories":result["items"],"retrieval_mode":"salience" if important else "chronological"}

    def snapshot(self, agent_id="example_agent", as_of=None):
        now = self.repo.projection_time(agent_id,self.clock.now()) if as_of is None else as_of
        state = self.repo.load_state(agent_id, now)
        return snapshot(state, now, self.config)

    def expression_context(self, raw):
        request = validate_request(raw)
        agent, actor, conversation = (request[k] for k in ('agent_id', 'actor_id', 'conversation_id'))
        now = self.repo.projection_time(agent, self.clock.now())
        # Read state, source receipt and assistant usage in one SQLite snapshot.
        # Never reserve an event or increment habit usage just for preparing a prompt.
        with self.repo.connect(readonly=True) as c:
            c.execute('BEGIN')
            source_status = 'background_only'
            if 'source_event_id' in request:
                row = c.execute('SELECT body,status FROM events WHERE agent_id=? AND event_id=?',
                    (agent, request['source_event_id'])).fetchone()
                if row is None: raise DomainError('expression_source_not_found', 404)
                body = json.loads(row['body'])
                if body.get('conversation_id') != conversation or body.get('actor_id') != actor or body.get('source') != 'user_message':
                    raise DomainError('expression_source_scope_mismatch', 409)
                if row['status'] != 'applied': raise DomainError('expression_source_not_applied', 409)
                source_status = 'applied'
            row = c.execute('SELECT state FROM agent_states WHERE agent_id=?', (agent,)).fetchone()
            state = json.loads(row['state']) if row else initial_state(agent, now, self.config)
            rows = c.execute("SELECT body FROM events WHERE agent_id=? AND status='applied' "
                "AND json_extract(body,'$.conversation_id')=? AND json_extract(body,'$.source')='agent_message' "
                "ORDER BY seq DESC LIMIT 8", (agent, conversation)).fetchall()
            recent = [json.loads(row['body'])['text'] for row in reversed(rows)]
        result = build_expression(state, max(now, state['state_time']), self.config, actor,
            self.expression_profiles.get(agent), (recent, request['recent_assistant']))
        return {**result, 'agent_id': agent, 'actor_id': actor, 'conversation_id': conversation,
                'source_event_id': request.get('source_event_id'), 'source_status': source_status,
                'history_basis': {'server_assistant_count': len(recent),
                                 'client_assistant_count': len(request['recent_assistant'])}}

    def settle_due(self,*,wait_seconds=75.0):
        now = self.clock.now()
        with self.repo.connect() as c:
            states = [json.loads(r[0]) for r in c.execute("SELECT state FROM agent_states ORDER BY agent_id")]
        settled = 0
        for state in states:
            for eid, item in state["expectations"].items():
                if item["status"] == "pending" and item["deadline"] + self.config.expectation_grace_s <= now:
                    try:
                        result = self.submit({"agent_id": state["agent_id"], "event_id": "expect-expiry:" + digest([state["agent_id"], eid]),
                                              "occurred_at": iso(item["deadline"] + self.config.expectation_grace_s),
                                              "source": "time", "event_type": "expect_resolve", "provenance": "system",
                                              "payload": {"id": eid, "result": "expired_unknown"}},wait_seconds=wait_seconds)
                        settled += bool(result.get("success") and result.get("status") != "pending")
                    except Exception as exc:
                        LOG.error("expiry_failed agent_hash=%s expectation_hash=%s error_type=%s code=%s",
                                  digest(state["agent_id"])[:12], digest(eid)[:12], type(exc).__name__,
                                  exc.code if isinstance(exc, DomainError) else "internal_or_storage_failure")
        return settled

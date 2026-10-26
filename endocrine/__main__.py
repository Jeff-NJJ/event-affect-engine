import argparse
import logging
import json
import os
import threading

from .api import make_server
from .classifier import HTTPClassifier, UnknownClassifier
from .core import validate_state
from .delivery import DeliveryService, HTTPDeliveryAdapter
from .semantic import SemanticMemory,HTTPParagraphReranker
from .model import Config
from .service import Service
from .store import Repository
from .expression import load_profiles
from .grounding import HTTPDialogueModel
from .external_memory import OBMemoryClient


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/affect.sqlite3")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5003)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    classifier = UnknownClassifier()
    endpoint = os.environ.get("CLASSIFIER_ENDPOINT")
    request_options=json.loads(os.environ.get('CLASSIFIER_REQUEST_OPTIONS_JSON','{}'))
    if endpoint:
        classifier = HTTPClassifier(endpoint, os.environ.get("CLASSIFIER_MODEL", ""), os.environ.get("CLASSIFIER_KEY", ""),
            timeout=float(os.environ.get('CLASSIFIER_TIMEOUT_SECONDS','60')),request_options=request_options)
    config = Config()
    repository = Repository(args.db, config)
    for state in repository.export()["states"]:
        validate_state(state, config)
    service = Service(repository, classifier,
        expression_profiles=load_profiles(os.environ.get('EXPRESSION_PROFILES_FILE')))
    gateway=os.environ.get("DELIVERY_GATEWAY_URL")
    if gateway:
        service.delivery=DeliveryService(repository,HTTPDeliveryAdapter(gateway,os.environ.get("DELIVERY_GATEWAY_TOKEN","")))
    semantic_endpoint=os.environ.get('SEMANTIC_ENDPOINT',endpoint)
    if semantic_endpoint:
        service.semantic=SemanticMemory(repository,HTTPParagraphReranker(semantic_endpoint,
            os.environ.get('SEMANTIC_MODEL',os.environ.get('CLASSIFIER_MODEL','')),
            os.environ.get('SEMANTIC_KEY',os.environ.get('CLASSIFIER_KEY','')),request_options=request_options))
    dialogue_endpoint=os.environ.get('DIALOGUE_ENDPOINT',endpoint)
    if dialogue_endpoint:
        service.grounded.model=HTTPDialogueModel(dialogue_endpoint,
            os.environ.get('DIALOGUE_MODEL',os.environ.get('CLASSIFIER_MODEL','')),
            os.environ.get('DIALOGUE_KEY',os.environ.get('CLASSIFIER_KEY','')),
            json.loads(os.environ['DIALOGUE_REQUEST_OPTIONS_JSON']) if os.environ.get('DIALOGUE_REQUEST_OPTIONS_JSON') else
            {k:v for k,v in request_options.items() if k!='response_format'},
            timeout=float(os.environ.get('DIALOGUE_TIMEOUT_SECONDS','120')),
            max_tokens=int(os.environ.get('DIALOGUE_MAX_TOKENS','8192')))
    server = make_server(service, args.host, args.port, os.environ.get("ENDOCRINE_TOKEN", ""),os.environ.get("ENDOCRINE_ADMIN_TOKEN",""))
    if os.environ.get('OB_BRIDGE_URL'):
        if not os.environ.get('OB_BRIDGE_TOKEN'):raise ValueError('OB_BRIDGE_TOKEN required')
        service.grounded.external_memory=OBMemoryClient(os.environ['OB_BRIDGE_URL'],os.environ['OB_BRIDGE_TOKEN'])
    stop = threading.Event()

    def worker():
        while not stop.is_set():
            try:
                service.recover_pending(limit=1)
                service.settle_due(wait_seconds=0)
                service.retry_classifications()

            except Exception:
                logging.error("maintenance_failed; retrying_without_reset")
            stop.wait(1)

    def delivery_worker():
        while not stop.is_set():
            try:
                service.delivery.maintain(limit=1)
            except Exception:
                logging.error("delivery_maintenance_failed; reconcile_before_retry")
            stop.wait(2)

    delivery_thread=threading.Thread(target=delivery_worker,daemon=True)
    delivery_thread.start()
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        logging.info("listening host=%s port=%s classifier_configured=%s", args.host, server.server_port, bool(endpoint))
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        t.join(timeout=65)
        delivery_thread.join(timeout=35)


if __name__ == "__main__":
    main()

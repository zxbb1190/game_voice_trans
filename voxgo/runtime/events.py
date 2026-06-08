from dataclasses import dataclass
from typing import Any, Callable, DefaultDict, List, Type
from collections import defaultdict


@dataclass
class TranscriptReady:
    text: str
    language: str
    trace_id: str


@dataclass
class TranslationReady:
    original: str
    translated: str
    source_lang: str
    target_lang: str
    trace_id: str


@dataclass
class AppNotice:
    level: str
    message: str
    title: str = ""


class EventBus:
    def __init__(self):
        self._subscribers: DefaultDict[Type[Any], List[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event_type: Type[Any], handler: Callable[[Any], None]):
        self._subscribers[event_type].append(handler)

    def publish(self, event: Any):
        for handler in list(self._subscribers.get(type(event), [])):
            handler(event)


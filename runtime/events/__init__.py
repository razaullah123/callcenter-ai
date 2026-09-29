from .bus import BoundEmitter, EventBus
from .schema import Event, EventType, Level
from .sinks import ConsoleSink, JsonlSink

__all__ = ["BoundEmitter", "ConsoleSink", "Event", "EventBus", "EventType", "JsonlSink", "Level", "create_bus"]


def create_bus(settings=None) -> EventBus:
    """Event bus wired with the sinks configured in settings."""
    from runtime.config import get_settings

    s = settings or get_settings()
    bus = EventBus()
    if s.log_console:
        bus.subscribe(ConsoleSink(min_level=Level(s.log_level)))
    bus.subscribe(JsonlSink(s.log_dir))
    return bus

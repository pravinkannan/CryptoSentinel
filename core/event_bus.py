"""
Asynchronous Publish-Subscribe Event Bus for Crypto Sentinel.
Uses asyncio.gather with subscriber isolation so that a failing subscriber
does not disrupt other subscribers or the publisher.
"""

import asyncio
import logging
from typing import Dict, List, Callable, Awaitable, Type, Any, Set

logger = logging.getLogger(__name__)

EventHandler = Callable[[Any], Awaitable[None]]


class EventBus:
    """Lightweight in-process async event bus."""

    def __init__(self) -> None:
        self._subscribers: Dict[Type[Any], List[EventHandler]] = {}
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: Type[Any], handler: EventHandler) -> None:
        """Register an async subscriber for a specific event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        if handler not in self._subscribers[event_type]:
            self._subscribers[event_type].append(handler)
            logger.debug(f"Subscribed {handler.__name__ if hasattr(handler, '__name__') else handler} to {event_type.__name__}")

    def unsubscribe(self, event_type: Type[Any], handler: EventHandler) -> None:
        """Unregister an async subscriber."""
        if event_type in self._subscribers and handler in self._subscribers[event_type]:
            self._subscribers[event_type].remove(handler)
            logger.debug(f"Unsubscribed {handler} from {event_type.__name__}")

    async def publish(self, event: Any) -> None:
        """
        Publish an event to all matching subscribers.
        Executes all subscribers concurrently with asyncio.gather(return_exceptions=True).
        Exceptions are captured and logged to preserve subscriber isolation.
        """
        event_cls = type(event)
        handlers: List[EventHandler] = []

        # Find all handlers subscribed to event_cls or any of its superclasses
        for sub_type, sub_handlers in self._subscribers.items():
            if issubclass(event_cls, sub_type):
                handlers.extend(sub_handlers)

        if not handlers:
            return

        async def _safe_execute(handler: EventHandler) -> None:
            try:
                res = handler(event)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                handler_name = getattr(handler, '__name__', str(handler))
                logger.error(
                    f"Error in event subscriber {handler_name} while processing {event_cls.__name__}: {e}",
                    exc_info=True
                )

        tasks = [_safe_execute(h) for h in handlers]
        await asyncio.gather(*tasks, return_exceptions=True)

    def clear(self) -> None:
        """Clear all registered event subscribers (used for tests)."""
        self._subscribers.clear()


# Global default event bus instance
_default_bus: EventBus = EventBus()

def get_event_bus() -> EventBus:
    """Retrieve global default EventBus instance."""
    return _default_bus

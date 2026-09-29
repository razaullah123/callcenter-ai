from starlette.websockets import WebSocketDisconnect

try:
    from starlette.websockets import WebSocketDisconnected
except ImportError:                     # older Starlette
    WebSocketDisconnected = WebSocketDisconnect

# Sending on a socket the caller / IVR already closed: harmless at hang-up, never worth a traceback.
CLOSED_ERRORS = (WebSocketDisconnect, WebSocketDisconnected, RuntimeError, ConnectionError)

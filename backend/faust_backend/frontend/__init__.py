from faust_backend.frontend.bridge import FrontendBridge

_bridge: FrontendBridge | None = None


def get_bridge() -> FrontendBridge:
    global _bridge
    if _bridge is None:
        _bridge = FrontendBridge()
    return _bridge

"""staywake — keep macOS awake while named work is in flight, sleep again when it's done."""

from .state import Holder, default_state_path
from .api import holding, hold, release, status

__all__ = ["Holder", "default_state_path", "holding", "hold", "release", "status"]
__version__ = "0.1.0"

"""Chat model factory and provider configuration.

Public API:
    get_chat_model()  — Returns a LangChain ChatModel built from .env,
                        with optional local→external fallback.
"""

from .factory import get_chat_model

__all__ = ["get_chat_model"]

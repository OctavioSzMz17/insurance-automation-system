"""Logging filter that stamps every record with the current trace_id.

Phase D2.4. Attach this filter once at app startup; from then on, any
`logger.info(...)` / `.error(...)` etc. inside a request scope picks
up the trace_id automatically — no per-call boilerplate.

Format strings that include `%(trace_id)s` will render the real value;
formats that don't are unaffected (the filter just adds an attribute).

The filter never blocks records: it always returns True. Its only
side-effect is attribute injection.
"""

from __future__ import annotations

import logging

from .correlation import get_trace_id


class TraceIdLoggingFilter(logging.Filter):
    """Inject `trace_id` into every LogRecord.

    When no request scope is active (e.g. startup logs, background
    tasks), the placeholder string `-` is used so format strings
    rendering `%(trace_id)s` don't crash.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id() or "-"
        return True

"""Shared upload size limits.

The multipart request body is slightly larger than the file it contains, so
request-level checks must allow for multipart framing overhead. The route
still enforces the exact per-file limit while streaming the uploaded file.
"""

from __future__ import annotations

import os


MAX_UPLOAD_BYTES = int(os.getenv("OBITAL_MAX_UPLOAD_MB", "20")) * 1024 * 1024
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MAX_MULTIPART_REQUEST_BYTES = MAX_UPLOAD_BYTES + MULTIPART_OVERHEAD_BYTES

"""Run the Stale web UI:  python -m stale.web"""
from __future__ import annotations

import os
import uvicorn


def main() -> None:
    uvicorn.run(
        "stale.web.app:app",
        host=os.environ.get("STALE_HOST", "127.0.0.1"),
        port=int(os.environ.get("STALE_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()

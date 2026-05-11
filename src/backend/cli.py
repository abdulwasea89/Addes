"""Console entry point declared in pyproject.toml [project.scripts]."""

from __future__ import annotations


def main() -> None:
    """Run the development server.

    Importing uvicorn lazily keeps `backend --help` fast and avoids hard-failing
    when uvicorn is somehow unavailable in tooling contexts.
    """
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )

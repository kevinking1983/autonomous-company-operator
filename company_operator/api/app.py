"""Operator API entry point."""

from fastapi import FastAPI

from company_operator import __version__
from company_operator.config import get_settings

app = FastAPI(title="Autonomous Company Operator", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "version": __version__, "company_pack": settings.company_pack}


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()

"""Local development entrypoint."""

from awap.api.app import create_app


def run() -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "uvicorn is required to run the development server. Install it with `uv add uvicorn`."
        ) from error

    uvicorn.run(create_app(), host="127.0.0.1", port=8000)

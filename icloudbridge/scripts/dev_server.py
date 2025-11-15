"""Development server runner for iCloudBridge API."""

from icloudbridge.core.config import load_config
from icloudbridge.utils.logging import setup_logging


def run():
    """Run the development server with uvicorn."""
    import uvicorn

    config = load_config()
    setup_logging(config)

    uvicorn.run(
        "icloudbridge.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
        log_config=None,
    )


if __name__ == "__main__":
    run()

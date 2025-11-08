"""Development server runner for iCloudBridge API."""

import sys


def run():
    """Run the development server with uvicorn."""
    import uvicorn

    uvicorn.run(
        "icloudbridge.api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    run()

"""`python -m app`: run the server on HOST/PORT from the environment (used by the Docker image)."""

import uvicorn

from app.main import app

settings = app.state.settings
uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())

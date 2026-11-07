# Start-up entry point.
# The whole app still lives in server.py. This file only loads it and adds the
# optional GitHub sign-in routes, so nothing in server.py has to change.
#
# Start command:  uvicorn app:app --host 0.0.0.0 --port 7860

from server import app, APP_TITLE

import github_auth

github_auth.install(app, APP_TITLE)

__all__ = ['app']

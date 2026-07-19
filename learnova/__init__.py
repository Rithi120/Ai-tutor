"""Learnova application package.

The root :mod:`app` module remains the deployment and backwards-compatible
entry point. Domain code lives below this package so it can be tested and
extended without importing the web server.
"""

from .config import configure_app

__all__ = ["configure_app"]

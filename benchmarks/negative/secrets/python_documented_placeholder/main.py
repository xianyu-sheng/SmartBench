"""Negative fixture: an environment-variable placeholder.

Resembles: a hardcoded database password (hardcoded_secret).
Not a bug because: the value is an explicit placeholder for deployment config.
True positive if: a real password replaces it.
"""

DATABASE_PASSWORD = "YOUR_PASSWORD_HERE"

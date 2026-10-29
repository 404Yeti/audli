"""Compatibility import for local harnesses; canonical code is packaged in app.

Historical benchmark source remains available at its recorded Git revisions.
Aliasing the module also preserves monkeypatch/ContextVar identity in tests.
"""
import sys
from app.claims.assessment_claims import bridge as implementation
sys.modules[__name__] = implementation

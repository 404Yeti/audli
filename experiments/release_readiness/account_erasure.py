"""Local harness import; account erasure has one packaged implementation."""
import sys
from app.claims.release_readiness import account_erasure as implementation
sys.modules[__name__] = implementation

"""Compatibility shim for legacy imports expecting `skeletonizer_test`.

The active implementation lives in `skeletonizer1.py`.
"""

from skeletonizer1 import load_and_preprocess, skeletonize_zhang


def method_skeletonize_zhang(binary):
    """Legacy wrapper preserving the old function name."""
    return skeletonize_zhang(binary)

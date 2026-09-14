"""Static language mapping; launch policy lives in each adapter module."""

from . import javascript, python, rust

ADAPTERS = {"python": python, "javascript": javascript, "typescript": javascript, "rust": rust}


def get(language):
    return ADAPTERS[language]

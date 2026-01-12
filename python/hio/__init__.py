# -*- encoding: utf-8 -*-
"""
hio package - Minimal scheduler-only version for Pyodide
Excludes lmdb, falcon, and other C-extension dependencies
"""

__version__ = '0.6.19-minimal'

from .hioing import Mixin, HioError, ValidationError, VersionError

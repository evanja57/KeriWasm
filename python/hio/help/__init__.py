# -*- encoding: utf-8 -*-
"""
hio.help package - Minimal version for Pyodide
Excludes ogling (logging) and other optional modules
"""

# Skipping ogler initialization - not needed for scheduler
# from . import ogling
# ogler = ogling.initOgler(prefix='hio')

from .timing import Timer, MonoTimer, TimerError, RetroTimerError
# Excluded: from .decking import Deck
from .hicting import Hict, Mict

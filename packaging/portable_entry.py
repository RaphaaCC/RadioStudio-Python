"""Portable bundle entry point; keeps settings beside the executable."""

import builtins

builtins._RADIOSTUDIO_PORTABLE = True

from radiostudio.cli import main

raise SystemExit(main())

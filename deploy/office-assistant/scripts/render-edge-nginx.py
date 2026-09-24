#!/usr/bin/env python3
"""Deprecated: use render-edge-traefik.py. Kept as a thin wrapper."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

sys.argv[0] = str(Path(__file__).with_name("render-edge-traefik.py"))
runpy.run_path(str(Path(__file__).with_name("render-edge-traefik.py")), run_name="__main__")

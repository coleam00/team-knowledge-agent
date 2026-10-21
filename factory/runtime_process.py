"""Expose ordinary process ownership to the directly executable runtime host."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from process_ownership import ProcessTree as ProcessTree

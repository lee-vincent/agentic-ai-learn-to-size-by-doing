"""
experiment-cli/tests

Bootstraps sys.path so `from experiment_cli import ...` resolves regardless of how the test
runner discovers/imports this package (unittest's discover top-level-dir resolution and pytest's
rootdir insertion behave slightly differently -- this makes both work without extra flags, per the
build brief's exact invocation: `python3 -m unittest discover -s experiment-cli/tests`).
"""
import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_EXPERIMENT_CLI_DIR = os.path.dirname(_THIS_DIR)  # experiment-cli/ (contains experiment_cli/ pkg)
if _EXPERIMENT_CLI_DIR not in sys.path:
    sys.path.insert(0, _EXPERIMENT_CLI_DIR)

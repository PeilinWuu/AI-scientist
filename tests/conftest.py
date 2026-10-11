"""Pytest import path setup for local source-tree execution."""

from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def pytest_configure(config):
    """API integration tests must never recover/mutate a developer's live jobs."""
    config._scientist_previous_projects = os.environ.get('AI_SCIENTIST_PROJECTS_DIR')
    config._scientist_test_projects = tempfile.TemporaryDirectory(prefix='scientist-tests-')
    os.environ['AI_SCIENTIST_PROJECTS_DIR'] = config._scientist_test_projects.name


def pytest_unconfigure(config):
    previous = getattr(config, '_scientist_previous_projects', None)
    if previous is None:
        os.environ.pop('AI_SCIENTIST_PROJECTS_DIR', None)
    else:
        os.environ['AI_SCIENTIST_PROJECTS_DIR'] = previous
    directory = getattr(config, '_scientist_test_projects', None)
    if directory:
        directory.cleanup()

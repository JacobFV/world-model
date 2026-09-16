"""Tiny zero-dependency .env loader. Never logs or returns variable values.

Reads ``<project>/.env`` and ``$WORLD_MODEL_ENV_FILE`` (in that order) into the
process environment without overriding variables that are already set. Set
``WORLD_MODEL_NO_DOTENV=1`` to disable loading entirely.
"""
import os
from pathlib import Path
import re

PROJECT = Path(__file__).resolve().parents[1]
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


def parse_env(text):
    """Parse KEY=VALUE lines. Supports comments, ``export``, and quoted values."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        name, separator, value = line.partition('=')
        name = name.strip()
        if not separator or not _NAME.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            quote, value = value[0], value[1:-1]
            if quote == '"':
                value = re.sub(r'\\([nrt"\\])', lambda m: {'n': '\n', 'r': '\r', 't': '\t'}.get(m[1], m[1]), value)
        else:
            comment = re.search(r'\s#', value)
            if comment:
                value = value[:comment.start()].rstrip()
        values[name] = value
    return values


def load_env_file(path, environ=None):
    """Load one file; returns the names that were newly set (never values)."""
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for name, value in parse_env(path.read_text(encoding='utf-8')).items():
        if name not in environ:
            environ[name] = value
            loaded.append(name)
    return loaded


def load_project_env(project=None, environ=None):
    environ = os.environ if environ is None else environ
    if environ.get('WORLD_MODEL_NO_DOTENV'):
        return []
    loaded = load_env_file(Path(project or PROJECT) / '.env', environ)
    extra = environ.get('WORLD_MODEL_ENV_FILE')
    if extra:
        loaded += load_env_file(Path(extra).expanduser(), environ)
    return loaded

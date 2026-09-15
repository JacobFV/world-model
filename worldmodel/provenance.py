"""Capture actual source files as well as optional Git identity."""
import importlib.metadata
import platform
import subprocess
from pathlib import Path
from .util import file_hash
from . import _PACKAGE_ROOT, _LOADED_SOURCE_HASHES


def capture_code(project, entrypoint):
    project = Path(project).resolve()
    runtime = {str(path.relative_to(_PACKAGE_ROOT)): file_hash(path)
               for path in _PACKAGE_ROOT.rglob('*.py')}
    if runtime != _LOADED_SOURCE_HASHES:
        raise ValueError('Implementation changed after import; restart the Python process')
    if (project / 'worldmodel').resolve() != _PACKAGE_ROOT:
        raise ValueError('Project does not match loaded implementation')
    paths = list((project / 'worldmodel').rglob('*.py'))
    paths += list((project / 'data').glob('*/dataset.json'))
    paths += [project / name for name in ('pyproject.toml', 'uv.lock', 'requirements.txt')
              if (project / name).is_file()]
    files = {str(path.relative_to(project)): file_hash(path) for path in sorted(paths)}
    sources = {name: (project / name).read_text(encoding='utf-8') for name in files}

    def git(*args):
        try:
            result = subprocess.run(['git', '-C', str(project), *args], capture_output=True, text=True)
        except FileNotFoundError:
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    git_root = git('rev-parse', '--show-toplevel')
    in_repo = git_root is not None and Path(git_root).resolve() == project
    packages = sorted({(d.metadata['Name'], d.version) for d in importlib.metadata.distributions()
                       if d.metadata['Name']})
    return {'entrypoint': entrypoint, 'files': files, 'sources': sources,
            'git_commit': git('rev-parse', 'HEAD') if in_repo else None,
            'git_dirty': bool(git('status', '--porcelain')) if in_repo else None,
            'python': platform.python_version(), 'platform': platform.platform(),
            'packages': dict(packages)}

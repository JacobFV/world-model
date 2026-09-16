"""Capture actual source files as well as optional Git identity."""
import importlib.metadata
import platform
import subprocess
from pathlib import Path
from .util import file_hash
from . import _PACKAGE_ROOT, _LOADED_SOURCE_HASHES


def capture_code(project, entrypoint, dataset_root=None):
    project = Path(project).resolve()
    runtime = {str(path.relative_to(_PACKAGE_ROOT)): file_hash(path)
               for path in _PACKAGE_ROOT.rglob('*.py')}
    if runtime != _LOADED_SOURCE_HASHES:
        raise ValueError('Implementation changed after import; restart the Python process')
    if (project / 'worldmodel').resolve() != _PACKAGE_ROOT:
        raise ValueError('Project does not match loaded implementation')
    paths = list((project / 'worldmodel').rglob('*.py'))
    # A dataset build already captures its own declaration through dataset_code (and the
    # manifest's 'definition'), so the catalog-wide snapshot is redundant there. Including
    # it made an unrelated dataset's edit abort a long build at publish time.
    if dataset_root is None:
        paths += list((project / 'data').glob('*/dataset.json'))
    paths += [p for p in (_PACKAGE_ROOT / '_resources').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')]
    paths += [project / name for name in ('pyproject.toml', 'uv.lock', 'requirements.txt', 'LICENSE', 'DATA_RIGHTS.md')
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
    captured = {'entrypoint': entrypoint, 'files': files, 'sources': sources,
            'git_commit': git('rev-parse', 'HEAD') if in_repo else None,
            'git_dirty': bool(git('status', '--porcelain')) if in_repo else None,
            'python': platform.python_version(), 'platform': platform.platform(),
            'packages': dict(packages)}

    if dataset_root is not None:
        captured['dataset_code'] = capture_dataset_code(dataset_root)
    return captured


def capture_dataset_code(dataset_root):
    """Snapshot local Python/JSON resources with portable relative names."""
    root = Path(dataset_root).resolve()
    excluded = {'artifacts', 'scratch', 'manifests', '__pycache__', '.git', 'raw', 'processing', 'final', 'runs', 'samples'}
    paths = []
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if any(part in excluded for part in relative.parts) or relative.name in ('latest.json', 'raw-latest.json'):
            continue
        if path.is_dir() and path.is_symlink():
            raise ValueError('Dataset resource directories must not be symlinks')
        if path.suffix not in ('.py', '.json') or path.is_dir():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('Dataset code must not traverse symlinks outside its root')
        paths.append(path)
    if root / 'pipeline.py' not in paths or root / 'dataset.json' not in paths or not all(p.is_file() for p in paths):
        raise ValueError('Dataset code requires dataset.json and pipeline.py')
    if len(paths) > 1000 or sum(p.stat().st_size for p in paths) > 8 * 1024 * 1024:
        raise ValueError('Dataset code snapshot exceeds 1000 files or 8 MiB')
    sources = {str(path.relative_to(root)): path.read_bytes().decode('utf-8') for path in sorted(paths)}
    import hashlib
    files = {name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in sources.items()}
    return {'files': files, 'sources': sources}


def verify_code_snapshot(project, code, dataset_root=None):
    if any(not (Path(project) / name).is_file() or file_hash(Path(project) / name) != checksum
           for name, checksum in code['files'].items()):
        raise ValueError('Code changed during execution; rerun with stable code')
    if dataset_root is not None and capture_dataset_code(dataset_root) != code.get('dataset_code'):
        raise ValueError('Dataset code changed during execution; rerun with stable code')

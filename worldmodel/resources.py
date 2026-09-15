"""Read-only distribution resources and independently writable runtime data."""
from pathlib import Path
import os


def resource_roots(package=None,environ=None):
    package=Path(package or Path(__file__).resolve().parent)
    env=os.environ if environ is None else environ
    project=package.parent
    checkout=(project/'pyproject.toml').is_file() and (project/'data').is_dir()
    bundled=package/'_resources'
    default_data=project/'data' if checkout else Path(env.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'worldmodel'
    return {'catalog':project/'data' if checkout else bundled/'catalog',
            'fixtures':project/'tests/fixtures' if checkout else bundled/'fixtures',
            'examples':project/'examples' if checkout else bundled/'examples',
            'data':Path(env.get('WORLD_MODEL_DATA',default_data))}


def local_fixture(path,project,roots=None):
    """Resolve catalog demo paths in checkouts and installed distributions."""
    path=Path(path)
    if path.is_absolute():return path
    try:relative=path.relative_to('tests/fixtures')
    except ValueError:return Path(project)/path
    if '..' in relative.parts:raise ValueError('Fixture traversal is not allowed')
    return (roots or resource_roots())['fixtures']/relative

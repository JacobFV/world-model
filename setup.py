"""Bundle read-only catalog/fixtures/examples without copying acquired data."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


EXCLUDED = {'artifacts','scratch','manifests','raw','final','processing','runs','samples','__pycache__'}


def catalog_resource(source, dataset):
    relative = source.relative_to(dataset)
    return (not any(part in EXCLUDED for part in relative.parts)
            and source.name not in ('latest.json', 'raw-latest.json')
            and not any(path.is_symlink() for path in (source, *source.parents) if path != dataset.parent)
            and source.is_file()
            and (source.suffix in ('.py', '.json', '.md') or source.name == '.gitignore'))


class SourceWithResources(sdist):
    def make_release_tree(self, base_dir, files):
        root = Path(__file__).resolve().parent
        selected = [name for name in files
                    if not name.startswith('data/') or
                    (len(Path(name).parts) > 2 and catalog_resource(root/name, root/'data'/Path(name).parts[1]))]
        super().make_release_tree(base_dir, selected)


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root=Path(__file__).resolve().parent
        target=Path(self.build_lib)/'worldmodel/_resources'
        if target.exists():shutil.rmtree(target)
        for definition in sorted((root/'data').glob('*/dataset.json')):
            dataset=definition.parent
            for source in sorted(dataset.rglob('*')):
                relative=source.relative_to(dataset)
                if not catalog_resource(source,dataset):continue
                destination=target/'catalog'/dataset.name/relative
                destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(source,destination)
        for source_name,target_name in [('tests/fixtures','fixtures'),('examples','examples')]:
            for source in sorted((root/source_name).rglob('*')):
                if source.is_file() and source.suffix in ('.json','.jsonl','.csv','.py'):
                    destination=target/target_name/source.relative_to(root/source_name)
                    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
        for name in ('pyproject.toml','LICENSE','DATA_RIGHTS.md'):
            shutil.copy2(root/name,target/name)

setup(cmdclass={'build_py':BuildWithResources,'sdist':SourceWithResources})

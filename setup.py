"""Bundle read-only catalog/fixtures/examples without copying acquired data."""
from pathlib import Path
import shutil
from setuptools import setup
from setuptools.command.build_py import build_py


class BuildWithResources(build_py):
    def run(self):
        super().run()
        root=Path(__file__).resolve().parent
        target=Path(self.build_lib)/'worldmodel/_resources'
        for source in sorted((root/'data').glob('*/dataset.json')):
            destination=target/'catalog'/source.parent.name/'dataset.json'
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination)
        for source_name,target_name in [('tests/fixtures','fixtures'),('examples','examples')]:
            for source in sorted((root/source_name).rglob('*')):
                if source.is_file() and source.suffix in ('.json','.jsonl','.csv','.py'):
                    destination=target/target_name/source.relative_to(root/source_name)
                    destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,destination)
        for name in ('pyproject.toml','LICENSE','DATA_RIGHTS.md'):
            shutil.copy2(root/name,target/name)

setup(cmdclass={'build_py':BuildWithResources})

"""Version-controlled dataset declarations and dependency planning."""
from pathlib import Path
from .util import read_json, slug


class Catalog:
    def __init__(self, root):
        self.root = Path(root)

    def get(self, dataset):
        slug(dataset)
        path = self.root / dataset / 'dataset.json'
        if not path.is_file():
            raise ValueError(f'Unknown dataset: {dataset}')
        definition = read_json(path)
        if definition.get('id') != dataset or definition.get('schema_version') != 1:
            raise ValueError(f'Invalid dataset definition: {dataset}')
        if definition.get('kind') not in ('source', 'derived'):
            raise ValueError(f'Invalid dataset kind: {dataset}')
        dependencies = definition.get('dependencies', [])
        if not isinstance(dependencies, list) or len(dependencies) != len(set(dependencies)):
            raise ValueError(f'Invalid dependencies: {dataset}')
        for dependency in dependencies:
            slug(dependency)
        return definition

    def list(self):
        return [self.get(path.parent.name) for path in sorted(self.root.glob('*/dataset.json'))]

    def plan(self, target):
        order, active, visited = [], set(), set()

        def visit(dataset):
            if dataset in active:
                raise ValueError(f'Dependency cycle at {dataset}')
            if dataset in visited:
                return
            definition = self.get(dataset)
            active.add(dataset)
            for dependency in definition.get('dependencies', []):
                visit(dependency)
            active.remove(dataset)
            visited.add(dataset)
            order.append(dataset)

        visit(target)
        return order

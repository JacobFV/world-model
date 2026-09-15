"""Dataset declarations and explicit external/internal dependency planning."""
from pathlib import Path
import re
from .util import read_json, slug


_FIELD_TYPES = {'number', 'integer', 'string', 'boolean', 'object', 'array', 'null'}


def validate_stages(definition):
    stages = definition.get('stages')
    if not isinstance(stages, list) or not 1 <= len(stages) <= 100:
        raise ValueError('stages must contain 1..100 stage declarations')
    names = set()
    for stage in stages:
        allowed = {'id', 'entrypoint', 'depends_on', 'schema', 'validation', 'cache', 'retention'}
        if not isinstance(stage, dict) or set(stage) - allowed:
            raise ValueError('Unknown stage declaration fields')
        name = stage.get('id'); slug(name)
        if name in names: raise ValueError('Duplicate stage ID')
        names.add(name)
        if not isinstance(stage.get('entrypoint'), str) or not re.fullmatch(r'pipeline\.py:[A-Za-z_][A-Za-z0-9_]*', stage['entrypoint']):
            raise ValueError('Stage entrypoint must be pipeline.py:function')
        parents = stage.get('depends_on', [])
        if not isinstance(parents, list) or any(not isinstance(p, str) for p in parents) or len(set(parents)) != len(parents):
            raise ValueError('Stage depends_on must contain distinct stage IDs')
        for parent in parents: slug(parent)
        schema = stage.get('schema')
        if not isinstance(schema, dict) or set(schema) - {'format', 'required'} or schema.get('format') not in ('evidence_jsonl', 'jsonl'):
            raise ValueError('Stage schema requires evidence_jsonl or jsonl format')
        required = schema.get('required', {})
        if not isinstance(required, dict) or len(required) > 100 or any(not isinstance(k, str) or not k or not isinstance(v, str) or v not in _FIELD_TYPES for k, v in required.items()):
            raise ValueError('Invalid stage required field types')
        validation = stage.get('validation', {})
        if not isinstance(validation, dict) or set(validation) - {'allow_empty', 'max_rows'}:
            raise ValueError('Unknown stage validation fields')
        if type(validation.get('allow_empty', False)) is not bool:
            raise ValueError('Stage allow_empty must be boolean')
        limit = validation.get('max_rows', 100000)
        if type(limit) is not int or not 1 <= limit <= 1000000:
            raise ValueError('Stage max_rows must be in 1..1000000')
        if stage.get('cache', 'content') not in ('content', 'off'):
            raise ValueError('Invalid stage cache policy')
        if stage.get('retention', 'retain') not in ('retain', 'rebuildable'):
            raise ValueError('Invalid stage retention policy')
    if definition.get('output_stage') not in names:
        raise ValueError('output_stage must identify a declared stage')
    by_name = {s['id']: s for s in stages}
    for stage in stages:
        if set(stage.get('depends_on', [])) - names:
            raise ValueError('Stage references missing dependency')
    active, visited = set(), set()
    def visit(name):
        if name in active: raise ValueError('Stage dependency cycle')
        if name in visited: return
        active.add(name)
        for parent in by_name[name].get('depends_on', []): visit(parent)
        active.remove(name); visited.add(name)
    for name in by_name: visit(name)
    return by_name


class Catalog:
    def __init__(self, root):
        self.root = Path(root)

    def get(self, dataset):
        slug(dataset)
        path = self.root / dataset / 'dataset.json'
        if path.is_symlink(): raise ValueError('Dataset declaration must not be a symlink')
        if not path.is_file(): raise ValueError(f'Unknown dataset: {dataset}')
        definition = read_json(path)
        if definition.get('id') != dataset or type(definition.get('schema_version')) is not int or definition['schema_version'] not in (1, 2):
            raise ValueError(f'Invalid dataset definition: {dataset}')
        if definition.get('kind') not in ('source', 'derived'):
            raise ValueError(f'Invalid dataset kind: {dataset}')
        dependencies = definition.get('dependencies', [])
        if not isinstance(dependencies, list) or any(not isinstance(d, str) for d in dependencies) or len(dependencies) != len(set(dependencies)):
            raise ValueError(f'Invalid dependencies: {dataset}')
        for dependency in dependencies: slug(dependency)
        if not isinstance(definition.get('parameters', {}), dict): raise ValueError('Dataset parameters must be an object')
        if definition['schema_version'] == 2:
            validate_stages(definition)
            pipeline = path.parent / 'pipeline.py'
            if not pipeline.is_file() or pipeline.is_symlink():
                raise ValueError('Schema 2 dataset requires a local pipeline.py file')
        return definition

    def stage_plan(self, dataset, stage=None):
        definition = self.get(dataset)
        if definition['schema_version'] != 2:
            if stage is not None: raise ValueError('Named stages require dataset schema 2')
            return []
        by_name = validate_stages(definition)
        target = stage or definition['output_stage']
        if target not in by_name: raise ValueError('Unknown target stage')
        visited, order = set(), []
        def visit(name):
            if name in visited: return
            for parent in by_name[name].get('depends_on', []): visit(parent)
            visited.add(name); order.append(name)
        visit(target)
        return order

    def list(self):
        return [self.get(path.parent.name) for path in sorted(self.root.glob('*/dataset.json'))]

    def plan(self, target):
        order, active, visited = [], set(), set()
        def visit(dataset):
            if dataset in active: raise ValueError(f'Dependency cycle at {dataset}')
            if dataset in visited: return
            definition = self.get(dataset)
            active.add(dataset)
            for dependency in definition.get('dependencies', []): visit(dependency)
            active.remove(dataset); visited.add(dataset); order.append(dataset)
        visit(target)
        return order

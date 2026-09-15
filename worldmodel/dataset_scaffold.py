"""Create a self-contained dataset definition and trusted local pipeline module."""
import keyword
import os
from pathlib import Path
import shutil
import tempfile
from .util import atomic_json, slug

IGNORE = '/artifacts/\n/scratch/\n__pycache__/\n*.py[cod]\n*.sqlite*\n/.lock\n# Preserved pre-layout data, never executable dataset code.\n/raw/\n/final/\n/processing/\n/runs/\n/samples/\n/latest.json\n/raw-latest.json\n'


def create_dataset(catalog,dataset,*,kind,description,dependencies=(),entrypoint='pipeline.py:run'):
    slug(dataset)
    if kind not in ('source','derived'):raise ValueError('Invalid dataset kind')
    module,sep,function=entrypoint.partition(':')
    if module!='pipeline.py' or not sep or not function.isidentifier() or keyword.iskeyword(function):
        raise ValueError('Scaffold entrypoint must be pipeline.py:function')
    dependencies=list(dependencies)
    if len(dependencies)!=len(set(dependencies)):raise ValueError('Duplicate dependency')
    for dependency in dependencies:catalog.get(dependency)
    root=Path(catalog.root);root.mkdir(parents=True,exist_ok=True);destination=root/dataset
    if destination.exists():raise ValueError('Dataset already exists')
    stage='normalized' if kind=='source' else 'graph'
    definition={'id':dataset,'schema_version':2,'kind':kind,'description':description,
                'status':'requires_configuration','dependencies':dependencies,'parameters':{},
                'entrypoint':entrypoint,'output_stage':stage,
                'stages':[{'id':stage,'entrypoint':entrypoint,'depends_on':[],
                           'schema':{'format':'evidence_jsonl'},
                           'validation':{'allow_empty':False,'max_rows':100000},
                           'cache':'content','retention':'retain'}],
                'sampling':{'strategy':'derived' if kind=='derived' else 'blocked','format':'jsonl',
                            'max_rows':100,'max_sample_bytes':1048576,'max_download_bytes':1048576,
                            'max_uncompressed_bytes':1048576,'disk_budget_bytes':67108864,
                            'criteria':'First 100 records, at most 1 MiB',
                            'reason':'Configure a documented source URL and format before acquisition'}}
    temporary=Path(tempfile.mkdtemp(prefix='.new-dataset-',dir=root))
    try:
        atomic_json(temporary/'dataset.json',definition)
        (temporary/'pipeline.py').write_text(f'"""Dataset-specific processing for {dataset}."""\n\n\ndef {function}(context):\n    raise ValueError("Configure the dataset-specific transformation before running")\n')
        (temporary/'.gitignore').write_text(IGNORE)
        (temporary/'README.md').write_text(f'# {dataset}\n\n{description}\n\nImplement `pipeline.py` and its declared stage contract before running.\nCode, definitions, tests and compact manifests belong in Git.\nGenerated payloads live in `artifacts/`; disposable files live in `scratch/`.\n')
        (temporary/'tests').mkdir()
        (temporary/'tests/test_pipeline.py').write_text('"""Add source-shape and transformation acceptance tests here before implementation."""\n')
        os.rename(temporary,destination)
    finally:
        if temporary.exists():shutil.rmtree(temporary)
    return definition

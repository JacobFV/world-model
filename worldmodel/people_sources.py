"""Shared ontology declarations and compatibility access to dataset-local pipelines."""
from .source_helpers import run_local_pipeline

SOURCE_IDS = ('congress_people', 'openalex_people')

def schema():
    return {'entity_types': {'role': {'parent': 'entity'}},
            'relations': {'holds_role': {'domain': 'person', 'range': 'role'},
                          'role_in_organization': {'domain': 'role', 'range': 'organization'},
                          'role_affiliation': {'domain': 'role', 'range': 'organization'}},
            'variables': {}}


def normalize(context):
    dataset = context.definition['id']
    if dataset not in SOURCE_IDS:
        raise ValueError('No acquired source adapter: ' + dataset)
    return run_local_pipeline(context)

"""Compatibility view over explicitly reviewed contract evidence."""
from copy import deepcopy

def run(context):
    if context.raw_inputs:raise ValueError('Import documents and reviews into adjacent contract datasets')
    ref=context.input_ref('reviewed_obligations')
    for source in context.records('reviewed_obligations'):
        row=deepcopy(source);row['evidence']=[{'input':ref,'record_id':source['id']}]
        yield row

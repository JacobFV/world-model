'Agriculture census, Quick Stats and cropland data'
import json
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

def run(context):
    dataset = 'usda_agriculture'
    raise ValueError(f'{dataset}: no sample normalizer available; BEA, freight and USDA samples are unavailable')

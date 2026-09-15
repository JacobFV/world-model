'Historical AIS availability; no bulk daily download on laptop'
import json
import math
from worldmodel.util import digest
from worldmodel.source_helpers import SERIES, next_day, month_end

def run(context):
    dataset = 'marine_ais'
    raise ValueError('No acquired source adapter: ' + dataset)

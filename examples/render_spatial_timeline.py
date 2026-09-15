"""Render the fictional timeline with explicitly supplied illustrative rectangles.

Run from repository root: python3 -m examples.render_spatial_timeline /tmp/spatial.html
"""
import json
import sys
from pathlib import Path
from worldmodel.spatial_store import SpatialStore
from worldmodel.spatial_surfaces import render_spatial_surface


def generate(output):
    example=json.loads(Path(__file__).with_name('spatial-timeline.json').read_text())
    crs={'id':'LOCAL:fictional-example','kind':'cartesian','axes':['x','y'],'unit':'m'}
    def polygon(x0,x1):
        return {'type':'Polygon','coordinates':[[[x0,0],[x1,0],[x1,1],[x0,1],[x0,0]]],'crs':crs,'simplified':False}
    for cell,bounds in zip(example['world']['cells'],[(0,1),(1,2)]):
        cell['geometry']=polygon(*bounds)
        cell['coordinates']=[sum(bounds)/2,.5]
    for cell,bounds in zip(example['request']['events'][0]['events'][0]['children'],[(0,.5),(.5,1)]):
        cell['geometry']=polygon(*bounds)
        cell['coordinates']=[sum(bounds)/2,.5]
    with SpatialStore(':memory:') as store:
        store.initialize(example['world'],coordinate_system=example['coordinate_system'])
        result=store.evolve_timeline(example['request'])
    html=render_spatial_surface(result,{'field':'vector','component':0})
    Path(output).write_text(html)
    return result


if __name__=='__main__':
    generate(sys.argv[1] if len(sys.argv)>1 else '/tmp/worldmodel-spatial.html')

"""Bounded planar simple polygons and explicit, uniform rectangle refinement.

No reprojection, holes, spherical geometry or automatic topology inference.
Predicates use exact comparisons of binary floating-point arithmetic.
"""
from copy import deepcopy
import math
from .util import digest


def _point(p):
    if not isinstance(p, (list, tuple)) or len(p) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e12 for x in p):
        raise ValueError('Point requires two finite coordinates within +/-1e12')
    return p


def validate_crs(crs):
    if not isinstance(crs, dict) or set(crs) != {'id', 'kind', 'axes', 'unit'} or crs['kind'] != 'cartesian' or crs['axes'] != ['x', 'y'] or any(not isinstance(crs[k], str) or not crs[k] for k in ('id', 'unit')):
        raise ValueError('CRS requires explicit Cartesian id, x/y axes and unit')
    return crs


def _cross(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])


def _on(p, a, b):
    return _cross(a, b, p) == 0 and all(min(a[i], b[i]) <= p[i] <= max(a[i], b[i]) for i in (0, 1))


def _intersect(a, b, c, d, strict=False):
    x, y, z, w = _cross(a,b,c), _cross(a,b,d), _cross(c,d,a), _cross(c,d,b)
    if x*y < 0 and z*w < 0:
        return True
    return not strict and (_on(c,a,b) or _on(d,a,b) or _on(a,c,d) or _on(b,c,d))


def validate_polygon(geometry):
    if not isinstance(geometry, dict) or geometry.get('type') != 'Polygon':
        raise ValueError('Expected explicit Polygon geometry')
    validate_crs(geometry.get('crs'))
    if type(geometry.get('simplified')) is not bool:
        raise ValueError('Geometry must declare simplified boolean')
    if geometry['simplified']:
        info = geometry.get('simplification')
        if not isinstance(info, dict) or any(not isinstance(info.get(k), str) or not info[k] for k in ('method', 'source_geometry_hash')):
            raise ValueError('Declared simplification requires method and source_geometry_hash')
    rings = geometry.get('coordinates')
    if not isinstance(rings, list) or len(rings) != 1:
        raise ValueError('Only one polygon ring supported; holes require a separate implementation')
    ring = rings[0]
    if not isinstance(ring, list) or not 4 <= len(ring) <= 129:
        raise ValueError('Polygon requires 3..128 vertices and a closing point')
    for p in ring:
        _point(p)
    if ring[0] != ring[-1] or len({tuple(p) for p in ring[:-1]}) != len(ring)-1:
        raise ValueError('Polygon must be closed with distinct vertices')
    n = len(ring)-1
    for i in range(n):
        for j in range(i+1,n):
            if j == i+1 or (i == 0 and j == n-1):
                continue
            if _intersect(ring[i],ring[i+1],ring[j],ring[j+1]):
                raise ValueError('Polygon self intersection')
    if math.fsum(_cross(ring[0], ring[i], ring[i+1]) for i in range(1,n-1)) == 0:
        raise ValueError('Polygon has zero area')
    return deepcopy(geometry)


def polygon_bounds(geometry):
    ring = geometry['coordinates'][0]
    return [min(p[0] for p in ring), min(p[1] for p in ring), max(p[0] for p in ring), max(p[1] for p in ring)]


def _ring(geometry, crs):
    validate_polygon(geometry)
    if validate_crs(crs) != geometry['crs']:
        raise ValueError('CRS mismatch; reprojection is not implicit')
    return geometry['coordinates'][0]


def _inside(p, ring, boundary=True):
    inside = False
    for a,b in zip(ring, ring[1:]):
        if _on(p,a,b):
            return boundary
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:
            inside = not inside
    return inside


def point_in_polygon(point, geometry, *, crs, include_boundary=True):
    return _inside(_point(point), _ring(geometry,crs), include_boundary)


def validate_bounds(bounds):
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError('Bounds require xmin,ymin,xmax,ymax')
    _point(bounds[:2]); _point(bounds[2:])
    if bounds[0] > bounds[2] or bounds[1] > bounds[3]:
        raise ValueError('Bounds must be ordered')
    return bounds


def polygon_intersects_bbox(geometry, bounds, *, crs):
    ring = _ring(geometry,crs)
    x0,y0,x1,y1 = validate_bounds(bounds)
    box = [[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]
    return (any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in ring) or
            any(_inside(p,ring) for p in box) or
            any(_intersect(a,b,c,d) for a,b in zip(ring,ring[1:]) for c,d in zip(box,box[1:])))


def polygons_adjacent(first, second, *, crs):
    a,b = _ring(first,crs),_ring(second,crs)
    if any(_inside(p,b,False) for p in a) or any(_inside(p,a,False) for p in b):
        return False
    shared = False
    for p,q in zip(a,a[1:]):
        if _inside([(p[0]+q[0])/2,(p[1]+q[1])/2],b,False):
            return False
        for r,s in zip(b,b[1:]):
            if _intersect(p,q,r,s,True):
                return False
            if _cross(p,q,r) == _cross(p,q,s) == 0:
                axis = 0 if p[0] != q[0] else 1
                if min(max(p[axis],q[axis]),max(r[axis],s[axis])) > max(min(p[axis],q[axis]),min(r[axis],s[axis])):
                    shared = True
    # Coincident rings have coincident interiors, not adjacent interiors.
    if set(map(tuple,a)) == set(map(tuple,b)):
        return False
    return shared and not any(_inside([(p[0]+q[0])/2,(p[1]+q[1])/2],a,False) for p,q in zip(b,b[1:]))


def _rectangle(cell):
    geometry = validate_polygon(cell.get('geometry'))
    bounds = polygon_bounds(geometry)
    x0,y0,x1,y1 = bounds
    if len(geometry['coordinates'][0]) != 5 or set(map(tuple,geometry['coordinates'][0])) != {(x0,y0),(x1,y0),(x1,y1),(x0,y1)}:
        raise ValueError('Refinement/coarsening requires axis aligned rectangles')
    measure = cell.get('measure')
    if type(measure) not in (int,float) or not math.isfinite(measure) or measure <= 0:
        raise ValueError('Positive finite measure required')
    return bounds, geometry


def _rect(bounds, original):
    x0,y0,x1,y1 = bounds
    result = deepcopy(original)
    result['coordinates'] = [[[x0,y0],[x1,y0],[x1,y1],[x0,y1],[x0,y0]]]
    return result


def rectangle_refinement(cell, *, axis, parts, child_ids, edges):
    bounds, geometry = _rectangle(cell)
    if axis not in ('x','y') or type(parts) is not int or not 2 <= parts <= 100 or not isinstance(child_ids,list) or len(child_ids) != parts or len(set(child_ids)) != parts or cell['id'] in child_ids or not isinstance(edges,list) or len(edges)>10000:
        raise ValueError('Refinement requires axis, 2..100 unique children and explicit bounded edges')
    from .model import identifier
    for key in child_ids:
        identifier(key)
    i = 0 if axis == 'x' else 1
    children = []
    for k,key in enumerate(child_ids):
        box = list(bounds)
        box[i] = bounds[i]+(bounds[i+2]-bounds[i])*k/parts
        box[i+2] = bounds[i]+(bounds[i+2]-bounds[i])*(k+1)/parts
        measure = cell['measure']/parts if k<parts-1 else cell['measure']-math.fsum(c['measure'] for c in children)
        child = {'id':key,'measure':measure,'coordinates':[(box[0]+box[2])/2,(box[1]+box[3])/2],'geometry':_rect(box,geometry)}
        _rectangle(child)
        children.append(child)
    return {'event':{'type':'split','cell':cell['id'],'children':children,'edges':deepcopy(edges)}, 'assumptions':['Uniform integral allocation by declared support measure; explicit replacement topology.'], 'source_geometry_hash':digest(geometry), 'crs':deepcopy(geometry['crs'])}


def rectangle_coarsening(cells, *, target_id):
    if not isinstance(cells,list) or not 2 <= len(cells) <= 100 or len({c['id'] for c in cells}) != len(cells):
        raise ValueError('Coarsening requires 2..100 distinct rectangles')
    from .model import identifier
    identifier(target_id)
    shapes = [_rectangle(c) for c in cells]
    if any(g['crs'] != shapes[0][1]['crs'] for _,g in shapes):
        raise ValueError('CRS mismatch')
    bounds = [min(b[0] for b,_ in shapes),min(b[1] for b,_ in shapes),max(b[2] for b,_ in shapes),max(b[3] for b,_ in shapes)]
    for i,(a,_) in enumerate(shapes):
        for b,_ in shapes[i+1:]:
            if min(a[2],b[2])>max(a[0],b[0]) and min(a[3],b[3])>max(a[1],b[1]):
                raise ValueError('Rectangles overlap')
    area = math.fsum((b[2]-b[0])*(b[3]-b[1]) for b,_ in shapes)
    if area != (bounds[2]-bounds[0])*(bounds[3]-bounds[1]):
        raise ValueError('Rectangles must tile the complete target rectangle')
    cell = {'id':target_id,'measure':math.fsum(c['measure'] for c in cells),'geometry':_rect(bounds,shapes[0][1]),'coordinates':[(bounds[0]+bounds[2])/2,(bounds[1]+bounds[3])/2]}
    return {'event':{'type':'merge','cells':sorted(c['id'] for c in cells),'cell':cell},'assumptions':['Complete rectangular tiling; conservative store merge.'],'crs':deepcopy(shapes[0][1]['crs'])}


def refinement_candidates(world, criterion, *, limit=1000):
    if type(limit) is not int or not 1<=limit<=1000 or len(world['cells'])>100000:
        raise ValueError('Candidate work/selection bound exceeded')
    field = world['fields'].get(criterion.get('field'))
    value = criterion.get('value')
    if not field or field['unit'] != criterion.get('unit') or criterion.get('operator') not in ('gte','lte') or type(value) not in (int,float) or not math.isfinite(value):
        raise ValueError('Criterion requires known field, exact unit, finite threshold and gte/lte operator')
    matches=[]
    for cell in sorted(world['cells'],key=lambda c:c['id']):
        current=field['values'][cell['id']]
        if isinstance(current,list):
            component=criterion.get('component')
            if type(component) is not int or not 0<=component<len(current):
                raise ValueError('Vector criterion requires component index')
            current=current[component]
        if type(current) not in (int,float) or not math.isfinite(current):
            raise ValueError('Criterion input must be finite')
        if (current>=value if criterion['operator']=='gte' else current<=value):
            matches.append(cell['id'])
    return {'cells':matches[:limit],'truncated':len(matches)>limit,'criterion':deepcopy(criterion),'assumptions':['Explicit threshold selection; no automatic adaptive refinement.']}

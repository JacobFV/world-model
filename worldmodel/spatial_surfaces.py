"""Bounded, explicit spatial timeline adapter for standalone surfaces."""
from copy import deepcopy
import math
from .limits import resolve_limits
from .util import canonical, digest
from .surfaces import _time
from .spatial_geometry import validate_polygon


def _bound(value, maximum, name='Spatial view bound', limits=None, limit=None):
    if type(value) is not int or value < 1:
        raise ValueError(f'{name} must be in 1..{maximum}')
    if limits is not None:
        return limits.check(limit, value, name)
    if value > maximum:
        raise ValueError(f'{name} must be in 1..{maximum}')
    return value


def adapt_spatial_result(result, selection, *, max_cells=200, max_edges=500, max_frames=100, max_events=1000, materialization_ref=None, limits=None):
    """Select one scalar/vector transformation; preserve explicit active frame sets.

    Selection is {field} for scalar, {field,component:index} or
    {field,magnitude:true} for vectors. Missing geometry never creates locations.
    Store selections without timeline frames require an explicit `time` field.
    """
    limits=resolve_limits(limits)
    _bound(max_cells,limits.surface_max_cells,'max_cells',limits,'surface_max_cells');_bound(max_edges,limits.surface_max_cells,'max_edges',limits,'surface_max_cells')
    _bound(max_frames,limits.surface_max_frames,'max_frames',limits,'surface_max_frames');_bound(max_events,limits.surface_max_frames,'max_events',limits,'surface_max_frames')
    if not isinstance(result,dict):
        raise ValueError('Spatial input must be an object')
    limits.check('surface_max_input_bytes',len(canonical(result)),'Spatial input exceeds bound')
    if not isinstance(selection,dict) or set(selection)-{'field','component','magnitude'} or not isinstance(selection.get('field'),str):
        raise ValueError('Explicit field selection required')
    raw=result.get('frames')
    if raw is None:
        if 'time' not in result:raise ValueError('Store selection requires explicit time')
        raw=[result]
    if not isinstance(raw,list) or not raw:
        raise ValueError('Expected explicit frames')
    limits.check('surface_max_frames',len(raw),'Explicit frames')
    frames=[];previous=None
    coords=result.get('coordinate_system',{})
    components=result.get('component_frames',{})
    for frame in raw[:max_frames]:
        time=_time(frame['time'])
        if previous is not None and time<=previous:raise ValueError('Frames must have strictly increasing times')
        previous=time
        state=frame['state']
        if state is None:raise ValueError('Summary timeline frames carry no state; render a full or every_n history')
        field=state['fields'].get(selection['field'])
        if field is None:raise ValueError('Selected field missing from frame')
        vector=field.get('value_type','scalar')=='vector'
        if vector:
            if ('component' in selection)==('magnitude' in selection) or ('magnitude' in selection and selection['magnitude'] is not True):
                raise ValueError('Vector requires explicit component or magnitude selection')
        elif set(selection)!={'field'}:raise ValueError('Scalar field does not accept vector transformation')
        source=frame.get('source',result.get('source',{}));cells=[]
        all_cells=state['cells'];all_edges=state.get('edges',[])
        for cell in sorted(all_cells,key=lambda c:c['id'])[:max_cells]:
            value=field['values'][cell['id']]
            values=value if vector else [value]
            if not isinstance(values,list) or not values or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                raise ValueError('Finite scalar/vector field values required')
            if vector and 'component' in selection:
                index=selection['component']
                if type(index) is not int or not 0<=index<len(values):raise ValueError('Vector component outside frame')
                scalar=values[index]
            elif vector:scalar=math.hypot(*values)
            else:scalar=value
            if not math.isfinite(scalar):raise ValueError('Nonfinite selected magnitude')
            point=cell.get('coordinates')
            if point is not None and (not isinstance(point,list) or len(point)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in point)):
                raise ValueError('Finite explicit point coordinates required')
            geometry=cell.get('geometry')
            if geometry is not None:
                validate_polygon(geometry)
                if {k:v for k,v in geometry['crs'].items() if k!='id'}!=coords:raise ValueError('Geometry CRS conflicts with coordinate system')
            component_frame=components.get(selection['field']) if vector else None
            cells.append({**deepcopy(cell),'entity':cell['id'],'value':scalar,'vector':deepcopy(values) if vector else None,
                          'variable':selection['field'],'unit':field['unit'],'measure_unit':state['measure_unit'],
                          'time':frame['time'],'origin':'synthetic_scenario','evidence':[],'sources':[deepcopy(source)],'source':deepcopy(source),'component_frame':deepcopy(component_frame),
                          'arrows_supported': bool(vector and len(values)==2 and coords.get('kind')=='cartesian' and component_frame=={'kind':'fixed_global','axes':coords.get('axes')})})
        ids={c['entity'] for c in cells}
        edges=[{**deepcopy(e),'subject':e['source'],'object':e['target'],'predicate':'spatial topology','time':frame['time'],'provenance':deepcopy(source)} for e in sorted(all_edges,key=lambda e:e['id']) if e['source'] in ids and e['target'] in ids][:max_edges]
        frames.append({'time':frame['time'],'surface_time':time,'cells':cells,'edges':edges,'source':deepcopy(source),
                       'omitted_cells':len(all_cells)-len(cells),'omitted_edges':len(all_edges)-len(edges),
                       'missing_geometry':sum(not c.get('geometry') and c.get('coordinates') is None for c in cells),
                       'selection':deepcopy(frame.get('selection',{})),'phase':frame.get('phase','selected'),
                       'nodes':[{'entity':c['entity'],'labels':[c['entity']],'sources':[deepcopy(c)]} for c in cells]})
    events=deepcopy(result.get('events',[]))
    snapshots=[{'time':e['time'],'entity':'spatial:lifecycle','variable':'lifecycle','value':e,'unit':'event','origin':'synthetic_scenario','evidence':[],'sources':[e]} for e in events[:max_events]]
    return {'spatial_frames':frames,'snapshots':snapshots,'coordinate_system':deepcopy(coords),'component_frames':deepcopy(components),
            'spatial_selection':deepcopy(selection),'omitted_frames':len(raw)-len(frames),'omitted_events':max(0,len(events)-max_events),
            'materialization_ref':deepcopy(materialization_ref if materialization_ref is not None else {'result_hash':digest(result),'source':result.get('final_source',result.get('source',{}))}),
            'lifecycle':events[:max_events]}


def render_spatial_surface(result, selection, *, spec=None, materialization_ref=None, **bounds):
    """Return local interactive HTML using a spatial, temporal graph and audit panel."""
    from .surfaces import render_surface
    data=adapt_spatial_result(result,selection,materialization_ref=materialization_ref,**bounds)
    if spec is None:
        spec={'title':'Spatial timeline','interactive':True,'panels':[
            {'kind':'spatial','title':'Selected field and support geometry'},
            {'kind':'graph','source':'spatial','title':'Active support topology'}]}
        if data['snapshots']:
            spec['panels'].append({'kind':'table','title':'Dated lifecycle audit'})
    return render_surface(data,{**deepcopy(spec),'spatial_selection':deepcopy(selection),'spatial_bounds':{'max_cells':bounds.get('max_cells',200),'max_edges':bounds.get('max_edges',500),'max_frames':bounds.get('max_frames',100)}})

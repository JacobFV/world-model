"""Local DOM-only enhancement for declarative surfaces; no remote dependencies."""
import json
from collections import deque


def enhance(html, data, spec):
    from .surfaces import _rows, _select, _time, _limit, _number, _text
    panels=[];times={};numeric=set()
    for panel in spec['panels']:
        kind=panel['kind'];item={'spec':panel,'limit':_limit(panel,200 if kind=='graph' else 500)}
        if kind=='graph':
            nodes={}
            for record in data.get('records',[]):
                if record.get('kind')=='entity':
                    key=record.get('entity_id',record.get('id'))
                    nodes.setdefault(key,{'entity':key,'labels':[],'sources':[]})
                    nodes[key]['labels'].append(record.get('label',key));nodes[key]['sources'].append(record)
            edges=[r for r in data.get('records',[]) if r.get('kind')=='assertion' and 'object' in r]
            if 'seeds' in panel:
                neighbors={}
                for e in edges:
                    if e['subject'] in nodes and e['object'] in nodes:
                        neighbors.setdefault(e['subject'],set()).add(e['object'])
                        neighbors.setdefault(e['object'],set()).add(e['subject'])
                queue=deque(sorted(set(panel['seeds'])));seen=set(queue);keys=[]
                while queue and len(keys)<item['limit']:
                    key=queue.popleft();keys.append(key)
                    for neighbor in sorted(neighbors.get(key,())):
                        if neighbor not in seen:queue.append(neighbor);seen.add(neighbor)
            else:keys=sorted(nodes)[:item['limit']]
            edges.sort(key=lambda e:not(e['subject'] in keys and e['object'] in keys))
            item.update(nodes=[nodes[k] for k in keys],edges=edges[:500],omitted_nodes=len(nodes)-len(keys),omitted_edges=max(0,len(edges)-500))
        else:
            rows=_rows(data,panel)
            if kind=='map':
                axes=(panel.get('latitude','latitude'),panel.get('longitude','longitude'))
                rows=[r for r in rows if r.get('variable') in axes and ('entity' not in panel or r.get('entity')==panel['entity'])]
            else:rows=_select(rows,panel)
            if kind=='plot':
                if len({(r.get('entity'),r.get('variable'),r.get('unit')) for r in rows})!=1:
                    raise ValueError('interactive plot requires one entity/variable/unit series')
                for r in rows:_number(r['value'])
            item['rows']=[]
            for r in rows:
                timestamp=_time(r['time']);numeric.add(type(r['time']) in (int,float))
                times.setdefault(timestamp,r['time'])
                item['rows'].append({**r,'surface_time':timestamp})
        panels.append(item)
    if len(numeric)>1:raise ValueError('interactive timeline cannot mix numeric and date times')
    payload={'spec':spec,'panels':panels,'times':[{'value':t,'label':times[t]} for t in sorted(times)]}
    def fields(value):
        if isinstance(value,str):_text(value)
        elif isinstance(value,dict):
            for key,item in value.items():_text(key);fields(item)
        elif isinstance(value,list):
            for item in value:fields(item)
    fields(payload)
    encoded=json.dumps(payload,ensure_ascii=True,allow_nan=False,separators=(',',':'))
    encoded=encoded.replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    controls='''<aside id="surface-controls" aria-label="Surface controls"><label>Linked entity <select id="surface-entity"><option value="">All entities</option></select></label><label>Filter entities <input id="surface-filter" type="search" placeholder="Entity identifier"></label><button id="surface-clear" type="button">Clear selection</button><label>Time cutoff <input id="surface-time" type="range" min="0" step="1"></label><output id="surface-clock"></output><button id="surface-play" type="button">Play</button><button id="surface-export" type="button">Export view specification</button><details><summary>Panels and layers</summary><div id="surface-layers"></div></details><p>Values and tables retain observations through the time cutoff. Maps show the latest available coordinates per entity. Click a row, point, marker, or node to link panels and inspect sources.</p></aside><aside id="surface-inspector"><h2>Source details</h2><pre id="surface-source" aria-live="polite">Select a row, marker, or node.</pre></aside>'''
    style='''<style>#surface-controls{display:flex;gap:14px;align-items:center;flex-wrap:wrap;background:#eaf0f8;border:1px solid #cbd5e1;border-radius:12px;padding:18px;margin:16px 0}#surface-controls label{display:flex;gap:8px;align-items:center}#surface-controls p{width:100%;margin:0;font-size:12px;color:#475569}button,input,select{font:inherit}button{cursor:pointer;padding:7px 11px;background:white;border:1px solid #94a3b8;border-radius:6px}button:focus-visible,input:focus-visible,select:focus-visible,[role=button]:focus-visible{outline:3px solid #0891b2}#surface-layers>div{display:flex;gap:8px;align-items:center;margin:8px 0}#surface-inspector{margin:16px 0}#surface-source{max-width:none;max-height:250px;overflow:auto}.surface-selected{background:#dbeafe}.surface-click{cursor:pointer}.surface-panel-content{min-height:50px}[hidden]{display:none!important}</style>'''
    return html.replace('</head>',style+'</head>').replace('<main>',controls+'<main>').replace('</body>','<script id="surface-data" type="application/json">'+encoded+'</script><script>'+_SCRIPT+'</script></body>')


_SCRIPT=r'''
(() => {
'use strict';
const payload=JSON.parse(document.getElementById('surface-data').textContent);
const main=document.querySelector('main'), entity=document.getElementById('surface-entity');
const filter=document.getElementById('surface-filter'), slider=document.getElementById('surface-time');
const clock=document.getElementById('surface-clock'), source=document.getElementById('surface-source');
const layers=document.getElementById('surface-layers'), play=document.getElementById('surface-play');
const panels=payload.panels.map((p,i)=>({...p,index:i,visible:p.spec.visible!==false}));
const times=payload.times; let timer=null;
const text=v=>typeof v==='string'?v:JSON.stringify(v===undefined?null:v);
const el=(tag,value)=>{const node=document.createElement(tag);if(value!==undefined)node.textContent=text(value);return node;};
const svg=(tag,attrs)=>{const node=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs||{}))node.setAttribute(k,String(v));return node;};
const button=(label,action)=>{const b=el('button',label);b.type='button';b.addEventListener('click',action);return b;};
const identifiers=[...new Set(panels.flatMap(p=>(p.rows||p.nodes||[]).map(r=>r.entity)).filter(v=>typeof v==='string'))].sort();
for(const id of identifiers){const option=el('option',id);option.value=id;entity.append(option);}
slider.max=String(Math.max(0,times.length-1));slider.value=slider.max;slider.disabled=!times.length;play.disabled=times.length<2;
const chosen=id=>!entity.value||id===entity.value;
const matches=id=>text(id).toLowerCase().includes(filter.value.toLowerCase());
const cutoff=()=>times.length?times[Number(slider.value)].value:Infinity;
const inspect=row=>{source.textContent=JSON.stringify(row,null,2);if(row.entity&&identifiers.includes(row.entity))entity.value=row.entity;render();};
function clickable(node,row){node.classList.add('surface-click');node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label','Inspect '+text(row.entity||row.subject||'source'));node.addEventListener('click',event=>{if(!event.target.closest('details'))inspect(row);});node.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();inspect(row);}});}
function notice(host,count,noun='rows'){if(count)host.append(el('p','Truncated: '+count+' '+noun+' omitted.'));}
function table(host,rows){const wrap=el('div');wrap.className='table-scroll';const tab=el('table'),head=el('thead'),header=el('tr');for(const f of ['time','entity','variable','value','unit','origin','provenance'])header.append(el('th',f));head.append(header);tab.append(head);const body=el('tbody');for(const row of rows){const tr=el('tr');if(entity.value===row.entity)tr.className='surface-selected';for(const f of ['time','entity','variable','value','unit','origin'])tr.append(el('td',row[f]));const td=el('td'),details=el('details');details.append(el('summary','Sources'));details.append(el('pre',JSON.stringify({record_id:row.record_id,evidence:row.evidence,sources:row.sources},null,2)));td.append(details);tr.append(td);clickable(tr,row);body.append(tr);}tab.append(body);wrap.append(tab);host.append(wrap);}
function chart(host,rows){const canvas=svg('svg',{viewBox:'0 0 800 420',role:'img','aria-label':'Selected time series'});const sorted=[...rows].sort((a,b)=>a.surface_time-b.surface_time);const xs=sorted.map(r=>r.surface_time),ys=sorted.map(r=>r.value);const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);const points=sorted.map(r=>[60+(r.surface_time-xmin)/(xmax-xmin||1)*700,350-(r.value-ymin)/(ymax-ymin||1)*300]);canvas.append(svg('path',{d:'M60 40V350H770',fill:'none',stroke:'#64748b'}));canvas.append(svg('polyline',{points:points.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#2563eb','stroke-width':2}));points.forEach(([x,y],i)=>{const dot=svg('circle',{cx:x,cy:y,r:5,fill:'#2563eb'});const title=svg('title');title.textContent=text(sorted[i].time)+' · '+text(sorted[i].value)+' '+text(sorted[i].unit);dot.append(title);clickable(dot,sorted[i]);canvas.append(dot);});const label=svg('text',{x:60,y:20});label.textContent=text(rows[0].variable)+' ('+text(rows[0].unit)+')';canvas.append(label);const range=svg('text',{x:60,y:395});range.textContent='Time: '+text(sorted[0].time)+' to '+text(sorted[sorted.length-1].time)+'; values '+ymin+' to '+ymax;canvas.append(range);host.append(canvas);table(host,rows);}
function map(host,p,rows){const lat=p.spec.latitude||'latitude',lon=p.spec.longitude||'longitude';const pairs=new Map();for(const row of rows){const key=JSON.stringify([row.entity,row.time]);if(!pairs.has(key))pairs.set(key,{entity:row.entity,time:row.time,surface_time:row.surface_time,sources:[]});const pair=pairs.get(key);pair[row.variable]=row.value;pair.sources.push(row);}const latest=new Map();for(const pair of pairs.values()){if(pair[lat]===undefined||pair[lon]===undefined)continue;if(!latest.has(pair.entity)||latest.get(pair.entity).surface_time<pair.surface_time)latest.set(pair.entity,pair);}const all=[...latest.values()],shown=all.slice(0,p.limit);if(!shown.length){host.append(el('p','No coordinates at this time cutoff.'));return;}const lats=shown.map(r=>r[lat]),lons=shown.map(r=>r[lon]),south=Math.min(...lats),north=Math.max(...lats),west=Math.min(...lons),east=Math.max(...lons);const canvas=svg('svg',{viewBox:'0 0 800 420',role:'img','aria-label':'Coordinate grid'});canvas.append(svg('rect',{x:40,y:40,width:720,height:320,fill:'#eff6ff',stroke:'#94a3b8'}));for(const row of shown){const x=60+(row[lon]-west)/(east-west||1)*680,y=340-(row[lat]-south)/(north-south||1)*280;const dot=svg('circle',{cx:x,cy:y,r:entity.value===row.entity?9:6,fill:entity.value===row.entity?'#c2410c':'#2563eb'});const title=svg('title');title.textContent=row.entity+' · '+text(row.time)+' · '+row[lat]+', '+row[lon]+' degrees';dot.append(title);clickable(dot,row);canvas.append(dot);}const label=svg('text',{x:40,y:20});label.textContent='Latitude / longitude (degrees); coordinate grid, no basemap';canvas.append(label);host.append(canvas);host.append(el('p','Latest available coordinate per entity through cutoff; extent longitude '+west+' to '+east+', latitude '+south+' to '+north+' degrees.'));notice(host,all.length-shown.length,'coordinate pairs');table(host,shown.flatMap(r=>r.sources));}
function graph(host,p){host.append(el('p','Static supplied topology; abstract layout, not geographic. Time playback does not infer historical graph state.'));const nodes=p.nodes.filter(n=>matches(n.entity)),positions=new Map(nodes.map((n,i)=>[n.entity,[400+280*Math.cos(i*2*Math.PI/Math.max(1,nodes.length)),210+150*Math.sin(i*2*Math.PI/Math.max(1,nodes.length))]]));const canvas=svg('svg',{viewBox:'0 0 800 420',role:'img','aria-label':'Entity topology'});let unresolved=0;for(const edge of p.edges){if(!positions.has(edge.subject)||!positions.has(edge.object)){unresolved++;continue;}const [x1,y1]=positions.get(edge.subject),[x2,y2]=positions.get(edge.object);const line=svg('line',{x1,y1,x2,y2,stroke:'#94a3b8','stroke-width':2});const title=svg('title');title.textContent=text(edge.predicate);line.append(title);clickable(line,edge);canvas.append(line);}for(const row of nodes){const [x,y]=positions.get(row.entity);const dot=svg('circle',{cx:x,cy:y,r:entity.value===row.entity?11:7,fill:entity.value===row.entity?'#c2410c':'#2563eb',opacity:chosen(row.entity)?1:.35});clickable(dot,row);canvas.append(dot);const label=svg('text',{x:x+12,y});label.textContent=row.labels.join(' / ');canvas.append(label);}host.append(canvas);notice(host,p.omitted_nodes,'nodes');notice(host,p.omitted_edges,'edge references');if(unresolved)host.append(el('p',unresolved+' edge references have unresolved or filtered endpoints.'));const details=el('details');details.append(el('summary','Node and edge provenance'));details.append(el('pre',JSON.stringify({nodes:p.nodes,edges:p.edges},null,2)));host.append(details);}
function render(){clock.textContent=times.length?text(times[Number(slider.value)].label):'No temporal rows';main.replaceChildren();for(const p of panels){const section=el('section');section.hidden=!p.visible;section.dataset.panel=String(p.index);section.append(el('h2',p.spec.title||p.spec.kind));const content=el('div');content.className='surface-panel-content';section.append(content);main.append(section);if(!p.visible)continue;if(p.spec.kind==='graph'){graph(content,p);continue;}const rows=p.rows.filter(r=>r.surface_time<=cutoff()&&chosen(r.entity)&&matches(r.entity));if(!rows.length){content.append(el('p','No matching observations through this time cutoff.'));continue;}if(p.spec.kind==='map'){map(content,p,rows);continue;}if(p.spec.kind==='value'){const row=rows[rows.length-1],value=el('div',row.value);value.className='scalar';const unit=el('span',row.unit);unit.className='scalar-unit';value.append(unit);content.append(value);content.append(el('p',text(row.entity)+' · '+text(row.variable)+' · '+text(row.time)+' · Origin: '+text(row.origin)));clickable(value,row);table(content,[row]);notice(content,rows.length-1);continue;}const shown=rows.slice(0,p.limit);if(p.spec.kind==='plot')chart(content,shown);else table(content,shown);notice(content,rows.length-shown.length);}}
function layerControls(){layers.replaceChildren();panels.forEach((p,i)=>{const row=el('div'),label=el('label'),check=el('input');check.type='checkbox';check.checked=p.visible;check.addEventListener('change',()=>{p.visible=check.checked;render();});label.append(check,document.createTextNode(p.spec.title||p.spec.kind));row.append(label);const move=button('Move earlier',()=>{[panels[i-1],panels[i]]=[panels[i],panels[i-1]];layerControls();render();});move.disabled=i===0;row.append(move);layers.append(row);});}
entity.addEventListener('change',render);filter.addEventListener('input',render);slider.addEventListener('input',render);
document.getElementById('surface-clear').addEventListener('click',()=>{entity.value='';filter.value='';source.textContent='Select a row, marker, or node.';render();});
play.addEventListener('click',()=>{if(timer){clearInterval(timer);timer=null;play.textContent='Play';return;}if(Number(slider.value)>=times.length-1)slider.value='0';render();play.textContent='Pause';timer=setInterval(()=>{if(Number(slider.value)>=times.length-1){clearInterval(timer);timer=null;play.textContent='Play';return;}slider.value=String(Number(slider.value)+1);render();},750);});
document.getElementById('surface-export').addEventListener('click',()=>{const spec={...payload.spec,panels:panels.map(p=>({...p.spec,visible:p.visible}))};const url=URL.createObjectURL(new Blob([JSON.stringify(spec,null,2)],{type:'application/json'}));const link=el('a');link.href=url;link.download='surface-spec.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
layerControls();render();
})();
'''

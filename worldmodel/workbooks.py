"""Bounded XLSX row extraction with explicit physical locators and metadata."""
import re
from xml.etree import ElementTree as ET


def xlsx_rows(archive, config):
    budget=config['max_uncompressed_bytes']; consumed=0
    def xml(member):
        nonlocal consumed
        info=archive.getinfo(member)
        if consumed+info.file_size>budget:raise ValueError('XLSX XML exceeds cumulative uncompressed budget')
        content=archive.read(member); consumed+=len(content)
        if re.search(br'<!\s*(DOCTYPE|ENTITY)',content.replace(b'\x00',b''),re.I):raise ValueError('XLSX DTD/entities are forbidden')
        return ET.fromstring(content)
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    names=archive.namelist()
    if len(names)!=len(set(names)):raise ValueError('Duplicate XLSX archive members')
    strings=[]
    if 'xl/sharedStrings.xml' in names:
        strings=[''.join(t.text or '' for t in e.findall('.//s:t',ns)) for e in xml('xl/sharedStrings.xml').findall('s:si',ns)]
    sheet=config.get('archive_member','xl/worksheets/sheet1.xml')
    rows={}; cells={}
    for fallback,element in enumerate(xml(sheet).findall('.//s:row',ns),1):
        n=int(element.get('r',fallback))
        if n<1 or n in rows:raise ValueError('Invalid or duplicate worksheet row')
        values={}
        for cell in element.findall('s:c',ns):
            ref=cell.get('r','');match=re.fullmatch(r'([A-Z]+)([1-9][0-9]*)',ref)
            if not match or int(match[2])!=n or ref in cells:raise ValueError('Invalid or duplicate worksheet cell')
            value=cell.find('s:v',ns); text=value.text if value is not None else ''.join(t.text or '' for t in cell.findall('.//s:t',ns))
            if cell.get('t')=='s':
                if not text or not text.isdigit() or int(text)>=len(strings):raise ValueError('Invalid shared string reference')
                text=strings[int(text)]
            cells[ref]=text; values[match[1]]=text
        rows[n]=values
    header_n=config.get('header_row',1)
    if type(header_n) is not int or header_n<1 or header_n not in rows:raise ValueError('Missing header_row')
    header={k:v for k,v in rows[header_n].items() if v not in (None,'')}
    if not header or len(set(header.values()))!=len(header):raise ValueError('Missing or duplicate XLSX headers')
    columns=config.get('columns',header)
    if not isinstance(columns,dict) or not columns or any(k not in header or not isinstance(v,str) or not v for k,v in columns.items()):raise ValueError('Invalid selected columns')
    if len(set(columns.values()))!=len(columns) or {'_source','_context'} & (set(header.values())|set(columns.values())):raise ValueError('Duplicate or reserved XLSX header')
    context=config.get('context_cells',{})
    if not isinstance(context,dict) or len(context)>100 or any(not isinstance(v,str) for v in context.values()) or len(set(context.values()))!=len(context):raise ValueError('Invalid or duplicate context cells')
    if any(not isinstance(k,str) or not k or k in columns.values() or k in ('_source','_context') or not isinstance(v,str) or v not in cells for k,v in context.items()):raise ValueError('Missing or colliding context cell')
    metadata={k:cells[v] for k,v in context.items()}
    start=config.get('start_row',header_n+1);end=config.get('end_row',max(rows))
    if type(start) is not int or type(end) is not int or start<=header_n or end<start:raise ValueError('Invalid worksheet row range')
    stop=config.get('stop_when_blank')
    if stop is not None and stop not in columns.values():raise ValueError('Unknown footer stop column')
    predicates=config.get('stop_when',{})
    if not isinstance(predicates,dict) or any(k not in columns.values() or not isinstance(v,str) for k,v in predicates.items()):raise ValueError('Invalid footer condition')
    for n,values in sorted(rows.items()):
        if n<start or n>end:continue
        result={name:values.get(column) for column,name in columns.items()}
        if stop is not None and result[stop] in (None,''):break
        if any(re.fullmatch(pattern,str(result[field] or '')) for field,pattern in predicates.items()):break
        if not any(v not in (None,'') for v in result.values()):continue
        result['_source']={'member':sheet,'row':n}
        if metadata:result['_context']=dict(metadata)
        yield result

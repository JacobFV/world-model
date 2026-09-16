"""Offline fixture test: Gazetteer ZIP + tiny polygon shapefile ZIP -> normalized entities and geometry stage."""
import io
import shutil
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name

GAZ = ('USPS\tGEOID\tANSICODE\tNAME\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG      \n'
       'CA\t06075\t00277302\tSan Francisco County\t121485107\t479107241\t46.906\t184.984\t37.727239\t-123.032229        \n')


def shapefile_zip(path, name, fields, records):
    """Write a minimal polygon shapefile + DBF into a ZIP (one ring per record)."""
    shp = io.BytesIO()
    bodies = []
    for number, (_, ring) in enumerate(records, 1):
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        content = struct.pack('<i4dii', 5, min(xs), min(ys), max(xs), max(ys), 1, len(ring)) + struct.pack('<i', 0)
        content += b''.join(struct.pack('<2d', *p) for p in ring)
        bodies.append(struct.pack('>ii', number, len(content) // 2) + content)
    length = (100 + sum(len(b) for b in bodies)) // 2
    shp.write(struct.pack('>i20xi', 9994, length) + struct.pack('<ii4d4d', 1000, 5, 0, 0, 0, 0, 0, 0, 0, 0))
    for body in bodies:
        shp.write(body)
    dbf = io.BytesIO()
    record_length = 1 + sum(size for _, size in fields)
    dbf.write(struct.pack('<B3BIHH20x', 3, 124, 1, 1, len(records), 32 + 32 * len(fields) + 1, record_length))
    for field, size in fields:
        dbf.write(field.encode().ljust(11, b'\x00') + b'C' + b'\x00' * 4 + bytes([size, 0]) + b'\x00' * 14)
    dbf.write(b'\r')
    for values, _ in records:
        dbf.write(b' ' + b''.join(str(v).encode().ljust(size) for v, (_, size) in zip(values, fields)))
    dbf.write(b'\x1a')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(name + '.shp', shp.getvalue())
        archive.writestr(name + '.dbf', dbf.getvalue())
        archive.writestr(name + '.cpg', 'UTF-8')


class CensusGeographyFullTest(unittest.TestCase):
    def test_gazetteer_state_dbf_and_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            for name in ('dataset.json', 'pipeline.py', 'helpers.py'):
                shutil.copy2(HERE / name, catalog / name)
            gaz = tmp / 'gaz.zip'
            with zipfile.ZipFile(gaz, 'w') as archive:
                archive.writestr('2024_Gaz_counties_national.txt', GAZ)
            ring = [(-122.5, 37.7), (-122.5, 37.8), (-122.4, 37.8), (-122.4, 37.7), (-122.5, 37.7)]
            shapefile_zip(tmp / 'county.zip', 'cb_2024_us_county_500k', [('GEOID', 5), ('NAME', 20)],
                          [(('06075', 'San Francisco'), ring)])
            shapefile_zip(tmp / 'state.zip', 'cb_2024_us_state_500k', [('GEOID', 2), ('NAME', 20), ('STUSPS', 2)],
                          [(('06', 'California', 'CA'), ring)])
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / n)} for n in ('gaz.zip', 'county.zip', 'state.zip')],
                                {'publisher': 'fixture'}, complete=True)
            runner = Runner(Catalog(tmp / 'catalog'), store, PROJECT)
            records = {r['id']: r for r in store.records(runner.run(DATASET))}
            geometry = {r['id']: r for r in store.records(runner.run(DATASET, stage='geometry'))}
        county = records['tiger:entity:geo:US:county:06075']
        self.assertEqual((county['entity_type'], county['label']), ('county', 'San Francisco County'))
        self.assertEqual(records['tiger:within:geo:US:county:06075']['object'], 'geo:US:state:06')
        self.assertEqual(records['tiger24:county:06075:latitude']['value'], 37.727239)
        self.assertEqual(records['tiger24:county:06075:land_area']['unit'], 'm2')
        self.assertEqual(records['tiger:entity:geo:US:state:06']['attributes']['stusps'], 'CA')  # from DBF (no state Gazetteer)
        shape = geometry['geom:county:06075']
        self.assertEqual(shape['rings'][0][:2], [-12250000, 3770000])
        from importlib import util
        spec = util.spec_from_file_location('geo_helpers', HERE / 'helpers.py')
        helpers = util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        self.assertEqual(helpers.decode_ring(shape['rings'][0]), ring)


if __name__ == '__main__':
    unittest.main()

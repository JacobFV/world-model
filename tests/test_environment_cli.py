import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from worldmodel.environment_cli import execute
from tests import test_materialize


class EnvironmentCliTests(unittest.TestCase):
    def test_initially_terminal_episode_publishes_without_executing_actions(self):
        fixture=test_materialize.MaterializeTests();fixture.setUp()
        try:
            config={'materialization':fixture.request,'step_seconds':2,
                    'environment':{'actions':{},'observations':{'stock':{'entity':'asset:a','variable':'stock','unit':'unit'}},
                                   'reward':{'terms':[]},'max_steps':2,
                                   'termination':{'selector':{'entity':'asset:a','variable':'stock','unit':'unit'},'operator':'gte','value':0}},
                    'actions':[{}]}
            path=fixture.root/'env.json';path.write_text(json.dumps(config))
            args=SimpleNamespace(command='environment',reference='test',request=path,dataset='test_episode')
            from worldmodel.environments import TemporalEvaluator
            def adapter(store,ref,request,step_seconds,**kwargs):return TemporalEvaluator(store,ref,request,step_seconds,fixture.registry,**kwargs)
            with patch('worldmodel.environments.TemporalEvaluator',side_effect=adapter):
                result=execute(args,None,fixture.store,test_materialize.PROJECT,lambda value,store:fixture.graph)
            self.assertEqual(len(result['frames']),1)
            self.assertTrue(result['frames'][0]['info']['terminated'])
            fixture.store.verify(result['artifact'])
        finally:fixture.tearDown()

    def test_surface_does_not_duplicate_exported_snapshots(self):
        fixture=test_materialize.MaterializeTests();fixture.setUp()
        try:
            from worldmodel.materialize import materialize
            view=materialize(fixture.store,fixture.graph,fixture.request,fixture.registry)
            path=fixture.root/'surface.json';path.write_text(json.dumps({'panels':[{'kind':'table','entity':'asset:a','variable':'stock'}]}))
            args=SimpleNamespace(command='surface',reference='test',spec=path,dataset='test_surface',output=fixture.root/'surface.html')
            from worldmodel.surfaces import render_surface
            seen=[]
            def capture(data,spec):seen.append(data);return render_surface(data,spec)
            with patch('worldmodel.surfaces.render_surface',side_effect=capture):
                result=execute(args,None,fixture.store,test_materialize.PROJECT,lambda value,store:view['artifact'])
            self.assertEqual(len(seen[0]['snapshots']),3)
            self.assertFalse(any(r['kind']=='observation' for r in seen[0]['records']))
            fixture.store.verify(result['artifact'])
        finally:fixture.tearDown()

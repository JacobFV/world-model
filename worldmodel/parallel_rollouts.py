"""Bounded spawned numerical episodes; deterministic ordering and no retries."""
from copy import deepcopy
import importlib
import json
import math
import multiprocessing
import re
import time
from .util import canonical, digest


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high: raise ValueError(f'{name} must be integer in {low}..{high}')


def _error(episode, status, message, transitions=0):
    return {'id': episode['id'], 'seed': episode['seed'], 'status': status,
            'error': str(message)[:100], 'transitions': transitions}


def _live(env):
    seen = set()
    while env is not None and id(env) not in seen:
        seen.add(id(env))
        evaluator = getattr(env, 'evaluate', None)
        if getattr(env, 'live', False) or getattr(evaluator, 'live', False) or getattr(evaluator, 'backend', None) is not None:
            return True
        env = getattr(env, '_env', None)
    return False


def _worker(connection, factory, episode, max_bytes):
    env = None; count = 0; result = None
    try:
        module, name = factory.split(':')
        create = getattr(importlib.import_module(module), name)
        env = create(deepcopy(episode['config']))
        if _live(env): raise ValueError('Numerical rollout workers reject live external backends')
        observation, info = env.reset(seed=episode['seed'])
        frames = [{'observation': observation, 'info': info}]
        if len(canonical(frames)) > max_bytes: raise OverflowError('Episode result exceeds byte budget')
        rewards = []
        if not (info.get('terminated') or info.get('truncated')):
            for action in episode['actions']:
                observation, reward, terminated, truncated, info = env.step(deepcopy(action))
                count += 1
                if type(reward) not in (int, float) or not math.isfinite(reward): raise ValueError('Reward must be finite')
                rewards.append(reward)
                frames.append({'observation': observation, 'reward': reward, 'terminated': bool(terminated),
                               'truncated': bool(truncated), 'info': info})
                if len(canonical(frames)) > max_bytes: raise OverflowError('Episode result exceeds byte budget')
                if terminated or truncated: break
        try: total = math.fsum(rewards)
        except OverflowError as exc: raise ValueError('Episode return is not finite') from exc
        if not math.isfinite(total): raise ValueError('Episode return is not finite')
        result = {'id': episode['id'], 'seed': episode['seed'], 'status': 'completed',
                  'transitions': count, 'return': total, 'frames': frames}
        if len(canonical(result)) > max_bytes: raise OverflowError('Episode result exceeds byte budget')
    except OverflowError as exc: result = _error(episode, 'oversize', exc, count)
    except Exception as exc: result = _error(episode, 'error', exc, count)
    finally:
        try:
            if env is not None and hasattr(env, 'close'): env.close()
        except Exception as exc: result = _error(episode, 'error', 'Environment cleanup failed: ' + str(exc), count)
        try: connection.send_bytes(canonical(result))
        except (BrokenPipeError, EOFError, OSError): pass
        connection.close()


def _stop(process):
    if process.is_alive(): process.terminate()
    process.join(timeout=.3)
    if process.is_alive(): process.kill(); process.join(timeout=.3)
    if process.is_alive(): raise RuntimeError('Worker could not be reaped')
    process.close()


def run_episodes(episodes, *, factory, mode='spawn', workers=2, max_transitions=100,
                 timeout_seconds=5, max_result_bytes=1048576, journal=None, quota='rollouts', reservation_key=None):
    """Run trusted serializable factories, with one fresh spawned process per episode.

    Factory is module:function accepting a JSON config and returning reset/step/close
    environment. Sequential mode uses the same isolated workers one at a time.
    Every episode has id,seed,config,actions; seeds and IDs must be unique. Result
    order is lexical episode ID, independent of completion. A failed batch is not
    rolled back: statuses retain successes and no failed worker is retried.

    Optional journal quota must already exist. A durable begin_effect reserves the
    complete transition allowance before any spawn; repeated keys never rerun.
    Timeouts count unknown actual transitions but consume their full reservation.
    Factories are trusted Python, not memory/security sandboxes. Live agents are
    prohibited; forced termination cannot undo arbitrary external side effects.
    """
    if not isinstance(factory, str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*', factory):
        raise ValueError('Factory must be an importable module:function')
    if mode not in ('spawn', 'sequential'): raise ValueError('Unknown rollout mode')
    _integer(workers, 'workers', 1, 8); _integer(max_transitions, 'max_transitions', 1, 100000)
    _integer(max_result_bytes, 'max_result_bytes', 1024, 1048576)
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
        raise ValueError('timeout_seconds must be finite in (0,60]')
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= 100: raise ValueError('Provide 1..100 episodes')
    ids, seeds = set(), set(); requested = 0
    for episode in episodes:
        if not isinstance(episode, dict) or set(episode) - {'id', 'seed', 'config', 'actions', 'live_backend'} or not {'id', 'seed', 'config', 'actions'} <= set(episode):
            raise ValueError('Episode requires id,seed,config,actions')
        eid = episode['id']
        if not isinstance(eid, str) or not 1 <= len(eid) <= 100 or eid in ids: raise ValueError('Episode IDs must be unique bounded strings')
        _integer(episode['seed'], 'seed', 0, 2**63 - 1)
        if episode['seed'] in seeds: raise ValueError('Episode seeds must be explicitly distinct')
        if episode.get('live_backend', False) is not False: raise ValueError('Workers cannot run live external backends')
        if not isinstance(episode['config'], dict) or not isinstance(episode['actions'], list) or not 1 <= len(episode['actions']) <= 1000:
            raise ValueError('Episode config/actions must be bounded JSON objects/list')
        if any(not isinstance(a, dict) for a in episode['actions']): raise ValueError('Actions must be objects')
        ids.add(eid); seeds.add(episode['seed']); requested += len(episode['actions'])
    if requested > max_transitions: raise ValueError('Rollout transition budget exceeded before dispatch')
    if len(canonical(episodes)) > 1048576 or len(episodes) * max_result_bytes > 32 * 1048576:
        raise ValueError('Rollout request/results exceed bounded payload budget')
    episodes = sorted(deepcopy(episodes), key=lambda e: e['id'])
    request = {'episodes_digest': digest(episodes), 'factory': factory, 'requested_transitions': requested,
               'timeout_seconds': timeout_seconds, 'max_result_bytes': max_result_bytes}
    if journal is not None:
        if not isinstance(reservation_key, str) or not reservation_key: raise ValueError('Journal rollout requires a reservation_key')
        prior = journal.begin_effect(reservation_key, request, quota=quota, units=requested)
        if not prior['execute']: raise ValueError('Rollout batch already completed; inspect its recorded result digest')
    ctx = multiprocessing.get_context('spawn')
    active = {}; results = {}; next_index = 0
    capacity = 1 if mode == 'sequential' else workers
    try:
        while len(results) < len(episodes):
            while next_index < len(episodes) and len(active) < capacity:
                episode = episodes[next_index]; next_index += 1
                receiving, sending = ctx.Pipe(duplex=False)
                process = ctx.Process(target=_worker, args=(sending, factory, episode, max_result_bytes))
                try: process.start()
                except Exception:
                    receiving.close(); sending.close(); process.close(); raise
                sending.close()
                active[episode['id']] = (process, receiving, time.monotonic(), episode)
            for eid, (process, receiving, started, episode) in list(active.items()):
                result = None
                if receiving.poll():
                    try:
                        result = json.loads(receiving.recv_bytes(maxlength=max_result_bytes))
                        if result.get('id') != eid or result.get('seed') != episode['seed']: raise ValueError('Worker identity mismatch')
                        canonical(result)
                    except (EOFError, OSError, ValueError, TypeError) as exc: result = _error(episode, 'error', exc, None)
                elif time.monotonic() - started >= timeout_seconds:
                    result = _error(episode, 'timeout', 'Worker exceeded declared timeout; not retried', None)
                elif not process.is_alive(): result = _error(episode, 'error', 'Worker exited without a result', None)
                if result is not None:
                    receiving.close(); _stop(process); del active[eid]; results[eid] = result
            if active: time.sleep(.005)
        output = {'episodes': [results[e['id']] for e in episodes], 'reserved_transitions': requested,
                  'completed_episodes': sum(r['status'] == 'completed' for r in results.values()),
                  'retry_policy': 'none', 'partial_failure_policy': 'retain successful independent results',
                  'epistemic_status': 'numerical_rollout', 'causally_validated': False}
        if journal is not None:
            journal.complete_effect(reservation_key, {'result_digest': digest(output),
                                     'statuses': {k: v['status'] for k, v in results.items()}})
        return output
    except BaseException:
        if journal is not None: journal.mark_uncertain(reservation_key, 'Batch interrupted after reservation; no automatic retry')
        raise
    finally:
        for process, receiving, _, _ in active.values():
            receiving.close(); _stop(process)

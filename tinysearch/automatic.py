"""Launcher-triggered scheduling; no timer may start indexing on its own."""
from contextlib import contextmanager
import json
import os
import threading
import time
import uuid

from .launcher import owner_alive, external_power

INTERVALS = {3600, 10800, 21600, 86400}
DEFAULTS = {'enabled': False, 'interval_seconds': 10800, 'only_on_power': True,
            'paused': False, 'pending_resume': False, 'in_progress': False,
            'last_started_at': None, 'last_completed_at': None,
            'last_error': None, 'last_reason': 'disabled', 'failures': 0, 'revision': 0,
            'continuation_origin': None}


class AutomaticIndexing:
    def __init__(self, settings, clock=time.time, probe=owner_alive, power=external_power):
        self.settings = settings
        self.clock, self.probe, self.power = clock, probe, power
        self.poll_seconds = 5
        self.lock = threading.RLock()
        config = settings.app.state.store.config
        folder = config.storage_path or (config.source_path.parent if config.source_path else config.database.parent)
        self.path = folder/'automatic-indexing.json'
        self.values = dict(DEFAULTS)
        if self.path.is_file():
            try:
                saved = json.loads(self.path.read_text())
                self._validate(saved)
                for key in DEFAULTS:
                    if key in saved:
                        self.values[key] = saved[key]
                # A killed process cannot resume its old thread. The next
                # authorized launcher tick rescans committed entries instead.
                if self.values['in_progress']:
                    self.values.update(in_progress=False, pending_resume=True,
                                       last_reason='interrupted')
                    self._save()
            except (OSError, ValueError, TypeError):
                self.values = dict(DEFAULTS)
                self.values['last_reason'] = 'invalid_settings'

    @staticmethod
    def _validate(values):
        if not isinstance(values, dict):
            raise ValueError('Invalid automatic indexing settings')
        for key in ('enabled', 'only_on_power', 'paused', 'pending_resume', 'in_progress'):
            if key in values and type(values[key]) is not bool:
                raise ValueError('Invalid automatic indexing setting')
        interval = values.get('interval_seconds', 10800)
        if type(interval) is not int or interval not in INTERVALS:
            raise ValueError('Choose a supported automatic update interval')
        for key in ('last_started_at', 'last_completed_at'):
            value = values.get(key)
            if value is not None and (type(value) not in (int, float) or not 0 <= value < float('inf')):
                raise ValueError('Invalid automatic indexing timestamp')
        for key in ('failures', 'revision'):
            if key in values and (type(values[key]) is not int or values[key] < 0):
                raise ValueError('Invalid automatic indexing counter')
        if values.get('continuation_origin') not in {None, 'manual', 'automatic'}:
            raise ValueError('Invalid indexing continuation')

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name('.automatic-' + uuid.uuid4().hex + '.tmp')
        try:
            with temp.open('x') as output:
                os.chmod(temp, 0o600)
                json.dump(self.values, output, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)

    def status(self):
        with self.lock:
            result = dict(self.values)
        interval = result['interval_seconds']
        if result['failures']:
            interval = max(interval, min(86400, 3600 * 4**min(result['failures']-1, 3)))
        last = result['last_started_at']
        result['next_update_at'] = (last + interval) if last is not None else None
        return result

    def configure(self, enabled, interval_seconds, only_on_power, owner=None):
        requested = dict(enabled=enabled, interval_seconds=interval_seconds, only_on_power=only_on_power)
        self._validate(requested)
        if enabled and not self.probe(owner):
            raise ValueError('Automatic updates require a running Tinycast or Raycast extension')
        with self.lock:
            self.values.update(requested)
            self.values['revision'] += 1
            if not enabled:
                self.values.update(last_reason='disabled', pending_resume=False)
            self._save()
        # A manual scan is not cancelled by switching off the automatic option.
        with self.settings.job_lock:
            if not enabled and self.settings.job.get('origin') == 'automatic' and self.settings.job['state'] == 'indexing':
                self.settings.job['interrupt_reason'] = 'disabled'
                self.settings.app.state.store.cancel_index()
        return self.status()

    def not_ready(self, message):
        with self.lock:
            self.values.update(last_reason='not_ready',last_error=message)
            self._save()
            return {**self.status(),'state':'not_ready'}

    def tick(self, owner, force=False):
        with self.lock:
            state = self.status()
            if not state['enabled']:
                return {**state, 'state': 'disabled'}
            if state['paused']:
                return {**state, 'state': 'paused'}
            if not self.probe(owner):
                raise ValueError('The launcher or extension is no longer running')
            if state['only_on_power']:
                power = self.power()
                if power is not True:
                    self.values['last_reason'] = 'on_battery' if power is False else 'power_unavailable'
                    self._save()
                    return {**self.status(), 'state': 'waiting_for_power'}
            if not force and not state['pending_resume'] and state['next_update_at'] is not None and self.clock() < state['next_update_at']:
                return {**state, 'state': 'scheduled'}
            try:
                job = self.settings.start_index(owner=owner, automatic=True)
            except RuntimeError:
                return {**self.status(), 'state': 'busy'}
            except ValueError as error:
                return self.not_ready(str(error))
            self.values.update(in_progress=True, pending_resume=False,
                               last_started_at=self.clock(), last_reason='indexing', last_error=None)
            self._save()
            return {**self.status(), 'state': 'indexing', 'job': job}

    def pause(self):
        with self.settings.job_lock:
            job = dict(self.settings.job)
        with self.lock:
            self.values.update(paused=True, last_reason='paused')
            if job['state'] == 'indexing':
                self.values['continuation_origin'] = job.get('origin', 'manual')
            self.values['revision'] += 1
            self._save()
        return self.settings.pause_index()

    def resume(self, owner=None):
        with self.lock:
            # Acknowledging resume before the old worker has stopped would lose
            # its interruption or allow its completion to overwrite a new job.
            with self.settings.job_lock:
                if self.settings.thread and self.settings.thread.is_alive():
                    raise RuntimeError('Indexing is still stopping. Resume when the pause is complete.')
                job = dict(self.settings.job)
            previous = dict(self.values)
            origin = self.values['continuation_origin'] or (job.get('origin') if job['state'] == 'paused' else None)
            self.values['paused'] = False
            self.values['revision'] += 1
            try:
                if origin == 'automatic' and self.values['enabled']:
                    self.values['pending_resume'] = True
                    result = self.tick(owner, force=True)
                elif job['state'] == 'paused' or origin == 'manual':
                    result = self.settings.start_index(owner=owner)
                    self.values['continuation_origin'] = None
                else:
                    result = self.status()
                self._save()
                return result
            except (RuntimeError, ValueError):
                self.values = previous
                self._save()
                raise

    def completed(self, job, result=None, error=None):
        with self.lock:
            reason = job.get('interrupt_reason')
            automatic = job.get('origin') == 'automatic'
            if automatic:
                self.values['in_progress'] = False
                self.values['pending_resume'] = bool(reason in {'launcher_closed', 'on_battery', 'power_unavailable'} and
                                                     self.values['enabled'] and not self.values['paused'])
            if result and result.get('paused'):
                self.values['continuation_origin'] = 'automatic' if automatic else 'manual'
            elif result and result.get('cancelled'):
                self.values['continuation_origin'] = None
                if reason == 'cancelled':
                    self.values.update(paused=False, pending_resume=False, last_started_at=self.clock())
            elif result is not None:
                self.values['continuation_origin'] = None
            if error is not None:
                self.values.update(last_error=str(error), last_reason='error')
                if automatic:
                    self.values['failures'] += 1
            elif result is not None:
                if result.get('paused'):
                    self.values['last_reason'] = reason or 'paused'
                elif result.get('cancelled'):
                    self.values['last_reason'] = reason or 'cancelled'
                else:
                    now = self.clock()
                    self.values.update(last_completed_at=now, last_started_at=now,
                                       last_reason='complete', last_error=None, failures=0, pending_resume=False)
            self._save()

    @contextmanager
    def guard(self, owner, automatic, job):
        stop = threading.Event()

        def reason():
            if owner is not None:
                try:
                    if not self.probe(owner):
                        return 'launcher_closed'
                except Exception:
                    return 'launcher_closed'
            if automatic:
                state = self.status()
                if not state['enabled']:
                    return 'disabled'
                if state['paused']:
                    return 'paused'
                if state['only_on_power']:
                    try:
                        power = self.power()
                    except Exception:
                        power = None
                    if power is not True:
                        return 'on_battery' if power is False else 'power_unavailable'
            return None

        def interrupt(cause, initial=False):
            with self.settings.job_lock:
                store = self.settings.app.state.store
                if stop.is_set() or self.settings.job is not job or job.get('interrupt_reason') or (not initial and not store.index_lock.locked()):
                    return False
                job['interrupt_reason'] = cause
                if cause in {'on_battery', 'power_unavailable', 'paused'}:
                    store.pause_index()
                else:
                    store.cancel_index()
                return True

        def monitor():
            while not stop.wait(self.poll_seconds):
                cause = reason()
                if cause and interrupt(cause):
                    return

        if owner is not None or automatic:
            cause = reason()
            if cause:
                interrupt(cause, initial=True)
            watcher = threading.Thread(target=monitor, daemon=True)
            watcher.start()
        else:
            watcher = None
        try:
            yield
        finally:
            stop.set()
            if watcher:
                watcher.join(timeout=5)

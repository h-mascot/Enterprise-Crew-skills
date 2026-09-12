#!/usr/bin/env python3
"""Bounded, locally owned execution. No board sweeps, pool claims, or PID kills."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from mc_auto_prompt import build_prompt, model_for
from mc_auto_support import BeltError, acquire_dispatch_lock, atomic_json, check_dispatch_host, identity, preflight, process_absent, read_json, request, task_object


def emit(**values):
    print(json.dumps(values), flush=True)


class Puller:
    def __init__(self, agent, dry_run):
        self.agent = agent
        self.dry_run = dry_run
        self.scripts = Path(os.environ.get('ENTITY_MC_TARGET_SCRIPTS_DIR',str(Path(__file__).resolve().parent)))
        self.state = Path(os.environ.get('ENTITY_MC_STATE_DIR', str(self.scripts.parent)))
        self.records_dir = self.state/'auto-attempts'
        self.trackers = self.state/'exec-tracking'
        self.url = os.environ.get('ENTITY_MC_MC_URL', os.environ.get('MC_URL','http://localhost:3000')).rstrip('/')
        self.runtime = os.environ.get('ENTITY_MC_RUNTIME','openclaw')
        self.max_attempts = int(os.environ.get('ENTITY_MC_MAX_ATTEMPTS','3'))
        self.backoff = int(os.environ.get('ENTITY_MC_RETRY_BACKOFF_SECS','600'))
        self.grace = float(os.environ.get('ENTITY_MC_STARTUP_GRACE_SECS','2'))
        self.task_filter = os.environ.get('ENTITY_MC_TASK_ID')
        if not 1 <= self.max_attempts <= 10 or not 1 <= self.backoff <= 86400 or not 0.05 <= self.grace <= 30:
            raise BeltError('invalid_attempt_configuration')
        if self.task_filter and not re.fullmatch(r'[1-9][0-9]*', self.task_filter):
            raise BeltError('invalid_task_filter')
        self.records = {}
        self.details = []
        self.last_handoff = None
        self.last_outcome = None
        self.started = time.time()

    def api(self, path, method='GET', body=None):
        if self.dry_run and method != 'GET':
            raise BeltError('dry_run_mutation_forbidden')
        return request(self.url + '/api/' + path, self.agent, method, body,
                       timeout=int(os.environ.get('MC_CURL_MAX_TIME','20')))

    def detail(self, task_id, reason, **extra):
        self.details.append(dict(task_id=str(task_id),reason=reason,**extra))

    def save(self, record):
        if self.dry_run:
            raise BeltError('dry_run_state_write_forbidden')
        self.records[str(record['task_id'])] = record
        atomic_json(self.records_dir / ('task-' + str(record['task_id']) + '.json'), record)
        if record.get('claimed'):
            atomic_json(self.trackers / ('task-' + str(record['task_id']) + '.json'), record)

    def failed(self, record, reason, **extra):
        record.update(extra)
        count = record['attempt_count']
        # Once a process was launched, missing output cannot prove work had no side effects.
        # Preserve ownership and require reconciliation instead of replaying that work.
        launched = bool(record.get('pid'))
        record.update(phase='attention' if launched else 'exhausted' if count >= self.max_attempts else 'failed',
                      reason=reason, next_retry_epoch=0 if launched else int(time.time()) + self.backoff * 2**max(0,count-1))
        self.save(record)
        self.detail(record['task_id'],reason,attempt_id=record.get('attempt_id'))
        emit(action='dispatch_failed',task_id=record['task_id'],reason=reason,
             attempt_count=count,phase=record['phase'],attempt_id=record.get('attempt_id'))

    def load_records(self):
        for path in sorted(self.records_dir.glob('task-*.json')):
            try:
                record = read_json(path)
                task_id = str(record['task_id'])
                if (not re.fullmatch(r'[1-9][0-9]*', task_id) or path.name != f'task-{task_id}.json'
                    or record.get('schema_version') != 1 or record.get('agent') != self.agent
                    or not re.fullmatch(r'[A-Za-z0-9_-]+', str(record.get('attempt_id','')))
                    or type(record.get('attempt_count')) is not int or not 0 <= record['attempt_count'] <= 10
                    or record.get('phase') not in ('reserving','claimed','launching','running','failed','exhausted','completed','attention')):
                    raise BeltError('invalid_attempt_record')
                self.records[task_id] = record
            except (BeltError, KeyError, TypeError):
                self.detail(path.stem.removeprefix('task-'),'invalid_attempt_record')
        previous = read_json(self.state/'auto-status.json', {})
        if isinstance(previous,dict):
            self.last_handoff = previous.get('last_handoff_epoch')
            self.last_outcome = previous.get('last_outcome_epoch')

    def board(self):
        value = self.api('tasks')
        tasks = value.get('tasks') if isinstance(value,dict) else value
        if not isinstance(tasks,list) or not all(isinstance(t,dict) and re.fullmatch(r'[1-9][0-9]*', str(t.get('id',''))) and t.get('column') in ('backlog','todo','doing','review','done','cancelled','archived') for t in tasks):
            raise BeltError('api_invalid_task_list')
        return {str(t['id']):t for t in tasks}

    def ours(self, task):
        return str(task.get('assignee') or '').casefold() == self.agent.casefold()

    @staticmethod
    def review_revision(task):
        metadata = task.get('metadata') or {}
        if isinstance(metadata,str):
            try:
                metadata = json.loads(metadata)
            except ValueError:
                return ''
        if (not isinstance(metadata,dict) or metadata.get('review_decision') != 'needs_fix'
            or not metadata.get('reviewed_by') or not metadata.get('reviewed_at')
            or len(str(metadata.get('review_note') or '')) < 20):
            return ''
        return str(metadata['reviewed_at']) + ':' + str(metadata['reviewed_by'])

    def reopened(self, task, record):
        revision = self.review_revision(task)
        return bool(record.get('claimed') and task.get('column') == 'todo' and revision
                    and revision != record.get('review_revision','')
                    and (not record.get('pid') or record.get('exit_code') is not None
                         or process_absent(record['pid'])))

    def reconcile(self, tasks):
        for task_id, record in list(self.records.items()):
            task = tasks.get(task_id)
            if not task:
                if record.get('phase') != 'completed':
                    self.detail(task_id,'owned_task_missing')
                continue
            if not self.ours(task):
                if record.get('phase') != 'completed':
                    self.detail(task_id,'ownership_changed')
                continue
            phase = record.get('phase')
            # Only a recorded prelaunch failure can resume after a temporary block.
            # Interrupted reservations and any claimed/launched work still need reconciliation.
            if (phase == 'completed' and record.get('reason') == 'task_blocked'
                and record.get('blocked_from_phase') == 'failed' and not record.get('claimed')
                and not record.get('pid') and task.get('column') == 'todo'
                and task.get('blocked') is not True and task.get('archived') not in (True,1)):
                record.update(phase='failed',reason='unblocked_prelaunch_retry')
                phase='failed'
                if not self.dry_run:
                    self.save(record)
            if self.reopened(task,record):
                if not self.dry_run and phase != 'completed':
                    record.update(phase='completed',reason='review_requested_fix')
                    self.save(record)
                continue
            if task.get('column') in ('review','done','archived','cancelled') or task.get('archived') in (True,1) or task.get('blocked') is True:
                if phase != 'completed':
                    if not self.dry_run:
                        reason='task_' + task['column']
                        if task.get('blocked') is True and task.get('column') not in ('review','done','archived','cancelled') and task.get('archived') not in (True,1):
                            reason='task_blocked'
                            record['blocked_from_phase']=phase
                        record.update(phase='completed',reason=reason,last_outcome_epoch=int(time.time()))
                        self.save(record)
                        self.last_outcome = record['last_outcome_epoch']
                    emit(action='outcome_observed',task_id=task_id,column=task['column'])
                continue
            if phase in ('reserving','claimed','launching'):
                self.detail(task_id,'interrupted_dispatch_needs_reconciliation')
            elif phase == 'running':
                actual = identity(record.get('pid'))
                if actual and actual == record.get('process_identity'):
                    if time.time() - record.get('started_epoch',0) > 2700:
                        self.detail(task_id,'worker_overdue_no_automatic_kill')
                    continue
                if actual:
                    self.detail(task_id,'process_identity_changed_no_automatic_kill')
                elif not process_absent(record.get('pid')):
                    self.detail(task_id,'process_state_unknown_requires_reconciliation')
                elif task.get('column') == 'doing':
                    if self.dry_run:
                        self.detail(task_id,'worker_exited_without_outcome')
                    else:
                        self.failed(record,'worker_exited_without_outcome')
                else:
                    self.detail(task_id,'owned_task_state_changed')
            elif phase == 'attention':
                self.detail(task_id,record.get('reason','needs_reconciliation'))
            elif phase == 'completed':
                self.detail(task_id,'completed_task_state_changed_requires_reconciliation')

        # Historical trackers are evidence only; missing process identity never authorizes a retry.
        for path in sorted(self.trackers.glob('task-*.json')):
            task_id = path.stem.removeprefix('task-')
            if task_id in self.records:
                continue
            try:
                legacy = read_json(path)
                if not isinstance(legacy,dict) or str(legacy.get('task_id')) != task_id:
                    raise BeltError('invalid_legacy_tracker')
                task = tasks.get(task_id)
                if task and self.ours(task) and (task.get('column') in ('review','done','archived','cancelled') or task.get('archived') in (True,1)):
                    if not self.dry_run:
                        destination = self.state/'legacy-tracker-archive'/path.name
                        atomic_json(destination,legacy)
                        path.unlink()
                    continue
                self.detail(task_id,'legacy_tracker_requires_reconciliation')
            except BeltError:
                self.detail(task_id,'invalid_legacy_tracker')
        for task_id, task in tasks.items():
            if self.ours(task) and task.get('column') == 'doing' and task_id not in self.records:
                self.detail(task_id,'unowned_doing_requires_reconciliation')

    def eligible(self, tasks):
        candidates = []
        for task_id, task in tasks.items():
            if not self.ours(task) or task.get('archived') in (True,1) or task.get('blocked') is True or (self.task_filter and task_id != self.task_filter):
                continue
            record = self.records.get(task_id)
            if record and self.reopened(task,record):
                candidates.append(task)
                continue
            if record:
                if record.get('phase') != 'failed' or record.get('attempt_count',0) >= self.max_attempts:
                    continue
                if record.get('next_retry_epoch',0) > time.time():
                    continue
                if record.get('pid') and record.get('exit_code') is None and not process_absent(record['pid']):
                    self.detail(task_id,'previous_worker_not_confirmed_absent')
                    continue
                if task.get('column') != ('doing' if record.get('claimed') else 'todo'):
                    continue
            elif task.get('column') != 'todo' or (self.trackers/f'task-{task_id}.json').exists():
                continue
            if any(d['task_id'] == task_id and 'invalid_' in d['reason'] for d in self.details):
                continue
            candidates.append(task)
        return sorted(candidates,key=lambda t:str(t.get('created_at') or ''))

    def dispatch(self, task):
        task_id = str(task['id'])
        old = self.records.get(task_id,{})
        fresh_cycle = self.reopened(task,old)
        if old:
            atomic_json(self.state/'auto-attempt-history'/(old['attempt_id'] + '.json'),old)
        record = dict(schema_version=1,agent=self.agent,task_id=task_id,
                      task_name=task.get('name',''),attempt_count=1 if fresh_cycle else old.get('attempt_count',0)+1,
                      attempt_id=uuid.uuid4().hex,phase='reserving',claimed=bool(old.get('claimed')) and not fresh_cycle,
                      review_revision=self.review_revision(task),
                      runtime=self.runtime,started_epoch=int(time.time()),next_retry_epoch=0)
        self.save(record)
        try:
            binary = preflight(self.runtime)
            prompt = build_prompt(task,self.agent,record['attempt_id'],self.scripts,self.state,self.url)
            expected = 'doing' if record['claimed'] else 'todo'
            fresh = task_object(self.api('tasks/' + task_id),task_id,expected,self.agent)
            if fresh.get('blocked') is True:
                raise BeltError('task_became_blocked')
            if not record['claimed']:
                result = self.api('tasks/' + task_id,'PATCH',{'column':'doing','actor':self.agent})
                task_object(result,task_id,'doing',self.agent)
                record['claimed'] = True
                record['phase'] = 'claimed'
                self.save(record)
            # Confirm persisted state immediately before launch as well as the PATCH response.
            fresh = task_object(self.api('tasks/' + task_id),task_id,'doing',self.agent)
            if fresh.get('blocked') is True:
                raise BeltError('task_became_blocked')
            self.launch(task,record,binary,prompt)
        except BeltError as exc:
            self.failed(record,str(exc))
        except (OSError, subprocess.SubprocessError) as exc:
            self.failed(record,'local_execution_error:' + type(exc).__name__)

    def launch(self, task, record, binary, prompt):
        session = 'mc-auto-' + re.sub('[^a-z0-9]+','-',self.agent.lower()) + '-' + record['task_id'] + '-' + record['attempt_id']
        record.update(phase='launching',session_id=session)
        self.save(record)
        command = ([binary,'chat','-q',prompt,'--yolo'] if self.runtime == 'hermes' else
                   [binary,'agent','-m',prompt,'--session-id',session,'--timeout','1800','--json'])
        log_path = Path(os.environ.get('ENTITY_MC_EXEC_LOG',str(self.state/'auto-exec.log')))
        log_path.parent.mkdir(parents=True,exist_ok=True)
        with log_path.open('ab') as log:
            child = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                     start_new_session=True,close_fds=True)
        record.update(pid=child.pid,started_epoch=int(time.time()))
        self.save(record)
        time.sleep(self.grace)
        code = child.poll()
        if code is not None:
            # A short worker can legitimately close the task; confirm the API before success.
            current = task_object(self.api('tasks/' + record['task_id']),record['task_id'],agent=self.agent)
            if code == 0 and (current.get('column') in ('review','done') or current.get('blocked') is True):
                record.update(phase='completed',reason='task_' + current['column'],exit_code=code,
                              last_outcome_epoch=int(time.time()))
                self.save(record)
                self.last_outcome = record['last_outcome_epoch']
                emit(action='outcome_observed',task_id=record['task_id'],column=current['column'])
            else:
                self.failed(record,'immediate_exit',exit_code=code)
            return
        process_identity = identity(child.pid)
        if not process_identity or child.poll() is not None:
            record.update(phase='attention',reason='startup_identity_unavailable')
            self.save(record)
            self.detail(record['task_id'],'startup_identity_unavailable')
            return
        record.update(phase='running',reason='worker_handoff',process_identity=process_identity)
        self.save(record)
        self.last_handoff = int(time.time())
        emit(action='pulled',task_id=record['task_id'],agent=self.agent,**model_for(task,self.agent))
        emit(exec='spawned',runtime=self.runtime,pid=child.pid,session_id=session,
             attempt_id=record['attempt_id'],evidence='alive_after_startup_grace')

    def status(self, error=None):
        records = list(self.records.values())
        failed = sum(r.get('phase') == 'failed' for r in records)
        exhausted = sum(r.get('phase') == 'exhausted' for r in records)
        active = sum(r.get('phase') == 'running' for r in records)
        attention = sum(r.get('phase') == 'attention' for r in records)
        orphan = len({d['task_id'] for d in self.details if 'unowned_doing' in d['reason'] or 'legacy_tracker' in d['reason']})
        status = 'error' if error else 'attention' if failed or exhausted or attention or self.details else 'running' if active else 'idle'
        result = dict(schema_version=1,belt='auto',agent=self.agent,checked_at_epoch=int(time.time()),
                      status=status,reason=error or ('action_required' if status=='attention' else status),
                      last_handoff_epoch=self.last_handoff,last_outcome_epoch=self.last_outcome,
                      active_count=active,failed_count=failed,exhausted_count=exhausted,
                      orphan_count=orphan,details=self.details)
        if not self.dry_run:
            atomic_json(self.state/'auto-status.json',result)
        emit(**result,dry_run=self.dry_run)

    def run(self):
        self.load_records()
        tasks = self.board()
        self.reconcile(tasks)
        candidates = self.eligible(tasks)
        doing = sum(self.ours(t) and t.get('column') == 'doing' for t in tasks.values())
        # A retry already consumes its doing slot; a new claim must respect the cap.
        candidates = [t for t in candidates if t['column']=='doing' or doing < 10]
        if candidates:
            if self.dry_run:
                emit(action='would_dispatch',task_id=str(candidates[0]['id']),dry_run=True)
            else:
                self.dispatch(candidates[0])
        else:
            emit(action='skip',reason='no_eligible_tasks')
        self.status()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('agent',nargs='?',default=os.environ.get('MC_USER',os.environ.get('ENTITY_MC_AGENT_NAME','Agent')))
    parser.add_argument('--dry-run',action='store_true')
    args = parser.parse_args()
    dry = args.dry_run or any(os.environ.get(k) == '1' for k in ('ENTITY_MC_NO_MUTATE','ENTITY_MC_NO_EXEC'))
    runner = None
    lock = None
    try:
        runner = Puller(args.agent,dry)
        if not dry:
            # The lock is held through reservation, verified transition, launch and tracker write.
            # Never delete or age-evict flock files; the kernel releases on parent exit.
            check_dispatch_host()
            try:
                lock = acquire_dispatch_lock(runner.url,runner.agent,'auto')
            except BlockingIOError:
                emit(action='skip',reason='already_running',agent=args.agent)
                return 0
        runner.run()
        return 0
    except (BeltError, ValueError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc,BeltError) else 'local_error:' + type(exc).__name__
        emit(error=reason)
        if runner and not reason.startswith('dispatch_host_'):
            runner.status(reason)
        return 1
    finally:
        if lock:
            lock.close()


if __name__ == '__main__':
    sys.exit(main())

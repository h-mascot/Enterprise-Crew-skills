"""Isolated conveyor regressions: never contact MC or launch an installed agent."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'source-scripts/mc-auto-pull.sh'
BASH = shutil.which('bash') or '/bin/bash'

MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
root=pathlib.Path(os.environ['FIXTURE'])
args=sys.argv[1:]
if pathlib.Path(sys.argv[0]).name == 'node':
 print(os.environ.get('NODE_VERSION','v22.22.3')); sys.exit(0)
if pathlib.Path(sys.argv[0]).name == 'openclaw':
 if '--version' in args: print('2026.9.12'); sys.exit(0)
 if 'config' in args:
  print(os.environ.get('CONFIG_JSON','{"valid":true}'))
  sys.exit(int(os.environ.get('CONFIG_EXIT','0')))
 with (root/'launches').open('a') as f: f.write(json.dumps(args)+'\n')
 time.sleep(float(os.environ.get('WORKER_SLEEP','0')))
 sys.exit(int(os.environ.get('WORKER_EXIT','1')))
method=args[args.index('-X')+1] if '-X' in args else 'GET'
url=next(a for a in args if a.startswith('http'))
if method != 'GET':
 with (root/'writes').open('a') as f: f.write(json.dumps(args)+'\n')
status=200
board=json.loads((root/'board.json').read_text())
if method == 'GET' and url.split('?')[0].endswith('/tasks'):
 body={'tasks':board}
else:
 try: task=next(t for t in board if str(t['id']) == url.rsplit('/',1)[-1])
 except StopIteration: task={}; status=404
 body=task
 if method == 'PATCH':
  time.sleep(float(os.environ.get('PATCH_SLEEP','0')))
  mode=os.environ.get('PATCH_MODE','ok')
  if mode=='409': status=409; body={'error':'conflict'}
  elif mode=='wrong': body=dict(task, column='todo')
  elif mode=='fake200': body=dict(task,column='doing')
  elif mode=='wrong_assignee': body=dict(task,column='doing',assignee='Builder')
  elif mode=='malformed': body='bad json'
  elif mode=='applied_malformed':
   task['column']='doing'; (root/'board.json').write_text(json.dumps(board)); body='bad json'
  else:
   data=json.loads(args[args.index('-d')+1]); task.update(data)
   (root/'board.json').write_text(json.dumps(board))
if os.environ.get('GET_MODE')=='malformed' and method=='GET': body='bad json'
print(body if isinstance(body,str) else json.dumps(body))
if '-w' in args or '--write-out' in args: print(status,end='')
'''


class AutoPullTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root/'state'
        self.scripts = self.root/'scripts'
        self.scripts.mkdir()
        self.entry = self.scripts/'mc-auto-pull.sh'
        shutil.copy2(ENTRY,self.entry)
        for support in ENTRY.parent.glob('mc_auto*.py'):
            shutil.copy2(support,self.scripts/support.name)
        self.bin = self.root/'bin'
        self.bin.mkdir()
        for name in ('curl', 'openclaw', 'node'):
            p = self.bin/name
            p.write_text(MOCK)
            p.chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.root), FIXTURE=str(self.root),
            TMPDIR=str(self.root), PATH=str(self.bin)+':'+os.environ['PATH'],
            ENTITY_MC_DISPATCH_HOST=socket.gethostname(), ENTITY_MC_STATE_DIR=str(self.state), ENTITY_MC_TARGET_SCRIPTS_DIR=str(self.scripts), ENTITY_MC_MC_URL='http://fixture',
            ENTITY_MC_OPENCLAW_BIN=str(self.bin/'openclaw'),
            ENTITY_MC_RUNTIME='openclaw', ENTITY_MC_STARTUP_GRACE_SECS='0.15',
            ENTITY_MC_RETRY_BACKOFF_SECS='60', ENTITY_MC_EXEC_LOG=str(self.root/'exec.log'))
        for key in ('ENTITY_MC_NO_EXEC','ENTITY_MC_NO_MUTATE','ENTITY_MC_TASK_ID'):
            self.env.pop(key,None)
        self.board([self.task()])

    def tearDown(self):
        self.tmp.cleanup()

    def task(self, **kwargs):
        return dict(id=1,name='check fixture',description='test',column='todo',
                    assignee='Agent',blocked=False,created_at='2020-01-01',**kwargs)

    def board(self, tasks):
        (self.root/'board.json').write_text(json.dumps(tasks))

    def run_pull(self, *args, **env):
        return subprocess.run([BASH,str(self.entry),'Agent',*args], env=dict(self.env,**env),
                              capture_output=True,text=True,timeout=12)

    def read(self,path,default=''):
        p=self.root/path
        return p.read_text() if p.exists() else default

    def record(self):
        return json.loads((self.state/'auto-attempts/task-1.json').read_text())

    def test_archived_completed_task_stays_terminal_without_replay(self):
        self.run_pull()
        task=self.task(); task['column']='done'; self.board([task]); self.run_pull()
        launches=self.read('launches')
        task.update(column='backlog',archived=True); self.board([task])
        result=self.run_pull()
        self.assertEqual(result.returncode,0)
        self.assertEqual(json.loads((self.state/'auto-status.json').read_text())['status'],'idle')
        self.assertEqual(self.record()['phase'],'completed')
        self.assertEqual(self.read('launches'),launches)

    def test_deleted_completed_task_retains_receipt_without_false_attention(self):
        self.run_pull()
        task=self.task(); task['column']='done'; self.board([task]); self.run_pull()
        receipt=self.record(); launches=self.read('launches')
        self.board([]); self.run_pull()
        self.assertEqual(json.loads((self.state/'auto-status.json').read_text())['status'],'idle')
        self.assertEqual(self.record(),receipt)
        self.assertEqual(self.read('launches'),launches)


    def test_deleted_unresolved_task_remains_attention(self):
        self.run_pull(CONFIG_EXIT='1'); receipt=self.record()
        self.board([]); self.run_pull()
        status=json.loads((self.state/'auto-status.json').read_text())
        self.assertEqual(status['status'],'attention')
        self.assertTrue(any(item['reason']=='owned_task_missing' for item in status['details']))
        self.assertEqual(self.record(),receipt)


    def test_reassigned_completed_task_preserves_receipt_without_false_attention(self):
        self.run_pull()
        task=self.task(); task['column']='done'; self.board([task]); self.run_pull()
        receipt=self.record(); launches=self.read('launches')
        task['assignee']='Other Agent'; self.board([task]); self.run_pull()
        self.assertEqual(json.loads((self.state/'auto-status.json').read_text())['status'],'idle')
        self.assertEqual(self.record(),receipt)
        self.assertEqual(self.read('launches'),launches)


    def test_reassigned_unresolved_task_remains_attention(self):
        self.run_pull(CONFIG_EXIT='1'); receipt=self.record()
        task=self.task(); task['assignee']='Other Agent'; self.board([task]); self.run_pull()
        status=json.loads((self.state/'auto-status.json').read_text())
        self.assertEqual(status['status'],'attention')
        self.assertTrue(any(item['reason']=='ownership_changed' for item in status['details']))
        self.assertEqual(self.record(),receipt)


    def test_unblocked_preflight_failure_preserves_bounded_retry_budget(self):
        self.run_pull(CONFIG_EXIT='1')
        for expected_count in (2,3):
            record=self.record(); record['next_retry_epoch']=0
            (self.state/'auto-attempts/task-1.json').write_text(json.dumps(record))
            task=self.task(); task.update(blocked=True,column='backlog'); self.board([task]); self.run_pull()
            self.board([self.task()]); self.run_pull(CONFIG_EXIT='1')
            self.assertEqual(self.record()['attempt_count'],expected_count)
        self.assertEqual(self.record()['phase'],'exhausted')
        exhausted_id=self.record()['attempt_id']
        task=self.task(); task.update(blocked=True,column='backlog'); self.board([task]); self.run_pull()
        self.board([self.task()]); self.run_pull()
        self.assertEqual(self.record()['attempt_id'],exhausted_id)
        self.assertEqual(self.read('launches'),'')


    def test_unblock_does_not_replay_ambiguous_attempt(self):
        self.run_pull(CONFIG_EXIT='1'); original=self.record()
        for phase in ('reserving','claimed','running','attention'):
            with self.subTest(phase=phase):
                record=dict(original,phase=phase,claimed=False,next_retry_epoch=0)
                (self.state/'auto-attempts/task-1.json').write_text(json.dumps(record))
                task=self.task(); task.update(blocked=True,column='backlog'); self.board([task]); self.run_pull()
                self.board([self.task()]); self.run_pull()
                self.assertEqual(self.record()['attempt_id'],original['attempt_id'])
                self.assertEqual(self.read('launches'),'')


    def test_preflight_retry_preserves_each_prior_attempt_receipt(self):
        self.run_pull(CONFIG_EXIT='1')
        for _ in range(2):
            previous=self.record(); previous['next_retry_epoch']=0
            (self.state/'auto-attempts/task-1.json').write_text(json.dumps(previous))
            self.run_pull(CONFIG_EXIT='1')
            archived=self.state/'auto-attempt-history'/(previous['attempt_id']+'.json')
            self.assertTrue(archived.exists())
            self.assertEqual(json.loads(archived.read_text()),previous)
            self.assertNotEqual(self.record()['attempt_id'],previous['attempt_id'])


    def test_http_409_does_not_launch_or_claim_success(self):
        result=self.run_pull(PATCH_MODE='409')
        self.assertNotIn('"action": "pulled"',result.stdout)
        self.assertEqual(self.read('launches'),'')
        self.assertIn('409',result.stdout)

    def test_wrong_state_200_does_not_launch(self):
        self.run_pull(PATCH_MODE='wrong')
        self.assertEqual(self.read('launches'),'')

    def test_malformed_patch_does_not_launch(self):
        self.run_pull(PATCH_MODE='malformed')
        self.assertEqual(self.read('launches'),'')

    def test_malformed_read_does_not_mutate(self):
        self.run_pull(GET_MODE='malformed')
        self.assertEqual(self.read('writes'),'')
        self.assertEqual(self.read('launches'),'')

    def test_unavailable_runtime_prevents_claim(self):
        result=self.run_pull(ENTITY_MC_OPENCLAW_BIN=str(self.bin/'missing'))
        self.assertEqual(self.read('writes'),'')
        self.assertEqual(self.read('launches'),'')
        self.assertIn('runtime_not_found',result.stdout)

    def test_invalid_config_prevents_claim(self):
        self.run_pull(CONFIG_EXIT='1')
        self.assertEqual(self.read('writes'),'')
        self.assertEqual(self.read('launches'),'')

    def test_immediate_exit_is_failed_dispatch_and_persists_backoff(self):
        result=self.run_pull()
        self.assertNotIn('"exec": "spawned"',result.stdout)
        rec=self.record()
        self.assertEqual(rec['phase'],'attention')
        self.assertEqual(rec['attempt_count'],1)
        self.assertEqual(rec['next_retry_epoch'],0)
        self.run_pull()
        self.assertEqual(len(self.read('launches').splitlines()),1)

    def test_retries_exhaust_and_attempt_ids_are_unique(self):
        ids=[]
        for _ in range(3):
            self.run_pull(CONFIG_EXIT='1')
            rec=self.record(); ids.append(rec['attempt_id'])
            rec['next_retry_epoch']=0
            (self.state/'auto-attempts/task-1.json').write_text(json.dumps(rec))
        self.run_pull()
        self.assertEqual(self.record()['phase'],'exhausted')
        self.assertEqual(len(self.read('launches').splitlines()),0)
        self.assertEqual(len(set(ids)),3)
        self.assertEqual(len(self.read('writes').splitlines()),0)

    def test_multiline_backtick_prompt_and_tracker_are_literal(self):
        marker=self.root/'injected'
        title='quoted "title"\n`touch '+str(marker)+'`'
        task=self.task(); task['name']=title; self.board([task])
        self.run_pull()
        self.assertFalse(marker.exists())
        self.assertEqual(self.record()['task_name'],title)
        args=json.loads(self.read('launches').splitlines()[0])
        self.assertIn(title,args[args.index('-m')+1])
        self.assertIn('`touch '+str(marker)+'`',args[args.index('-m')+1])

    def test_dry_run_existing_tracker_has_no_side_effects(self):
        tracker=self.state/'exec-tracking/task-1.json'
        tracker.parent.mkdir(parents=True)
        tracker.write_text(json.dumps(dict(task_id='1',pid=999999,started_epoch=1)))
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.run_pull('--dry-run')
        after={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after,result.stderr)

    def test_unowned_doing_is_actionable_without_mutation(self):
        task=self.task(); task['column']='doing'; self.board([task])
        result=self.run_pull()
        self.assertEqual(self.read('writes'),'')
        self.assertEqual(self.read('launches'),'')
        status=json.loads((self.state/'auto-status.json').read_text())
        self.assertEqual(status['orphan_count'],1)
        self.assertEqual(status['status'],'attention')

    def test_backlog_and_pool_are_not_promoted(self):
        task=self.task(); task.update(column='backlog',assignee='Unassigned Pool')
        self.board([task]); self.run_pull()
        self.assertEqual(self.read('writes'),'')

    def test_concurrent_pull_reservation_and_launch_are_locked(self):
        env=dict(self.env,PATCH_SLEEP='0.2',WORKER_SLEEP='0.5')
        first=subprocess.Popen([BASH,str(self.entry),'Agent'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        time.sleep(0.1)
        second=self.run_pull(WORKER_SLEEP='0.5',ENTITY_MC_STATE_DIR=str(self.root/'another-state'))
        first.communicate(timeout=12)
        self.assertEqual(len(self.read('launches').splitlines()),1)
        self.assertIn('already_running',second.stdout)
        time.sleep(0.5)

    def test_profile_cannot_override_explicit_runtime_path(self):
        (self.root/'.bashrc').write_text('touch '+str(self.root/'sourced')+'\nexport PATH=/nonexistent\n')
        self.run_pull()
        self.assertFalse((self.root/'sourced').exists())
        self.assertTrue(self.read('launches'))


    def test_wrong_assignee_and_unpersisted_claim_do_not_launch(self):
        for mode in ('fake200','wrong_assignee'):
            with self.subTest(mode=mode):
                self.run_pull(PATCH_MODE=mode)
                self.assertEqual(self.read('launches'),'')
                if (self.state/'auto-attempts').exists():
                    shutil.rmtree(self.state/'auto-attempts')
                if (self.state/'exec-tracking').exists():
                    shutil.rmtree(self.state/'exec-tracking')

    def test_unknown_runtime_and_old_node_prevent_claim(self):
        self.run_pull(ENTITY_MC_RUNTIME='custom')
        self.assertEqual(self.read('writes'),'')
        shutil.rmtree(self.state)
        self.run_pull(NODE_VERSION='v22.21.1')
        self.assertEqual(self.read('writes'),'')

    def test_malformed_attempt_never_resets_budget(self):
        directory=self.state/'auto-attempts'; directory.mkdir(parents=True)
        (directory/'task-1.json').write_text('malformed')
        self.run_pull()
        self.assertEqual(self.read('launches'),'')
        self.assertEqual(self.read('writes'),'')

    def test_existing_live_process_is_never_killed_or_retried(self):
        worker=subprocess.Popen(['python3','-c','import time; time.sleep(10)'])
        try:
            task=self.task(); task['column']='doing'; self.board([task])
            directory=self.state/'auto-attempts'; directory.mkdir(parents=True)
            record=dict(schema_version=1,agent='Agent',task_id='1',task_name='fixture',
                        attempt_count=1,attempt_id='fixture-old',phase='running',claimed=True,
                        next_retry_epoch=0,pid=worker.pid,started_epoch=1,
                        process_identity={'started':'wrong','command_hash':'wrong'})
            (directory/'task-1.json').write_text(json.dumps(record))
            self.run_pull()
            self.assertIsNone(worker.poll())
            self.assertEqual(self.read('launches'),'')
            record['phase']='failed'
            (directory/'task-1.json').write_text(json.dumps(record))
            self.run_pull()
            self.assertIsNone(worker.poll())
            self.assertEqual(self.read('launches'),'')
        finally:
            worker.terminate(); worker.wait(timeout=3)

    def test_completed_legacy_tracker_is_archived_without_board_writes(self):
        task=self.task(); task['column']='review'; self.board([task])
        tracker=self.state/'exec-tracking/task-1.json'; tracker.parent.mkdir(parents=True)
        payload=dict(task_id='1',task_name='quote " and\nnewline',pid=999999,started_epoch=1)
        tracker.write_text(json.dumps(payload))
        self.run_pull()
        self.assertFalse(tracker.exists())
        self.assertEqual(json.loads((self.state/'legacy-tracker-archive/task-1.json').read_text()),payload)
        self.assertEqual(self.read('writes'),'')

    def test_dry_run_on_fresh_home_creates_no_files(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.run_pull('--dry-run')
        after={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.assertFalse(self.state.exists())

    def test_task_id_filter_limits_dispatch(self):
        one=self.task(); two=dict(one,id=2,name='second')
        self.board([one,two]); self.run_pull(ENTITY_MC_TASK_ID='2')
        self.assertFalse((self.state/'auto-attempts/task-1.json').exists())
        self.assertTrue((self.state/'auto-attempts/task-2.json').exists())


    def test_config_validation_requires_valid_json_success(self):
        for value in ('bad json','{"valid": false}'):
            with self.subTest(value=value):
                self.run_pull(CONFIG_JSON=value)
                self.assertEqual(self.read('writes'),'')
                if self.state.exists(): shutil.rmtree(self.state)


    def test_review_request_fix_starts_new_bounded_cycle_once(self):
        self.run_pull()
        old=self.record()
        old.update(phase='completed',reason='task_review',attempt_count=3)
        (self.state/'auto-attempts/task-1.json').write_text(json.dumps(old))
        task=self.task(); task['metadata']=json.dumps(dict(review_decision='needs_fix',
            reviewed_by='Builder',reviewed_at='2026-09-12T12:00:00Z',review_note='The required output fixture is incomplete.'))
        self.board([task])
        self.run_pull()
        new=self.record()
        self.assertEqual(new['attempt_count'],1)
        self.assertNotEqual(new['attempt_id'],old['attempt_id'])
        self.assertTrue((self.state/'auto-attempt-history'/ (old['attempt_id']+'.json')).exists())
        self.run_pull()
        self.assertEqual(len(self.read('launches').splitlines()),2)


    def test_context_builder_receives_existing_contract(self):
        builder=self.scripts/'mc-build-context.sh'
        builder.write_text('#!/bin/bash\ncat > "$FIXTURE/context-input.json"\nprintf "Loaded fixture context"\n')
        task=self.task(); task.update(description='Unique task description',metadata=json.dumps(dict(skill='fixture-skill',context=['one','two'])))
        self.board([task]); self.run_pull()
        supplied=json.loads(self.read('context-input.json'))
        self.assertEqual(supplied.get('task_description'),'Unique task description')
        self.assertEqual(supplied.get('skill'),'fixture-skill')
        self.assertEqual(supplied.get('context'),'one,two')


    def test_wrong_or_missing_dispatch_host_fails_before_any_write(self):
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for expected in ('other-host',''):
            result=self.run_pull(ENTITY_MC_DISPATCH_HOST=expected)
            self.assertIn('dispatch_host_',result.stdout)
            self.assertEqual(self.read('writes'),'')
            self.assertEqual(self.read('launches'),'')
        after={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)


    def test_exported_empty_runtime_bins_use_path_default(self):
        self.run_pull(ENTITY_MC_OPENCLAW_BIN='',ENTITY_MC_NODE_BIN='')
        self.assertEqual(len(self.read('launches').splitlines()),1)

    def test_external_relative_symlink_entry_resolves_modules_without_bytecode(self):
        links=self.root/'entrypoints'; links.mkdir()
        (links/'first.sh').symlink_to('second.sh')
        (links/'second.sh').symlink_to('../scripts/mc-auto-pull.sh')
        self.entry=links/'first.sh'
        result=self.run_pull('--dry-run')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('would_dispatch',result.stdout)
        self.assertEqual(list(self.root.rglob('__pycache__')),[])
        self.assertFalse(self.state.exists())

    def test_immediate_exit_attention_persists_across_later_ticks(self):
        self.run_pull()
        for _ in range(2):
            self.run_pull()
            status=json.loads((self.state/'auto-status.json').read_text())
            self.assertEqual(status['status'],'attention')
            self.assertTrue(any(d.get('reason')=='immediate_exit' for d in status['details']))
        self.assertEqual(len(self.read('launches').splitlines()),1)

    def test_completed_task_requeue_without_new_receipt_requires_attention(self):
        self.run_pull()
        old=self.record(); old.update(phase='completed',reason='task_doing')
        (self.state/'auto-attempts/task-1.json').write_text(json.dumps(old))
        self.board([self.task()])
        self.run_pull()
        status=json.loads((self.state/'auto-status.json').read_text())
        self.assertEqual(status['status'],'attention')
        self.assertTrue(any('completed_task' in d.get('reason','') for d in status['details']))
        self.assertEqual(len(self.read('launches').splitlines()),1)
        self.assertEqual(self.record()['attempt_id'],old['attempt_id'])

    def test_uncertain_successful_patch_never_authorizes_doing_retry(self):
        self.run_pull(PATCH_MODE='applied_malformed')
        record=self.record()
        self.assertFalse(record['claimed'])
        record['next_retry_epoch']=0
        (self.state/'auto-attempts/task-1.json').write_text(json.dumps(record))
        self.run_pull()
        self.assertEqual(self.read('launches'),'')
        self.assertEqual(len(self.read('writes').splitlines()),1)


if __name__ == '__main__':
    unittest.main()

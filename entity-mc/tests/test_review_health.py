import json, os, shlex, socket, subprocess, threading, time
import pytest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]; SCRIPTS=ROOT/"source-scripts"

class Handler(BaseHTTPRequestHandler):
    tasks=[]; writes=[]; fail_task_id=None; detail_task=None; board_response=None
    def do_GET(self):
        if self.fail_task_id and self.path==f"/api/tasks/{self.fail_task_id}": self.send_response(503); self.end_headers(); return
        if self.path.startswith("/api/tasks/"):
            task_id=self.path.rsplit("/",1)[-1]; payload=self.detail_task if self.detail_task is not None else next((task for task in self.tasks if str(task.get("id"))==task_id),{})
        else: payload=self.board_response if self.board_response is not None else {"tasks":self.tasks}
        body=json.dumps(payload).encode(); self.send_response(200); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        size=int(self.headers.get("Content-Length",0)); self.writes.append((self.path,self.rfile.read(size))); self.send_response(201); self.send_header("Content-Type","application/json"); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self,*_): pass

def server(tasks,fail_task_id=None,detail_task=None,board_response=None):
    Handler.board_response=board_response; Handler.tasks=tasks; Handler.writes=[]; Handler.fail_task_id=fail_task_id; Handler.detail_task=detail_task; httpd=ThreadingHTTPServer(("127.0.0.1",0),Handler); thread=threading.Thread(target=httpd.serve_forever,daemon=True); thread.start()
    shutdown=httpd.shutdown
    def close(): shutdown(); httpd.server_close(); thread.join()
    httpd.shutdown=close
    return httpd
def run(script,env,*args): return subprocess.run([str(SCRIPTS/script),*args],env={**os.environ,"ENTITY_MC_DISPATCH_HOST":socket.gethostname(),**env},text=True,capture_output=True)
def peer(task_id=1,**metadata):
    base={"reviewer":"Reviewer","review_type":"peer","review_decision":"pending","submitted_by":"Producer","created_by":"Producer"}; base.update(metadata)
    return {"id":task_id,"name":"Review me","description":"desc","output":"artifact","assignee":"Producer","column":"review","metadata":base}

def test_review_immediate_failure_is_held_for_reconciliation(tmp_path):
    api=server([peer()]); runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    env={"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path/"state"),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.3","ENTITY_MC_REVIEW_BACKOFF_SECS":"600","ENTITY_MC_REVIEW_MAX_ATTEMPTS":"2"}
    first=run("mc-review-pull.sh",env,"Reviewer"); assert first.returncode==0; assert "spawned_review" not in first.stdout
    tracker=tmp_path/"state/review-tracking/review-1.json"; assert tracker.exists()
    second=run("mc-review-pull.sh",env,"Reviewer"); assert second.returncode==0; assert "review_process_attention" in second.stdout
    api.shutdown()

def test_review_dry_run_has_zero_mutation_including_watchdog(tmp_path):
    api=server([peer()]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True); tracker=tracking/"review-99.json"; tracker.write_text('{"task_id":99,"pid":999999,"started_epoch":1}')
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state)},"--dry-run","Reviewer")
    assert result.returncode==0 and tracker.exists() and not (state/"review-status.json").exists() and Handler.writes==[]
    api.shutdown()

def test_review_never_selects_human_gate_or_self_review(tmp_path):
    api=server([peer(1,human_gate_required=True),peer(2,review_type="human"),peer(3,submitted_by="Reviewer")])
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"--dry-run","Reviewer")
    payload=json.loads(result.stdout); assert payload["pending"]==[] and payload["human_gate_count"]==2
    api.shutdown()

@pytest.mark.parametrize("flag", ["requires_approval", "requires_human_read"])
def test_review_packet_human_gate_is_held_without_launch(tmp_path,flag):
    api=server([peer(review_packet={flag:True})]); runtime=tmp_path/"adapter"; marker=tmp_path/"launched"
    runtime.write_text(f'#!/bin/sh\ntouch {shlex.quote(str(marker))}\nexit 7\n'); runtime.chmod(0o755)
    env={"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path/"state"),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime)}
    try:
        dry=run("mc-review-pull.sh",env,"--dry-run","Reviewer"); payload=json.loads(dry.stdout)
        assert dry.returncode==0 and payload["pending"]==[] and payload["human_gate_count"]==1
        result=run("mc-review-pull.sh",env,"Reviewer"); payload=json.loads(result.stdout)
        assert result.returncode==0 and payload["reason"]=="human_gate_only" and payload["human_gate_count"]==1
        assert not marker.exists() and Handler.writes==[]
        assert not (tmp_path/"state/review-tracking/review-1.json").exists()
    finally: api.shutdown()

def test_review_dead_process_after_decision_is_successful_outcome(tmp_path):
    finished=peer(); finished["column"]="done"; finished["metadata"]["review_decision"]="accepted"
    api=server([finished]); tracking=tmp_path/"review-tracking"; tracking.mkdir(); (tracking/"review-1.json").write_text(json.dumps({"task_id":1,"pid":999999,"started_epoch":1,"attempt_id":"a","process_identity":{"started":"x","command_hash":"y"}}))
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"Reviewer")
    status=json.loads((tmp_path/"review-status.json").read_text()); assert result.returncode==0 and status["status"]=="idle" and status["last_outcome_epoch"] is not None and status["failed_count"]==0
    api.shutdown()

@pytest.mark.parametrize("column", ["archived","cancelled"])
def test_review_dead_process_terminal_column_records_outcome(tmp_path,column):
    finished=peer(); finished["column"]=column
    api=server([finished]); tracking=tmp_path/"review-tracking"; tracking.mkdir()
    tracker=tracking/"review-1.json"; tracker.write_text(json.dumps({"task_id":1,"pid":999999,"started_epoch":1,"attempt_id":"existing"}))
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"Reviewer")
        status=json.loads((tmp_path/"review-status.json").read_text())
        assert result.returncode==0 and status["status"]=="idle"
        assert not tracker.exists() and list((tracking/"archive").glob("review-1-*.json"))
        assert status["last_outcome_epoch"] is not None
    finally: api.shutdown()

def test_review_outcome_api_failure_preserves_tracker_without_retry(tmp_path):
    api=server([peer()],fail_task_id=1); tracking=tmp_path/"review-tracking"; tracking.mkdir(); tracker=tracking/"review-1.json"; tracker.write_text(json.dumps({"task_id":1,"pid":999999,"started_epoch":1,"attempt_id":"a","process_identity":{"started":"x","command_hash":"y"}}))
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"Reviewer")
    status=json.loads((tmp_path/"review-status.json").read_text()); assert result.returncode==0 and tracker.exists() and not (tracking/"attempt-1.json").exists(); assert status["status"]=="attention"
    api.shutdown()

def test_review_incomplete_reservation_is_preserved_as_attention(tmp_path):
    api=server([peer()]); tracking=tmp_path/"review-tracking"; tracking.mkdir(); tracker=tracking/"review-1.json"; tracker.write_text(json.dumps({"task_id":1,"pid":None,"phase":"reserving","attempt_id":"a"}))
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"Reviewer")
    status=json.loads((tmp_path/"review-status.json").read_text()); assert result.returncode==0 and tracker.exists() and status["status"]=="attention"
    api.shutdown()

@pytest.mark.parametrize("human_metadata", [{"human_gate_required":True}, *({"review_packet":{flag:True}} for flag in ("requires_approval","requires_human_read"))])
def test_review_revalidates_human_gate_before_launch(tmp_path,human_metadata):
    human=peer(**human_metadata); api=server([peer()],detail_task=human); runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 99\n'); runtime.chmod(0o755)
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime)},"Reviewer")
    assert result.returncode==0 and "launch_revalidation_ineligible" in result.stdout and not (tmp_path/"review-tracking/review-1.json").exists()
    api.shutdown()

def test_review_retry_ineligible_task_does_not_starve_later_candidate(tmp_path):
    api=server([peer(1),peer(2)]); state=tmp_path/"state"; attempts=state/"review-tracking"; attempts.mkdir(parents=True); (attempts/"attempt-1.json").write_text(json.dumps({"task_id":1,"failure_count":3,"exhausted":True}))
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.1","ENTITY_MC_REVIEW_PULL_LIMIT":"1"},"Reviewer")
    assert result.returncode==1 and (attempts/"review-2.json").exists()
    api.shutdown()

def test_review_invalid_outcome_response_preserves_tracker(tmp_path):
    api=server([peer()],detail_task={}); tracking=tmp_path/"review-tracking"; tracking.mkdir(); tracker=tracking/"review-1.json"; tracker.write_text(json.dumps({"task_id":1,"pid":999999,"started_epoch":1,"attempt_id":"a","process_identity":{"started":"x","command_hash":"y"}}))
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"Reviewer")
    status=json.loads((tmp_path/"review-status.json").read_text()); assert result.returncode==0 and tracker.exists() and status["status"]=="attention"
    api.shutdown()

def test_review_persistent_backoff_remains_unhealthy(tmp_path):
    api=server([peer()]); state=tmp_path/"state"; missing=tmp_path/"missing-adapter"; env={"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(missing),"ENTITY_MC_REVIEW_BACKOFF_SECS":"600"}
    first=run("mc-review-pull.sh",env,"Reviewer"); second=run("mc-review-pull.sh",env,"Reviewer"); status=json.loads((state/"review-status.json").read_text())
    assert first.returncode==1 and second.returncode==1 and status["status"]=="error" and status["failed_count"]==1
    api.shutdown()

def test_review_task_filter_selects_exact_eligible_task(tmp_path):
    api=server([peer(1),peer(2)]); state=tmp_path/"state"; runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_TASK_ID":"2","ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.1"},"Reviewer")
    assert result.returncode==0 and (state/"review-tracking/review-2.json").exists() and not (state/"review-tracking/review-1.json").exists()
    api.shutdown()

def test_review_invalid_task_filter_has_zero_mutation(tmp_path):
    state=tmp_path/"state"
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":"http://127.0.0.1:1","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_TASK_ID":"2 OR 1=1"},"Reviewer")
    assert result.returncode==2 and "task_id_filter_invalid" in result.stdout and not state.exists()

def test_review_unknown_tracker_ownership_blocks_dispatch(tmp_path):
    api=server([peer()]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True); (tracking/"review-unknown.json").write_text("{}")
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime)},"Reviewer")
    assert result.returncode==0 and "incomplete_reservation_attention" in result.stdout and not (tracking/"review-1.json").exists()
    api.shutdown()

def test_review_known_reservation_blocks_only_its_task(tmp_path):
    api=server([peer(1),peer(2)]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True); (tracking/"review-1.json").write_text(json.dumps({"task_id":1,"pid":None,"phase":"reserving"}))
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.1"},"Reviewer")
    assert result.returncode==0 and (tracking/"review-1.json").exists() and (tracking/"review-2.json").exists()
    api.shutdown()

@pytest.mark.parametrize("previous_generation", [None,"generation-1"])
def test_review_resubmission_archives_absent_prior_worker_and_launches_new_round(tmp_path,previous_generation):
    resubmitted=peer(review_submitted_at="generation-2"); api=server([resubmitted]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True); (tracking/"review-1.json").write_text(json.dumps({"task_id":1,"pid":999999,"started_epoch":1,"attempt_id":"old","process_identity":{"started":"x","command_hash":"y"},"review_submitted_at":previous_generation}))
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.1"},"Reviewer")
    current=json.loads((tracking/"review-1.json").read_text()); archived=list((tracking/"archive").glob("review-1-*.json")); assert result.returncode==0 and current["review_submitted_at"]=="generation-2" and len(archived)==1
    api.shutdown()

def test_review_new_generation_retires_legacy_prelaunch_exhaustion(tmp_path):
    api=server([peer(review_submitted_at="generation-2")]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True)
    attempt=tracking/"attempt-1.json"; attempt.write_text(json.dumps({"task_id":1,"failure_count":3,"exhausted":True,"review_submitted_at":None}))
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2"},"Reviewer")
        assert result.returncode==0 and (tracking/"review-1.json").exists()
        assert not attempt.exists() and list((tracking/"archive").glob("attempt-1-*.json"))
    finally: api.shutdown()

def test_review_new_generation_does_not_replay_live_legacy_worker(tmp_path):
    api=server([peer(review_submitted_at="generation-2")]); tracking=tmp_path/"review-tracking"; tracking.mkdir()
    tracker=tracking/"review-1.json"; tracker.write_text(json.dumps({"task_id":1,"pid":os.getpid(),"started_epoch":int(time.time()),"attempt_id":"live-legacy","review_submitted_at":None})); original=tracker.read_bytes()
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"Reviewer")
        assert result.returncode==0 and tracker.read_bytes()==original
        assert not (tracking/"archive").exists()
        assert json.loads((tmp_path/"review-status.json").read_text())["active_count"]==1
    finally: api.shutdown()

def test_review_resolved_task_archives_persistent_preflight_attempt(tmp_path):
    finished=peer(review_submitted_at="generation-1"); finished["column"]="done"; finished["metadata"]["review_decision"]="accepted"; api=server([finished]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True); attempt=tracking/"attempt-1.json"; attempt.write_text(json.dumps({"task_id":1,"failure_count":2,"exhausted":False,"review_submitted_at":"generation-1"}))
    result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state)},"Reviewer")
    status=json.loads((state/"review-status.json").read_text()); assert result.returncode==0 and not attempt.exists() and status["failed_count"]==0 and list((tracking/"archive").glob("attempt-1-*.json"))
    api.shutdown()

def test_mc_review_metadata_records_dedicated_submission_generation():
    text=(SCRIPTS/"mc.sh").read_text(); assert "review_submitted_at: (now|tostring)" in text

@pytest.mark.parametrize("generation", [None, "generation-' quoted; $(literal)"])
def test_review_prompt_preserves_fresh_submission_packet(tmp_path,generation):
    packet={"output_artifact":"https://example.invalid/fresh-proof", "evidence":"Verified fresh receipt 42", "done_criteria":["Fresh acceptance criterion"]}
    current=peer(review_packet=packet,review_submitted_at=generation)
    current.update(name="Fresh title",description="Fresh description",output="Fresh output")
    api=server([peer()],detail_task=current)
    runtime=tmp_path/"adapter"; captured=tmp_path/"argv.json"
    runtime.write_text('#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\nif sys.argv[1:] == ["--preflight"]: raise SystemExit(0)\nPath(os.environ["CAPTURE_PATH"]).write_text(json.dumps({"argv":sys.argv[1:],"submission":os.getenv("ENTITY_MC_REVIEW_SUBMISSION")}))\n')
    runtime.chmod(0o755)
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path/"state"),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2","CAPTURE_PATH":str(captured)},"Reviewer")
        assert result.returncode==0
        capture=json.loads(captured.read_text()); argv=capture["argv"]; prompt=argv[argv.index("-m")+1]
        assert capture["submission"]==(generation or "")
        for value in (current["name"],current["description"],current["output"],packet["output_artifact"],packet["evidence"],packet["done_criteria"][0]):
            assert value in prompt
        for command,note in (("accept-review","substantive verification"),("request-fix","specific defect")):
            assert f"{command} 1 '<{note}>' --submission {shlex.quote(generation or '')}" in prompt
    finally: api.shutdown()

def test_review_log_open_failure_is_bounded_before_launch(tmp_path):
    api=server([peer()]); state=tmp_path/"state"; log_path=tmp_path/"missing-parent/review.log"
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nprintf "review output\\n"\n'); runtime.chmod(0o755)
    env={"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_EXEC_LOG":str(log_path),"ENTITY_MC_REVIEW_BACKOFF_SECS":"0","ENTITY_MC_REVIEW_MAX_ATTEMPTS":"2","ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2"}
    try:
        first=run("mc-review-pull.sh",env,"Reviewer")
        assert first.returncode==1
        tracking=state/"review-tracking"; attempt=json.loads((tracking/"attempt-1.json").read_text())
        assert attempt["failure_count"]==1 and attempt["last_reason"]=="log_open_failed"
        assert not (tracking/"review-1.json").exists()
        log_path.parent.mkdir()
        second=run("mc-review-pull.sh",env,"Reviewer")
        assert second.returncode==0 and "review output" in log_path.read_text()
        assert (tracking/"review-1.json").exists()
    finally: api.shutdown()

def test_review_detail_failure_backs_off_to_allow_next_task(tmp_path):
    api=server([peer(1),peer(2)],fail_task_id=1); state=tmp_path/"state"
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    env={"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_BACKOFF_SECS":"600","ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2"}
    try:
        first=run("mc-review-pull.sh",env,"Reviewer")
        assert first.returncode==1
        tracking=state/"review-tracking"; attempt_path=tracking/"attempt-1.json"; attempt=json.loads(attempt_path.read_text())
        assert attempt["failure_count"]==1 and attempt["next_retry_epoch"]>time.time()
        assert attempt["last_reason"]=="launch_revalidation_failed:api_http_503"
        run("mc-review-pull.sh",env,"Reviewer")
        assert json.loads(attempt_path.read_text())["failure_count"]==1
        assert not (tracking/"review-1.json").exists() and (tracking/"review-2.json").exists()
    finally: api.shutdown()

def test_review_uses_shared_execution_log_fallback(tmp_path):
    api=server([peer()]); shared_log=tmp_path/"shared-execution.log"
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nprintf "shared review receipt\\n"\n'); runtime.chmod(0o755)
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path/"state"),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_EXEC_LOG":"","ENTITY_MC_EXEC_LOG":str(shared_log),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2"},"Reviewer")
        assert result.returncode==0 and "shared review receipt" in shared_log.read_text()
    finally: api.shutdown()

@pytest.mark.parametrize("bad_attempt", ["{bad json", "[]", '{"task_id":1,"failure_count":"broken"}', '{"task_id":1,"next_retry_epoch":"later"}', '{"task_id":1,"exhausted":"false"}'])
def test_review_corrupt_attempt_preserves_file_and_allows_next_task(tmp_path,bad_attempt):
    api=server([peer(1),peer(2)]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True)
    corrupt=tracking/"attempt-1.json"; corrupt.write_text(bad_attempt)
    write_status(state,"review","idle",1)
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2"},"Reviewer")
        status=json.loads((state/"review-status.json").read_text())
        assert result.returncode==0 and "Traceback" not in result.stderr
        assert corrupt.read_text()==bad_attempt and not (tracking/"review-1.json").exists()
        assert (tracking/"review-2.json").exists()
        assert status["status"]=="attention" and status["checked_at_epoch"]>1
        assert any(item["reason"]=="invalid_attempt_attention" for item in status["details"])
    finally: api.shutdown()

@pytest.mark.parametrize("field", ["pid","started_epoch"])
def test_review_invalid_numeric_tracker_preserves_evidence_and_reports_attention(tmp_path,field):
    api=server([peer(1),peer(2)]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True)
    tracker=tracking/"review-1.json"; record={"task_id":1,"pid":999999,"started_epoch":1,"attempt_id":"existing"}; record[field]="invalid"; tracker.write_text(json.dumps(record)); original=tracker.read_bytes()
    write_status(state,"review","idle",1)
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2"},"Reviewer")
        status=json.loads((state/"review-status.json").read_text())
        assert result.returncode==0 and "Traceback" not in result.stderr
        assert tracker.read_bytes()==original and (tracking/"review-2.json").exists()
        assert status["status"]=="attention" and status["checked_at_epoch"]>1
        assert any(item["reason"]=="invalid_tracker_fields_attention" for item in status["details"])
    finally: api.shutdown()

@pytest.mark.parametrize("exhausted", [False,True])
@pytest.mark.parametrize("column", ["review","backlog"])
def test_review_temporary_block_preserves_same_submission_retry_budget(tmp_path,exhausted,column):
    task=peer(review_submitted_at="same-generation"); task["blocked"]=True
    task["column"]=column
    api=server([task]); state=tmp_path/"state"; tracking=state/"review-tracking"; tracking.mkdir(parents=True)
    attempt=tracking/"attempt-1.json"; receipt={"task_id":1,"failure_count":3 if exhausted else 1,"exhausted":exhausted,"next_retry_epoch":0 if exhausted else int(time.time())+3600,"review_submitted_at":"same-generation"}; attempt.write_text(json.dumps(receipt))
    runtime=tmp_path/"adapter"; runtime.write_text('#!/bin/sh\n[ "$1" = "--preflight" ] && exit 0\nexit 7\n'); runtime.chmod(0o755)
    env={"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(state),"ENTITY_MC_RUNTIME":"codex","ENTITY_MC_OPENCLAW_BIN":str(runtime),"ENTITY_MC_REVIEW_STARTUP_GRACE_SECS":"0.2"}
    try:
        held=run("mc-review-pull.sh",env,"Reviewer")
        assert held.returncode==0 and json.loads(attempt.read_text())==receipt
        assert json.loads((state/"review-status.json").read_text())["status"]=="idle"
        task.update(blocked=False,column="review")
        resumed=run("mc-review-pull.sh",env,"Reviewer")
        assert resumed.returncode==1 and json.loads(attempt.read_text())==receipt
        assert not (tracking/"review-1.json").exists()
    finally: api.shutdown()

def write_status(state,belt,status,now,**extra):
    value={"schema_version":1,"belt":belt,"agent":"A","checked_at_epoch":now,"status":status,"reason":"fixture","last_handoff_epoch":None,"last_outcome_epoch":None,"active_count":0,"failed_count":0,"exhausted_count":0,"orphan_count":0,"details":[]}; value.update(extra); (state/f"{belt}-status.json").write_text(json.dumps(value))

def test_health_distinguishes_idle_failure_held_stale_and_unreachable(tmp_path):
    now=int(time.time()); agents=[]
    for name in ("Idle","Failed","Held","Stale"):
        state=tmp_path/name; state.mkdir(); log=state/"cron.log"; log.write_text("tick"); os.utime(log,(now,now)); agents.append({"name":name,"host":"local","cron_log":str(log),"state_dir":str(state),"belts":["auto"]})
    write_status(tmp_path/"Idle","auto","idle",now); write_status(tmp_path/"Failed","auto","error",now,failed_count=1)
    write_status(tmp_path/"Held","auto","error",now,failed_count=1); agents[2]["held"]=True
    os.utime(tmp_path/"Stale/cron.log",(now-5000,now-5000)); write_status(tmp_path/"Stale","auto","idle",now)
    agents.append({"name":"Gone","host":"invalid@127.0.0.1","cron_log":"/x","state_dir":"/x","belts":["auto"]})
    inventory=tmp_path/"inventory.json"; inventory.write_text(json.dumps({"agents":agents})); health_state=tmp_path/"health.json"
    result=run("mc-health-check.sh",{"ENTITY_MC_HEALTH_INVENTORY":str(inventory),"ENTITY_MC_HEALTH_STATE":str(health_state),"ENTITY_MC_HEALTH_NOW_EPOCH":str(now),"ENTITY_MC_HEALTH_NO_NOTIFY":"1","ENTITY_MC_HEALTH_SSH_TIMEOUT":"1"})
    records={r["agent"]:r for r in map(json.loads,result.stdout.splitlines()) if "agent" in r}; assert records["Idle"]["status"]=="idle_healthy"; assert records["Failed"]["status"]=="failure"; assert records["Held"]["status"]=="held"; assert records["Stale"]["status"]=="stale"; assert records["Gone"]["status"]=="unreachable"

def test_health_dry_run_does_not_write_or_notify(tmp_path):
    state=tmp_path/"agent"; state.mkdir(); log=state/"cron.log"; log.write_text("tick"); now=int(time.time()); write_status(state,"auto","error",now,failed_count=1)
    inv=tmp_path/"inventory.json"; inv.write_text(json.dumps({"agents":[{"name":"A","host":"local","cron_log":str(log),"state_dir":str(state),"belts":["auto"]}]})); output=tmp_path/"state.json"
    result=run("mc-health-check.sh",{"ENTITY_MC_HEALTH_INVENTORY":str(inv),"ENTITY_MC_HEALTH_STATE":str(output),"ENTITY_MC_HEALTH_NOW_EPOCH":str(now),"MC_ESCALATOR_WEBHOOK":"http://127.0.0.1:1/should-not-run"},"--dry-run")
    assert result.returncode==0 and not output.exists() and '"notification": "suppressed"' in result.stdout

def test_health_requires_every_configured_belt_to_be_fresh(tmp_path):
    state=tmp_path/"agent"; state.mkdir(); log=state/"cron.log"; log.write_text("tick"); now=int(time.time()); write_status(state,"auto","idle",now)
    inv=tmp_path/"inventory.json"; inv.write_text(json.dumps({"agents":[{"name":"A","host":"local","cron_log":str(log),"state_dir":str(state),"belts":["auto","review"]}]}))
    result=run("mc-health-check.sh",{"ENTITY_MC_HEALTH_INVENTORY":str(inv),"ENTITY_MC_HEALTH_NOW_EPOCH":str(now)},"--dry-run")
    record=json.loads(result.stdout.splitlines()[0]); assert record["status"]=="failure" and record["missing_belts"]==["review"]

def test_health_webhook_failure_does_not_persist_transition(tmp_path):
    state=tmp_path/"agent"; state.mkdir(); log=state/"cron.log"; log.write_text("tick"); now=int(time.time()); write_status(state,"auto","error",now,failed_count=1); write_status(state,"review","idle",now)
    inv=tmp_path/"inventory.json"; inv.write_text(json.dumps({"agents":[{"name":"A","host":"local","cron_log":str(log),"state_dir":str(state),"belts":["auto","review"]}]})); health=tmp_path/"health.json"
    result=run("mc-health-check.sh",{"ENTITY_MC_HEALTH_INVENTORY":str(inv),"ENTITY_MC_HEALTH_STATE":str(health),"ENTITY_MC_HEALTH_NOW_EPOCH":str(now),"MC_ESCALATOR_WEBHOOK":"http://127.0.0.1:1/fail"})
    assert result.returncode==1 and not health.exists()

def test_health_declared_remote_hold_skips_transport(tmp_path):
    inv=tmp_path/"inventory.json"; inv.write_text(json.dumps({"agents":[{"name":"HeldRuntime","host":"invalid@127.0.0.1","cron_log":"/missing","state_dir":"/missing","enabled_belts":[],"held":True,"held_reason":"unsupported_runtime:custom"}]}))
    result=run("mc-health-check.sh",{"ENTITY_MC_HEALTH_INVENTORY":str(inv),"ENTITY_MC_HEALTH_STATE":str(tmp_path/"health.json"),"ENTITY_MC_HEALTH_SSH_TIMEOUT":"1","ENTITY_MC_HEALTH_NO_NOTIFY":"1"})
    record=json.loads(result.stdout.splitlines()[0]); assert result.returncode==0 and record["status"]=="held" and record["reason"]=="unsupported_runtime:custom"

def test_health_uses_cron_mtime_without_reading_large_invalid_log(tmp_path):
    state=tmp_path/"agent"; state.mkdir(); log=state/"cron.log"; log.write_bytes(b"\xff"*(2*1024*1024)); now=int(time.time()); os.utime(log,(now,now)); write_status(state,"auto","idle",now)
    inv=tmp_path/"inventory.json"; inv.write_text(json.dumps({"agents":[{"name":"A","host":"local","cron_log":str(log),"state_dir":str(state),"enabled_belts":["auto"]}]}))
    result=run("mc-health-check.sh",{"ENTITY_MC_HEALTH_INVENTORY":str(inv),"ENTITY_MC_HEALTH_NOW_EPOCH":str(now)},"--dry-run")
    record=json.loads(result.stdout.splitlines()[0]); assert result.returncode==0 and record["status"]=="idle_healthy"

def test_shell_wrappers_use_installer_python_contract(tmp_path):
    for name in ("mc-review-pull.sh","mc-health-check.sh"):
        text=(SCRIPTS/name).read_text(); assert "ENTITY_MC_PYTHON_BIN" in text and "ENTITY_MC_PYTHON:-" not in text
        first=tmp_path/(name+".first"); second=tmp_path/(name+".second"); first.symlink_to(SCRIPTS/name); second.symlink_to(first.name)
        python=tmp_path/"python"; python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n'); python.chmod(0o755)
        result=subprocess.run([str(second),"marker"],env={**os.environ,"ENTITY_MC_PYTHON_BIN":str(python)},text=True,capture_output=True)
        assert result.returncode==0 and result.stdout.splitlines()==["-B",str(SCRIPTS/("mc-review-runtime.py" if "review" in name else "mc-health-runtime.py")),"marker"]

@pytest.mark.parametrize("payload", [{}, {"error":"temporary backend error"}, {"tasks":{}}, {"tasks":[{}]}, {"tasks":[{"id":1,"column":"unknown"}]}])
def test_review_invalid_board_preserves_all_receipts_and_reports_error(tmp_path,payload):
    finished=peer(); finished["column"]="done"
    api=server([finished],board_response=payload)
    tracking=tmp_path/"review-tracking"; tracking.mkdir()
    attempt=tracking/"attempt-1.json"; attempt.write_text(json.dumps({"task_id":1,"failure_count":2}))
    tracker=tracking/"review-1.json"; tracker.write_text(json.dumps({"task_id":1,"pid":999999,"started_epoch":1,"attempt_id":"old"}))
    original={p.name:p.read_bytes() for p in (attempt,tracker)}
    try:
        result=run("mc-review-pull.sh",{"ENTITY_MC_MC_URL":f"http://127.0.0.1:{api.server_port}","ENTITY_MC_STATE_DIR":str(tmp_path)},"Reviewer")
        assert result.returncode==1
        assert {p.name:p.read_bytes() for p in (attempt,tracker)}==original
        assert not (tracking/"archive").exists() and Handler.writes==[]
        status=json.loads((tmp_path/"review-status.json").read_text())
        assert status["status"]=="error" and "api_invalid_task_list" in result.stdout
    finally: api.shutdown()

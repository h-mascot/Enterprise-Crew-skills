import json
import os
import subprocess
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "source-scripts" / "mc.sh"
          if (ROOT / "source-scripts" / "mc.sh").exists()
          else ROOT / "bundle" / "source-scripts" / "mc.sh")


class FixtureHandler(BaseHTTPRequestHandler):
    status = 200
    body = {"id": 7, "column": "todo", "blocked": False, "output": "receipt"}
    requests = []
    get_body = None
    get_status = 200

    def do_PATCH(self):
        self._write_response()

    def do_POST(self):
        self._write_response()

    def do_GET(self):
        body = type(self).get_body or {
            "id": 7,
            "name": "Fixture task",
            "description": "",
            "column": "doing",
            "assignee": "Test Agent",
            "metadata": "{}",
        }
        encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(type(self).get_status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _write_response(self):
        length = int(self.headers.get("content-length", "0"))
        raw = self.rfile.read(length)
        type(self).requests.append((self.command, self.path, raw.decode()))
        body = type(self).body
        encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(type(self).status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


class McCliTests(unittest.TestCase):
    def setUp(self):
        FixtureHandler.status = 200
        FixtureHandler.body = {"id": 7, "column": "todo", "blocked": False, "output": "receipt"}
        FixtureHandler.requests = []
        FixtureHandler.get_body = None
        FixtureHandler.get_status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def run_cli(self, *args):
        env = os.environ.copy()
        env.update({
            "MC_URL": f"http://127.0.0.1:{self.server.server_port}",
            "MC_USER": 'Test "Agent"',
            "ENTITY_MC_CONNECT_TIMEOUT": "1",
            "ENTITY_MC_MAX_TIME": "2",
        })
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )

    def test_review_preserves_specific_packet_and_existing_approval(self):
        output = "Completed the current fixture with verified evidence for this submission."
        for flag in ('requires_approval', 'requires_human_read'):
            with self.subTest(flag=flag):
                packet = {"requested_outcome": "Keep the endpoint inaccessible to anonymous users", "done_criteria": ["Anonymous requests return HTTP 401"], flag: True, "external_risk": True, "evidence": "Prior evidence"}
                FixtureHandler.get_body = {"id": 7, "column": "doing", "assignee": "Builder", "metadata": {"reviewer": "Operator", "review_packet": packet}}
                FixtureHandler.requests = []
                FixtureHandler.body = {"id": 7, "column": "done", "output": output}
                result = self.run_cli("deliver", "7", output)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(FixtureHandler.requests, [])
                FixtureHandler.body = {"id": 7, "column": "review", "output": output}
                result = self.run_cli("review", "7", output, "--risk", "low", "--reviewer", "Peer")
                self.assertEqual(result.returncode, 0, result.stderr)
                metadata = json.loads(json.loads(FixtureHandler.requests[0][2])["metadata"])
                self.assertTrue(metadata['human_gate_required'])
                self.assertEqual(metadata["review_type"], 'human')
                current_packet = metadata["review_packet"]
                self.assertEqual(current_packet["requested_outcome"], packet["requested_outcome"])
                self.assertEqual(current_packet["done_criteria"], packet["done_criteria"])
                self.assertTrue(current_packet[flag])
                self.assertTrue(current_packet["external_risk"])
                self.assertEqual(current_packet["evidence"], output)

    def test_default_peer_reviewer_must_be_independent_before_submission(self):
        output = "Completed the fixture with verified output and reproducible evidence."
        FixtureHandler.get_body = {"id": 7, "column": "doing", "assignee": 'Builder', "metadata": {}}
        FixtureHandler.body = {"id": 7, "column": "review", "output": output}
        with patch.dict(os.environ, {"MC_USER": 'Reviewer'}):
            env = dict(os.environ, MC_URL=f"http://127.0.0.1:{self.server.server_port}")
            result = subprocess.run(["bash", str(SCRIPT), "review", "7", output, "--risk", "low"], env=env, capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(FixtureHandler.requests, [])
        self.assertIn("independent", result.stderr.lower())

    def test_review_explicit_proof_is_serialized_and_existing_proof_is_preserved(self):
        output = "Completed the current fixture with verified evidence and an accessible artifact."
        proof = 'https://example.invalid/evidence/receipt.txt?check="verified"&part=1'
        FixtureHandler.get_body = {"id": 7, "column": "doing", "assignee": "Builder", "metadata": {"proof_ref": "original-proof", "proof_url": "https://example.invalid/original"}}
        FixtureHandler.body = {"id": 7, "column": "review", "output": output}
        result = self.run_cli("review", "7", output, "--reviewer", "Peer", "--proof", proof)
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(json.loads(FixtureHandler.requests[0][2])["metadata"])
        self.assertEqual(metadata["proof_ref"], proof)
        self.assertEqual(metadata["proof_url"], "https://example.invalid/original")
        FixtureHandler.requests = []
        result = self.run_cli("review", "7", output, "--reviewer", "Peer")
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(json.loads(FixtureHandler.requests[0][2])["metadata"])
        self.assertEqual(metadata["proof_ref"], "original-proof")

    def test_invalid_review_packet_blocks_review_and_direct_delivery(self):
        output = "Completed the fixture with reviewed evidence and an accessible receipt."
        for packet in ("invalid packet", ["invalid packet"]):
            for command in ("review", "deliver"):
                with self.subTest(packet=packet, command=command):
                    FixtureHandler.get_body = {"id": 7, "column": "doing", "assignee": "Builder", "metadata": {"review_type": "human", "reviewer": "Operator", "review_packet": packet}}
                    FixtureHandler.body = {"id": 7, "column": "review" if command == "review" else "done", "output": output}
                    FixtureHandler.requests = []
                    extra = () if command == "deliver" else ("--reviewer", "Peer")
                    result = self.run_cli(command, "7", output, *extra)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(FixtureHandler.requests, [])

    def test_review_invalid_task_reads_never_mutate(self):
        cases = [
            (400, {"error": "bad request"}),
            (503, {"error": "task storage unavailable"}),
            (200, {"error": "logical failure"}),
            (200, b"not-json"),
            (200, {"id": 8, "column": "doing", "metadata": {}}),
        ]
        output = "Completed the fixture with reproducible checks and a complete receipt."
        for status, body in cases:
            with self.subTest(status=status, body=body):
                FixtureHandler.get_status = status
                FixtureHandler.get_body = body
                FixtureHandler.requests = []
                FixtureHandler.body = {"id": 7, "column": "review", "output": output}
                result = self.run_cli("review", "7", output, "--risk", "low")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(FixtureHandler.requests, [])

    def test_move_rejects_json_http_error(self):
        FixtureHandler.status = 409
        FixtureHandler.body = {"error": "Potential duplicate tasks found"}
        result = self.run_cli("move", "7", "todo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Potential duplicate tasks found", result.stderr + result.stdout)

    def test_comment_rejects_json_http_error(self):
        FixtureHandler.status = 400
        FixtureHandler.body = {"error": "comment body is required"}
        result = self.run_cli("note", "7", "hello")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("comment body is required", result.stderr + result.stdout)

    def test_move_rejects_invalid_json_success(self):
        FixtureHandler.status = 200
        FixtureHandler.body = b"not-json"
        result = self.run_cli("move", "7", "todo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-JSON", result.stderr + result.stdout)

    def test_move_rejects_error_payload_even_with_2xx(self):
        FixtureHandler.body = {"error": "logical failure"}
        result = self.run_cli("move", "7", "todo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("logical failure", result.stderr + result.stdout)

    def test_move_requires_expected_column(self):
        FixtureHandler.body = {"id": 7, "column": "doing"}
        result = self.run_cli("move", "7", "todo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("did not move to 'todo'", result.stderr + result.stdout)

    def test_move_uses_safe_json_for_actor(self):
        result = self.run_cli("move", "7", "todo")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(FixtureHandler.requests[0][2])
        self.assertEqual(payload, {"column": "todo", "actor": 'Test "Agent"'})

    def test_unblock_requires_blocked_false_readback(self):
        FixtureHandler.body = {"id": 7, "blocked": True}
        result = self.run_cli("unblock", "7", "Recovered through the verified path")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("blocked state did not become 'false'", result.stderr + result.stdout)

    def test_block_preserves_reason_in_canonical_task_field(self):
        reason = "Cannot finish without the required access; checked available credentials and need an owner decision."
        FixtureHandler.body = {"id": 7, "blocked": True}
        result = self.run_cli("block", "7", reason)
        self.assertEqual(result.returncode, 0, result.stderr)
        update = next(json.loads(raw) for method, path, raw in FixtureHandler.requests if method == "PATCH")
        self.assertEqual(update.get("blocker_reason"), reason)

    def test_output_requires_exact_output_readback(self):
        FixtureHandler.body = {"id": 7, "column": "doing", "output": "different"}
        result = self.run_cli("output", "7", "expected receipt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("output was not preserved", result.stderr + result.stdout)

    def test_local_evidence_link_does_not_rewrite_matching_url(self):
        original_url = "https://example.invalid/output/report.md"
        output = f"Local output/report.md; external {original_url}"
        result = self.run_cli("output", "7", output)
        payload = json.loads(FixtureHandler.requests[0][2])
        self.assertIn("/docs/source/", payload["output"])
        self.assertIn(original_url, payload["output"])
        self.assertNotIn("example.invalid/http", payload["output"])

    def test_deliver_does_not_print_success_after_patch_error(self):
        FixtureHandler.status = 409
        FixtureHandler.body = {"error": "completion gate rejected"}
        result = self.run_cli(
            "deliver",
            "7",
            "Delivered a complete fixture artifact in chat.",
            "--source",
            "chat",
            "--source-id",
            "fixture/message",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("completion gate rejected", result.stderr + result.stdout)
        self.assertNotIn("Delivered directly to done", result.stdout)

    def test_every_api_write_uses_checked_helper(self):
        source = SCRIPT.read_text()
        direct_write_curls = [
            line for line in source.splitlines()
            if "curl " in line and ("-X PATCH" in line or "-X POST" in line)
        ]
        self.assertEqual(direct_write_curls, [])
        self.assertIn("--connect-timeout", source)
        self.assertIn("--max-time", source)

    def test_create_requires_returned_task_identity_and_state(self):
        FixtureHandler.body = {}
        result = self.run_cli("create", "Fixture task", "A fixture description")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("created task receipt", result.stderr + result.stdout)

    def test_create_sends_canonical_principals(self):
        result = self.run_cli("create", "Fixture task", "A fixture description")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(FixtureHandler.requests[0][2])
        for field in ("created_by_principal_id", "initiator_principal_id", "owner_principal_id"):
            self.assertEqual(payload[field], 'Test "Agent"')
        self.assertEqual(payload["initiator_type"], "agent")
        self.assertEqual(payload["owner_principal_type"], "agent")

    def test_archive_requires_archived_readback(self):
        FixtureHandler.body = {"id": 7, "column": "backlog", "archived": False}
        result = self.run_cli("archive", "7")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("archived state", result.stderr + result.stdout)

    def test_unauthorized_peer_cannot_accept_review(self):
        FixtureHandler.get_body = {
            "id": 7, "column": "review", "assignee": "Builder",
            "metadata": {"review_type": "peer", "reviewer": "Reviewer", "submitted_by": "Builder"},
        }
        result = self.run_cli("accept-review", "7", "Verified the complete fixture output.")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("assigned reviewer", result.stderr + result.stdout)
        self.assertEqual(FixtureHandler.requests, [])

    def test_non_operator_actor_cannot_decide_human_review(self):
        FixtureHandler.get_body = {
            "id": 7, "column": "review", "assignee": "Builder",
            "metadata": {"review_type": "human", "reviewer": "Operator", "human_gate_required": True},
        }
        result = self.run_cli("request-fix", "7", "The fixture still lacks required proof.")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ENTITY_MC_HUMAN_REVIEWERS", result.stderr + result.stdout)
        self.assertEqual(FixtureHandler.requests, [])

    def test_assigned_independent_peer_can_accept_review(self):
        FixtureHandler.get_body = {
            "id": 7, "column": "review", "assignee": "Builder",
            "metadata": {"review_type": "peer", "reviewer": 'Test "Agent"',
                         "submitted_by": "Builder", "created_by": "Builder"},
        }
        FixtureHandler.body = {"id": 7, "column": "done"}
        result = self.run_cli("accept-review", "7", "Verified the complete fixture output.")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(FixtureHandler.requests[0][0:2], ("PATCH", "/api/tasks/7"))

    def test_update_rejects_wrong_task_receipt(self):
        FixtureHandler.body = {"id": 8, "column": "todo"}
        result = self.run_cli("move", "7", "todo")
        self.assertNotEqual(result.returncode, 0)

    def test_review_failed_read_cannot_replace_existing_metadata(self):
        FixtureHandler.get_body = {"error": "Task storage unavailable"}
        output = "Completed the fixture with reproducible checks and a complete receipt."
        FixtureHandler.body = {"id": 7, "column": "review", "output": output}
        result = self.run_cli("review", "7", output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(FixtureHandler.requests, [])

    def test_human_gate_cannot_be_bypassed_with_deliver(self):
        FixtureHandler.get_body = {
            "id": 7, "column": "review", "assignee": "Builder",
            "metadata": {"human_gate_required": True, "reviewer": "Operator"},
        }
        FixtureHandler.body = {"id": 7, "column": "done", "output": "The complete fixture was delivered in chat."}
        result = self.run_cli("deliver", "7", FixtureHandler.body["output"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(FixtureHandler.requests, [])

    def test_mixed_case_human_review_type_survives_delivery_and_resubmission(self):
        output = "Completed the fixture with reproducible checks and a complete receipt."
        FixtureHandler.get_body = {
            "id": 7, "column": "doing", "assignee": "Builder",
            "metadata": {"review_type": "Human", "reviewer": "Operator"},
        }
        FixtureHandler.body = {"id": 7, "column": "done", "output": output}
        result = self.run_cli("deliver", "7", output)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(FixtureHandler.requests, [])
        FixtureHandler.body = {"id": 7, "column": "review", "output": output}
        result = self.run_cli("review", "7", output, "--risk", "low")
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(json.loads(FixtureHandler.requests[0][2])["metadata"])
        self.assertTrue(metadata["human_gate_required"])
        self.assertEqual(metadata["reviewer"], "Operator")

    def test_review_submission_has_new_round_and_preserves_human_gate(self):
        output = "Completed the fixture with reproducible checks and a complete receipt."
        FixtureHandler.get_body = {
            "id": 7, "name": "Fixture task", "column": "doing", "assignee": "Builder",
            "metadata": {"human_gate_required": True, "review_type": "human", "reviewer": "Operator"},
        }
        FixtureHandler.body = {"id": 7, "column": "review", "output": output}
        result = self.run_cli("review", "7", output, "--risk", "low")
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata = json.loads(json.loads(FixtureHandler.requests[0][2])["metadata"])
        self.assertTrue(metadata["human_gate_required"])
        self.assertEqual(metadata["reviewer"], "Operator")
        self.assertGreater(float(metadata["review_submitted_at"]), 0)

    def test_review_preserves_gate_derived_from_configured_human_reviewer(self):
        output = "Completed the fixture with reproducible checks and a complete receipt."
        FixtureHandler.get_body = {"id":7,"column":"doing","assignee":"Builder","metadata":{"reviewer":"Operator","review_type":"peer"}}
        FixtureHandler.body = {"id":7,"column":"review","output":output}
        with patch.dict(os.environ,{"ENTITY_MC_HUMAN_REVIEWERS":"Operator"}):
            result=self.run_cli("review","7",output,"--risk","low","--reviewer","Peer")
        self.assertEqual(result.returncode,0,result.stderr)
        metadata=json.loads(json.loads(FixtureHandler.requests[0][2])["metadata"])
        self.assertTrue(metadata["human_gate_required"])

    def test_actual_submitter_cannot_review_work_assigned_to_someone_else(self):
        output="Completed the fixture with reproducible checks and a complete receipt."
        FixtureHandler.get_body={"id":7,"column":"doing","assignee":"Other Agent","metadata":{"created_by":"Other Agent"}}
        FixtureHandler.body={"id":7,"column":"review","output":output}
        result=self.run_cli("review","7",output,"--reviewer","Peer")
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertNotIn("jq: error", result.stderr)
        metadata=json.loads(json.loads(FixtureHandler.requests[0][2])["metadata"])
        self.assertEqual(metadata["submitted_by"],'Test "Agent"')
        metadata["reviewer"] = 'Test "Agent"'
        FixtureHandler.get_body.update(column="review",metadata=metadata)
        FixtureHandler.requests=[]
        FixtureHandler.body={"id":7,"column":"done"}
        result=self.run_cli("accept-review","7","Verified the complete fixture output.")
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(FixtureHandler.requests,[])

    def test_stale_review_generation_cannot_decide_a_resubmission(self):
        FixtureHandler.get_body={"id":7,"column":"review","assignee":"Builder","metadata":{"reviewer":'Test "Agent"',"submitted_by":"Builder","review_submitted_at":"generation-2"}}
        FixtureHandler.body={"id":7,"column":"done"}
        for command in ("accept-review","request-fix"):
            with self.subTest(command=command):
                FixtureHandler.requests=[]
                result=self.run_cli(command,"7","Verified the prior fixture output.","--submission","generation-1")
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(FixtureHandler.requests,[])

    def test_current_review_generation_can_be_accepted_and_is_required(self):
        FixtureHandler.get_body={"id":7,"column":"review","assignee":"Builder","metadata":{"reviewer":'Test "Agent"',"submitted_by":"Builder","review_submitted_at":"generation-2"}}
        FixtureHandler.body={"id":7,"column":"done"}
        rejected=self.run_cli("accept-review","7","Verified the complete fixture output.")
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(FixtureHandler.requests,[])
        accepted=self.run_cli("accept-review","7","Verified the complete fixture output.","--submission","generation-2")
        self.assertEqual(accepted.returncode,0,accepted.stderr)
        self.assertEqual(FixtureHandler.requests[0][0],"PATCH")

    def test_review_options_require_values_without_hanging(self):
        output="Completed the fixture with reproducible checks and a complete receipt."
        for option in ("--risk","--reviewer","--proof"):
            with self.subTest(option=option):
                result=self.run_cli("review","7",output,option)
                self.assertNotEqual(result.returncode,0)
                self.assertEqual(FixtureHandler.requests,[])


    def test_review_generation_can_be_bound_through_worker_environment(self):
        FixtureHandler.get_body={"id":7,"column":"review","assignee":"Builder","metadata":{"reviewer":'Test "Agent"',"submitted_by":"Builder","review_submitted_at":"generation-2"}}
        FixtureHandler.body={"id":7,"column":"done"}
        with patch.dict(os.environ,{"ENTITY_MC_REVIEW_SUBMISSION":"generation-1"}):
            rejected=self.run_cli("accept-review","7","Verified the previous fixture output.")
        self.assertNotEqual(rejected.returncode,0)
        self.assertEqual(FixtureHandler.requests,[])
        with patch.dict(os.environ,{"ENTITY_MC_REVIEW_SUBMISSION":"generation-2"}):
            accepted=self.run_cli("accept-review","7","Verified the current fixture output.")
        self.assertEqual(accepted.returncode,0,accepted.stderr)


if __name__ == "__main__":
    unittest.main()

import os
import json
import pathlib
import shlex
import shutil
import subprocess
import tempfile
import unittest

SOURCE = pathlib.Path(__file__).resolve().parents[1]

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='entity-mc-install-')
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.bundle = self.root / 'bundle'
        shutil.copytree(SOURCE, self.bundle, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        self.home = self.root / 'Agent Home'
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.cron = self.root / 'crontab'
        self.cron.write_text('17 * * * * /unrelated/job\n')
        mock = self.bin / 'crontab'
        mock.write_text('#!/bin/sh\nif [ "$1" = "-l" ]; then cat "$TEST_CRON"; else cp "$1" "$TEST_CRON"; fi\n')
        mock.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.bin)+os.pathsep+os.environ['PATH'], TEST_CRON=str(self.cron))
        self.manifest = self.root / 'agent.env'
        self.manifest.write_text(f'''ENTITY_MC_AGENT_NAME="Fixture"
ENTITY_MC_TARGET_HOME="{self.home}"
ENTITY_MC_RUNTIME="hermes"
ENTITY_MC_BASH_BIN="/bin/bash"
ENTITY_MC_EXEC_PATH="/verified/runtime/bin:/usr/bin:/bin"
ENTITY_MC_HERMES_HOME="{self.home}/hermes-profile"
ENTITY_MC_OPENCLAW_STATE_DIR="{self.home}/openclaw-profile"
ENTITY_MC_ENABLE_AUTO_PULL="false"
ENTITY_MC_ENABLE_REVIEW_PULL="false"
ENTITY_MC_ENABLE_STALL_CHECK="false"
''')

    def run_script(self, name, check=True):
        return subprocess.run(['bash', str(self.bundle/name), '--manifest', str(self.manifest)], env=self.env, text=True, capture_output=True, check=check)

    def test_python_support_is_installed_as_module_not_shell_wrapper(self):
        (self.bundle/'source-scripts/mc_fixture.py').write_text('VALUE = "support"\n')
        self.run_script('install.sh')
        installed=self.home/'.entity-mc/runtime/mc_fixture.py'
        self.assertTrue(installed.exists(), 'Python runtime support must ship with entry points')
        self.assertEqual(installed.read_text(),'VALUE = "support"\n')
        self.assertFalse((self.home/'scripts/mc_fixture.py').exists(), 'support modules are not command wrappers')

    def test_wrapper_preserves_explicit_executor_environment(self):
        (self.bundle/'source-scripts/mc-auto-pull.sh').write_text('#!/bin/bash\nprintf "%s\\n" "$PATH" "$HERMES_HOME" "$OPENCLAW_STATE_DIR"\n')
        self.run_script('install.sh')
        result=subprocess.run(['bash',str(self.home/'scripts/mc-auto-pull.sh')],env=self.env,text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.splitlines(),['/verified/runtime/bin:/usr/bin:/bin',str(self.home/'hermes-profile'),str(self.home/'openclaw-profile')])

    def test_symlink_mode_preserves_manifest_authority(self):
        with self.manifest.open('a') as f:
            f.write('\nENTITY_MC_MODE="symlink"\nENTITY_MC_DISPATCH_HOST="fixture-host"\n')
        (self.bundle/'source-scripts/mc-auto-pull.sh').write_text('#!/bin/bash\nprintf "%s\\n" "$ENTITY_MC_DISPATCH_HOST" "$HERMES_HOME"\n')
        self.run_script('install.sh')
        wrapper=self.home/'scripts/mc-auto-pull.sh'
        self.assertTrue(wrapper.is_symlink())
        result=subprocess.run(['bash',str(wrapper)],env=self.env,text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.splitlines(),['fixture-host',str(self.home/'hermes-profile')])
        self.run_script('verify.sh')

    def test_wrapper_preserves_dispatch_review_and_health_environment(self):
        with self.manifest.open('a') as f:
            f.write('''\nENTITY_MC_DISPATCH_HOST="fixture-host"
ENTITY_MC_REVIEW_MAX_ATTEMPTS="7"
ENTITY_MC_HEALTH_NO_NOTIFY="1"
''')
        (self.bundle/'source-scripts/mc-auto-pull.sh').write_text(
            '#!/bin/bash\nprintf "%s\\n" "$ENTITY_MC_DISPATCH_HOST" "$ENTITY_MC_REVIEW_MAX_ATTEMPTS" "$ENTITY_MC_HEALTH_NO_NOTIFY"\n'
        )
        self.run_script('install.sh')
        result=subprocess.run(['bash',str(self.home/'scripts/mc-auto-pull.sh')],env=self.env,text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.splitlines(),['fixture-host','7','1'])

    def test_wrapper_preserves_configured_docs_source_and_review_log(self):
        review_log = self.home / 'custom review.log'
        with self.manifest.open('a') as manifest:
            manifest.write(f'\nENTITY_MC_DOCS_SOURCE_ID="fixture-source"\nENTITY_MC_REVIEW_EXEC_LOG="{review_log}"\n')
        (self.bundle/'source-scripts/mc.sh').write_text(
            '#!/bin/bash\nprintf "%s\\n" "$ENTITY_MC_DOCS_SOURCE_ID" "$ENTITY_MC_REVIEW_EXEC_LOG"\n'
        )
        self.run_script('install.sh')
        result = subprocess.run(
            ['bash', str(self.home/'scripts/mc.sh')], env=self.env, text=True, capture_output=True, check=True,
        )
        self.assertEqual(result.stdout.splitlines(), ['fixture-source', str(review_log)])

    def test_repeated_install_preserves_unrelated_cron_and_hold(self):
        self.run_script('install.sh'); self.run_script('install.sh')
        self.assertEqual(self.cron.read_text().count('# BEGIN ENTITY_MC:Fixture'),1)
        self.assertIn('17 * * * * /unrelated/job',self.cron.read_text())
        self.assertNotIn('mc-auto-pull.sh',self.cron.read_text())
        self.run_script('verify.sh')

    def test_verifier_rejects_commented_out_enabled_entry(self):
        with self.manifest.open('a') as f:f.write('\nENTITY_MC_ENABLE_AUTO_PULL="true"\n')
        self.run_script('install.sh')
        self.cron.write_text('\n'.join('# HOLD '+line if 'mc-auto-pull.sh' in line else line for line in self.cron.read_text().splitlines())+'\n')
        self.assertNotEqual(self.run_script('verify.sh',False).returncode,0,'commented schedule is not installed active scheduling')

    def test_verifier_rejects_active_entry_for_disabled_schedule(self):
        self.run_script('install.sh')
        content=self.cron.read_text()
        marker='# END ENTITY_MC:Fixture'
        self.cron.write_text(content.replace(marker, '* * * * * /tmp/mc-auto-pull.sh\n'+marker))
        self.assertNotEqual(self.run_script('verify.sh',False).returncode,0,'disabled schedule must have zero active entries')

    def test_verifier_checks_copy_wrapper_bytes_and_symlink_target(self):
        self.run_script('install.sh')
        wrapper=self.home/'scripts/mc-auto-pull.sh'
        wrapper.write_text('#!/bin/bash\nexit 0\n')
        wrapper.chmod(0o755)
        self.assertNotEqual(self.run_script('verify.sh',False).returncode,0,'executable wrapper with wrong bytes must fail')

        with self.manifest.open('a') as f:f.write('\nENTITY_MC_MODE="symlink"\n')
        self.run_script('install.sh')
        wrong=self.root/'wrong-target.sh'
        wrong.write_text('#!/bin/bash\nexit 0\n')
        wrong.chmod(0o755)
        wrapper.unlink()
        wrapper.symlink_to(wrong)
        self.assertNotEqual(self.run_script('verify.sh',False).returncode,0,'symlink wrapper must target the exact runtime entrypoint')

    def test_release_version_cannot_be_overwritten_with_new_bytes(self):
        self.run_script('install.sh')
        live=self.home/'.entity-mc/current/mc-auto-pull.sh'
        original=live.read_bytes()
        (self.bundle/'source-scripts/mc-auto-pull.sh').write_text('#!/bin/bash\necho changed\n')
        self.assertNotEqual(self.run_script('install.sh',False).returncode,0)
        self.assertEqual(live.read_bytes(),original)

    def test_release_version_cannot_be_reused_for_different_context(self):
        self.run_script('install.sh')
        (self.bundle/'context/entity-mc-context.md').write_text('Changed operating rules\n')
        self.assertNotEqual(self.run_script('install.sh',False).returncode,0)

    def test_generated_cron_runs_in_workspace_with_spaces(self):
        with self.manifest.open('a') as f:f.write('\nENTITY_MC_ENABLE_AUTO_PULL="true"\n')
        (self.bundle/'source-scripts/mc-auto-pull.sh').write_text('#!/bin/bash\nprintf "cron fixture executed\\n"\n')
        self.run_script('install.sh')
        line=next(line for line in self.cron.read_text().splitlines() if 'mc-auto-pull.sh' in line)
        command=line.split(None,5)[5]
        result=subprocess.run(['/bin/bash','-c',command],env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('cron fixture executed',(self.home/'.entity-mc/cron.log').read_text())

    def test_auto_install_preserves_runtime_path_under_minimal_cron_environment(self):
        for name in ('node', 'openclaw'):
            executable = self.bin / name
            executable.write_text('#!/bin/sh\nprintf "fixture runtime\\n"\n')
            executable.chmod(0o755)
        (self.bundle/'source-scripts/mc-auto-pull.sh').write_text(
            '#!/bin/bash\ncommand -v node\ncommand -v openclaw\n'
        )
        subprocess.run(
            ['bash', str(self.bundle/'install-auto.sh'), '--workspace', str(self.home), '--agent', 'Fixture'],
            env=self.env, capture_output=True, text=True, check=True,
        )
        cron_line = next(line for line in self.cron.read_text().splitlines() if 'mc-auto-pull.sh' in line)
        result = subprocess.run(
            ['/bin/bash', '-c', cron_line.split(None, 5)[5]],
            env={'PATH': '/usr/bin:/bin', 'HOME': str(self.home)}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.home/'.entity-mc/cron.log').read_text().splitlines(),
                         [str(self.bin/'node'), str(self.bin/'openclaw')])

    def test_existing_release_rejects_obsolete_extra_file(self):
        self.run_script('install.sh')
        release=self.home/'.entity-mc/current'
        (release/'obsolete-runtime.sh').write_text('#!/bin/bash\n')
        result=self.run_script('install.sh',False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unexpected file',result.stderr)

    def test_legacy_runtime_backup_remains_a_valid_rollback_target(self):
        legacy=self.home/'.entity-mc/runtime'
        legacy.mkdir(parents=True)
        (legacy/'VERSION').write_text('legacy-version\n')
        self.run_script('install.sh')
        previous=pathlib.Path((self.home/'.entity-mc/previous-release-path').read_text().strip())
        self.assertEqual((previous/'VERSION').read_text(),'legacy-version\n')
        self.assertNotEqual(previous,legacy)

    def test_rollback_preserves_wrapper_environment_and_previous_bytes(self):
        script=self.bundle/'source-scripts/mc-auto-pull.sh'
        script.write_text('#!/bin/bash\nprintf "%s\\n" "$ENTITY_MC_RUNTIME" "$HERMES_HOME"\n')
        old_version=(self.bundle/'VERSION').read_text()
        self.run_script('install.sh')
        (self.bundle/'VERSION').write_text('new-fixture-version\n')
        script.write_text('#!/bin/bash\necho new\n')
        self.run_script('install.sh')
        self.run_script('rollback.sh')
        result=subprocess.run(['bash',str(self.home/'scripts/mc-auto-pull.sh')],env=self.env,text=True,capture_output=True,check=True)
        self.assertEqual(result.stdout.splitlines(),['hermes',str(self.home/'hermes-profile')])
        self.assertEqual((self.home/'.entity-mc/current-version').read_text(),old_version)

    def test_rollback_restores_context_visible_to_workers(self):
        context=self.bundle/'context/entity-mc-context.md'
        original=context.read_text()
        self.run_script('install.sh')
        (self.bundle/'VERSION').write_text('next-context-version\n')
        context.write_text('New context contract\n')
        self.run_script('install.sh')
        self.run_script('rollback.sh')
        self.assertEqual((self.home/'.entity-mc/context/entity-mc-context.md').read_text(),original)
        self.assertEqual((self.home/'memory/entity-mc/entity-mc-context.md').read_text(),original)

    def test_rollback_accepts_release_before_review_dispatch_existed(self):
        self.run_script('install.sh')
        previous=self.home/'.entity-mc/current'
        (previous/'mc-review-pull.sh').unlink()
        (self.bundle/'VERSION').write_text('next-runtime-version\n')
        with self.manifest.open('a') as f:f.write('\nENTITY_MC_ENABLE_REVIEW_PULL="true"\n')
        self.run_script('install.sh')
        self.assertIn('mc-review-pull.sh',self.cron.read_text())
        self.run_script('rollback.sh')
        self.assertTrue((self.home/'scripts/mc-auto-pull.sh').is_file())
        self.assertFalse((self.home/'scripts/mc-review-pull.sh').exists())
        active=[line for line in self.cron.read_text().splitlines() if not line.lstrip().startswith('#')]
        self.assertFalse(any('mc-review-pull.sh' in line for line in active))

    def test_verifier_checks_installed_context(self):
        self.run_script('install.sh')
        (self.home/'memory/entity-mc/entity-mc-context.md').write_text('stale context\n')
        self.assertNotEqual(self.run_script('verify.sh',False).returncode,0)

    def test_installed_context_builder_loads_operating_rules_and_task(self):
        with self.manifest.open('a') as f:f.write(f'\nENTITY_MC_EXEC_PATH={shlex.quote(self.env["PATH"])}\n')
        self.run_script('install.sh')
        result=subprocess.run(
            ['/bin/bash',str(self.home/'scripts/mc-build-context.sh')],
            input=json.dumps({'task_id':'7','task_name':'Fixture task','task_description':'Unique fixture details'}),
            env=dict(self.env,HOME=str(self.home),QMD_BIN='/missing-fixture-qmd'),
            capture_output=True,text=True,timeout=5,
        )
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('Entity MC Operating Context',result.stdout)
        self.assertIn('Unique fixture details',result.stdout)

if __name__=='__main__':unittest.main()

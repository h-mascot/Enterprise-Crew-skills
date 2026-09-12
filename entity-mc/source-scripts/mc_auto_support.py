"""Small, side-effect-explicit primitives shared by local conveyor runners."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile


class BeltError(RuntimeError):
    pass


def atomic_json(path, value):
    """jq validates/escapes every persistent JSON receipt; replacement is atomic."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(['jq', '-c', '.'], input=json.dumps(value), text=True,
                            capture_output=True, check=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(result.stdout)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default
    except (ValueError, OSError) as exc:
        raise BeltError('invalid_local_json:' + Path(path).name) from exc


def request(url, agent, method='GET', body=None, timeout=20):
    command = ['curl', '-sS', '--max-time', str(timeout), '-X', method, url,
               '-H', 'X-Agent-Name: ' + agent, '-w', '\n%{http_code}']
    if body is not None:
        command += ['-H', 'Content-Type: application/json', '-d', json.dumps(body)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout+2)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BeltError('api_transport_error') from exc
    if result.returncode:
        raise BeltError('api_transport_error')
    payload, separator, code = result.stdout.rpartition('\n')
    if not separator or not code.isdigit() or not 200 <= int(code) < 300:
        raise BeltError('api_http_' + (code if code.isdigit() else 'invalid_status'))
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise BeltError('api_invalid_json') from exc


def task_object(value, task_id, column=None, agent=None):
    if isinstance(value, dict) and isinstance(value.get('task'), dict):
        value = value['task']
    if not isinstance(value, dict) or str(value.get('id')) != str(task_id):
        raise BeltError('api_wrong_task')
    if column is not None and value.get('column') != column:
        raise BeltError('api_wrong_state')
    if agent is not None and str(value.get('assignee') or '').casefold() != agent.casefold():
        raise BeltError('api_wrong_assignee')
    return value


def executable(value):
    resolved = shutil.which(value)
    if not resolved or not os.access(resolved, os.X_OK):
        raise BeltError('runtime_not_found')
    return str(Path(resolved).absolute())


def probe(command, reason):
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=30)
        if result.returncode:
            raise BeltError(reason)
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BeltError(reason) from exc


def preflight(runtime, environ=None):
    """Check the exact configured executable/config without loading shell dotfiles.

    Deliberately called only during real execution: runtime CLIs may initialize
    caches even for version/config commands, so dry-run must never invoke them.
    """
    env = os.environ if environ is None else environ
    if runtime not in ('openclaw', 'hermes', 'codex'):
        raise BeltError('runtime_unsupported:' + runtime)
    key = 'ENTITY_MC_HERMES_BIN' if runtime == 'hermes' else 'ENTITY_MC_OPENCLAW_BIN'
    if runtime == 'codex' and not env.get(key):
        raise BeltError('codex_adapter_required')
    binary = executable(env.get(key) or runtime)
    if runtime == 'openclaw':
        node = executable(env.get('ENTITY_MC_NODE_BIN') or 'node')
        # The OpenClaw shebang must resolve the very Node executable we check.
        actual_node = shutil.which('node')
        if not actual_node or Path(node).resolve() != Path(actual_node).resolve():
            raise BeltError('node_path_mismatch')
        version = probe([node, '--version'], 'node_preflight_failed')
        try:
            parts = tuple(int(p) for p in version.lstrip('v').split('.')[:3])
        except ValueError as exc:
            raise BeltError('node_version_invalid') from exc
        if parts < (22, 22, 3):
            raise BeltError('node_version_unsupported')
        probe([binary, '--version'], 'runtime_version_failed')
        validation = probe([binary, 'config', 'validate', '--json'], 'runtime_config_invalid')
        try:
            config_result = json.loads(validation)
        except ValueError as exc:
            raise BeltError('runtime_config_invalid_json') from exc
        if not isinstance(config_result, dict) or config_result.get('valid') is not True:
            raise BeltError('runtime_config_invalid')
    elif runtime == 'hermes':
        probe([binary, '--version'], 'runtime_version_failed')
        probe([binary, 'config', 'check'], 'runtime_config_invalid')
    else:
        probe([binary, '--preflight'], 'codex_adapter_preflight_failed')
    return binary


def identity(pid):
    """PID plus start time and command digest; never use PID alone as ownership."""
    if not isinstance(pid, int) or pid <= 1:
        return None
    result = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'lstart=', '-o', 'args='],
                            capture_output=True, text=True)
    line = result.stdout.strip()
    if result.returncode or not line:
        return None
    # ps lstart has five whitespace-separated date fields on macOS and Linux.
    fields = line.split(None, 5)
    if len(fields) != 6:
        return None
    return {'started': ' '.join(fields[:5]),
            'command_hash': hashlib.sha256(fields[5].encode()).hexdigest()}


def process_absent(pid):
    """Only an OS-confirmed missing process permits retry; errors fail closed."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (PermissionError, OSError):
        return False
    return False


def check_dispatch_host():
    """Managed singleton host contract. Relocation requires holding the old scheduler."""
    expected = os.environ.get('ENTITY_MC_DISPATCH_HOST','').strip().rstrip('.').casefold()
    actual = socket.gethostname().rstrip('.').casefold()
    if not expected:
        raise BeltError('dispatch_host_required')
    if expected != actual:
        raise BeltError('dispatch_host_mismatch')


def acquire_dispatch_lock(url, agent, belt):
    """Serialize an agent/belt across all its state directories on this host."""
    check_dispatch_host()
    directory = Path.home()/'.entity-mc-locks'
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    key = hashlib.sha256((url.rstrip('/') + '\n' + agent.casefold() + '\n' + belt).encode()).hexdigest()
    handle = (directory/(key + '.lock')).open('a')
    try:
        fcntl.flock(handle,fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError,OSError):
        handle.close()
        raise
    return handle

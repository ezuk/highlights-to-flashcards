"""Assistants, models and running the claude and codex commands.

Book text is untrusted input, so the AI gets nothing that can run commands, read files or
reach the network, in an empty folder, without a shell. Every failure becomes one plain
sentence, carried by AIError.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import paths

DEFAULT_TIMEOUT = 300
_KILL_GRACE_SECONDS = 2
_POLL_SECONDS = 0.05
_VERSION_TIMEOUT = 15
_LIST_TIMEOUT = 30
_TEMP_PREFIX = "h2f-ai-"

# Commands under way, process group to working folder
_running = {}

ASSISTANTS = {
    "claude": {"name": "Claude Code", "env": "H2F_CLAUDE_BIN", "command": "claude"},
    "codex": {"name": "Codex", "env": "H2F_CODEX_BIN", "command": "codex"},
}

# Copies that come inside other apps, used when newer than the one on PATH
_BUNDLED = {
    "codex": ("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",),
}
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)(-[0-9A-Za-z.]+)?")
# Path to ((modified, size), version key), so --version runs again only after an update
_version_keys = {}

MODELS = [
    {"id": "claude-haiku-5-5", "label": "Haiku 5.5", "assistant": "claude"},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5", "assistant": "claude"},
    {"id": "claude-opus-5-5", "label": "Opus 5.5", "assistant": "claude"},
    {"id": "claude-fable-5-1", "label": "Fable 5.1", "assistant": "claude"},
    {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol (Codex)", "assistant": "codex"},
]

# Order of the Claude families in menus
_CLAUDE_FAMILIES = ("haiku", "sonnet", "opus", "fable")
_CLAUDE_ID_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$")
_CODEX_ID_RE = re.compile(r"^gpt-(\d+)(?:\.(\d+))?-([a-z]+)$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Every model each assistant offered when last asked, read from the models file
_catalog = {"path": None, "data": {}}
_catalog_lock = threading.Lock()

# Codex features that give the model tools or run the user's own setup, checked against
# the tool list codex 0.171.0 really sends. With code_mode_host off, its exec tool fails closed
_CODEX_OFF = (
    "shell_tool", "unified_exec", "unified_exec_tty", "code_mode", "code_mode_only", "code_mode_host",
    "view_image", "image_generation", "multi_agent", "multi_agent_v2", "apps", "plugins", "remote_plugin",
    "hooks", "browser_use", "browser_use_external", "computer_use", "in_app_browser", "goals", "sleep_tool",
    "skill_search", "skill_mcp_dependency_install", "tool_suggest", "tool_call_mcp_elicitation",
    "worktrees", "workspace_dependencies",
)
_CODEX_CONFIG = ('mcp_servers={}', 'web_search="disabled"', "project_doc_max_bytes=0", "skills.include_instructions=false")

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_META_MODEL = "claude-opus-5-5"


class AIError(Exception):
    """The AI could not answer. The message is a plain sentence shown to the user."""


class AICancelled(AIError):
    """The request was stopped on purpose."""

    def __init__(self):
        super().__init__("The AI request was stopped.")


class CancelToken:
    """Handed to query(); cancel() from any thread kills the running command."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    @property
    def cancelled(self):
        return self._event.is_set()


def _extra_dirs():
    # A Mac app started from Finder gets a short PATH, without these
    return [str(Path.home() / ".local" / "bin"), "/opt/homebrew/bin"]


def child_env():
    """Environment for the commands: this one, with the usual install folders on PATH."""
    env = dict(os.environ)
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p]
    env["PATH"] = os.pathsep.join(parts + [d for d in _extra_dirs() if d not in parts])
    return env


def find_binary(assistant):
    """Full path of an assistant's command, or None. The environment override wins, then the
    newest of the one on PATH and any copy inside another app."""
    info = ASSISTANTS[assistant]
    override = os.environ.get(info["env"])
    if override:
        return override if os.access(override, os.X_OK) else None
    on_path = shutil.which(info["command"], path=child_env()["PATH"])
    candidates = ([on_path] if on_path else []) + [p for p in _BUNDLED.get(assistant, ()) if os.access(p, os.X_OK)]
    best, best_key = None, None
    for path in candidates:
        key = _version_key(path)
        if best is None or (key is not None and (best_key is None or key > best_key)):
            best, best_key = path, key
    return best


def _version_key(path):
    """(numbers, is a release) from a command's --version, for comparing, or None."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    stamp = (info.st_mtime_ns, info.st_size)
    cached = _version_keys.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    match = _VERSION_RE.search(_version_text(path))
    key = (tuple(int(part) for part in match.group(1).split(".")), not match.group(2)) if match else None
    _version_keys[path] = (stamp, key)
    return key


def _display_path(path):
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def detect():
    """Each assistant: {id, name, found, path, version, display}, with version from --version."""
    found = []
    for assistant, info in ASSISTANTS.items():
        path = find_binary(assistant)
        version = _version(path) if path else None
        display = None
        if path:
            display = _display_path(path) + (f" · version {version}" if version else "")
        found.append({"id": assistant, "name": info["name"], "found": bool(path),
                      "path": path, "version": version, "display": display})
    return found


def _version_text(path):
    try:
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run([path, "--version"], capture_output=True, cwd=folder, env=child_env(),
                                  timeout=_VERSION_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.decode("utf-8", "replace")


def _version(path):
    match = re.search(r"\d+(?:\.\d+)+", _version_text(path))
    return match.group(0) if match else None


def _family(model_id):
    """(family, version) of a model id, or (id, ()) for an id without the usual shape."""
    match = _CLAUDE_ID_RE.match(model_id)
    if match:
        return "claude-" + match.group(1), (int(match.group(2)), int(match.group(3) or 0))
    match = _CODEX_ID_RE.match(model_id)
    if match:
        return "gpt-" + match.group(3), (int(match.group(1)), int(match.group(2) or 0))
    return model_id, ()


def _newest(models):
    """The newest model of each family, in the order given."""
    best = {}
    for model in models:
        family, version = _family(model["id"])
        if family not in best or version > _family(best[family]["id"])[1]:
            best[family] = model
    keep = {model["id"] for model in best.values()}
    return [model for model in models if model["id"] in keep]


def _catalog_data():
    path = paths.home() / "models.json"
    with _catalog_lock:
        if _catalog["path"] != path:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            _catalog.update(path=path, data=data if isinstance(data, dict) else {})
        return _catalog["data"]


def _save_catalog(data):
    path = paths.home() / "models.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    temp.replace(path)
    with _catalog_lock:
        _catalog.update(path=path, data=data)


def _offered(assistant):
    """Every model an assistant offered when last asked, or the built-in ones."""
    listed = _catalog_data().get(assistant)
    if isinstance(listed, list) and listed:
        return [{"id": m["id"], "label": m["label"], "assistant": assistant} for m in listed]
    return [model for model in MODELS if model["assistant"] == assistant]


def models():
    """Models to pick from: the newest of each family, for each assistant."""
    return [model for assistant in ASSISTANTS for model in _newest(_offered(assistant))]


def enabled_models(assistants):
    """Models of the assistants turned on in `assistants`, {id: bool}."""
    return [model for model in models() if assistants.get(model["assistant"])]


def current_model(model_id):
    """The newest model of `model_id`'s family, when a newer one is out; otherwise `model_id`."""
    family, version = _family(model_id)
    for model in models():
        other, newer = _family(model["id"])
        if other == family and newer > version:
            return model["id"]
    return model_id


def _claude_models(stdout):
    """Models from the answer to Claude Code's start-up handshake, or None."""
    listed = None
    for line in stdout.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "control_response":
            listed = ((data.get("response") or {}).get("response") or {}).get("models")
            break
    if not isinstance(listed, list):
        return None
    found = {}
    for entry in listed:
        if not isinstance(entry, dict) or entry.get("value") == "default":
            continue
        model_id, label = entry.get("resolvedModel") or entry.get("value"), entry.get("displayName")
        if isinstance(model_id, str) and isinstance(label, str) and _SAFE_ID_RE.match(model_id):
            found.setdefault(model_id, {"id": model_id, "label": label})

    def order(model):
        name = _family(model["id"])[0].removeprefix("claude-")
        return _CLAUDE_FAMILIES.index(name) if name in _CLAUDE_FAMILIES else len(_CLAUDE_FAMILIES)

    return sorted(found.values(), key=order) or None


def _codex_models(stdout):
    """Models from `codex debug models`, or None."""
    try:
        listed = json.loads(stdout).get("models")
    except (ValueError, AttributeError):
        return None
    if not isinstance(listed, list):
        return None
    entries = [e for e in listed if isinstance(e, dict) and e.get("visibility") == "list"]
    entries.sort(key=lambda e: e.get("priority") if isinstance(e.get("priority"), int) else 1_000_000)
    found = {}
    for entry in entries:
        model_id, name = entry.get("slug"), entry.get("display_name")
        if isinstance(model_id, str) and isinstance(name, str) and _SAFE_ID_RE.match(model_id):
            label = re.sub(r"(\d)-(?=[A-Za-z])", r"\1 ", name) + " (Codex)"
            found.setdefault(model_id, {"id": model_id, "label": label})
    return list(found.values()) or None


_HANDSHAKE = json.dumps({"type": "control_request", "request_id": "models", "request": {"subtype": "initialize"}})


def _list_models(assistant, binary):
    """Every model an installed assistant offers, or None when it couldn't say."""
    if assistant == "claude":
        argv = [binary, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands", "--tools", "",
                "--no-session-persistence"]
        stdin, parse = _HANDSHAKE + "\n", _claude_models
    else:
        argv, stdin, parse = [binary, "debug", "models"], "", _codex_models
    try:
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run(argv, input=stdin.encode("utf-8"), capture_output=True, cwd=folder,
                                  env=child_env(), timeout=_LIST_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return parse(done.stdout.decode("utf-8", "replace"))


def refresh_models():
    """Asks each installed assistant which models it offers, and keeps the answer for next time.
    True when the models to pick from changed."""
    before = models()
    data = dict(_catalog_data())
    for assistant in ASSISTANTS:
        binary = find_binary(assistant)
        listed = _list_models(assistant, binary) if binary else None
        if listed:
            data[assistant] = listed
    try:
        _save_catalog(data)
    except OSError:
        return False
    return models() != before


def set_enabled(assistants, assistant, on):
    """`assistants` with one turned on or off, keeping at least one on."""
    updated = {**assistants, assistant: bool(on)}
    if not any(updated.values()):
        return dict(assistants)
    return updated


def model_info(model_id):
    for assistant in ASSISTANTS:
        for model in _offered(assistant):
            if model["id"] == model_id:
                return model
    for model in MODELS:
        if model["id"] == model_id:
            return model
    raise AIError("That AI model isn't one the app knows.")


def split_messages(messages):
    """(system, user) text from messages, system parts joined in order."""
    # A zero byte can't go in a command's arguments
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system").replace("\x00", "")
    user = "\n\n".join(m["content"] for m in messages if m["role"] != "system").replace("\x00", "")
    return system, user


def claude_command(binary, model_id, system):
    return [binary, "-p", "--model", model_id, "--setting-sources", "", "--strict-mcp-config",
            "--disable-slash-commands", "--tools", "", "--no-session-persistence",
            "--output-format", "json", "--system-prompt", system]


def codex_command(binary, model_id, output_file):
    off = [arg for feature in _CODEX_OFF for arg in ("--disable", feature)]
    config = [arg for value in _CODEX_CONFIG for arg in ("-c", value)]
    return [binary, "exec", "-m", model_id, "--skip-git-repo-check", "--sandbox", "read-only",
            "--ephemeral", "--ignore-user-config", "--ignore-rules", *off, *config,
            "-o", str(output_file), "-"]


def _kill_group(proc):
    # The command runs in its own session, so its group id is its pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=_KILL_GRACE_SECONDS)
            # The leader is gone, but a child may still be shutting down
            if sig == signal.SIGTERM:
                time.sleep(0.05)
        except subprocess.TimeoutExpired:
            pass
    try:
        proc.wait(timeout=_KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        pass


def kill_running():
    """For quitting at once: ends every AI command still going and removes its temporary folder.
    Safe to call from a signal handler."""
    for pid, cwd in list(_running.items()):
        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        base = Path(cwd).parent
        if base.name.startswith(_TEMP_PREFIX):
            shutil.rmtree(base, ignore_errors=True)


def _run(argv, stdin_text, cwd, timeout, cancel, name):
    """(exit code, stdout, stderr) of a command. Raises AIError on timeout or a failed start."""
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=cwd, env=child_env(), start_new_session=True)
    except (OSError, ValueError) as e:
        raise AIError(f"{name} could not be started: {getattr(e, 'strerror', None) or e}.") from e
    _running[proc.pid] = cwd
    try:
        return _finish(proc, stdin_text, timeout, cancel, name)
    finally:
        _running.pop(proc.pid, None)


def _finish(proc, stdin_text, timeout, cancel, name):
    out, err = [], []

    def feed():
        try:
            proc.stdin.write(stdin_text.encode("utf-8", "replace"))
            proc.stdin.close()
        except OSError:
            pass

    def drain(stream, sink):
        with stream:
            while chunk := stream.read1(65536):
                sink.append(chunk)

    threads = [
        threading.Thread(target=feed, daemon=True),
        threading.Thread(target=drain, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, err), daemon=True),
    ]
    for thread in threads:
        thread.start()

    def join_all():
        for thread in threads:
            thread.join(timeout=_KILL_GRACE_SECONDS)

    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=_POLL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.cancelled:
            _kill_group(proc)
            join_all()
            raise AICancelled()
        if time.monotonic() > deadline:
            _kill_group(proc)
            join_all()
            raise AIError(f"{name} took longer than {_duration(timeout)} and was stopped.")
    # A child that outlived the command must not keep running, or keep its output open
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    join_all()
    return proc.returncode, b"".join(out).decode("utf-8", "replace"), b"".join(err).decode("utf-8", "replace")


def _duration(seconds):
    if seconds >= 120:
        return f"{round(seconds / 60)} minutes"
    return f"{round(seconds)} seconds"


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_ERROR_LINE_RE = re.compile(r"^\s*(?:\w*error|fatal)\s*:\s*(.+)$", re.I)
_NOISE_RE = re.compile(
    r"^\s*(at |File \"|Traceback|\^|throw |code:|requireStack:|Require stack:|- /|node:|Node\.js v|warning:"
    r"|\d{4}-\d\d-\d\dT|\w+Error\b)|^\s*[\[\{\]\}]",
    re.I,
)
_JSON_MESSAGE_RE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')
_REQUEST_DETAILS_RE = re.compile(r",?\s*\b(?:url|cf-ray|request id):\s*\S+", re.I)
_STATUS_RE = re.compile(r"^unexpected status \d+[^:]*:\s*", re.I)
_SIGNED_OUT_RE = re.compile(
    r"not logged in|not signed in|please run /login|codex login|status 401|unauthorized|missing bearer"
    r"|token_expired|sign in again|log in again",
    re.I,
)


def _error_lines(text):
    return [m.group(1) for m in map(_ERROR_LINE_RE.match, text.splitlines()) if m]


def _short_detail(text):
    """The one line of a command's output that says what went wrong, as it was written."""
    text = _ANSI_RE.sub("", text)
    errors = _error_lines(text)
    if errors:
        return errors[-1].strip()
    lines = [line.strip() for line in text.splitlines() if line.strip() and not _NOISE_RE.match(line)]
    return lines[-1] if lines else ""


def _readable(detail):
    # The server's own message out of its JSON, without urls and ids
    match = _JSON_MESSAGE_RE.search(detail)
    if match:
        try:
            detail = json.loads(f'"{match.group(1)}"')
        except ValueError:
            detail = match.group(1)
    detail = _STATUS_RE.sub("", _REQUEST_DETAILS_RE.sub("", detail)).strip()
    return detail[:200]


def _problem_sentence(name, command, detail):
    if _SIGNED_OUT_RE.search(detail):
        return f"{name} isn't signed in. Open Terminal, run {command} and sign in, then try again."
    detail = _readable(detail)
    if detail:
        return f"{name} couldn't answer: {detail}"
    return f"{name} couldn't answer, and gave no reason."


def _ask_claude(binary, model_id, system, user, folder, timeout, cancel):
    name, command = ASSISTANTS["claude"]["name"], ASSISTANTS["claude"]["command"]
    code, stdout, stderr = _run(claude_command(binary, model_id, system), user, folder, timeout, cancel, name)
    try:
        data = json.loads(stdout)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if data.get("is_error"):
            detail = data.get("result") if isinstance(data.get("result"), str) else ""
            raise AIError(_problem_sentence(name, command, _short_detail(detail)))
        if code == 0:
            answer = data.get("result")
            if isinstance(answer, str) and answer.strip():
                return answer
            raise AIError(f"{name} gave an empty reply.")
    if code != 0:
        raise AIError(_problem_sentence(name, command, _short_detail(stderr or stdout)))
    raise AIError(f"{name}'s reply could not be read.")


def _ask_codex(binary, model_id, system, user, folder, timeout, cancel):
    name, command = ASSISTANTS["codex"]["name"], ASSISTANTS["codex"]["command"]
    output = Path(folder).parent / "answer.txt"
    prompt = system + "\n\n" + user if system else user
    code, stdout, stderr = _run(codex_command(binary, model_id, output), prompt, folder, timeout, cancel, name)
    if code != 0:
        raise AIError(_problem_sentence(name, command, _short_detail(stderr or stdout)))
    try:
        answer = output.read_bytes().decode("utf-8", "replace")
    except OSError:
        raise AIError(f"{name} finished without writing an answer.") from None
    if not answer.strip():
        raise AIError(f"{name} gave an empty reply.")
    return answer


def query(model_id, messages, timeout=DEFAULT_TIMEOUT, cancel=None):
    """The AI's answer to `messages` (see cardgen) as text. Raises AIError with a plain
    sentence on any failure, or AICancelled when `cancel` (a CancelToken) fires."""
    assistant = model_info(model_id)["assistant"]
    binary = find_binary(assistant)
    if not binary:
        name = ASSISTANTS[assistant]["name"]
        raise AIError(f"{name} isn't installed, or the app can't find it. Check Settings.")
    if cancel is not None and cancel.cancelled:
        raise AICancelled()
    system, user = split_messages(messages)
    ask = _ask_claude if assistant == "claude" else _ask_codex
    with tempfile.TemporaryDirectory(prefix=_TEMP_PREFIX, ignore_cleanup_errors=True) as base:
        folder = Path(base) / "work"
        folder.mkdir()
        return ask(binary, model_id, system, user, str(folder), timeout, cancel)

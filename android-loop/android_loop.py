#!/usr/bin/env python3
"""Grok Bot remote Android development loop (#617).

Phone / Bot phrases map to this CLI. Always run it on the registered local
Mac — not the Grok Bot cloud computer. JSON on stdout; logs on stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

HERE = Path(__file__).resolve().parent
DEFAULT_PROJECT = HERE / "fixture"
OUT_DIR = HERE / "out"
SCREEN_DIR = OUT_DIR / "screenshots"
DEFAULT_AVD = "Pixel_3a_API_29"
DEFAULT_PACKAGE = "com.cvolkernick.androidloop.fixture"
DEFAULT_ACTIVITY = ".MainActivity"
MIN_JAVA_MAJOR = 17
BOOT_TIMEOUT_S = 180
SCREENSHOT_SETTLE_S = 2.0

GROK_BOT_APP = Path("/Applications/Grok Bot.app")
STUDIO_JAVA_MARKERS = (
    "Android Studio.app/Contents/jre",
    "Android Studio.app/Contents/jbr",
)

# AC phrases from github.com/cvolkernick/personal-workspace/issues/617
PHRASE_ACTIONS = (
    (
        "screenshot",
        (
            "open the android emulator",
            "take a screenshot",
            "emulator screenshot",
        ),
    ),
    (
        "run",
        (
            "install the latest debug",
            "launch the app",
            "send back a screenshot",
            "install and launch",
        ),
    ),
    (
        "assemble",
        (
            "build the debug apk",
            "report whether it compiled",
            "assemble debug",
            "compile the apk",
        ),
    ),
    (
        "test",
        (
            "run instrumented tests",
            "failing stack traces",
            "connectedandroidtest",
            "instrumented test",
        ),
    ),
    ("doctor", ("doctor", "preflight", "check the android loop")),
    ("status", ("emulator status", "adb devices")),
)


class HarnessError(RuntimeError):
    """User-facing failure with an exit payload."""


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def emit(payload: dict[str, Any], *, ok: Optional[bool] = None) -> int:
    if ok is not None:
        payload["ok"] = ok
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


def log(msg: str) -> None:
    sys.stderr.write(msg.rstrip() + "\n")
    sys.stderr.flush()


def which(name: str) -> Optional[str]:
    return shutil.which(name)


def run_cmd(
    args: list[str],
    *,
    env: Optional[dict[str, str]] = None,
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    check: bool = False,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        env=env,
        timeout=timeout,
        check=False,
        capture_output=capture,
        text=True,
    )
    if check and proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-4000:]
        raise HarnessError(f"{args[0]} exited {proc.returncode}: {tail}")
    return proc


def java_major(version_blob: str) -> Optional[int]:
    m = re.search(r'version "(\d+)(?:\.(\d+))?', version_blob)
    if not m:
        return None
    major = int(m.group(1))
    if major == 1 and m.group(2):
        return int(m.group(2))
    return major


def _java_home_usable(home: Path) -> bool:
    java = home / "bin" / "java"
    if not java.is_file():
        return False
    marker = str(home)
    if any(s in marker for s in STUDIO_JAVA_MARKERS) and not home.exists():
        return False
    proc = run_cmd([str(java), "-version"])
    blob = (proc.stderr or "") + (proc.stdout or "")
    major = java_major(blob)
    return proc.returncode == 0 and major is not None and major >= MIN_JAVA_MAJOR


def candidate_java_homes() -> list[Path]:
    homes: list[Path] = []
    seen: set[str] = set()

    def add(p: Optional[Path]) -> None:
        if p is None:
            return
        try:
            resolved = p.expanduser().resolve()
        except OSError:
            return
        key = str(resolved)
        if key in seen:
            return
        seen.add(key)
        homes.append(resolved)

    env_override = os.environ.get("ANDROID_LOOP_JAVA_HOME")
    if env_override:
        add(Path(env_override))

    java_home_bin = Path("/usr/libexec/java_home")
    if java_home_bin.is_file():
        proc = run_cmd([str(java_home_bin), "-v", "17+"])
        if proc.returncode == 0 and (proc.stdout or "").strip():
            add(Path(proc.stdout.strip()))

    for p in (
        Path("/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"),
        Path("/opt/homebrew/opt/openjdk@17"),
        Path("/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home"),
        Path("/opt/homebrew/opt/openjdk@21"),
        Path("/usr/local/opt/openjdk@17"),
    ):
        add(p)

    jvms = Path("/Library/Java/JavaVirtualMachines")
    if jvms.is_dir():
        for child in sorted(jvms.iterdir()):
            add(child / "Contents" / "Home")

    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        add(Path(env_home))
    return homes


def resolve_java_home() -> Optional[Path]:
    for home in candidate_java_homes():
        if _java_home_usable(home):
            return home
    return None


def resolve_android_home() -> Optional[Path]:
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = os.environ.get(key)
        if raw:
            p = Path(raw).expanduser()
            if p.is_dir():
                return p.resolve()
    default = Path.home() / "Library" / "Android" / "sdk"
    if default.is_dir():
        return default.resolve()
    return None


def sdk_tool(home: Path, *parts: str) -> Path:
    return home.joinpath(*parts)


def list_avds(android_home: Path, env: dict[str, str]) -> list[str]:
    emu = sdk_tool(android_home, "emulator", "emulator")
    if not emu.is_file():
        return []
    proc = run_cmd([str(emu), "-list-avds"], env=env)
    if proc.returncode != 0:
        return []
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def pick_avd(android_home: Path, env: dict[str, str], requested: Optional[str]) -> str:
    avds = list_avds(android_home, env)
    if requested:
        if requested not in avds and avds:
            raise HarnessError(f"AVD {requested!r} not found. Available: {', '.join(avds)}")
        return requested
    if DEFAULT_AVD in avds:
        return DEFAULT_AVD
    if avds:
        return avds[0]
    raise HarnessError("No Android Virtual Devices found. Create one with avdmanager or Android Studio.")


def tool_env(*, java_home: Optional[Path] = None, android_home: Optional[Path] = None) -> dict[str, str]:
    env = dict(os.environ)
    java_home = java_home or resolve_java_home()
    android_home = android_home or resolve_android_home()
    if java_home:
        env["JAVA_HOME"] = str(java_home)
        env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    elif any(s in env.get("JAVA_HOME", "") for s in STUDIO_JAVA_MARKERS):
        env.pop("JAVA_HOME", None)
    if android_home:
        env["ANDROID_HOME"] = str(android_home)
        env["ANDROID_SDK_ROOT"] = str(android_home)
        extra = [
            str(android_home / "platform-tools"),
            str(android_home / "emulator"),
            str(android_home / "cmdline-tools" / "latest" / "bin"),
        ]
        env["PATH"] = os.pathsep.join(extra) + os.pathsep + env.get("PATH", "")
    return env


def grok_bot_snapshot() -> dict[str, Any]:
    running = False
    local_exec = False
    proc = run_cmd(["pgrep", "-lf", "Grok Bot"])
    blob = proc.stdout or ""
    if proc.returncode == 0 and blob.strip():
        running = True
        local_exec = "local-exec-daemon" in blob
    return {
        "app_present": GROK_BOT_APP.is_dir(),
        "app_path": str(GROK_BOT_APP) if GROK_BOT_APP.is_dir() else None,
        "process_running": running,
        "local_exec_daemon": local_exec,
        "docs": {
            "local_execution": "Settings → General → Agent → Execution on Local Computer",
            "default_policy": "Ask every time",
            "source": "https://docs.x.ai/grok-bot/approvals-security-and-privacy",
        },
    }


def adb(android_home: Path, env: dict[str, str], *args: str, timeout: Optional[float] = None) -> subprocess.CompletedProcess[str]:
    binary = sdk_tool(android_home, "platform-tools", "adb")
    if not binary.is_file():
        raise HarnessError(f"adb not found at {binary}")
    return run_cmd([str(binary), *args], env=env, timeout=timeout)


def adb_devices(android_home: Path, env: dict[str, str]) -> list[dict[str, str]]:
    proc = adb(android_home, env, "devices", "-l")
    devices: list[dict[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        devices.append({"serial": serial, "state": state, "raw": line})
    return devices


def listed_emulator(android_home: Path, env: dict[str, str]) -> Optional[str]:
    """Serial of an emulator even if it is still offline/authorizing."""
    for dev in adb_devices(android_home, env):
        if dev["serial"].startswith("emulator-") or "emulator" in dev.get("raw", ""):
            return dev["serial"]
    return None


def online_emulator(android_home: Path, env: dict[str, str]) -> Optional[str]:
    for dev in adb_devices(android_home, env):
        if dev["state"] == "device" and (
            dev["serial"].startswith("emulator-") or "emulator" in dev.get("raw", "")
        ):
            return dev["serial"]
    for dev in adb_devices(android_home, env):
        if dev["state"] == "device":
            return dev["serial"]
    return None


def boot_completed(android_home: Path, env: dict[str, str], serial: str) -> bool:
    proc = adb(android_home, env, "-s", serial, "shell", "getprop", "sys.boot_completed")
    return (proc.stdout or "").strip() == "1"


def bootanim_stopped(android_home: Path, env: dict[str, str], serial: str) -> bool:
    proc = adb(android_home, env, "-s", serial, "shell", "getprop", "init.svc.bootanim")
    return (proc.stdout or "").strip() == "stopped"


def package_manager_ready(android_home: Path, env: dict[str, str], serial: str) -> bool:
    proc = adb(android_home, env, "-s", serial, "shell", "pm", "path", "android")
    blob = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0 and "package:" in blob


def current_focus(android_home: Path, env: dict[str, str], serial: str) -> str:
    proc = adb(android_home, env, "-s", serial, "shell", "dumpsys", "window", timeout=30)
    blob = proc.stdout or ""
    for line in blob.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            return line.strip()
    return blob[:300]


def is_focus_ready(blob: str) -> bool:
    low = (blob or "").lower()
    if "setupwizard" in low:
        return False
    return "launcher" in low or "androidloop.fixture" in low


def skip_setup_wizard(android_home: Path, env: dict[str, str], serial: str) -> None:
    for args in (
        ("settings", "put", "global", "device_provisioned", "1"),
        ("settings", "put", "secure", "user_setup_complete", "1"),
        ("wm", "dismiss-keyguard"),
    ):
        adb(android_home, env, "-s", serial, "shell", *args)


def emulator_ui_ready(android_home: Path, env: dict[str, str], serial: str) -> bool:
    if not boot_completed(android_home, env, serial):
        return False
    if not bootanim_stopped(android_home, env, serial):
        return False
    if not package_manager_ready(android_home, env, serial):
        return False
    skip_setup_wizard(android_home, env, serial)
    return is_focus_ready(current_focus(android_home, env, serial))


def ensure_emulator(
    android_home: Path,
    env: dict[str, str],
    avd: str,
    *,
    timeout_s: int = BOOT_TIMEOUT_S,
    no_window: bool = False,
) -> dict[str, Any]:
    existing = listed_emulator(android_home, env) or online_emulator(android_home, env)
    if existing:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            serial = online_emulator(android_home, env) or existing
            if serial and emulator_ui_ready(android_home, env, serial):
                return {
                    "serial": serial,
                    "started": False,
                    "avd": avd,
                    "reused": True,
                }
            time.sleep(2)
        raise HarnessError(
            f"emulator {existing} is listed but did not become UI-ready within {timeout_s}s"
        )

    emu = sdk_tool(android_home, "emulator", "emulator")
    if not emu.is_file():
        raise HarnessError(f"emulator binary not found at {emu}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = OUT_DIR / "emulator.log"
    cmd = [
        str(emu),
        "-avd",
        avd,
        "-netdelay",
        "none",
        "-netspeed",
        "full",
        "-gpu",
        "auto",
        "-no-audio",
        "-no-boot-anim",
    ]
    if no_window:
        cmd.append("-no-window")
    log(f"starting emulator: {' '.join(cmd)}")
    log_handle = log_path.open("ab")
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    (OUT_DIR / "emulator.pid").write_text(str(proc.pid) + "\n", encoding="utf-8")

    deadline = time.time() + timeout_s
    serial: Optional[str] = None
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise HarnessError(f"emulator exited {proc.returncode} before boot.\n{tail}")
        serial = online_emulator(android_home, env)
        if serial and emulator_ui_ready(android_home, env, serial):
            return {
                "serial": serial,
                "started": True,
                "avd": avd,
                "reused": False,
                "pid": proc.pid,
                "log": str(log_path),
            }
        time.sleep(2)
    raise HarnessError(
        f"emulator {avd} did not boot within {timeout_s}s (pid {proc.pid}). See {log_path}"
    )


def stop_emulator(android_home: Path, env: dict[str, str]) -> dict[str, Any]:
    serial = online_emulator(android_home, env)
    killed = False
    if serial:
        proc = adb(android_home, env, "-s", serial, "emu", "kill")
        killed = proc.returncode == 0
    pid_file = OUT_DIR / "emulator.pid"
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid:
            run_cmd(["kill", str(pid)])
        pid_file.unlink(missing_ok=True)
    return {"killed": killed, "serial": serial}


def capture_screenshot(
    android_home: Path, env: dict[str, str], serial: str, dest: Optional[Path] = None
) -> Path:
    SCREEN_DIR.mkdir(parents=True, exist_ok=True)
    dest = dest or (SCREEN_DIR / f"{utc_stamp()}.png")
    # adb text mode mangles PNG bytes — capture binary.
    binary = sdk_tool(android_home, "platform-tools", "adb")
    raw = subprocess.run(
        [str(binary), "-s", serial, "exec-out", "screencap", "-p"],
        env=env,
        capture_output=True,
        check=False,
    )
    if raw.returncode != 0 or not raw.stdout.startswith(b"\x89PNG"):
        raise HarnessError(
            "screencap did not return a PNG. Is the emulator fully booted?"
        )
    dest.write_bytes(raw.stdout)
    latest = SCREEN_DIR / "latest.png"
    latest.write_bytes(raw.stdout)
    return dest.resolve()


def write_local_properties(project: Path, android_home: Path) -> Path:
    path = project / "local.properties"
    path.write_text(f"sdk.dir={android_home}\n", encoding="utf-8")
    return path


def gradle_wrapper(project: Path) -> Path:
    wrapper = project / "gradlew"
    if not wrapper.is_file():
        raise HarnessError(f"gradlew missing in {project}. This harness expects a Gradle wrapper.")
    wrapper.chmod(wrapper.stat().st_mode | 0o111)
    return wrapper


def read_application_id(project: Path) -> Optional[str]:
    gradle = project / "app" / "build.gradle"
    if not gradle.is_file():
        return None
    text = gradle.read_text(encoding="utf-8")
    m = re.search(r'applicationId\s+"([^"]+)"', text)
    return m.group(1) if m else None


def find_debug_apk(project: Path) -> Optional[Path]:
    debug_dir = project / "app" / "build" / "outputs" / "apk" / "debug"
    if not debug_dir.is_dir():
        return None
    apks = sorted(debug_dir.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    return apks[0] if apks else None


def gradle(
    project: Path,
    env: dict[str, str],
    *tasks: str,
    timeout: Optional[float] = 900,
) -> dict[str, Any]:
    wrapper = gradle_wrapper(project)
    t0 = time.time()
    proc = run_cmd(
        [str(wrapper), "--no-daemon", *tasks],
        env=env,
        cwd=project,
        timeout=timeout,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    duration = round(time.time() - t0, 2)
    successful = "BUILD SUCCESSFUL" in out and proc.returncode == 0
    return {
        "exit": proc.returncode,
        "successful": successful,
        "duration_s": duration,
        "output": out,
        "tasks": list(tasks),
    }


def parse_instrumented_failures(output: str) -> list[dict[str, str]]:
    """Extract failing test names + stack traces from Gradle/ADB instrumented output."""
    failures: list[dict[str, str]] = []
    lines = output.splitlines()
    i = 0
    failed_re = re.compile(r"^(\S+)\s+>\s+(.+?)\s+FAILED\b")
    short_re = re.compile(r"^(\S+)\s+FAILED\s*$")
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        m = failed_re.match(stripped) or short_re.match(stripped)
        if m:
            name = " > ".join(g for g in m.groups() if g)
            stack_lines: list[str] = []
            i += 1
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    if stack_lines:
                        break
                    i += 1
                    continue
                if nxt.startswith(" ") or nxt.startswith("\t") or nxt.strip().startswith("at "):
                    stack_lines.append(nxt.rstrip())
                    i += 1
                    continue
                if failed_re.match(nxt.strip()) or short_re.match(nxt.strip()):
                    break
                if nxt.startswith(">") or nxt.startswith("BUILD ") or nxt.startswith("* "):
                    break
                stack_lines.append(nxt.rstrip())
                i += 1
            failures.append({"test": name, "stack": "\n".join(stack_lines).strip()})
            continue
        i += 1
    return failures


def normalize_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def map_phrase(text: str) -> Optional[str]:
    blob = normalize_phrase(text)
    if not blob:
        return None
    if blob in {"doctor", "status", "screenshot", "assemble", "install", "launch", "run", "test"}:
        return blob
    scored: list[tuple[int, str]] = []
    for action, needles in PHRASE_ACTIONS:
        hits = sum(1 for n in needles if n in blob)
        if hits:
            scored.append((hits, action))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[0][1]


def require_java() -> Path:
    home = resolve_java_home()
    if home is None:
        raise HarnessError(
            "JDK 17+ is required (current AGP). Install Homebrew openjdk@17 "
            "(`brew install openjdk@17`) or set ANDROID_LOOP_JAVA_HOME. "
            "Do not point JAVA_HOME at a missing Android Studio JRE."
        )
    return home


def require_android() -> Path:
    home = resolve_android_home()
    if home is None:
        raise HarnessError(
            "Android SDK not found. Set ANDROID_HOME to ~/Library/Android/sdk "
            "or install platform-tools + emulator via sdkmanager."
        )
    return home


def cmd_doctor(args: argparse.Namespace) -> dict[str, Any]:
    android_home = resolve_android_home()
    java_home = resolve_java_home()
    env = tool_env(java_home=java_home, android_home=android_home)
    avds = list_avds(android_home, env) if android_home else []
    devices = adb_devices(android_home, env) if android_home else []
    stale_java = False
    env_java = os.environ.get("JAVA_HOME", "")
    if env_java and any(s in env_java for s in STUDIO_JAVA_MARKERS):
        stale_java = not Path(env_java).exists()

    checks = {
        "android_sdk": android_home is not None,
        "adb": bool(android_home and sdk_tool(android_home, "platform-tools", "adb").is_file()),
        "emulator": bool(android_home and sdk_tool(android_home, "emulator", "emulator").is_file()),
        "avd": bool(avds),
        "jdk17": java_home is not None,
        "fixture": (DEFAULT_PROJECT / "gradlew").is_file() or (DEFAULT_PROJECT / "app").is_dir(),
        "grok_bot_app": GROK_BOT_APP.is_dir(),
    }
    payload = {
        "action": "doctor",
        "ok": all(checks[k] for k in ("android_sdk", "adb", "emulator", "avd", "jdk17")),
        "checks": checks,
        "android_home": str(android_home) if android_home else None,
        "java_home": str(java_home) if java_home else None,
        "java_home_env_stale_android_studio": stale_java,
        "avds": avds,
        "devices": devices,
        "harness": str(HERE / "android_loop.py"),
        "default_project": str(DEFAULT_PROJECT),
        "default_avd": DEFAULT_AVD,
        "grok_bot": grok_bot_snapshot(),
        "invoke": {
            "local_only": True,
            "cli": f"python3 {HERE / 'android_loop.py'} <screenshot|assemble|run|test|doctor>",
            "phrases": [
                "Open the Android emulator on the registered machine and take a screenshot.",
                "Build the debug APK and report whether it compiled.",
                "Install the latest debug build on the emulator, launch the app, and send back a screenshot.",
                "Run instrumented tests and paste the failing stack traces.",
            ],
        },
        "message": "Android loop preflight.",
    }
    if not payload["ok"]:
        missing = [k for k, v in checks.items() if not v and k in ("android_sdk", "adb", "emulator", "avd", "jdk17")]
        payload["message"] = "Preflight failed: " + ", ".join(missing)
    return payload


def cmd_status(args: argparse.Namespace) -> dict[str, Any]:
    android_home = require_android()
    env = tool_env(android_home=android_home)
    devices = adb_devices(android_home, env)
    serial = online_emulator(android_home, env)
    booted = bool(serial and boot_completed(android_home, env, serial))
    return {
        "action": "status",
        "ok": True,
        "devices": devices,
        "serial": serial,
        "boot_completed": booted,
        "message": "emulator ready" if booted else "no booted emulator",
    }


def _ready_device(args: argparse.Namespace) -> tuple[Path, dict[str, str], str, str]:
    android_home = require_android()
    java_home = resolve_java_home()
    env = tool_env(java_home=java_home, android_home=android_home)
    avd = pick_avd(android_home, env, getattr(args, "avd", None))
    info = ensure_emulator(android_home, env, avd, no_window=getattr(args, "no_window", False))
    return android_home, env, info["serial"], avd


def cmd_screenshot(args: argparse.Namespace) -> dict[str, Any]:
    android_home, env, serial, avd = _ready_device(args)
    dest = Path(args.out).expanduser() if getattr(args, "out", None) else None
    path = capture_screenshot(android_home, env, serial, dest)
    return {
        "action": "screenshot",
        "ok": True,
        "avd": avd,
        "serial": serial,
        "screenshot": str(path),
        "message": "Emulator screenshot captured.",
    }


def cmd_assemble(args: argparse.Namespace) -> dict[str, Any]:
    java_home = require_java()
    android_home = require_android()
    project = Path(args.project).expanduser().resolve()
    env = tool_env(java_home=java_home, android_home=android_home)
    write_local_properties(project, android_home)
    result = gradle(project, env, "assembleDebug")
    apk = find_debug_apk(project)
    compiled = bool(result["successful"] and apk)
    payload = {
        "action": "assemble",
        "ok": compiled,
        "compiled": compiled,
        "project": str(project),
        "apk": str(apk) if apk else None,
        "gradle_exit": result["exit"],
        "duration_s": result["duration_s"],
        "message": "assembleDebug succeeded." if compiled else "assembleDebug failed.",
    }
    if not compiled:
        payload["output_tail"] = result["output"][-4000:]
    return payload


def cmd_install(args: argparse.Namespace) -> dict[str, Any]:
    android_home, env, serial, avd = _ready_device(args)
    project = Path(args.project).expanduser().resolve()
    apk = Path(args.apk).expanduser() if getattr(args, "apk", None) else find_debug_apk(project)
    if apk is None or not apk.is_file():
        raise HarnessError("No debug APK. Run assemble first.")
    blob = ""
    ok = False
    for attempt in range(1, 6):
        proc = adb(android_home, env, "-s", serial, "install", "-r", "-t", str(apk))
        blob = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        ok = proc.returncode == 0 and "Success" in blob
        if ok:
            break
        log(f"adb install attempt {attempt}/5 failed: {blob[-400:]}")
        time.sleep(3)
    return {
        "action": "install",
        "ok": ok,
        "avd": avd,
        "serial": serial,
        "apk": str(apk),
        "message": "APK installed." if ok else blob[-2000:],
    }


def cmd_launch(args: argparse.Namespace) -> dict[str, Any]:
    android_home, env, serial, avd = _ready_device(args)
    project = Path(args.project).expanduser().resolve()
    package = args.package or read_application_id(project) or DEFAULT_PACKAGE
    activity = args.activity or DEFAULT_ACTIVITY
    component = f"{package}/{activity}"
    proc = adb(
        android_home,
        env,
        "-s",
        serial,
        "shell",
        "am",
        "start",
        "-n",
        component,
        "-a",
        "android.intent.action.MAIN",
        "-c",
        "android.intent.category.LAUNCHER",
    )
    blob = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 and "Error" not in blob
    time.sleep(SCREENSHOT_SETTLE_S)
    shot = None
    if ok and getattr(args, "screenshot", True):
        shot = str(capture_screenshot(android_home, env, serial))
    return {
        "action": "launch",
        "ok": ok,
        "avd": avd,
        "serial": serial,
        "component": component,
        "screenshot": shot,
        "message": "App launched." if ok else blob[-2000:],
    }


def cmd_run(args: argparse.Namespace) -> dict[str, Any]:
    assembled = cmd_assemble(args)
    if not assembled.get("compiled"):
        assembled["action"] = "run"
        assembled["message"] = "Debug APK did not compile; install/launch skipped."
        return assembled
    installed = cmd_install(args)
    if not installed.get("ok"):
        installed["action"] = "run"
        installed["compiled"] = True
        installed["apk"] = assembled.get("apk")
        return installed
    args.screenshot = True
    launched = cmd_launch(args)
    return {
        "action": "run",
        "ok": bool(launched.get("ok") and launched.get("screenshot")),
        "compiled": True,
        "apk": assembled.get("apk"),
        "avd": launched.get("avd"),
        "serial": launched.get("serial"),
        "component": launched.get("component"),
        "screenshot": launched.get("screenshot"),
        "message": "Installed, launched, screenshot captured."
        if launched.get("ok")
        else launched.get("message"),
    }


def cmd_test(args: argparse.Namespace) -> dict[str, Any]:
    java_home = require_java()
    android_home, env, serial, avd = _ready_device(args)
    env = tool_env(java_home=java_home, android_home=android_home)
    project = Path(args.project).expanduser().resolve()
    write_local_properties(project, android_home)
    result = gradle(project, env, "connectedDebugAndroidTest")
    failures = parse_instrumented_failures(result["output"])
    tests_ok = result["successful"] and not failures
    payload = {
        "action": "test",
        "ok": tests_ok,
        "avd": avd,
        "serial": serial,
        "project": str(project),
        "gradle_exit": result["exit"],
        "duration_s": result["duration_s"],
        "failures": failures,
        "failure_count": len(failures),
        "message": "Instrumented tests passed."
        if tests_ok
        else f"{len(failures) or 'unknown'} instrumented test(s) failed. Stack traces in failures[].",
    }
    if not tests_ok and not failures:
        payload["output_tail"] = result["output"][-4000:]
    return payload


def cmd_emulator(args: argparse.Namespace) -> dict[str, Any]:
    android_home = require_android()
    env = tool_env(android_home=android_home)
    if args.emulator_cmd == "stop":
        info = stop_emulator(android_home, env)
        return {"action": "emulator-stop", "ok": True, **info, "message": "Emulator stop requested."}
    avd = pick_avd(android_home, env, args.avd)
    info = ensure_emulator(android_home, env, avd, no_window=args.no_window)
    return {
        "action": "emulator-start",
        "ok": True,
        **info,
        "message": "Emulator ready.",
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="android_loop.py",
        description="Remote Android loop for Grok Bot on the registered local Mac (#617).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--project", default=str(DEFAULT_PROJECT), help="Android Gradle project root")
        sp.add_argument("--avd", default=None, help=f"AVD name (default {DEFAULT_AVD} if present)")
        sp.add_argument("--no-window", action="store_true", help="Start emulator headless")
        sp.add_argument("--package", default=None)
        sp.add_argument("--activity", default=None)

    sub.add_parser("doctor", help="Preflight SDK / JDK / AVD / Grok Bot")
    sub.add_parser("status", help="adb devices + boot completed")
    shot = sub.add_parser("screenshot", help="Ensure emulator + capture PNG")
    add_common(shot)
    shot.add_argument("--out", default=None, help="PNG destination")

    assemble = sub.add_parser("assemble", help="gradlew assembleDebug")
    add_common(assemble)

    install = sub.add_parser("install", help="adb install -r debug APK")
    add_common(install)
    install.add_argument("--apk", default=None)

    launch = sub.add_parser("launch", help="am start + screenshot")
    add_common(launch)

    run = sub.add_parser("run", help="assemble + install + launch + screenshot")
    add_common(run)

    test = sub.add_parser("test", help="connectedDebugAndroidTest + stack traces")
    add_common(test)

    emu = sub.add_parser("emulator", help="start/stop the AVD")
    emu.add_argument("emulator_cmd", choices=("start", "stop"))
    emu.add_argument("--avd", default=None)
    emu.add_argument("--no-window", action="store_true")

    phrase = sub.add_parser("phrase", help="Map an AC sentence to an action and run it")
    phrase.add_argument("text", nargs="+")
    add_common(phrase)
    return p


HANDLERS = {
    "doctor": cmd_doctor,
    "status": cmd_status,
    "screenshot": cmd_screenshot,
    "assemble": cmd_assemble,
    "install": cmd_install,
    "launch": cmd_launch,
    "run": cmd_run,
    "test": cmd_test,
    "emulator": cmd_emulator,
}


def main(argv: Optional[Iterable[str]] = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] not in {
        "doctor",
        "status",
        "screenshot",
        "assemble",
        "install",
        "launch",
        "run",
        "test",
        "emulator",
        "phrase",
        "-h",
        "--help",
    }:
        argv_list = ["phrase", *argv_list]

    parser = build_parser()
    try:
        args = parser.parse_args(argv_list)
        if args.cmd == "phrase":
            text = " ".join(args.text)
            action = map_phrase(text)
            if not action:
                return emit(
                    {
                        "action": "phrase",
                        "ok": False,
                        "phrase": text,
                        "message": "Could not map phrase. Use screenshot|assemble|run|test|doctor.",
                    }
                )
            args.cmd = action
            payload_meta = {"phrase": text, "mapped_to": action}
            handler = HANDLERS[action]
            payload = handler(args)
            payload.update(payload_meta)
        else:
            payload = HANDLERS[args.cmd](args)
        return emit(payload)
    except HarnessError as exc:
        return emit({"ok": False, "action": "error", "message": str(exc)})
    except subprocess.TimeoutExpired as exc:
        return emit({"ok": False, "action": "error", "message": f"timeout: {exc}"})
    except KeyboardInterrupt:
        return emit({"ok": False, "action": "error", "message": "interrupted"})


if __name__ == "__main__":
    raise SystemExit(main())

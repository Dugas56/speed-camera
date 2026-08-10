from __future__ import annotations

import os
import json
import shlex
import sqlite3
import subprocess
import secrets
from html import escape
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from flask import Flask, Response, abort, redirect, render_template_string, request, send_from_directory, session, url_for
from werkzeug.security import check_password_hash


APP_DIR = Path(os.environ.get("SPEED_CAMERA_APP_DIR", "/opt/speed-camera"))
DATA_DIR = Path(os.environ.get("SPEED_CAMERA_DATA_DIR", "/srv/speed-camera"))
BASE_PATH = os.environ.get("SPEED_CAMERA_BASE_PATH", "/speed-camera").rstrip("/")
DB_PATH = DATA_DIR / "speed-camera.db"
SPEED_DB_PATH = DATA_DIR / "data" / "speed_cam.db"
MEDIA_DIR = DATA_DIR / "media"
LIVE_FRAME_PATH = MEDIA_DIR / "live.jpg"
CONFIG_PATH = APP_DIR / "camera.env"
RUNTIME_DIR = APP_DIR / "runtime"
RUNTIME_CONFIG_PATH = RUNTIME_DIR / "config.py"
DETECTOR_LOG_PATH = DATA_DIR / "speed-cam-runtime.log"
ADMIN_CONFIG_PATH = APP_DIR / "admin.env"
SERVICE_NAME = "speed-camera.service"
YOUTUBE_SERVICE_NAME = "youtube-speed-camera.service"
YOUTUBE_ENV_PATH = APP_DIR / "youtube.env"
CALIBRATION_MARKS_PATH = DATA_DIR / "calibration-marks.jsonl"
LARGE_VEHICLE_AREA = 50000
LARGE_VEHICLE_MIN_WIDTH = 300
LARGE_VEHICLE_MIN_HEIGHT = 90
PASS_MERGE_SECONDS = 8
MAX_VALID_SPEED_KPH = 60.5

app = Flask(__name__)


def load_admin_config() -> dict[str, str]:
    values: dict[str, str] = {}
    if ADMIN_CONFIG_PATH.exists():
        for line in ADMIN_CONFIG_PATH.read_text().splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    return values


admin_config = load_admin_config()
app.secret_key = admin_config.get("FLASK_SECRET_KEY")


def require_admin() -> None:
    if not session.get("speed_camera_admin"):
        abort(403)


def service_action(action: str) -> tuple[bool, str]:
    if action not in {"start", "stop", "restart", "status"}:
        return False, "Unsupported service action."
    try:
        result = subprocess.run(
            ["sudo", "/bin/systemctl", action, SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Unable to run service command: {exc}"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output or f"Service {action} completed."


def youtube_config() -> dict[str, str]:
    values: dict[str, str] = {}
    if not YOUTUBE_ENV_PATH.exists():
        return values
    for line in YOUTUBE_ENV_PATH.read_text().splitlines():
        if not line.strip() or line.strip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def write_youtube_config(stream_key: str) -> None:
    config = youtube_config()
    config["YOUTUBE_STREAM_URL"] = "rtmps://a.rtmps.youtube.com:443/live2"
    config["YOUTUBE_STREAM_KEY"] = stream_key
    YOUTUBE_ENV_PATH.write_text(
        "\n".join(
            [
                f'YOUTUBE_STREAM_URL="{config["YOUTUBE_STREAM_URL"]}"',
                f'YOUTUBE_STREAM_KEY="{config["YOUTUBE_STREAM_KEY"]}"',
            ]
        ) + "\n"
    )
    YOUTUBE_ENV_PATH.chmod(0o600)


def youtube_status() -> dict[str, object]:
    result = subprocess.run(
        ["/bin/systemctl", "show", YOUTUBE_SERVICE_NAME, "--property=ActiveState,MainPID"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    values = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if "=" in line
    )
    main_pid = values.get("MainPID", "0")
    connected = False
    if main_pid.isdigit() and main_pid != "0":
        sockets = subprocess.run(
            ["ss", "-tnp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        connected = f"pid={main_pid}," in sockets.stdout and ":443" in sockets.stdout
    return {
        "running": values.get("ActiveState") == "active",
        "connected": connected,
        "configured": bool(youtube_config().get("YOUTUBE_STREAM_KEY")),
    }


def youtube_action(action: str) -> tuple[bool, str]:
    if action not in {"start", "stop", "restart"}:
        return False, "Unsupported YouTube action."
    try:
        result = subprocess.run(
            ["sudo", "/usr/bin/systemctl", action, YOUTUBE_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"Unable to run YouTube command: {exc}"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output or f"YouTube stream {action} completed."


def init_storage() -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS large_truck_training (
                capture_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                created_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT ''
            )
            """
        )


def large_truck_training_labels() -> dict[str, str]:
    if not DB_PATH.exists():
        return {}
    try:
        with sqlite3.connect(DB_PATH) as con:
            return {
                str(row[0]): str(row[1])
                for row in con.execute("SELECT capture_id, label FROM large_truck_training")
            }
    except sqlite3.Error:
        return {}


def write_large_truck_training(capture_id: str, label: str, notes: str = "") -> None:
    init_storage()
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            INSERT INTO large_truck_training (capture_id, label, created_at, notes)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(capture_id) DO UPDATE SET
                label = excluded.label,
                created_at = excluded.created_at,
                notes = excluded.notes
            """,
            (capture_id, label, datetime.now().isoformat(timespec="seconds"), notes),
        )


def read_config() -> dict[str, str]:
    values = {
        "CAMERA_NAME": "Reolink Camera",
        "REOLINK_HOST": "",
        "REOLINK_USERNAME": "",
        "REOLINK_PASSWORD": "",
        "REOLINK_STREAM": "main",
        "RTSP_URL": "",
    }
    if CONFIG_PATH.exists():
        for line in CONFIG_PATH.read_text().splitlines():
            if not line.strip() or line.strip().startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"')
    return values


def write_config(values: dict[str, str]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Speed Camera Reolink configuration",
        "# Reolink RTSP is usually /h264Preview_01_main or /h264Preview_01_sub.",
        f'CAMERA_NAME="{values.get("CAMERA_NAME", "Reolink Camera")}"',
        f'REOLINK_HOST="{values.get("REOLINK_HOST", "")}"',
        f'REOLINK_USERNAME="{values.get("REOLINK_USERNAME", "")}"',
        f'REOLINK_PASSWORD="{values.get("REOLINK_PASSWORD", "")}"',
        f'REOLINK_STREAM="{values.get("REOLINK_STREAM", "main")}"',
        f'RTSP_URL="{values.get("RTSP_URL", "")}"',
    ]
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
    CONFIG_PATH.chmod(0o600)


def set_config_value(text: str, key: str, value: str) -> str:
    updated = []
    replaced = False
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(key) and "=" in stripped.split("#", 1)[0]:
            indent = line[: len(line) - len(stripped)]
            comment = ""
            if "#" in line:
                comment = "  #" + line.split("#", 1)[1]
            updated.append(f"{indent}{key} = {value}{comment}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{key} = {value}")
    return "\n".join(updated) + "\n"


def sync_runtime_config() -> None:
    if not RUNTIME_CONFIG_PATH.exists():
        return
    cfg = read_config()
    overrides = {
        "CALIBRATE_ON": "False",
        "ALIGN_CAM_ON": "False",
        "CAL_OBJ_PX_L2R": "710",
        "CAL_OBJ_MM_L2R": "20802",
        "CAL_OBJ_PX_R2L": "710",
        "CAL_OBJ_MM_R2L": "23118",
        "SHOW_SETTINGS_ON": "True",
        "GUI_WINDOW_ON": "False",
        "GUI_IMAGE_WIN_ON": "False",
        "GUI_THRESH_WIN_ON": "False",
        "GUI_CROP_WIN_ON": "False",
        "LOG_TO_FILE_ON": "True",
        "LOG_FILE_PATH": repr(str(DETECTOR_LOG_PATH)),
        "CAMERA": repr("rtspcam"),
        "CAM_LOCATION": repr(cfg.get("CAMERA_NAME", "Tapo Camera")),
        "RTSPCAM_SRC": repr(rtsp_url()),
        "IM_SIZE": repr((640, 360)),
        "IM_DIR_PATH": repr(str(MEDIA_DIR / "images")),
        "IM_RECENT_DIR_PATH": repr(str(MEDIA_DIR / "recent")),
        "IM_SAVE_4AI_POS_DIR": repr(str(MEDIA_DIR / "ai" / "pos")),
        "IM_SAVE_4AI_NEG_DIR": repr(str(MEDIA_DIR / "ai" / "neg")),
        "SPACE_MEDIA_DIR": repr(str(MEDIA_DIR / "images")),
        "GRAPH_PATH": repr(str(MEDIA_DIR / "graphs")),
        "DB_DIR": repr(str(DATA_DIR / "data")),
        "DB_NAME": repr("speed_cam.db"),
        "IM_SHOW_SPEED_FILENAME_ON": "True",
        "IM_SHOW_CROP_AREA_ON": "True",
        "IM_BIGGER": "1.5",
        "MO_TRACK_EVENT_COUNT": "4",
        "MO_MIN_AREA_PX": "2500",
        "MO_MAX_X_DIFF_PX": "240",
        "MO_EVENT_TIMEOUT_SEC": "0.8",
        "MO_CROP_X_LEFT": "220",
        "MO_CROP_X_RIGHT": "930",
        "MO_CROP_Y_UPPER": "285",
        "MO_CROP_Y_LOWER": "365",
    }
    text = RUNTIME_CONFIG_PATH.read_text()
    for key, value in overrides.items():
        text = set_config_value(text, key, value)
    RUNTIME_CONFIG_PATH.write_text(text)
    RUNTIME_CONFIG_PATH.chmod(0o600)


def detector_pids() -> list[int]:
    result = subprocess.run(
        ["pgrep", "-f", str(RUNTIME_DIR / "speed-cam.py")],
        capture_output=True,
        text=True,
        timeout=3,
    )
    if result.returncode != 0:
        return []
    return [int(pid) for pid in result.stdout.split() if pid.isdigit()]


def restart_detector() -> None:
    for pid in detector_pids():
        subprocess.run(["kill", str(pid)], capture_output=True, text=True, timeout=3)


def clean_log_text(text: str) -> str:
    password = read_config().get("REOLINK_PASSWORD", "")
    if password:
        text = text.replace(password, "********")
    return text


def detector_status() -> dict[str, object]:
    pids = detector_pids()
    rows = 0
    latest = None
    if SPEED_DB_PATH.exists():
        try:
            con = sqlite3.connect(SPEED_DB_PATH)
            rows = con.execute("SELECT count(*) FROM speed WHERE ave_speed IS NULL OR CAST(ave_speed AS REAL) <= 80").fetchone()[0]
            latest = con.execute(
                "SELECT log_timestamp, ave_speed, speed_units, image_path FROM speed ORDER BY replace(trim(log_timestamp, '\"'), 'T', ' ') DESC LIMIT 1"
            ).fetchone()
            con.close()
        except sqlite3.Error:
            rows = 0
    log_tail = ""
    if DETECTOR_LOG_PATH.exists():
        log_tail = "\n".join(DETECTOR_LOG_PATH.read_text(errors="replace").splitlines()[-6:])
    return {
        "running": bool(pids),
        "pids": pids,
        "rows": rows,
        "latest": latest,
        "log_tail": clean_log_text(log_tail),
    }


def rtsp_url() -> str:
    cfg = read_config()
    explicit = cfg.get("RTSP_URL", "").strip()
    if explicit:
        return explicit
    host = cfg.get("REOLINK_HOST", "").strip()
    username = cfg.get("REOLINK_USERNAME", "").strip()
    password = cfg.get("REOLINK_PASSWORD", "").strip()
    stream = cfg.get("REOLINK_STREAM", "main").strip() or "main"
    suffix = "h264Preview_01_main" if stream == "main" else "h264Preview_01_sub"
    if not host or not username:
        return ""
    auth = username if not password else f"{username}:{password}"
    return f"rtsp://{auth}@{host}:554/{suffix}"


def public_rtsp_display() -> str:
    url = rtsp_url()
    cfg = read_config()
    password = cfg.get("REOLINK_PASSWORD", "")
    return url.replace(password, "********") if password else url


def record_capture(kind: str, filename: str, notes: str = "") -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            "INSERT INTO captures (kind, filename, created_at, notes) VALUES (?,?,?,?)",
            (kind, filename, datetime.now().isoformat(timespec="seconds"), notes),
        )


def captures() -> list[sqlite3.Row]:
    rows: list[dict[str, object]] = []
    seen_files = set()
    if DB_PATH.exists():
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        for row in con.execute("SELECT * FROM captures ORDER BY id DESC LIMIT 60").fetchall():
            seen_files.add(row["filename"])
            rows.append({
                "id": row["id"],
                "kind": capture_kind_label(row["kind"]),
                "filename": row["filename"],
                "created_at": row["created_at"],
                "notes": row["notes"],
                "ave_speed": None,
                "speed_units": "",
            })
        con.close()
    if SPEED_DB_PATH.exists():
        con = sqlite3.connect(SPEED_DB_PATH)
        con.row_factory = sqlite3.Row
        try:
            speed_rows = con.execute(
                """
                SELECT idx, log_timestamp, ave_speed, speed_units, image_path, direction, status, cam_location, mw, mh, m_area
                FROM speed
                WHERE ave_speed IS NULL OR CAST(ave_speed AS REAL) <= 80
                ORDER BY log_timestamp DESC
                LIMIT 60
                """
            ).fetchall()
        except sqlite3.Error:
            speed_rows = []
        for row in speed_rows:
            image_path = str(row["image_path"] or "")
            filename = media_path_for_url(image_path)
            seen_files.add(filename)
            rows.append({
                "id": row["idx"],
                "kind": "speedshot",
                "filename": filename,
                "created_at": str(row["log_timestamp"] or "").strip('"').replace(" ", "T"),
                "notes": "automatic detector",
                "ave_speed": row["ave_speed"],
                "speed_units": row["speed_units"] or "kph",
                "direction": row["direction"] or "",
                "status": row["status"] or "",
                "cam_location": row["cam_location"] or "",
                "mw": row["mw"] or 0,
                "mh": row["mh"] or 0,
                "m_area": row["m_area"] or 0,
            })
        con.close()
    for path in sorted((MEDIA_DIR / "images").glob("**/speed-*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)[:60]:
        filename = path.relative_to(MEDIA_DIR).as_posix()
        if filename in seen_files:
            continue
        rows.append({
            "id": path.name,
            "kind": "speedshot",
            "filename": filename,
            "created_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
            "notes": "automatic detector image",
            "ave_speed": None,
            "speed_units": "kph",
        })
        seen_files.add(filename)
    rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    return rows[:60]


def detector_passes(limit: int | None = None) -> list[dict[str, object]]:
    if not SPEED_DB_PATH.exists():
        return []
    training_labels = large_truck_training_labels()
    con = sqlite3.connect(SPEED_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        raw_rows = con.execute(
            """
            SELECT idx, log_timestamp, ave_speed, speed_units, image_path, direction, status,
                   cam_location, mw, mh, m_area
            FROM speed
            ORDER BY replace(trim(log_timestamp, '"'), 'T', ' '), idx
            """
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    passes: list[dict[str, object]] = []
    current_rows: list[sqlite3.Row] = []
    current_direction = ""
    previous_time: datetime | None = None

    def add_pass(rows: list[sqlite3.Row]) -> None:
        if not rows:
            return
        representative = max(rows, key=lambda row: int(row["m_area"] or 0))
        pass_speed = median(float(row["ave_speed"]) for row in rows)
        image_path = str(representative["image_path"] or "")
        representative_id = str(representative["idx"])
        passes.append({
            "id": representative_id,
            "kind": "speedshot",
            "filename": media_path_for_url(image_path),
            "created_at": str(representative["log_timestamp"] or "").strip('"').replace(" ", "T"),
            "ave_speed": pass_speed,
            "speed_units": representative["speed_units"] or "kph",
            "direction": representative["direction"] or "",
            "status": representative["status"] or "",
            "cam_location": representative["cam_location"] or "",
            "mw": representative["mw"] or 0,
            "mh": representative["mh"] or 0,
            "m_area": representative["m_area"] or 0,
            "fragments": len(rows),
            "training_label": training_labels.get(representative_id, ""),
        })

    for row in raw_rows:
        try:
            if float(row["ave_speed"]) > MAX_VALID_SPEED_KPH:
                continue
        except (TypeError, ValueError):
            continue
        row_time = capture_datetime(row["log_timestamp"])
        direction = str(row["direction"] or "").upper()
        is_new_pass = (
            not current_rows
            or direction != current_direction
            or row_time is None
            or previous_time is None
            or (row_time - previous_time).total_seconds() > PASS_MERGE_SECONDS
        )
        if is_new_pass:
            add_pass(current_rows)
            current_rows = []
            current_direction = direction
        current_rows.append(row)
        previous_time = row_time
    add_pass(current_rows)
    passes.reverse()
    return passes[:limit] if limit else passes


def run_ffmpeg(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timed out"
    output = (result.stderr or result.stdout or "").strip()
    return result.returncode == 0, output[-1200:]


def mjpeg_frames():
    url = rtsp_url()
    if not url:
        return
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        url,
        "-an",
        "-vf",
        "scale=960:-1",
        "-r",
        "8",
        "-q:v",
        "5",
        "-f",
        "mjpeg",
        "pipe:1",
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buffer = b""
    try:
        while proc.stdout:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            buffer += chunk
            while True:
                start = buffer.find(b"\xff\xd8")
                end = buffer.find(b"\xff\xd9", start + 2)
                if start < 0 or end < 0:
                    break
                frame = buffer[start:end + 2]
                buffer = buffer[end + 2:]
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def app_url(path: str = "") -> str:
    return f"{BASE_PATH}/{path.lstrip('/')}" if path else f"{BASE_PATH}/"


def media_url(filename: str) -> str:
    return app_url(f"media/{filename}")


def runtime_config_values() -> dict[str, str]:
    wanted = {
        "CAL_OBJ_PX_L2R",
        "CAL_OBJ_MM_L2R",
        "CAL_OBJ_PX_R2L",
        "CAL_OBJ_MM_R2L",
        "MO_TRACK_EVENT_COUNT",
        "MO_MIN_AREA_PX",
        "MO_MAX_X_DIFF_PX",
        "MO_EVENT_TIMEOUT_SEC",
        "MO_CROP_X_LEFT",
        "MO_CROP_X_RIGHT",
        "MO_CROP_Y_UPPER",
        "MO_CROP_Y_LOWER",
    }
    values: dict[str, str] = {}
    if not RUNTIME_CONFIG_PATH.exists():
        return values
    for line in RUNTIME_CONFIG_PATH.read_text(errors="replace").splitlines():
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if key in wanted:
            values[key] = raw_value.split("#", 1)[0].strip().strip("\"'")
    return values


def media_path_for_url(image_path: str) -> str:
    path = Path(image_path)
    if path.is_absolute():
        try:
            return path.relative_to(MEDIA_DIR).as_posix()
        except ValueError:
            return path.name
    parts = path.parts
    if parts[:1] == ("media",):
        return Path(*parts[1:]).as_posix()
    return path.as_posix()


def capture_speed(row: sqlite3.Row | None) -> str:
    if not row:
        return "--"
    if row.get("ave_speed") is not None:
        return str(round(float(row["ave_speed"]), 1))
    filename = row["filename"]
    parts = filename.split("-")
    if filename.startswith("speed-") and len(parts) > 1 and parts[1].isdigit():
        return parts[1]
    return "--"


def speed_number(row: sqlite3.Row | dict[str, object] | None) -> float | None:
    speed = capture_speed(row)
    try:
        return float(speed)
    except (TypeError, ValueError):
        return None


def speed_class(row: sqlite3.Row | dict[str, object] | None) -> str:
    speed = speed_number(row)
    return " over-limit" if speed is not None and speed > 50 else ""


def vehicle_area(row: sqlite3.Row | dict[str, object] | None) -> int:
    if not row:
        return 0
    try:
        return int(row.get("m_area") or 0)
    except (TypeError, ValueError):
        return 0


def vehicle_is_large(row: sqlite3.Row | dict[str, object] | None) -> bool:
    if not row:
        return False
    if str(row.get("training_label") or "") == "not_large_truck":
        return False
    try:
        width = int(row.get("mw") or 0)
        height = int(row.get("mh") or 0)
        area = int(row.get("m_area") or 0)
    except (TypeError, ValueError):
        return False
    return (
        area >= LARGE_VEHICLE_AREA
        and width >= LARGE_VEHICLE_MIN_WIDTH
        and height >= LARGE_VEHICLE_MIN_HEIGHT
    )


def vehicle_size(row: sqlite3.Row | dict[str, object] | None) -> str:
    if not row:
        return "--"
    try:
        width = int(row.get("mw") or 0)
        height = int(row.get("mh") or 0)
        area = int(row.get("m_area") or 0)
    except (TypeError, ValueError):
        return "--"
    if area <= 0:
        return "--"
    label = "truck candidate" if vehicle_is_large(row) else "normal"
    return f"{width}x{height} = {area} sqpx ({label})"


def vehicle_class(row: sqlite3.Row | dict[str, object] | None) -> str:
    return " large-vehicle" if vehicle_is_large(row) else ""


def attr(value: object) -> str:
    return escape(str(value or ""), quote=True)


def capture_datetime(value: object) -> datetime | None:
    text = str(value or "").strip().strip('"').replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def capture_short_date(value: object) -> str:
    dt = capture_datetime(value)
    if not dt:
        return str(value or "").strip().strip('"')
    return f"{dt.month}/{dt.day}"


def capture_short_datetime(value: object) -> str:
    dt = capture_datetime(value)
    if not dt:
        return str(value or "").strip().strip('"').replace("T", " ")
    return f"{dt.month}/{dt.day} {dt.strftime('%H:%M:%S')}"


def capture_time(value: object) -> str:
    dt = capture_datetime(value)
    if not dt:
        return ""
    return dt.strftime("%H:%M")


def capture_display_name(row: sqlite3.Row | dict[str, object] | None) -> str:
    if not row:
        return "No photo"
    speed = capture_speed(row)
    when = capture_short_date(row["created_at"])
    time = capture_time(row["created_at"])
    if speed != "--":
        return f"{speed} kph - {when} {time}".strip()
    return f"Photo - {when} {time}".strip()


def read_calibration_marks(limit: int = 20) -> list[dict[str, object]]:
    if not CALIBRATION_MARKS_PATH.exists():
        return []
    marks: list[dict[str, object]] = []
    for line in CALIBRATION_MARKS_PATH.read_text(errors="replace").splitlines():
        try:
            marks.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(marks[-limit:]))


def calibration_target_speed(value: str) -> int:
    try:
        speed = int(value)
    except (TypeError, ValueError):
        return 30
    return speed if speed in {30, 40, 50} else 30


def write_calibration_mark(direction: str, target_speed_kph: int, note: str = "") -> dict[str, object]:
    CALIBRATION_MARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    mark = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "direction": direction,
        "target_speed_kph": target_speed_kph,
        "vehicle": "red Buick SUV",
        "note": note,
    }
    with CALIBRATION_MARKS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(mark) + "\n")
    return mark


def nearby_detector_rows(mark: dict[str, object], window_seconds: int = 25) -> list[dict[str, object]]:
    mark_time = capture_datetime(mark.get("created_at"))
    if not mark_time:
        return []
    nearby: list[dict[str, object]] = []
    wanted_direction = str(mark.get("direction") or "").upper()
    for row in detector_passes(limit=120):
        row_time = capture_datetime(row.get("created_at"))
        if not row_time:
            continue
        diff = abs((row_time - mark_time).total_seconds())
        if diff > window_seconds:
            continue
        row_direction = str(row.get("direction") or "").upper()
        direction_ok = wanted_direction in {"", "UNKNOWN"} or row_direction == wanted_direction
        copy = dict(row)
        copy["calibration_diff"] = int(diff)
        copy["direction_ok"] = direction_ok
        nearby.append(copy)
    nearby.sort(key=lambda row: (not row.get("direction_ok"), row.get("calibration_diff", 999)))
    return nearby[:5]


def capture_stats(rows: list[sqlite3.Row | dict[str, object]]) -> dict[str, str]:
    captures_only = [r for r in rows if r["kind"] in ("snapshot", "speedshot")]
    now = datetime.now()
    hour_count = 0
    day_count = 0
    large_count = 0
    large_l2r = 0
    large_r2l = 0
    high_speed: float | None = None
    biggest_area = 0
    for row in captures_only:
        dt = capture_datetime(row.get("created_at"))
        if dt:
            if (now - dt).total_seconds() <= 3600:
                hour_count += 1
            if dt.date() == now.date():
                day_count += 1
        speed = speed_number(row)
        if speed is not None and (high_speed is None or speed > high_speed):
            high_speed = speed
        area = vehicle_area(row)
        if vehicle_is_large(row):
            large_count += 1
            if str(row.get("direction", "")).upper() == "L2R":
                large_l2r += 1
            if str(row.get("direction", "")).upper() == "R2L":
                large_r2l += 1
        biggest_area = max(biggest_area, area)
    return {
        "total": str(len(captures_only)),
        "hour": str(hour_count),
        "day": str(day_count),
        "high": f"{high_speed:.1f} kph" if high_speed is not None else "--",
        "large": str(large_count),
        "large_dirs": f"{large_l2r}/{large_r2l}",
        "biggest": f"{biggest_area} sqpx" if biggest_area else "--",
    }


def capture_heading(row: sqlite3.Row | None) -> str:
    if not row:
        return "No capture yet"
    speed = capture_speed(row)
    when = capture_short_datetime(row["created_at"])
    return f"{speed} km/h - {when}"


def capture_kind_label(kind: str) -> str:
    return "speedshot" if kind == "snapshot" else kind


PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Speed Camera</title>
  <style>
    :root{--ink:#182023;--muted:#677276;--line:#d9e0e2;--green:#238795;--green-dark:#176572;--gold:#c9974a;--red:#a33a2d;--soft:#f5f7f7}
    *{box-sizing:border-box}
    body{margin:0;font-family:Arial,sans-serif;background:#fff;color:var(--ink)}
    header{max-width:1100px;margin:0 auto;padding:18px 16px 12px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:minmax(260px,1fr) minmax(360px,520px);gap:18px;align-items:end;text-align:left}
    header h1{margin:0;font-size:clamp(2rem,5vw,3rem);line-height:1.05;letter-spacing:0}
    header p{margin:8px 0 0;color:var(--muted);font-size:1.05rem}
    .header-stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
    .stat{border:1px solid var(--line);border-radius:8px;background:var(--soft);padding:9px 10px;min-width:0}
    .stat span{display:block;color:var(--green-dark);font-size:.78rem;font-weight:900;line-height:1.1}
    .stat strong{display:block;margin-top:4px;font-size:1.1rem;line-height:1.05;white-space:nowrap}
    main{max-width:1100px;margin:18px auto 42px;padding:0 16px}
    .capture-view{display:block}
    .live-layout{display:grid;grid-template-columns:220px minmax(0,1fr);grid-template-areas:"left photo" "left details";gap:16px;align-items:start}
    .left-column{grid-area:left;display:grid;gap:12px}
    .capture-rail{border:1px solid var(--line);border-radius:8px;background:#fff;overflow:hidden}
    .rail-head{padding:10px 12px;border-bottom:1px solid var(--line);font-weight:800;color:var(--muted)}
    .capture-list{list-style:none;margin:0;padding:8px;max-height:430px;overflow:auto}
    .capture-list li + li{margin-top:8px}
    .night-watch{border:1px solid var(--line);border-radius:8px;background:#182023;color:#eef8f8;padding:12px}
    .night-watch h2{margin:0 0 8px;font-size:1rem;color:#fff}
    .night-watch p{margin:0 0 10px;color:#c9d5d3;font-size:.88rem}
    .night-watch ul{list-style:none;margin:0;padding:0}
    .night-watch li{padding:7px 0;border-top:1px solid #415052;font-size:.85rem}
    .night-watch strong{color:#fff}
    .capture-pick{width:100%;display:block;text-align:left;border:1px solid var(--line);border-radius:6px;background:var(--soft);color:var(--ink);padding:10px;min-height:48px;cursor:pointer}
    .capture-pick:hover,.capture-pick.active{border-color:var(--green);background:#eef8f8}
    .capture-pick.over-limit{position:relative;border-color:#e0aaa3;background:#fff2f0}
    .capture-pick.over-limit:after{content:"FAST";position:absolute;right:8px;top:8px;border-radius:4px;background:var(--red);color:#fff;font-size:.7rem;font-weight:900;padding:2px 5px}
    .capture-pick.large-vehicle{box-shadow:inset 4px 0 0 var(--gold)}
    .capture-pick.over-limit strong,.over-limit{color:var(--red)!important}
    .capture-pick strong{display:block;font-size:1.15rem;line-height:1}
    .capture-pick span{display:block;margin-top:5px;color:var(--muted);font-size:.9rem}
    .capture-pick.live-pick strong{color:var(--green-dark)}
    .mini-panel{border:1px solid var(--line);border-radius:8px;background:#fff;padding:12px}
    .mini-panel h2{font-size:1rem;margin:0 0 8px;color:var(--green-dark)}
    .mini-panel summary{cursor:pointer;font-weight:800;color:var(--green-dark)}
    .mini-panel summary::-webkit-details-marker{display:none}
    .mini-panel summary:after{content:"Show";float:right;color:var(--muted);font-size:.85rem}
    .mini-panel[open] summary:after{content:"Hide"}
    .mini-panel label{font-size:.9rem;margin-top:8px}
    .mini-panel input,.mini-panel select{padding:8px}
    .mini-panel .setup-actions{min-height:38px;padding:9px;margin-top:10px}
    .mini-panel code{font-size:.78rem}
    .mini-panel .log-tail{max-height:140px;font-size:.78rem}
    .photo-column{grid-area:photo;min-width:0}
    .image-shell{position:relative;display:flex;justify-content:center;align-items:center;height:320px;background:var(--soft);border:1px solid var(--line);border-radius:8px;overflow:hidden}
    img.preview{display:block;width:100%;height:auto;max-height:none;object-fit:contain;cursor:pointer}
    .speed-overlay{position:absolute;top:12px;right:12px;background:rgba(255,255,255,.92);border:2px solid var(--line);border-radius:8px;padding:8px 12px;font-size:clamp(1.8rem,4vw,3.1rem);font-weight:900;line-height:1;color:var(--ink);box-shadow:0 4px 16px rgba(0,0,0,.18)}
    .speed-overlay.over-limit{border-color:#e0aaa3;background:#fff2f0;color:var(--red)}
    .speed-overlay.over-limit:after{content:"FAST";display:block;margin-top:4px;font-size:.78rem;letter-spacing:0;color:var(--red)}
    .large-note{color:var(--gold);font-weight:900}
    .nav-buttons{display:grid;grid-template-columns:64px 64px minmax(0,1fr);gap:10px;margin-top:12px;align-items:center}
    .nav-icon{font-size:2rem;line-height:1;min-height:52px;padding:8px}
    .photo-caption{color:var(--muted);font-weight:700;min-width:0}
    .details{grid-area:details;border:1px solid var(--line);border-radius:8px;padding:18px;background:#fff}
    .details-top{display:grid;grid-template-columns:minmax(0,1fr) 170px;gap:16px;align-items:start;margin-bottom:16px}
    .details h2{font-size:1.65rem;margin:0;color:var(--muted)}
    .last-car{display:grid;gap:8px}
    .last-car button{display:block;width:100%;aspect-ratio:4/3;border:1px solid var(--line);border-radius:8px;background:#101918;padding:0;overflow:hidden;cursor:pointer}
    .last-car img{display:block;width:100%;height:100%;object-fit:cover}
    .last-car span{display:block;color:var(--muted);font-size:.85rem;font-weight:800;line-height:1.15}
    .road-watch{border:1px solid var(--line);border-radius:8px;background:#eef8f8;padding:10px 12px;margin-bottom:16px}
    .road-watch strong{display:block;color:var(--green-dark);font-size:.95rem}
    .road-watch span{display:block;margin-top:3px;color:var(--muted);font-size:.85rem}
    .status-line{display:flex;align-items:center;gap:10px;margin:0 0 14px}
    .status-pill{display:inline-flex;align-items:center;border-radius:999px;padding:5px 10px;font-weight:800;font-size:.85rem;background:#f1f5f3;color:var(--green-dark);border:1px solid var(--line)}
    .status-pill.warn{background:#fff7ed;color:var(--red);border-color:#efc7bd}
    .detail-grid{display:grid;grid-template-columns:160px minmax(0,1fr) 160px minmax(0,1fr);gap:0 14px}
    .detail-grid dt{color:var(--green-dark);font-weight:800;background:#eef8f8;border-top:1px solid var(--line);padding:8px 6px 2px}
    .detail-grid dt:first-child{border-top:0}
    .detail-grid dd{margin:0;word-break:break-word;border-bottom:1px solid var(--line);padding:2px 0 8px;line-height:1.25}
    .modal{position:fixed;inset:0;background:rgba(9,15,17,.86);display:none;align-items:center;justify-content:center;padding:62px 22px 22px;z-index:20}
    .modal.open{display:flex}
    .modal img{display:block;max-width:96vw;max-height:86vh;object-fit:contain;background:#fff;border-radius:8px}
    .modal-close{position:fixed;top:16px;right:18px;background:#fff;color:var(--ink);border:2px solid var(--line);border-radius:8px;min-width:112px;font-size:1.05rem}
    .cal-buttons{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
    .cal-button{display:block;width:100%;min-height:76px;border:0;border-radius:8px;background:var(--green);color:#fff;font-size:1.2rem;font-weight:900}
    .cal-button.gold{background:var(--gold)}
    .cal-button.secondary{background:#fff;color:var(--ink);border:1px solid var(--line)}
    .cal-button span{display:block;margin-top:4px;font-size:.85rem;font-weight:700;opacity:.9}
    .panel{border-top:1px solid var(--line);padding:24px 0;margin-top:28px}
    .panel h2{font-size:1.35rem;margin:0 0 14px}
    details.panel summary{cursor:pointer;font-weight:800;font-size:1.35rem;list-style:none}
    details.panel summary::-webkit-details-marker{display:none}
    details.panel summary:after{content:"Show";float:right;font-size:.95rem;color:var(--green-dark)}
    details.panel[open] summary:after{content:"Hide"}
    details.panel .config-grid{margin-top:18px}
    .config-grid{display:grid;grid-template-columns:minmax(220px,.75fr) minmax(280px,1.25fr);gap:28px}
    label{display:block;font-weight:700;margin-top:12px}
    input,select{width:100%;padding:10px;border:1px solid #b8c5c0;border-radius:6px;margin-top:6px;font:inherit}
    button,.button{display:inline-flex;align-items:center;justify-content:center;border:1px solid transparent;border-radius:6px;background:var(--green);color:white;text-decoration:none;padding:12px 14px;font-weight:800;cursor:pointer;min-height:42px}
    .button.secondary{background:#fff;color:var(--ink);border-color:var(--line)}
    .button.gold{background:var(--gold)}
    .button.red{background:var(--red)}
    .setup-actions{margin-top:14px;width:100%}
    .muted{color:var(--muted)}
    code{display:inline-block;max-width:100%;background:#f1f5f3;border:1px solid var(--line);border-radius:6px;padding:4px 6px;word-break:break-all}
    .log-tail{white-space:pre-wrap;background:#101918;color:#e8f2ef;border-radius:8px;padding:12px;max-height:180px;overflow:auto;font-size:.85rem}
    table{width:100%;border-collapse:collapse}
    th,td{text-align:left;border-bottom:1px solid #e2e9e6;padding:10px 8px;vertical-align:top}
    .history-wrap{max-height:520px;overflow:auto}
    pre{white-space:pre-wrap;background:#101918;color:#e8f2ef;border-radius:8px;padding:12px;max-height:260px;overflow:auto}
    .version-note{margin-top:12px;font-size:.85rem;color:var(--muted)}
    .credit{max-width:1100px;margin:0 auto 28px;padding:0 16px;color:var(--muted);font-size:.85rem}
    .credit a{color:var(--green-dark)}
    @media(max-width:920px){header{grid-template-columns:1fr}.header-stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}.live-layout,.config-grid{grid-template-columns:1fr}.live-layout{grid-template-areas:"photo" "details" "left"}.detail-grid{grid-template-columns:140px minmax(0,1fr)}.details-top{grid-template-columns:1fr}.last-car button{max-width:260px}.capture-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;max-height:none}.capture-list li + li{margin-top:0}.image-shell{min-height:240px}.nav-buttons{grid-template-columns:64px 64px minmax(0,1fr)}}
    @media(max-width:620px){.cal-buttons{grid-template-columns:1fr}.cal-button{min-height:88px;font-size:1.35rem}}
  </style>
</head>
<body>
<header>
  <div><h1 id="page-heading" class="{{ heading_class }}">{{ heading }}</h1><p>{{ subheading }}</p></div>
  <div class="header-stats">{{ header_stats|safe }}</div>
</header>
<main>{{ body|safe }}</main>
<footer class="credit">Speed camera runtime based on the open-source speed-camera project by Claude Pageau. HomeServer web dashboard and tuning added for this installation.</footer>
<div id="image-modal" class="modal" aria-hidden="true">
  <button class="modal-close" type="button" onclick="closeImage()">Close</button>
  <img id="modal-image" alt="Large speed camera capture">
</div>
</body>
</html>
"""


def page(body: str, heading: str = "Speed Camera", subheading: str = "Tapo capture station on HomeServer", heading_class: str = "", header_stats: str = "") -> str:
    return render_template_string(PAGE, body=body, heading=heading, subheading=subheading, heading_class=heading_class, header_stats=header_stats)


@app.route("/admin/login", methods=["POST"])
def admin_login():
    config = load_admin_config()
    password_hash = config.get("ADMIN_PASSWORD_HASH", "")
    submitted_password = request.form.get("password", "")
    if not password_hash or not check_password_hash(password_hash, submitted_password):
        return redirect(app_url(""), code=303)
    session.clear()
    session["speed_camera_admin"] = True
    session["speed_camera_csrf"] = secrets.token_urlsafe(32)
    return redirect(app_url(""), code=303)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    require_admin()
    session.clear()
    return redirect(app_url(""), code=303)


@app.route("/admin/service", methods=["POST"])
def admin_service():
    require_admin()
    if not secrets.compare_digest(session.get("speed_camera_csrf", ""), request.form.get("csrf_token", "")):
        abort(403)
    action = request.form.get("action", "")
    ok, output = service_action(action)
    message = escape(output)
    state = "completed" if ok else "failed"
    return page(
        f"<section class='panel'><h2>Service command {state}</h2><pre>{message}</pre><a class='button' href='{app_url()}'>Back</a></section>",
        heading="Camera service",
        subheading=f"{action.title()} {SERVICE_NAME}",
    )


@app.route("/admin/youtube", methods=["POST"])
def admin_youtube():
    require_admin()
    if not secrets.compare_digest(session.get("speed_camera_csrf", ""), request.form.get("csrf_token", "")):
        abort(403)
    action = request.form.get("action", "")
    if action == "save-and-start":
        stream_key = request.form.get("stream_key", "").strip()
        if not stream_key:
            return page(
                f"<section class='panel'><h2>Stream key required</h2><p>Paste the YouTube stream key before starting.</p><a class='button' href='{app_url()}'>Back</a></section>",
                heading="YouTube stream",
            )
        write_youtube_config(stream_key)
        ok, output = youtube_action("restart")
    else:
        ok, output = youtube_action(action)
    message = escape(output)
    state = "completed" if ok else "failed"
    return page(
        f"<section class='panel'><h2>YouTube command {state}</h2><pre>{message}</pre><a class='button' href='{app_url()}'>Back</a></section>",
        heading="YouTube stream",
    )


@app.route("/admin/large-truck-training", methods=["POST"])
def admin_large_truck_training():
    require_admin()
    if not secrets.compare_digest(session.get("speed_camera_csrf", ""), request.form.get("csrf_token", "")):
        abort(403)
    capture_id = request.form.get("capture_id", "").strip()
    label = request.form.get("label", "").strip()
    if not capture_id or label != "not_large_truck":
        abort(400)
    write_large_truck_training(
        capture_id,
        label,
        "Marked from admin button: This is not a large truck.",
    )
    return redirect(app_url(), code=303)


@app.route("/large-vehicles")
def large_vehicles():
    rows = [row for row in detector_passes() if vehicle_is_large(row)]
    table_rows = "".join(
        f"<tr><td>{escape(capture_short_datetime(row['created_at']))}</td>"
        f"<td>{escape(str(row.get('direction') or '--'))}</td>"
        f"<td>{escape(capture_speed(row))} {escape(str(row.get('speed_units') or 'kph'))}</td>"
        f"<td>{escape(vehicle_size(row))}</td>"
        f"<td>{int(row.get('fragments') or 1)}</td>"
        f"<td><a href='{attr(media_url(str(row['filename'])))}'>Image</a></td></tr>"
        for row in rows
    )
    body = f"""
    <section class="panel">
      <h2>Large vehicle passes</h2>
      <p class="muted">One row represents one directionally consistent pass. Tracking fragments within {PASS_MERGE_SECONDS} seconds are merged.</p>
      <div class="history-wrap">
        <table>
          <thead><tr><th>Time</th><th>Direction</th><th>Speed</th><th>Object size</th><th>Fragments</th><th>Capture</th></tr></thead>
          <tbody>{table_rows or '<tr><td colspan="6">No large vehicle passes recorded.</td></tr>'}</tbody>
        </table>
      </div>
      <p><a class="button secondary" href="{app_url()}">Back to live camera</a></p>
    </section>
    """
    return page(body, heading="Large vehicles", subheading="Deduplicated automatic detector passes")


@app.route("/calibrate", methods=["GET", "POST"])
def calibrate():
    saved_mark = None
    if request.method == "POST":
        saved_mark = write_calibration_mark(
            request.form.get("direction", "UNKNOWN").strip().upper(),
            calibration_target_speed(request.form.get("target_speed_kph", "30")),
            request.form.get("note", "").strip(),
        )
    marks = read_calibration_marks()
    button_forms = "".join(
        f"<form method='post' action='{app_url('calibrate')}'>"
        f"<input type='hidden' name='direction' value='{direction}'>"
        f"<input type='hidden' name='target_speed_kph' value='{speed}'>"
        f"<input type='hidden' name='note' value='red Buick SUV'>"
        f"<button class='cal-button{' gold' if direction == 'R2L' else ''}' type='submit'>{direction} {speed} km/h<span>tap as you enter view</span></button>"
        f"</form>"
        for speed in (30, 40, 50)
        for direction in ("L2R", "R2L")
    )
    mark_rows = ""
    for mark in marks[:8]:
        nearby = nearby_detector_rows(mark)
        matches = "".join(
            f"<tr><td>{escape(capture_short_datetime(row.get('created_at')))}</td>"
            f"<td>{escape(str(row.get('direction') or '--'))}</td>"
            f"<td>{escape(capture_speed(row))} {escape(str(row.get('speed_units') or 'kph'))}</td>"
            f"<td>{int(row.get('calibration_diff') or 0)} sec</td>"
            f"<td><a href='{attr(media_url(str(row.get('filename') or '')))}'>Image</a></td></tr>"
            for row in nearby
        ) or "<tr><td colspan='5'>No detector row within 25 seconds yet.</td></tr>"
        mark_rows += (
            f"<section class='panel'>"
            f"<h2>{escape(str(mark.get('vehicle') or 'Calibration car'))} at {escape(str(mark.get('target_speed_kph') or '30'))} km/h</h2>"
            f"<p class='muted'>Marked {escape(capture_short_datetime(mark.get('created_at')))} &middot; Direction {escape(str(mark.get('direction') or '--'))}</p>"
            f"<div class='history-wrap'><table><thead><tr><th>Detected</th><th>Direction</th><th>Speed</th><th>Gap</th><th>Photo</th></tr></thead><tbody>{matches}</tbody></table></div>"
            f"</section>"
        )
    saved_html = (
        f"<p class='status-line'><span class='status-pill'>Marked</span><span class='muted'>{escape(capture_short_datetime(saved_mark.get('created_at')))} {escape(str(saved_mark.get('direction') or ''))}</span></p>"
        if saved_mark else ""
    )
    body = f"""
    <section class="panel">
      <h2>Speed calibration pass</h2>
      <p class="muted">Use this from your phone. Tap the matching button as your red Buick SUV enters the camera view, then hold that speed through the full road box.</p>
      {saved_html}
      <div class="config-grid">
        <div>
          <div class="cal-buttons">{button_forms}</div>
        </div>
        <div>
          <p class="muted">Do at least two passes each direction when traffic is clear. The table below shows detector rows closest to each tap.</p>
          <p><a class="button secondary" href="{app_url()}">Back to live camera</a></p>
        </div>
      </div>
    </section>
    {mark_rows or '<section class="panel"><h2>No calibration marks yet</h2><p class="muted">Tap a direction button when you do the first pass.</p></section>'}
    """
    return page(body, heading="Speed calibration", subheading="Phone marker for red Buick 30 km/h passes")


@app.route("/")
def index():
    init_storage()
    detector_rows = detector_passes()
    rows = detector_rows or captures()
    stats = capture_stats(rows)
    stats_html = (
        f"<div class='stat'><span>Total captures</span><strong>{escape(stats['total'])}</strong></div>"
        f"<div class='stat'><span>Cars / hour</span><strong>{escape(stats['hour'])}</strong></div>"
        f"<div class='stat'><span>Cars / day</span><strong>{escape(stats['day'])}</strong></div>"
        f"<div class='stat'><span>Highest speed</span><strong>{escape(stats['high'])}</strong></div>"
        f"<div class='stat'><span>Large vehicles</span><strong><a href='{app_url('large-vehicles')}'>{escape(stats['large'])}</a></strong></div>"
        f"<div class='stat'><span>Large L/R</span><strong>{escape(stats['large_dirs'])}</strong></div>"
        f"<div class='stat'><span>Biggest object</span><strong>{escape(stats['biggest'])}</strong></div>"
    )
    latest_image = next((r for r in rows if r["kind"] in ("snapshot", "speedshot")), None)
    capture_rows = [r for r in rows if r["kind"] in ("snapshot", "speedshot") and r.get("filename")][:24]
    if not latest_image and capture_rows:
        latest_image = capture_rows[0]
    cfg = read_config()
    latest_caption = "Live camera"
    live_html = f'<img id="selected-image" class="preview" data-stream="mjpg" src="{app_url("live.mjpg")}" alt="Live camera frame" onclick="openImage()">'
    latest_index = rows.index(latest_image) if latest_image in rows else -1
    previous_snapshot = next((r for r in rows[latest_index + 1:] if r["kind"] in ("snapshot", "speedshot")), None) if latest_index >= 0 else None
    next_snapshot = next((r for r in reversed(rows[:latest_index]) if r["kind"] in ("snapshot", "speedshot")), None) if latest_index > 0 else None
    list_html = (
        f"<li><button class='capture-pick live-pick active' type='button' "
        f"data-id='' data-large='0' "
        f"data-live='1' data-image='{attr(app_url('live.mjpg'))}' data-speed='LIVE' "
        f"data-date='Now' data-name='Live camera' data-file='Live camera' "
        f"data-direction='' data-status='' data-location='{attr(cfg.get('CAMERA_NAME','Tapo Camera'))}' "
        f"onclick='selectCapture(this)'><strong>LIVE</strong><span>Now</span></button></li>"
    ) + "".join(
        (
            f"<li><button class='capture-pick{speed_class(r)}{vehicle_class(r)}' type='button' "
            f"data-id='{attr(r.get('id', ''))}' "
            f"data-large='{'1' if vehicle_is_large(r) else '0'}' "
            f"data-image='{attr(media_url(r['filename']))}' "
            f"data-speed='{attr(capture_speed(r))}' "
            f"data-date='{attr(capture_short_datetime(r['created_at']))}' "
            f"data-short-date='{attr(capture_short_date(r['created_at']))}' "
            f"data-file='{attr(r['filename'])}' "
            f"data-name='{attr(capture_display_name(r))}' "
            f"data-notes='{attr(r.get('notes', ''))}' "
            f"data-direction='{attr(r.get('direction', ''))}' "
            f"data-status='{attr(r.get('status', ''))}' "
            f"data-location='{attr(r.get('cam_location', ''))}' "
            f"data-size='{attr(vehicle_size(r))}' "
            f"onclick='selectCapture(this)'>"
            f"<strong>{escape(capture_speed(r))}</strong><span>{escape(capture_short_date(r['created_at']))}</span>"
            "</button></li>"
        )
        for r in capture_rows
    )
    current_speed = capture_speed(latest_image)
    now = datetime.now()
    tonight_start = now.replace(hour=18, minute=0, second=0, microsecond=0)
    if now < tonight_start:
        tonight_start -= timedelta(days=1)
    tonight_rows = [
        row for row in detector_rows
        if (row_time := capture_datetime(row.get("created_at"))) and row_time >= tonight_start
    ]
    tonight_l2r = sum(str(row.get("direction", "")).upper() == "L2R" for row in tonight_rows)
    tonight_r2l = sum(str(row.get("direction", "")).upper() == "R2L" for row in tonight_rows)
    tonight_speeds = [
        float(row["ave_speed"]) for row in tonight_rows
        if isinstance(row.get("ave_speed"), (int, float)) or str(row.get("ave_speed") or "").replace(".", "", 1).isdigit()
    ]
    tonight_fastest = f"{max(tonight_speeds):.1f} kph" if tonight_speeds else "--"
    latest_tonight_time = capture_datetime(tonight_rows[0]["created_at"]) if tonight_rows else None
    quiet_for = (
        f"Last pass {int((now - latest_tonight_time).total_seconds() // 60)} min ago"
        if latest_tonight_time else "Quiet since 6 PM"
    )
    tonight_recent = "".join(
        f"<li><strong>{escape(capture_short_datetime(row['created_at']))}</strong> "
        f"{escape(capture_speed(row))} {escape(str(row.get('speed_units') or 'kph'))} "
        f"{escape(str(row.get('direction') or ''))}</li>"
        for row in tonight_rows[:3]
    ) or "<li>No validated passes yet tonight.</li>"
    tonight_html = f"""
      <section class="night-watch">
        <h2>Tonight's traffic</h2>
        <p>{len(tonight_rows)} validated passes since 6 PM &middot; {tonight_l2r} L2R / {tonight_r2l} R2L</p>
        <p>Fastest: <strong>{tonight_fastest}</strong> &middot; {escape(quiet_for)}</p>
        <ul>{tonight_recent}</ul>
      </section>
    """
    status = detector_status()
    detector_label = "Running" if status["running"] else "Stopped"
    detector_class = "status-pill" if status["running"] else "status-pill warn"
    runtime_cfg = runtime_config_values()
    calibration_text = "Not set"
    if all(runtime_cfg.get(k) for k in ["CAL_OBJ_MM_L2R", "CAL_OBJ_PX_L2R", "CAL_OBJ_MM_R2L", "CAL_OBJ_PX_R2L"]):
        l2r_feet = int(runtime_cfg["CAL_OBJ_MM_L2R"]) / 304.8
        r2l_feet = int(runtime_cfg["CAL_OBJ_MM_R2L"]) / 304.8
        calibration_text = (
            f"R {l2r_feet:.1f} ft / {runtime_cfg['CAL_OBJ_PX_L2R']} px; "
            f"L {r2l_feet:.1f} ft / {runtime_cfg['CAL_OBJ_PX_R2L']} px"
        )
    crop_text = "Not set"
    if all(runtime_cfg.get(k) for k in ["MO_CROP_X_LEFT", "MO_CROP_Y_UPPER", "MO_CROP_X_RIGHT", "MO_CROP_Y_LOWER"]):
        crop_text = (
            f"{runtime_cfg['MO_CROP_X_LEFT']},{runtime_cfg['MO_CROP_Y_UPPER']}"
            f" to {runtime_cfg['MO_CROP_X_RIGHT']},{runtime_cfg['MO_CROP_Y_LOWER']}"
        )
    latest_detector = status["latest"]
    latest_detector_text = "No automatic speed rows yet"
    latest_detector_image = "None"
    if latest_detector:
        latest_detector_text = f"{latest_detector[1]} {latest_detector[2]} at {str(latest_detector[0]).replace('T', ' ')}"
        latest_detector_image = os.path.basename(str(latest_detector[3])) if latest_detector[3] else "None"
    last_car_url = media_url(str(latest_image["filename"])) if latest_image and latest_image.get("filename") else ""
    last_car_label = capture_display_name(latest_image) if latest_image else "No car yet"
    last_car_html = (
        f"<div class='last-car'><button type='button' onclick='openLastCar()' aria-label='Open last car image'>"
        f"<img src='{attr(last_car_url)}' alt='Last car that passed'></button>"
        f"<span>Last car: {escape(last_car_label)}</span></div>"
        if last_car_url
        else "<div class='last-car'><button type='button' disabled></button><span>Last car: waiting</span></div>"
    )
    day_count = int(stats["day"]) if str(stats["day"]).isdigit() else 0
    next_milestone = ((day_count // 25) + 1) * 25
    to_milestone = max(next_milestone - day_count, 0)
    road_watch_text = f"{to_milestone} more to {next_milestone} today" if day_count else "Waiting for today's first pass"
    latest_capture_id = str(latest_image.get("id", "")) if latest_image else ""
    log_tail = escape(str(status["log_tail"])) or "No detector log yet."
    admin_configured = bool(load_admin_config().get("ADMIN_PASSWORD_HASH"))
    if session.get("speed_camera_admin"):
        csrf_token = attr(session.get("speed_camera_csrf", ""))
        youtube = youtube_status()
        youtube_state = "Connected" if youtube["connected"] else ("Starting" if youtube["running"] else "Stopped")
        youtube_state_class = "status-pill" if youtube["connected"] else "status-pill warn"
        admin_controls_html = f"""
          <details class="mini-panel">
            <summary>Admin controls</summary>
            <p class="muted">Controls affect only {SERVICE_NAME}; the YouTube stream remains separate.</p>
            <p><a class="button secondary" href="{app_url()}">Camera app</a></p>
            <form method="post" action="{app_url('admin/service')}">
              <input type="hidden" name="csrf_token" value="{csrf_token}">
              <button class="setup-actions" name="action" value="status">Service status</button>
              <button class="setup-actions" name="action" value="start">Start camera</button>
              <button class="setup-actions red" name="action" value="stop">Stop camera</button>
              <button class="setup-actions gold" name="action" value="restart">Restart camera</button>
            </form>
            <form method="post" action="{app_url('admin/large-truck-training')}">
              <input type="hidden" name="csrf_token" value="{csrf_token}">
              <input type="hidden" id="training-capture-id" name="capture_id" value="">
              <input type="hidden" name="label" value="not_large_truck">
              <button id="not-large-truck-button" class="setup-actions gold" type="submit" disabled>This is not a large truck</button>
            </form>
            <form method="post" action="{app_url('admin/logout')}">
              <button class="setup-actions secondary">Sign out</button>
            </form>
          </details>
          <details class="mini-panel">
            <summary>YouTube live stream</summary>
            <p class="status-line"><span class="{youtube_state_class}">{youtube_state}</span><span class="muted">HomeServer publisher</span></p>
            <p class="muted">Paste the key from YouTube Studio, then save and start. The key is stored only on HomeServer.</p>
            <form method="post" action="{app_url('admin/youtube')}">
              <input type="hidden" name="csrf_token" value="{csrf_token}">
              <label>Stream key<input type="password" name="stream_key" autocomplete="off" placeholder="Paste YouTube stream key"></label>
              <button class="setup-actions" name="action" value="save-and-start">Save key and start stream</button>
              <button class="setup-actions gold" name="action" value="restart">Restart stream</button>
              <button class="setup-actions red" name="action" value="stop">Stop stream</button>
            </form>
          </details>
        """
    elif admin_configured:
        admin_controls_html = f"""
          <details class="mini-panel">
            <summary>Admin controls</summary>
            <p><a class="button secondary" href="{app_url()}">Camera app</a></p>
            <form method="post" action="{app_url('admin/login')}">
              <label>Admin password<input type="password" name="password" autocomplete="current-password" required></label>
              <button class="setup-actions">Sign in</button>
            </form>
          </details>
        """
    else:
        admin_controls_html = ""
    body = f"""
    <section class="capture-view">
      <div class="live-layout">
        <aside class="left-column">
          <div class="capture-rail">
            <div class="rail-head">Captures</div>
            <ul class="capture-list">{list_html}</ul>
          </div>
          {tonight_html}
          <details class="mini-panel">
            <summary>Camera setup</summary>
            <p class="muted">Current RTSP:</p>
            <code>{public_rtsp_display() or 'not configured yet'}</code>
            <form method="post" action="{app_url('config')}">
              <label>Camera name<input name="CAMERA_NAME" value="{cfg.get('CAMERA_NAME','')}"></label>
              <label>Camera IP address<input name="REOLINK_HOST" value="{cfg.get('REOLINK_HOST','')}" placeholder="192.168.2.xxx"></label>
              <label>Username<input name="REOLINK_USERNAME" value="{cfg.get('REOLINK_USERNAME','')}" placeholder="admin"></label>
              <label>Password<input type="password" name="REOLINK_PASSWORD" value="" placeholder="Saved password"></label>
              <label>Stream<select name="REOLINK_STREAM"><option value="main" {'selected' if cfg.get('REOLINK_STREAM') == 'main' else ''}>Main quality</option><option value="sub" {'selected' if cfg.get('REOLINK_STREAM') == 'sub' else ''}>Sub stream</option></select></label>
              <label>Override RTSP URL<input name="RTSP_URL" value="" placeholder="Saved override URL"></label>
              <button class="setup-actions">Save</button>
            </form>
          </details>
          <details class="mini-panel">
            <summary>Detector status</summary>
            <p class="muted">Mint service watching the camera automatically.</p>
            <pre class="log-tail">{log_tail}</pre>
          </details>
          <section class="mini-panel">
            <h2>Calibration</h2>
            <p class="muted">Mark your red Buick SUV passes from a phone while driving 30 km/h.</p>
            <a class="button secondary" href="{app_url('calibrate')}">Open marker</a>
          </section>
          {admin_controls_html}
        </aside>
        <div class="photo-column">
          <div class="image-shell">
            <div id="speed-overlay" class="speed-overlay">LIVE</div>
            {live_html}
          </div>
          <div class="nav-buttons">
            <button class="button secondary nav-icon" type="button" onclick="moveCapture(1)" aria-label="Older capture">&lt;</button>
            <button class="button secondary nav-icon" type="button" onclick="moveCapture(-1)" aria-label="Newer capture">&gt;</button>
            <div id="photo-caption" class="photo-caption">{latest_caption}</div>
          </div>
        </div>
        <aside class="details">
          <div class="details-top">
            <h2 id="detail-speed">Live</h2>
            {last_car_html}
          </div>
          <div class="road-watch">
            <strong>Road watch</strong>
            <span>{escape(road_watch_text)}</span>
          </div>
          <p class="status-line"><span class="{detector_class}">{detector_label}</span><span class="muted">automatic detector</span></p>
          <dl class="detail-grid">
            <dt>Captured</dt><dd id="detail-captured">Now</dd>
            <dt>Camera</dt><dd>{cfg.get('CAMERA_NAME','')}</dd>
            <dt>Host</dt><dd>{cfg.get('REOLINK_HOST','')}</dd>
            <dt>Stream</dt><dd>{cfg.get('REOLINK_STREAM','main')}</dd>
            <dt>Photo</dt><dd id="detail-file">Live camera</dd>
            <dt>Direction</dt><dd id="detail-direction"></dd>
            <dt>Status</dt><dd id="detail-status"></dd>
            <dt>Location</dt><dd id="detail-location">{cfg.get('CAMERA_NAME','')}</dd>
            <dt>Vehicle size</dt><dd id="detail-size"></dd>
            <dt>Auto rows</dt><dd>{status["rows"]}</dd>
            <dt>Latest auto</dt><dd>{latest_detector_text}</dd>
            <dt>Auto image</dt><dd>{latest_detector_image}</dd>
            <dt>Calibration</dt><dd>{calibration_text}</dd>
            <dt>Track hits</dt><dd>{runtime_cfg.get('MO_TRACK_EVENT_COUNT', 'Not set')}</dd>
            <dt>Min object</dt><dd>{runtime_cfg.get('MO_MIN_AREA_PX', 'Not set')} px</dd>
            <dt>Max jump</dt><dd>{runtime_cfg.get('MO_MAX_X_DIFF_PX', 'Not set')} px</dd>
            <dt>Timeout</dt><dd>{runtime_cfg.get('MO_EVENT_TIMEOUT_SEC', 'Not set')} sec</dd>
            <dt>Road box</dt><dd>{crop_text}</dd>
          </dl>
          <p class="version-note">Live server updated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ADT</p>
        </aside>
      </div>
    </section>

    <script>
      let liveMode = true;
      const liveImageUrl = "{app_url("live.jpg")}";
      const liveStreamUrl = "{app_url("live.mjpg")}";
      const loadedCaptureId = {json.dumps(latest_capture_id)};
      function activeButton() {{
        return document.querySelector(".capture-pick.active");
      }}
      function refreshLiveImage() {{
        if (!liveMode) return;
        const image = document.getElementById("selected-image");
        if (image.dataset.stream !== "mjpg") {{
          image.dataset.stream = "mjpg";
          image.src = liveStreamUrl;
        }}
      }}
      function selectCapture(button) {{
        document.querySelectorAll(".capture-pick").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        liveMode = button.dataset.live === "1";
        const overLimit = Number(button.dataset.speed) > 50;
        const image = document.getElementById("selected-image");
        if (liveMode) {{
          image.dataset.stream = "mjpg";
          image.src = liveStreamUrl;
        }} else {{
          delete image.dataset.stream;
          image.src = button.dataset.image;
        }}
        image.alt = liveMode ? "Live camera frame" : "Speed camera capture " + button.dataset.speed + " km/h";
        const title = liveMode ? "Live camera" : button.dataset.speed + " km/h - " + button.dataset.date;
        document.getElementById("photo-caption").textContent = title;
        document.getElementById("page-heading").textContent = title;
        document.getElementById("page-heading").classList.toggle("over-limit", overLimit);
        const speed = document.getElementById("detail-speed");
        speed.textContent = liveMode ? "Live" : button.dataset.speed + " km/h";
        speed.classList.toggle("over-limit", overLimit);
        const overlay = document.getElementById("speed-overlay");
        overlay.textContent = liveMode ? "LIVE" : button.dataset.speed;
        overlay.classList.toggle("over-limit", overLimit);
        document.getElementById("detail-captured").textContent = button.dataset.date;
        document.getElementById("detail-file").textContent = button.dataset.name || button.dataset.file || "None";
        document.getElementById("detail-direction").textContent = button.dataset.direction || "";
        document.getElementById("detail-status").textContent = button.dataset.status || (overLimit ? "Fast pass" : "");
        document.getElementById("detail-location").textContent = button.dataset.location || "";
        document.getElementById("detail-size").textContent = button.dataset.size || "";
        const trainingInput = document.getElementById("training-capture-id");
        const trainingButton = document.getElementById("not-large-truck-button");
        if (trainingInput && trainingButton) {{
          trainingInput.value = liveMode ? "" : (button.dataset.id || "");
          trainingButton.disabled = liveMode || !button.dataset.id;
        }}
      }}
      function moveCapture(step) {{
        const buttons = Array.from(document.querySelectorAll(".capture-pick"));
        if (!buttons.length) return;
        const current = buttons.indexOf(activeButton());
        const next = current + step;
        if (next < 0 || next >= buttons.length) return;
        selectCapture(buttons[next]);
      }}
      function openImage() {{
        const image = document.getElementById("selected-image");
        const modal = document.getElementById("image-modal");
        document.getElementById("modal-image").src = liveMode ? liveImageUrl + "?t=" + Date.now() : image.src;
        modal.classList.add("open");
        modal.setAttribute("aria-hidden", "false");
      }}
      function openLastCar() {{
        const image = document.querySelector(".last-car img");
        if (!image || !image.src) return;
        const modal = document.getElementById("image-modal");
        document.getElementById("modal-image").src = image.src;
        modal.classList.add("open");
        modal.setAttribute("aria-hidden", "false");
      }}
      function closeImage() {{
        const modal = document.getElementById("image-modal");
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
      }}
      document.addEventListener("keydown", (event) => {{
        if (event.key === "Escape") closeImage();
      }});
      function refreshWhenNewCaptureArrives() {{
        fetch("{app_url('capture-status.json')}?t=" + Date.now(), {{cache: "no-store"}})
          .then((response) => response.ok ? response.json() : null)
          .then((status) => {{
            if (!status || !status.latest_id) return;
            if (loadedCaptureId && status.latest_id !== loadedCaptureId) {{
              window.location.reload();
            }}
          }})
          .catch(() => {{}});
      }}
      setInterval(refreshLiveImage, 5000);
      setInterval(refreshWhenNewCaptureArrives, 3000);
    </script>
    """
    return page(body, heading="Live camera", subheading=f"{cfg.get('CAMERA_NAME','Speed Camera')} on HomeServer", heading_class="", header_stats=stats_html)


@app.route("/capture-status.json")
def capture_status_json():
    rows = detector_passes(limit=60) or captures()
    latest = next((r for r in rows if r["kind"] in ("snapshot", "speedshot")), None)
    payload = {
        "latest_id": str(latest.get("id", "")) if latest else "",
        "latest_speed": capture_speed(latest) if latest else "",
        "latest_time": capture_short_datetime(latest["created_at"]) if latest else "",
        "rows": sum(1 for r in rows if r["kind"] in ("snapshot", "speedshot")),
    }
    response = Response(json.dumps(payload), mimetype="application/json")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/config", methods=["POST"])
def save_config():
    values = {key: request.form.get(key, "") for key in ["CAMERA_NAME", "REOLINK_HOST", "REOLINK_USERNAME", "REOLINK_PASSWORD", "REOLINK_STREAM", "RTSP_URL"]}
    if not values["REOLINK_PASSWORD"]:
        values["REOLINK_PASSWORD"] = read_config().get("REOLINK_PASSWORD", "")
    if not values["RTSP_URL"]:
        values["RTSP_URL"] = read_config().get("RTSP_URL", "")
    write_config(values)
    try:
        sync_runtime_config()
        restart_detector()
    except Exception as exc:
        app.logger.warning("Detector config sync failed: %s", exc)
    return redirect(app_url())


@app.route("/live.mjpg")
def live_mjpg():
    if not rtsp_url():
        return Response("Camera not configured", status=404, mimetype="text/plain")
    response = Response(mjpeg_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.route("/live.jpg")
def live_jpg():
    if not LIVE_FRAME_PATH.exists():
        return Response("Live frame is starting", status=503, mimetype="text/plain")
    response = send_from_directory(MEDIA_DIR, "live.jpg")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/speedshot")
@app.route("/snapshot")
def speedshot():
    init_storage()
    url = rtsp_url()
    if not url:
        return page(f"<section class='panel'><h2>Camera not configured</h2><p>Add the camera IP address and login first.</p><a class='button' href='{app_url()}'>Back</a></section>")
    filename = "speedshot-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".jpg"
    target = MEDIA_DIR / filename
    command = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", url, "-frames:v", "1", "-q:v", "2", str(target)]
    ok, output = run_ffmpeg(command, timeout=25)
    if ok and target.exists():
        record_capture("speedshot", filename)
        return redirect(app_url())
    return page(f"<section class='panel'><h2>Speedshot failed</h2><p>Command: <code>{shlex.join(command).replace(url, public_rtsp_display())}</code></p><pre>{output}</pre><a class='button' href='{app_url()}'>Back</a></section>")


@app.route("/clip")
def clip():
    init_storage()
    url = rtsp_url()
    if not url:
        return page(f"<section class='panel'><h2>Camera not configured</h2><p>Add the camera IP address and login first.</p><a class='button' href='{app_url()}'>Back</a></section>")
    filename = "clip-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".mp4"
    target = MEDIA_DIR / filename
    command = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", url, "-t", "10", "-an", "-c:v", "copy", str(target)]
    ok, output = run_ffmpeg(command, timeout=35)
    if ok and target.exists():
        record_capture("clip", filename)
        return redirect(app_url())
    return page(f"<section class='panel'><h2>Clip failed</h2><p>Command: <code>{shlex.join(command).replace(url, public_rtsp_display())}</code></p><pre>{output}</pre><a class='button' href='{app_url()}'>Back</a></section>")


@app.route("/media/<path:filename>")
def media(filename: str):
    return send_from_directory(MEDIA_DIR, filename)


if __name__ == "__main__":
    init_storage()
    app.run(host="127.0.0.1", port=8020)

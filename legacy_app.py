from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_FILE = ROOT / ".data" / "projects.json"
DATA_LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_projects() -> list[dict]:
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def write_projects(projects: list[dict]) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp_file = DATA_FILE.with_suffix(".tmp")
    temp_file.write_text(json.dumps(projects, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_file.replace(DATA_FILE)


def get_project(projects: list[dict], project_id: str) -> dict | None:
    return next((project for project in projects if project["id"] == project_id), None)


def build_tasks(question: str) -> list[dict]:
    short_question = question.strip().rstrip("?？")
    return [
        {"id": str(uuid.uuid4()), "title": f"Define scope and key terms for: {short_question}", "phase": "Frame", "status": "todo"},
        {"id": str(uuid.uuid4()), "title": "Map the main perspectives and competing explanations", "phase": "Map", "status": "todo"},
        {"id": str(uuid.uuid4()), "title": "Collect primary sources and recent evidence", "phase": "Gather", "status": "todo"},
        {"id": str(uuid.uuid4()), "title": "Compare evidence, limitations, and open questions", "phase": "Synthesize", "status": "todo"},
    ]


def create_project(payload: dict) -> dict:
    question = str(payload.get("question", "")).strip()
    if not question:
        raise ValueError("A research question is required.")
    title = str(payload.get("title", "")).strip() or question[:72]
    stamp = now()
    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "question": question,
        "objective": str(payload.get("objective", "")).strip(),
        "status": "planning",
        "created_at": stamp,
        "updated_at": stamp,
        "tasks": build_tasks(question),
        "sources": [],
        "findings": [],
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepResearch/1.0"

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size > 1_000_000:
                raise ValueError("Request body is too large.")
            payload = json.loads(self.rfile.read(size) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/projects":
            with DATA_LOCK:
                projects = sorted(read_projects(), key=lambda item: item["updated_at"], reverse=True)
            self.send_json(200, projects)
            return
        match = re.fullmatch(r"/api/projects/([a-f0-9-]+)", parsed.path)
        if match:
            with DATA_LOCK:
                project = get_project(read_projects(), match.group(1))
            if project:
                self.send_json(200, project)
            else:
                self.send_json(404, {"error": "Project not found."})
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        try:
            payload = self.read_json()
            with DATA_LOCK:
                projects = read_projects()
                if path == "/api/projects":
                    project = create_project(payload)
                    projects.append(project)
                    write_projects(projects)
                    self.send_json(201, project)
                    return
                match = re.fullmatch(r"/api/projects/([a-f0-9-]+)/(sources|findings|run)", path)
                if match:
                    project = get_project(projects, match.group(1))
                    if not project:
                        self.send_json(404, {"error": "Project not found."})
                        return
                    action = match.group(2)
                    if action == "run":
                        project["status"] = "active"
                        project["updated_at"] = now()
                    elif action == "sources":
                        url = str(payload.get("url", "")).strip()
                        title = str(payload.get("title", "")).strip()
                        if not title or not url:
                            raise ValueError("A source title and URL are required.")
                        if urlparse(url).scheme.lower() not in {"http", "https"} or not urlparse(url).netloc:
                            raise ValueError("Source URL must use http or https.")
                        source = {
                            "id": str(uuid.uuid4()),
                            "title": title,
                            "url": url,
                            "publisher": str(payload.get("publisher", "")).strip(),
                            "note": str(payload.get("note", "")).strip(),
                            "added_at": now(),
                        }
                        project["sources"].append(source)
                    else:
                        text = str(payload.get("text", "")).strip()
                        if not text:
                            raise ValueError("A finding cannot be empty.")
                        finding = {
                            "id": str(uuid.uuid4()),
                            "text": text,
                            "source_id": str(payload.get("source_id", "")).strip(),
                            "added_at": now(),
                        }
                        project["findings"].append(finding)
                    project["updated_at"] = now()
                    write_projects(projects)
                    self.send_json(201, project)
                    return
            self.send_json(404, {"error": "Route not found."})
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})

    def do_PATCH(self) -> None:
        path = unquote(urlparse(self.path).path)
        objective_match = re.fullmatch(r"/api/projects/([a-f0-9-]+)/objective", path)
        task_match = re.fullmatch(r"/api/projects/([a-f0-9-]+)/tasks/([a-f0-9-]+)", path)
        if not objective_match and not task_match:
            self.send_json(404, {"error": "Route not found."})
            return
        try:
            payload = self.read_json()
            with DATA_LOCK:
                projects = read_projects()
                project = get_project(projects, objective_match.group(1) if objective_match else task_match.group(1))
                if not project:
                    self.send_json(404, {"error": "Project not found."})
                    return
                if objective_match:
                    objective = str(payload.get("objective", "")).strip()
                    if len(objective) > 300:
                        raise ValueError("Research objective must be 300 characters or fewer.")
                    project["objective"] = objective
                else:
                    status = payload.get("status")
                    if status not in {"todo", "doing", "done"}:
                        raise ValueError("Task status must be todo, doing, or done.")
                    task = next((item for item in project["tasks"] if item["id"] == task_match.group(2)), None)
                    if not task:
                        self.send_json(404, {"error": "Task not found."})
                        return
                    task["status"] = status
                    project["status"] = "complete" if project["tasks"] and all(item["status"] == "done" for item in project["tasks"]) else "active"
                project["updated_at"] = now()
                write_projects(projects)
            self.send_json(200, project)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        match = re.fullmatch(r"/api/projects/([a-f0-9-]+)", urlparse(self.path).path)
        if not match:
            self.send_json(404, {"error": "Route not found."})
            return
        with DATA_LOCK:
            projects = read_projects()
            filtered = [item for item in projects if item["id"] != match.group(1)]
            if len(filtered) == len(projects):
                self.send_json(404, {"error": "Project not found."})
                return
            write_projects(filtered)
        self.send_json(200, {"ok": True})

    def serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in candidate.parents and candidate != WEB_ROOT / "index.html":
            self.send_error(403)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        content_type = "text/html; charset=utf-8" if candidate.suffix == ".html" else "text/css; charset=utf-8" if candidate.suffix == ".css" else "text/javascript; charset=utf-8" if candidate.suffix == ".js" else "application/octet-stream"
        data = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    host = "127.0.0.1"
    port = 8000
    print(f"DeepResearch is running at http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()

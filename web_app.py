"""Local-only browser interface for the paper model engine.

Run on the Linux compute host and reach it through an SSH local port forward.
The server binds to loopback and requires a per-launch bearer token.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import re
import secrets
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from custom_equations import compile_modules, module_parameters, module_ranges
from paper_catalog import (CELL_ROLE_MODELS, CHANNEL_LABELS,
                           available_channels, channel_sources, effective_model,
                           validate_assignment, validate_disabled_channels,
                           validate_ranges)
from paper_engine import classify, save_result, simulate
from paper_formulas import equations_for
from paper_search import MAX_CONDITIONS, cpu_count, default_workers, group_total, run_search

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "web"
JOB_ID = re.compile(r"^[a-f0-9]{16}$")
FILE_TYPES = {".pdf": "application/pdf", ".csv": "text/csv; charset=utf-8",
              ".json": "application/json; charset=utf-8", ".npz": "application/octet-stream"}


def model_spec(config):
    role = config.get("role", "E")
    model = config.get("model", "Sato 2025 NAN")
    validate_assignment(role, model)
    composition = config.get("composition")
    modules = config.get("custom_modules") or []
    spec = effective_model(model, composition)
    states = (("Na_mM", "Ca_uM") if spec["family"] in ("FNAN", "COMPOSED") else
              ("Na_mM",) if spec["family"] == "NAN" else ("Ca_uM",))
    compile_modules(modules, spec["parameters"], spec["fixed"], states)
    parameters = {**spec["parameters"], **module_parameters(modules)}
    ranges = {**spec["ranges"], **module_ranges(modules)}
    disabled = validate_disabled_channels(model, config.get("disabled_channels", []), composition)
    formula = equations_for(model, disabled, composition)
    if modules:
        for module in modules:
            formula += f'\n\n{module["name"]}\nI = {module["current"]}\n'
            for variable in module.get("variables", []):
                name = variable["name"]
                expr = variable["equation"]
                if variable["kind"] == "ode" and variable.get("ode_form") == "relaxation":
                    expr = f'({expr} - {name}) / ({variable["tau"]})'
                formula += f'{"d" + name + "/dt" if variable["kind"] == "ode" else name} = {expr}\n'
            formula += f'dNa/dt += {module.get("na_rate", "0")}\n'
            formula += f'dCa/dt += {module.get("ca_rate", "0")}\n'
    return {
        "role": role, "model": model, "family": spec["family"],
        "composition": spec.get("composition"), "custom_modules": modules,
        "parameters": parameters, "fixed": spec["fixed"], "ranges": ranges,
        "channels": available_channels(model, composition), "formula": formula,
        "sources": {key: spec[key] for key in ("equations", "baseline", "search")},
    }


def checked_config(payload, search=False):
    built = model_spec(payload)
    keys = set(built["parameters"])
    parameters = payload.get("parameters", built["parameters"])
    fixed = payload.get("fixed", built["fixed"])
    ranges = payload.get("ranges", built["ranges"])
    if set(parameters) != keys or set(fixed) != set(built["fixed"]):
        raise ValueError("Parameter and constant names must match the selected model")
    if set(ranges) != keys:
        raise ValueError("Every parameter needs a search range")
    parameters = {key: float(value) for key, value in parameters.items()}
    fixed = {key: float(value) for key, value in fixed.items()}
    ranges = {key: (float(value[0]), float(value[1]), str(value[2]), str(value[3]))
              for key, value in ranges.items()}
    if any(not math.isfinite(v) for v in (*parameters.values(), *fixed.values())):
        raise ValueError("Parameter and constant values must be finite")
    validate_ranges({"ranges": built["ranges"]}, ranges, [list(keys)])
    disabled = validate_disabled_channels(built["model"], payload.get("disabled_channels", []),
                                          built["composition"])
    if search:
        groups = payload.get("groups", [])
        if not isinstance(groups, list) or not groups:
            raise ValueError("Add at least one parameter combination")
        if sum(group_total(group) for group in groups) > MAX_CONDITIONS:
            raise ValueError(f"At most {MAX_CONDITIONS:,} conditions can run together")
        workers = int(payload.get("workers", default_workers()))
        if not 1 <= workers <= cpu_count():
            raise ValueError(f"Workers must be between 1 and {cpu_count()}")
        for group in groups:
            if not group["parameters"] or not set(group["parameters"]) <= keys:
                raise ValueError("A combination has invalid parameter names")
            if not 1 <= group_total(group) <= MAX_CONDITIONS:
                raise ValueError("A combination has too many conditions")
    return {**built, "parameters": parameters, "fixed": fixed, "ranges": ranges,
            "disabled_channels": disabled}


class JobManager:
    def __init__(self, output_root):
        self.root = Path(output_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.lock = threading.RLock()
        self.active = None
        for record in sorted(self.root.glob("*/web_job.json")):
            try:
                job = json.loads(record.read_text(encoding="utf-8"))
                if (JOB_ID.fullmatch(job["id"]) and job["mode"] in ("baseline", "search")
                        and job["output"] == record.parent.name
                        and job["state"] in ("done", "stopped", "error")):
                    self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def list_jobs(self):
        with self.lock:
            return [{key: value for key, value in job.items() if key not in ("stop", "thread", "config")}
                    for job in reversed(list(self.jobs.values()))]

    def get(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job ID")
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("Unknown job")
            job = self.jobs[job_id]
            return {key: value for key, value in job.items() if key not in ("stop", "thread", "config")}

    def start(self, mode, config, source_job=None, row=None):
        with self.lock:
            if self.active and self.jobs[self.active]["state"] == "running":
                raise ValueError("A calculation is already running")
            identifier = secrets.token_hex(8)
            out = self.root / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + identifier)
            job = {"id": identifier, "state": "running", "mode": mode,
                   "completed": 0, "total": 0, "message": "Preparing calculation",
                   "output": out.name, "last_label": "", "trace": "", "error": "",
                   "stop": threading.Event(), "config": copy.deepcopy(config)}
            self.jobs[identifier] = job
            self.active = identifier
            target = threading.Thread(target=self._work, args=(job, source_job, row), daemon=True)
            job["thread"] = target
            target.start()
            return self.get(identifier)

    def stop_job(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise ValueError("Unknown job")
            self.jobs[identifier]["stop"].set()
            self.jobs[identifier]["message"] = "Stopping after the current task"
            return self.get(identifier)

    def _progress(self, job, done, total, label=""):
        with self.lock:
            job.update(completed=int(done), total=int(total), last_label=label,
                       message=f"{done:,} / {total:,}")

    def _work(self, job, source_job, row):
        cfg = job["config"]
        out = self.root / job["output"]
        try:
            if job["mode"] == "search":
                total = sum(group_total(group) for group in cfg["groups"])
                self._progress(job, 0, total)
                run_search(cfg["model"], cfg["ranges"], cfg["groups"], out,
                           workers=int(cfg["workers"]), baseline_parameters=cfg["parameters"],
                           fixed_overrides=cfg["fixed"], cell_role=cfg["role"],
                           disabled_channels=cfg["disabled_channels"],
                           composition=cfg["composition"], custom_modules=cfg["custom_modules"],
                           stop=job["stop"], progress=lambda n, t, result:
                           self._progress(job, n, t, result["label"]))
                message = "Search stopped" if job["stop"].is_set() else "Search completed"
            else:
                self._progress(job, 0, 20000)
                result = simulate(cfg["model"], cfg["parameters"],
                                  fixed_overrides=cfg["fixed"], disabled_channels=cfg["disabled_channels"],
                                  composition=cfg["composition"], custom_modules=cfg["custom_modules"],
                                  stop=job["stop"], progress=lambda n, total:
                                  self._progress(job, n, total))
                result["cell_role"] = cfg["role"]
                if source_job is not None:
                    source_output = self.root / self.get(source_job)["output"]
                    out = source_output / row["group"] / f'candidate_{int(row["index"]):06d}'
                save_result(result, out)
                job["trace"] = str((out / "trace.pdf").relative_to(self.root))
                job["last_label"] = classify(result)["label"]
                message = "Trace completed"
            with self.lock:
                job["state"] = "done"
                job["message"] = message
        except Exception as exc:
            with self.lock:
                job["state"] = "stopped" if isinstance(exc, InterruptedError) else "error"
                job["message"] = "Trace stopped" if isinstance(exc, InterruptedError) else "Calculation failed"
                job["error"] = "" if isinstance(exc, InterruptedError) else f"{type(exc).__name__}: {exc}"
                # Retain the full traceback on the compute host for troubleshooting.
                if not isinstance(exc, InterruptedError):
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            with self.lock:
                if self.active == job["id"]:
                    self.active = None
                if job["mode"] in ("baseline", "search"):
                    folder = self.root / job["output"]
                    folder.mkdir(parents=True, exist_ok=True)
                    public = {key: value for key, value in job.items()
                              if key not in ("stop", "thread", "config")}
                    (folder / "web_job.json").write_text(
                        json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def inspect(self, job_id, group, index):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job ID")
        output = self.root / self.get(job_id)["output"]
        config = json.loads((output / "search_config.json").read_text(encoding="utf-8"))
        with (output / "summary.csv").open(newline="", encoding="utf-8") as handle:
            row = next((item for item in csv.DictReader(handle)
                        if item["group"] == group and item["index"] == str(index)), None)
        if row is None:
            raise ValueError("Candidate not found")
        spec = effective_model(config["model"], config.get("composition"))
        modules = config.get("custom_modules", [])
        keys = {**spec["parameters"], **module_parameters(modules)}
        cfg = {"role": config["cell_role"], "model": config["model"],
               "composition": config.get("composition"), "custom_modules": modules,
               "disabled_channels": config.get("disabled_channels", []),
               "parameters": {key: float(row[key]) for key in keys},
               "fixed": config["fixed_values"]}
        return self.start("inspect", checked_config(cfg), job_id, row)

    def results(self, job_id, label="", offset=0, limit=100):
        output = self.root / self.get(job_id)["output"]
        csv_file = output / "summary.csv"
        if not csv_file.is_file():
            return {"rows": [], "total": 0}
        offset = max(0, min(int(offset), MAX_CONDITIONS))
        limit = max(1, min(int(limit), 500))
        rows = []
        matched = 0
        with csv_file.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                if label and row["label"] != label:
                    continue
                if offset <= matched < offset + limit:
                    if not row["trace_path"]:
                        candidate = output / row["group"] / f'candidate_{int(row["index"]):06d}'
                        if (candidate / "trace.pdf").is_file():
                            row["trace_path"] = str(candidate.relative_to(output))
                    rows.append(row)
                matched += 1
        return {"rows": rows, "total": matched}


class Handler(BaseHTTPRequestHandler):
    server_version = "ChannelCircuitLab/1.0-prime"

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, query):
        return (secrets.compare_digest(self.headers.get("X-App-Token", ""), self.server.token) or
                secrets.compare_digest(query.get("token", [""])[0], self.server.token))

    def _send_file(self, file, mime, attachment=False):
        size = file.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
        self.end_headers()
        with file.open("rb") as source:
            while data := source.read(1024 * 1024):
                self.wfile.write(data)

    def do_GET(self):
        split = urlsplit(self.path)
        path = unquote(split.path)
        query = parse_qs(split.query)
        if path not in ("/app.js", "/style.css") and not self._authorized(query):
            self._json({"error": "Use the full URL printed by the server at startup"}, 403)
            return
        try:
            if path in ("/", "/app.js", "/style.css"):
                name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
                mime = ("text/javascript" if name.endswith(".js") else
                        "text/css" if name.endswith(".css") else "text/html")
                self._send_file(ASSETS / name, mime + "; charset=utf-8")
            elif path == "/api/bootstrap":
                self._json({"roles": CELL_ROLE_MODELS, "sources": {key: channel_sources(key)
                           for key in CHANNEL_LABELS}, "channel_labels": CHANNEL_LABELS,
                           "cpus": cpu_count(), "default_workers": default_workers(),
                           "output_root": str(self.server.manager.root),
                           "default": model_spec({})})
            elif path == "/api/jobs":
                self._json({"jobs": self.server.manager.list_jobs()})
            elif path.startswith("/api/job/"):
                self._json(self.server.manager.get(path.split("/")[-1]))
            elif path.startswith("/api/results/"):
                self._json(self.server.manager.results(path.split("/")[-1],
                           query.get("label", [""])[0], query.get("offset", [0])[0]))
            elif path.startswith("/api/file/"):
                parts = path.split("/")
                if len(parts) < 5 or not JOB_ID.fullmatch(parts[3]):
                    raise ValueError("Invalid file path")
                relative = Path(*parts[4:])
                job = self.server.manager.get(parts[3])
                root = (self.server.manager.root / job["output"]).resolve()
                file = (root / relative).resolve()
                if not file.is_file() or not file.is_relative_to(root) or file.suffix not in FILE_TYPES:
                    raise ValueError("Result file not found")
                self._send_file(file, FILE_TYPES[file.suffix], file.suffix != ".pdf")
            else:
                self._json({"error": "Not found"}, 404)
        except (ValueError, KeyError, OSError) as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self):
        split = urlsplit(self.path)
        if not self._authorized(parse_qs(split.query)):
            self._json({"error": "Unauthorized"}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256 * 1024:
                raise ValueError("Invalid request size")
            data = json.loads(self.rfile.read(length))
            if split.path == "/api/spec":
                self._json(model_spec(data))
            elif split.path == "/api/run":
                mode = data.get("mode")
                if mode not in ("baseline", "search"):
                    raise ValueError("Select representative trace or search")
                cfg = checked_config(data, mode == "search")
                if mode == "search":
                    cfg["groups"] = data["groups"]
                    cfg["workers"] = int(data["workers"])
                self._json(self.server.manager.start(mode, cfg), 202)
            elif split.path == "/api/stop":
                self._json(self.server.manager.stop_job(data["id"]))
            elif split.path == "/api/inspect":
                self._json(self.server.manager.inspect(data["job"], data["group"], data["index"]), 202)
            else:
                self._json({"error": "Not found"}, 404)
        except (ValueError, TypeError, KeyError, IndexError, OSError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)

    def log_message(self, format, *args):
        # Do not log the launch token included in the browser URL.
        print("HTTP", self.address_string(), urlsplit(self.path).path, flush=True)


def main():
    parser = argparse.ArgumentParser(description="Local browser interface for Channel Circuit Lab")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--output", type=Path, default=Path.home() / "ChannelCircuitLab" / "WebRuns")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.token = secrets.token_urlsafe(24)
    server.manager = JobManager(args.output)
    print(f"Output folder: {server.manager.root}")
    print(f"Open through an SSH tunnel: http://127.0.0.1:{args.port}/?token={server.token}", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("Stopping server...", flush=True)
    finally:
        for job in server.manager.jobs.values():
            job["stop"].set()
        server.server_close()


if __name__ == "__main__":
    main()

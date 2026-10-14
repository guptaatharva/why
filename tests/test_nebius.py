"""The Nebius backend, end to end without Nebius: the generated job script runs locally with bash,
the bundle goes through a stand-in bucket over real HTTP, and results come back via the log markers."""

import http.server
import shlex
import subprocess
import sys
import threading
from datetime import datetime, timezone

import yaml

from why import objstore, runner
from why.mine import mine
from why.ops import approve, check


def test_presign_matches_aws_reference_vector():
    # docs.aws.amazon.com/AmazonS3/latest/API/sigv4-query-string-auth.html
    url = objstore.presign("GET", "https://examplebucket.s3.amazonaws.com/test.txt", "us-east-1",
                           "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", 86400,
                           datetime(2013, 5, 24, tzinfo=timezone.utc))
    assert url.endswith("X-Amz-Signature=aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404")


class FakeBucket:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        objects = self.objects

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = objects.get(self.path.split("?")[0].lstrip("/"))
                self.send_response(200 if body else 404)
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def put(self, key, data):
        self.objects[key] = data

    def url(self, method, key, expires=3600):
        return f"http://127.0.0.1:{self.srv.server_port}/{key}?X-Amz-Signature=x&b=1"

    def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


def test_large_sandbox_goes_through_object_storage(store, tmp_path, monkeypatch):
    mine(store, github=False, use_model=False)
    d = next(x for x in store.decisions() if "retry" in x.title)
    assert approve(store, d.id, use_model=False).activated

    cfg = yaml.safe_load((store.dir / "config.yaml").read_text())
    cfg.update(sandbox="nebius", nebius={"bucket": "why-sandboxes"})
    (store.dir / "config.yaml").write_text(yaml.safe_dump(cfg))

    bucket = FakeBucket()
    shims = tmp_path / "bin"
    shims.mkdir()
    (shims / "python").write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
    (shims / "pip").write_text("#!/bin/sh\nexit 0\n")
    for f in shims.iterdir():
        f.chmod(0o755)
    calls = []
    logs = {}

    def fake_nebius(*args):
        calls.append(args)
        if args[:3] == ("ai", "job", "create"):
            assert "--inject-file" not in args, "big bundles must not be injected"
            script = shlex.split(args[args.index("--args") + 1])[1]
            r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                               env={"PATH": f"{shims}:/usr/bin:/bin"})
            logs["job-1"] = r.stdout + r.stderr
            return "job-1"
        if args[:3] == ("ai", "job", "get"):
            return '{"status": {"state": "COMPLETED"}}'
        if args[:3] == ("ai", "job", "logs"):
            return logs["job-1"]
        raise AssertionError(args)

    monkeypatch.setattr(runner, "INJECT_LIMIT", 0)
    monkeypatch.setattr(runner, "JOB_ROOT", str(tmp_path / "job"))
    monkeypatch.setattr(runner, "_nebius", fake_nebius)
    monkeypatch.setattr(runner.shutil, "which", lambda name: "/usr/bin/nebius" if name == "nebius" else None)
    monkeypatch.setattr(objstore.Bucket, "from_config", classmethod(lambda cls, c: bucket))

    report = check(store, context="nebius-test")
    assert report.ok, [(i.decision_id, i.result, i.log[-500:]) for i in report.items] + [logs.get("job-1", "")[-800:]]
    assert any(i.decision_id == d.id and i.result == "pass" for i in report.items)
    assert bucket.deleted and not bucket.objects, "the staged bundle is removed after the job"
    assert any(a[:3] == ("ai", "job", "create") for a in calls)

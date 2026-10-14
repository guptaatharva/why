"""Entire Checkpoints as a mining source (branch-based layout: entire/checkpoints/v1, <id[:2]>/<id[2:]>/)."""

import json
import subprocess

from why.mine import mine
from why.sources.entire import checkpoints

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
CID = "a1b2c3d4e5f6"


def _git(repo, *args):
    return subprocess.run([*GIT, *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _with_checkpoint(store):
    repo = store.root
    (repo / "larkspur" / "shipping.py").write_text(
        "def shipping_quote(weight_grams: int) -> int:\n    return 499 if weight_grams < 2000 else 999\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"Add shipping quotes\n\nEntire-Checkpoint: {CID}")
    _git(repo, "checkout", "-q", "--orphan", "entire/checkpoints/v1")
    _git(repo, "rm", "-rq", "--cached", ".")
    d = repo / CID[:2] / CID[2:]
    d.mkdir(parents=True)
    (d / "full.jsonl").write_text("\n".join(json.dumps(m) for m in [
        {"type": "user", "message": {"role": "user", "content":
            "Never call `shipping_quote()` with kilograms: the carrier API bills in grams, and we undercharged "
            "every order last month."}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Got it."}]}},
    ]) + "\n")
    (d / "metadata.json").write_text(json.dumps({"checkpoint_id": CID, "agent": "Claude Code"}))
    _git(repo, "add", f"{CID[:2]}/")
    _git(repo, "commit", "-qm", "checkpoint")
    _git(repo, "checkout", "-q", "-f", "main")
    subprocess.run(["git", "clean", "-fdq", CID[:2]], cwd=repo, check=True)


def test_reads_checkpoint_transcripts(store):
    _with_checkpoint(store)
    snips = list(checkpoints(store.root))
    assert snips and all(s.type == "entire_checkpoint" and s.ref == CID for s in snips)
    assert any("bills in grams" in s.text for s in snips)


def test_mines_a_decision_from_a_checkpoint(store):
    _with_checkpoint(store)
    found = mine(store, github=False, use_model=False)
    d = next(x for x in found if "shipping_quote" in x.rule)
    assert d.source.type == "entire_checkpoint" and d.source.ref == CID
    assert "larkspur.shipping:shipping_quote" in d.scope["symbols"]

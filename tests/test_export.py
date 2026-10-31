import json
from pathlib import Path

from beeja.cli import main
from beeja.export import export_web, to_madr, to_portable_text
from tests.test_retrieval_diff import _rec

ROOT = Path(__file__).resolve().parent.parent


def test_madr_lists_options_outcomes_and_marks_unverified():
    md = to_madr(_rec())
    assert md.startswith("# How should external debuggers attach?")
    assert "* Single shared runtime buffer — **rejected**" in md
    assert 'Chosen option: "Per-thread debugger buffers".' in md
    assert "Bad, because forces serialization" in md and "[m2](https://x/t/1/2)" in md
    assert "m4 [unverified]" in md  # the unverified reason is labelled, not hidden
    assert "not human-reviewed" in md


def test_portable_text_is_valid_and_links_only_verified_sources():
    blocks = to_portable_text(_rec())
    assert all(b["_type"] == "block" and b["children"] for b in blocks)
    keys = [b["_key"] for b in blocks]
    assert len(keys) == len(set(keys))
    for b in blocks:
        defs = {d["_key"] for d in b["markDefs"]}
        for c in b["children"]:
            assert set(c["marks"]) - {"strong"} <= defs  # every annotation mark has a definition
    texts = " ".join(c["text"] for b in blocks for c in b["children"])
    assert "Single shared runtime buffer" in texts and "Use signals" not in texts  # unresolved not narrated


def test_export_web_includes_only_cited_messages(tmp_path):
    out = tmp_path / "data"
    ids = export_web(ROOT / "examples", out)
    index = json.loads((out / "index.json").read_text())
    assert sorted(ids) == sorted(e["id"] for e in index) and len(ids) >= 3
    payload = json.loads((out / f"{ids[0]}.json").read_text())
    cited = {e["message_id"] for e in payload["record"]["evidence"]}
    assert set(payload["messages"]) <= cited


def test_committed_web_data_is_current(tmp_path):
    out = tmp_path / "data"
    export_web([d for d in (ROOT / "examples", ROOT / "imports") if d.is_dir()], out)
    committed = ROOT / "web" / "public" / "data"
    assert sorted(p.name for p in committed.iterdir()) == sorted(p.name for p in out.iterdir())
    for p in out.iterdir():
        assert (committed / p.name).read_text() == p.read_text(), f"run `beeja export-web` ({p.name} is stale)"


def test_cli_export_madr(tmp_path, capsys):
    from beeja import storage

    storage.save(_rec(), tmp_path)
    assert main(["export-madr", "dec-768", "--store", str(tmp_path)]) == 0
    assert "## Considered Options" in capsys.readouterr().out
    assert main(["export-madr", "dec-nope", "--store", str(tmp_path)]) == 1

import json

import pytest

from beeja.ingest import IngestError, html_to_text, load_discussion
from tests.conftest import FIXTURES


def test_text_headers_parsed(tabs_discussion):
    d = tabs_discussion
    assert d.title == "Indentation style for the config parser"
    assert [m.message_id for m in d.messages] == [f"m{i}" for i in range(1, 8)]
    carol = d.message("m3")
    assert carol.author_id == "carol" and carol.author_role == "maintainer"
    assert d.message("m2").author_role is None  # never invented
    assert all(m.source_snapshot_hash.startswith("sha256:") for m in d.messages)
    assert [m.position for m in d.messages] == list(range(7))


def test_plain_text_split_on_rules(tmp_path):
    p = tmp_path / "notes.md"
    p.write_text("first idea\n---\nsecond idea\n\n---\n")
    d = load_discussion(p)
    assert [m.body for m in d.messages] == ["first idea", "second idea"]
    assert d.messages[0].author_id is None


def test_discourse_reply_tree_and_metadata():
    d = load_discussion(FIXTURES / "discourse_small.json")
    assert d.platform == "discourse" and d.thread_id == "999"
    p2 = d.message("post-2")
    assert p2.reply_to == "post-1" and p2.parent_message_id == "post-1"
    assert p2.source_url == "https://discuss.example.org/t/small-topic/999/2"
    assert p2.author_display_name == "critic"
    assert "> make foo optional" in p2.body  # quoted reply kept, marked
    assert "author1:" not in p2.body  # quote header dropped
    assert "break & confuse" in p2.body
    assert d.message("post-1").author_role == "admin"


def test_github_comments(tmp_path):
    p = tmp_path / "issue-12.json"
    p.write_text(json.dumps([
        {"id": 5, "user": {"login": "octo"}, "body": "Try caching", "author_association": "MEMBER",
         "created_at": "2024-01-01T00:00:00Z", "html_url": "https://github.com/o/r/issues/12#c5"},
        {"id": 6, "user": {"login": "cat"}, "body": "No", "in_reply_to_id": 5},
    ]))
    d = load_discussion(p)
    assert d.message("comment-6").reply_to == "comment-5"
    assert d.message("comment-5").author_role == "MEMBER"


def test_html_to_text_preserves_code_and_lists():
    t = html_to_text("<p>a</p><ul><li>one</li><li>two</li></ul><pre><code>x = 1\ny = 2</code></pre>")
    assert "- one" in t and "- two" in t and "x = 1\ny = 2" in t


@pytest.mark.parametrize("content,suffix", [("{not json", ".json"), ('{"foo": 1}', ".json"), ("x", ".pdf")])
def test_bad_inputs(tmp_path, content, suffix):
    p = tmp_path / f"bad{suffix}"
    p.write_text(content)
    with pytest.raises(IngestError):
        load_discussion(p)


def test_missing_file(tmp_path):
    with pytest.raises(IngestError):
        load_discussion(tmp_path / "nope.json")

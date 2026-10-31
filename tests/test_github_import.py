"""GitHub import: URL parsing, pagination, PR reviews, errors, storage/export, re-import, server.
GitHub and the model are faked (httpx.MockTransport / ScriptedProvider): no network, no model."""

import json
import threading
import time
import urllib.error
import urllib.request

import httpx
import pytest

from beeja import storage
from beeja.github import GitHubClient, GitHubError, fetch_discussion, parse_url
from beeja.importer import ImportError_, import_github
from beeja.model_adapter import ModelError
from beeja.server import Jobs, make_handler

API = "https://api.github.com"


def issue(n=7, pr=False, body="We need a way to delete projects that still have threads."):
    d = {"number": n, "title": "Delete non-empty projects", "state": "open", "body": body,
         "user": {"login": "alice"}, "created_at": "2026-01-01T00:00:00Z", "author_association": "NONE",
         "html_url": f"https://github.com/o/r/issues/{n}"}
    if pr:
        d["pull_request"] = {"url": "x"}
    return d


def comment(i, body, user="bob"):
    return {"id": i, "body": body, "user": {"login": user}, "created_at": f"2026-01-0{min(i, 9)}T00:00:00Z",
            "html_url": f"https://github.com/o/r/issues/7#issuecomment-{i}", "author_association": "MEMBER"}


def gh_for(routes, token=None):
    """routes: {path: (status, json, headers)}; query strings are part of the key when present."""
    seen = []

    def handler(req):
        key = req.url.path + (f"?{req.url.query.decode()}" if req.url.query and b"page=" in req.url.query else "")
        seen.append(key)
        status, body, headers = routes.get(key, routes.get(req.url.path, (404, {"message": "Not Found"}, {})))
        return httpx.Response(status, json=body, headers=headers)

    c = GitHubClient(token, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c.seen = seen
    return c


# ------------------------------------------------------------------ parsing / fetching


@pytest.mark.parametrize("url,case,kind", [
    ("https://github.com/pingdotgg/t3code/issues/665", "gh-pingdotgg-t3code-issue-665", "issue"),
    ("https://github.com/o/r/pull/12/files", "gh-o-r-pr-12", "pull"),
    ("github.com/o/r/issues/3".replace("github", "https://github"), "gh-o-r-issue-3", "issue"),
])
def test_parse_url(url, case, kind):
    ref = parse_url(url)
    assert ref.case == case and ref.kind == kind


@pytest.mark.parametrize("url", ["", "https://gitlab.com/o/r/issues/1", "https://github.com/o/r", "not a url",
                                 "https://github.com/o/r/discussions/5"])
def test_parse_url_rejects(url):
    with pytest.raises(GitHubError) as e:
        parse_url(url)
    assert e.value.kind == "invalid_url"


def test_issue_comments_are_paginated_and_preserve_metadata():
    nxt = f'<{API}/repos/o/r/issues/7/comments?per_page=100&page=2>; rel="next"'
    gh = gh_for({
        "/repos/o/r/issues/7": (200, issue(), {}),
        "/repos/o/r/issues/7/comments": (200, [comment(1, "Use a confirm dialog?")], {"Link": nxt}),
        "/repos/o/r/issues/7/comments?per_page=100&page=2": (200, [comment(2, "Bulk delete in #651 helps.", "carol")], {}),
    })
    d = fetch_discussion(parse_url("https://github.com/o/r/issues/7"), gh)
    assert [m["message_id"] for m in d["messages"]] == ["description", "comment-1", "comment-2"]
    assert d["messages"][2]["author_id"] == "carol"
    assert d["messages"][1]["source_url"].endswith("#issuecomment-1")
    assert d["messages"][1]["created_at"] == "2026-01-01T00:00:00Z"
    assert gh.requests == 3


def test_pull_request_includes_reviews_and_review_threads():
    gh = gh_for({
        "/repos/o/r/issues/9": (200, issue(9, pr=True), {}),
        "/repos/o/r/issues/9/comments": (200, [comment(1, "LGTM overall")], {}),
        "/repos/o/r/pulls/9/reviews": (200, [
            {"id": 50, "body": "Please don't add a new dependency.", "state": "CHANGES_REQUESTED", "user": {"login": "dave"},
             "submitted_at": "2026-01-03T00:00:00Z", "html_url": "u"},
            {"id": 51, "body": "", "state": "APPROVED", "user": {"login": "erin"}, "submitted_at": "2026-01-04T00:00:00Z"},
        ], {}),
        "/repos/o/r/pulls/9/comments": (200, [
            {"id": 70, "body": "Why not stdlib here?", "path": "a.py", "user": {"login": "dave"},
             "created_at": "2026-01-05T00:00:00Z"},
            {"id": 71, "body": "Good point, switching.", "path": "a.py", "in_reply_to_id": 70, "user": {"login": "alice"},
             "created_at": "2026-01-06T00:00:00Z"},
        ], {}),
    })
    d = fetch_discussion(parse_url("https://github.com/o/r/pull/9"), gh)
    ids = [m["message_id"] for m in d["messages"]]
    assert "review-50" in ids and "review-51" not in ids  # empty approvals carry no discussion
    reply = next(m for m in d["messages"] if m["message_id"] == "review-comment-71")
    assert reply["reply_to"] == "review-comment-70" and reply["body"].startswith("[on a.py]")
    assert "commits and code diffs" in d["metadata"]["github"]["omitted"]


@pytest.mark.parametrize("status,headers,kind", [
    (404, {}, "not_found"),
    (403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "123"}, "rate_limited"),
    (429, {}, "rate_limited"),
    (401, {}, "forbidden"),
])
def test_github_errors_are_specific(status, headers, kind):
    gh = gh_for({"/repos/o/r/issues/7": (status, {"message": "x"}, headers)})
    with pytest.raises(GitHubError) as e:
        fetch_discussion(parse_url("https://github.com/o/r/issues/7"), gh)
    assert e.value.kind == kind


def test_empty_discussion_is_refused():
    gh = gh_for({"/repos/o/r/issues/7": (200, issue(), {}), "/repos/o/r/issues/7/comments": (200, [], {})})
    with pytest.raises(GitHubError) as e:
        fetch_discussion(parse_url("https://github.com/o/r/issues/7"), gh)
    assert e.value.kind == "empty"


def test_token_is_only_a_header():
    gh = GitHubClient("secret-token-123")
    assert gh._c.headers["authorization"] == "Bearer secret-token-123" and gh.authenticated


# ------------------------------------------------------------------ import workflow


def ok_routes():
    return {"/repos/o/r/issues/7": (200, issue(), {}),
            "/repos/o/r/issues/7/comments": (200, [comment(1, "A confirm dialog would be safer than blocking.")], {})}


def model_answer(status="unresolved", quote=""):
    return {"question": "How should deleting non-empty projects work?", "alternatives": [{
        "title": "Confirm dialog before deleting a non-empty project", "same_as": "", "description": "dialog",
        "proposed_in": "comment-1", "proponents": ["bob"], "status": status, "status_message_id": "comment-1",
        "status_quote": quote, "arguments": [{"stance": "for", "text": "safer", "message_id": "comment-1",
                                              "quote": "A confirm dialog would be safer than blocking"}]}]}


def run_import(root, scripted, reimport=False, answers=None):
    return import_github("https://github.com/o/r/issues/7", root=root, reimport=reimport,
                         provider=scripted(answers or [model_answer()] + [{"explanation": "", "status": "unresolved",
                                                                            "status_message_id": "", "status_quote": "",
                                                                            "reasons": []}] * 3),
                         gh=gh_for(ok_routes()))


def test_import_saves_record_store_and_viewer_data(tmp_path, scripted):
    (tmp_path / "examples").mkdir()
    res = run_import(tmp_path, scripted)
    case = tmp_path / "imports" / "gh-o-r-issue-7"
    rid = res["decision_id"]
    assert (case / "decision.json").is_file() and (case / "input" / "discussion.json").is_file()
    assert (case / "input_path.txt").read_text().strip() == "imports/gh-o-r-issue-7/input/discussion.json"
    assert storage.load_file(tmp_path / "decisions" / f"{rid}.json")[0].alternatives[0].status == "unresolved"
    index = json.loads((tmp_path / "web/public/data/index.json").read_text())
    assert [e["id"] for e in index] == [rid]
    payload = json.loads((tmp_path / f"web/public/data/{rid}.json").read_text())
    assert "comment-1" in payload["messages"]  # cited source message available for highlighting
    assert not (case / ".staging").exists()


def test_repeat_import_is_refused_and_reimport_keeps_history(tmp_path, scripted):
    first = run_import(tmp_path, scripted)
    with pytest.raises(ImportError_) as e:
        run_import(tmp_path, scripted)
    assert e.value.kind == "exists" and e.value.extra["decision_id"] == first["decision_id"]
    second = run_import(tmp_path, scripted, reimport=True)
    hist = list((tmp_path / "imports/gh-o-r-issue-7/history").iterdir())
    assert len(hist) == 1 and (hist[0] / "decision.json").is_file() and (hist[0] / "input/discussion.json").is_file()
    assert second["archived_previous"].startswith("imports/gh-o-r-issue-7/history/")


def test_model_failure_reports_error_and_writes_nothing(tmp_path):
    class Down:
        name = "ollama:qwen3:4b"

        def generate_json(self, *a):
            raise ModelError("ollama:qwen3:4b failed after 3 attempts: [Errno 61] Connection refused")

    with pytest.raises(ImportError_) as e:
        import_github("https://github.com/o/r/issues/7", root=tmp_path, provider=Down(), gh=gh_for(ok_routes()))
    assert e.value.kind == "model_unreachable" and "BEEJA_PROVIDER" in str(e.value)
    assert not (tmp_path / "decisions").exists()
    assert not (tmp_path / "imports/gh-o-r-issue-7/decision.json").exists()


def test_import_keeps_existing_examples_in_viewer(tmp_path, scripted):
    import shutil

    from tests.test_examples_regression import ROOT

    shutil.copytree(ROOT / "examples" / "synthetic-config-format", tmp_path / "examples" / "synthetic-config-format")
    shutil.copytree(ROOT / "benchmarks" / "synthetic-config-format", tmp_path / "benchmarks" / "synthetic-config-format")
    run_import(tmp_path, scripted)
    ids = {e["case"] for e in json.loads((tmp_path / "web/public/data/index.json").read_text())}
    assert ids == {"synthetic-config-format", "gh-o-r-issue-7"}


def test_imported_record_is_visible_to_mcp(tmp_path, scripted):
    pytest.importorskip("mcp")
    import anyio
    from mcp import Client

    from beeja.mcp_server import build_server

    rid = run_import(tmp_path, scripted)["decision_id"]

    async def go():
        async with Client(build_server(tmp_path / "decisions")) as c:
            s = await c.call_tool("search_decisions", {"query": "confirm dialog delete project"})
            g = await c.call_tool("get_decision", {"ref": rid})
            return s, g

    s, g = anyio.run(go)
    assert rid in json.dumps(s.structured_content)
    assert g.structured_content["decision"]["id"] == rid


# ------------------------------------------------------------------ local server


@pytest.fixture
def server(tmp_path):
    from http.server import ThreadingHTTPServer

    static = tmp_path / "web" / "dist"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<html>viewer</html>")
    calls = []

    def fake_import(url, root, reimport, progress):
        calls.append(url)
        progress("Fetching…")
        if "missing" in url:
            raise ImportError_("Issue or pull request not found.", "not_found")
        (root / "web/public/data").mkdir(parents=True, exist_ok=True)
        (root / "web/public/data/dec-x.json").write_text("{}")
        return {"decision_id": "dec-x", "summary": "1 alternatives recovered", "archived_previous": None}

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path, static, Jobs(), import_fn=fake_import))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", tmp_path, calls
    httpd.shutdown()


def _req(url, data=None):
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _wait(base, jid):
    for _ in range(50):
        job = json.loads(_req(f"{base}/api/jobs/{jid}")[1])
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_server_import_job_success_and_error(server, monkeypatch):
    base, root, calls = server
    monkeypatch.setenv("BEEJA_API_KEY", "sk-should-never-leak")
    code, body = _req(f"{base}/api/status")
    assert code == 200 and b"sk-should-never-leak" not in body
    code, body = _req(f"{base}/api/import", {"url": "https://github.com/o/r/issues/7"})
    assert code == 202
    job = _wait(base, json.loads(body)["job"])
    assert job["state"] == "done" and job["result"]["decision_id"] == "dec-x" and job["progress"] == ["Fetching…"]
    assert _req(f"{base}/data/dec-x.json")[0] == 200
    code, body = _req(f"{base}/api/import", {"url": "https://github.com/o/missing/issues/1"})
    job = _wait(base, json.loads(body)["job"])
    assert job["state"] == "error" and job["kind"] == "not_found"


def test_server_rejects_bad_urls_and_existing_imports(server):
    base, root, calls = server
    code, body = _req(f"{base}/api/import", {"url": "https://example.com/x"})
    assert code == 400 and json.loads(body)["kind"] == "invalid_url"
    case = root / "imports" / "gh-o-r-issue-7"
    case.mkdir(parents=True)
    (case / "decision.json").write_text(json.dumps({"id": "dec-old"}))
    code, body = _req(f"{base}/api/import", {"url": "https://github.com/o/r/issues/7"})
    assert code == 409
    assert json.loads(body) == {"error": "Already imported as dec-old.", "kind": "exists", "decision_id": "dec-old"}
    assert calls == []


def test_server_static_and_path_traversal(server):
    base, root, _ = server
    assert _req(f"{base}/")[1] == b"<html>viewer</html>"
    assert _req(f"{base}/some/route")[1] == b"<html>viewer</html>"  # SPA fallback
    assert _req(f"{base}/data/../../.env")[0] == 404

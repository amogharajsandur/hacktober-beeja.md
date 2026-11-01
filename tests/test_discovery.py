import httpx

from beeja.discovery import discover
from beeja.github import GitHubClient


def test_discovery_is_scoped_bounded_and_keeps_source_outcomes(tmp_path):
    def handler(request):
        assert request.url.path == '/search/issues'
        assert request.url.params['q'].startswith('repo:o/r ')
        assert 'is:open' not in request.url.params['q']
        assert request.url.params['per_page'] == '10'
        return httpx.Response(200, json={'items': [
            {'number': n, 'title': 'Configuration format choice', 'body': '', 'state': 'closed',
             'html_url': f'https://github.com/o/r/issues/{n}'} for n in range(1, 7)
        ] + [{'title': 'Configuration format', 'html_url': 'https://github.com/other/r/issues/9'}]})

    gh = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(handler)))
    calls = []

    def import_one(url):
        calls.append(url)
        return {'decision_id': f'dec-{len(calls)}', 'status': 'unresolved'}

    result = discover({'source_url': 'https://github.com/o/r/issues/1',
                       'title': 'o/r#1: Configuration format'}, tmp_path, gh, import_one, lambda _: None)
    assert len(calls) == 3
    assert 'https://github.com/o/r/issues/1' not in calls
    assert all(source['status'] == 'unresolved' for source in result['sources'])
    assert result['warnings'] == []


def test_discovery_failure_is_visible(tmp_path):
    gh = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(429, json={})) ))
    result = discover({'source_url': 'https://github.com/o/r/issues/1', 'title': 'Configuration format'},
                      tmp_path, gh, lambda _: None, lambda _: None)
    assert result['warnings'] and result['sources'] == []


def test_existing_decision_is_reused(tmp_path, monkeypatch):
    from tests.test_retrieval_diff import _rec

    (tmp_path / "decisions").mkdir()
    record = _rec()
    record.source_threads = ['https://github.com/o/r/issues/2']
    record.question = 'Configuration format'
    monkeypatch.setattr('beeja.discovery.storage.load_dir', lambda _: [record])
    gh = GitHubClient(client=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={'items': []}))))

    def unexpected_import(_):
        raise AssertionError('Stored record should be reused')

    result = discover({'source_url': 'https://github.com/o/r/issues/1', 'title': 'Configuration format'},
                      tmp_path, gh, unexpected_import, lambda _: None)
    assert result['sources'][0]['reused'] is True
    assert result['sources'][0]['decision_id'] == record.id

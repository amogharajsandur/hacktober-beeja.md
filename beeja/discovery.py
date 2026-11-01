"""Bounded related-history search. Similarity selects context, never proves an outcome."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from . import storage
from .github import GitHubClient, GitHubError, parse_url

STOP = set('the a an to of in on for with and or is are be this that feature request bug fix add support'.split())


def terms(text: str) -> set[str]:
    return {s for s in re.findall(r'[a-z][a-z0-9_]+', text.lower()) if len(s) > 2 and s not in STOP}


def discover(data: dict, root: Path, gh: GitHubClient, import_fn: Callable, progress: Callable) -> dict[str, Any]:
    ref = parse_url(data['source_url'])
    title = data['title'].split(': ', 1)[-1]
    words = terms(title)
    query = f'repo:{ref.owner}/{ref.repo} in:title,body ' + ' '.join(sorted(words, key=lambda s: (-len(s), s))[:2])
    report: dict[str, Any] = {'query': query, 'limit': 3, 'sources': [], 'warnings': []}
    if not words:
        report['warnings'].append('No useful title terms for related-history search.')
        return report
    candidates = {}
    records = storage.load_dir(root / 'decisions') if (root / 'decisions').is_dir() else []
    for rec in records:
        for url in rec.source_threads:
            try:
                other = parse_url(url)
            except GitHubError:
                continue
            overlap = sorted(words & terms(rec.question + ' ' + rec.topic))
            if (other.owner.lower(), other.repo.lower()) == (ref.owner.lower(), ref.repo.lower()) and other.number != ref.number:
                if len(overlap) >= 2:
                    candidates[other.number] = {'url': other.html_url, 'title': rec.question, 'matched_terms': overlap,
                                                 'decision_id': rec.id, 'status': rec.status, 'reused': True}
    progress('Searching related open and closed GitHub issues/PRs…')
    try:
        # shortcut: inspect the first 10 relevance-ranked hits; increase the budget for repository-wide research.
        response = gh._get('/search/issues', {'q': query, 'per_page': 10}).json()
        report['incomplete_results'] = bool(response.get('incomplete_results'))
        for item in response.get('items', []):
            other = parse_url(item['html_url'])
            overlap = sorted(words & terms(item.get('title', '') + ' ' + (item.get('body') or '')))
            if (other.owner.lower(), other.repo.lower()) != (ref.owner.lower(), ref.repo.lower()) or other.number == ref.number:
                continue
            if len(overlap) >= 2 and other.number not in candidates:
                candidates[other.number] = {'url': other.html_url, 'title': item['title'], 'matched_terms': overlap,
                                             'github_state': item.get('state'), 'reused': False}
    except GitHubError as exc:
        report['warnings'].append(str(exc))
    for candidate in sorted(candidates.values(), key=lambda c: (-len(c['matched_terms']), c['url']))[:3]:
        progress('Related history: ' + candidate['url'])
        if not candidate['reused']:
            try:
                result = import_fn(candidate['url'])
                candidate.update(decision_id=result['decision_id'], status=result['status'])
            except Exception as exc:
                # Related-source failures must not discard the successfully imported starting issue.
                candidate['error'] = str(exc)
        report['sources'].append(candidate)
    return report

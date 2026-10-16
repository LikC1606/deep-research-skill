#!/usr/bin/env python3
"""Conservative retriever: web search plus narrow academic and artifact lanes."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from contextvars import ContextVar
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _load_v1():
    path = Path(__file__).with_name("search_candidate.py")
    spec = importlib.util.spec_from_file_location("drbench_retriever_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reader helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_anysearch():
    path = Path(__file__).with_name("anysearch_provider.py")
    spec = importlib.util.spec_from_file_location("deep_research_anysearch", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load AnySearch adapter: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_workspace():
    path = Path(__file__).with_name("workspace.py")
    spec = importlib.util.spec_from_file_location("deep_research_workspace", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load research workspace: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v1 = _load_v1()
base = v1.base
anysearch = _load_anysearch()
workspace = _load_workspace()


def _load_extension(name):
    spec = importlib.util.spec_from_file_location(f"deep_research_{name}", Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reporting = _load_extension("reporting")
citation_graph = _load_extension("citation_graph")
evaluation = _load_extension("evaluation")
calibration = _load_extension("calibration")
paired_evaluation = _load_extension("paired_evaluation")
claim_review = _load_extension("claim_review")
fixed_corpus = _load_extension("fixed_corpus")
search_feedback = _load_extension("search_feedback")
provider_cache = _load_extension("provider_cache")
ATOM = {"atom": "http://www.w3.org/2005/Atom"}
EXTRA_STOPWORDS = {"arxiv", "http", "https", "paper", "report", "site", "study", "www"}
PRACTITIONER_CONTEXT_RE = re.compile(
    r"\b(?:kaggle|competition|challenge|hackathon|benchmark)\b|竞赛|比赛|赛题|挑战赛",
    re.IGNORECASE,
)
PRACTITIONER_OUTCOME_RE = re.compile(
    r"\b(?:winner|winning|gold|silver|bronze|[1-9]\d*(?:st|nd|rd|th)[ -]?place|high[ -]?scor(?:e|ing))\b|冠军|金牌|银牌|铜牌|高分|获奖",
    re.IGNORECASE,
)
PRACTITIONER_ARTIFACT_RE = re.compile(
    r"\b(?:solution|write[ -]?up|postmortem|repository|repo|code|implementation)\b|方案|复盘|仓库|代码|实现",
    re.IGNORECASE,
)
PLACEMENT_RE = re.compile(r"\b([1-9]\d*)(?:st|nd|rd|th)[ -]?place\b", re.IGNORECASE)
WINNER_RE = re.compile(r"\b(?:winner|winning|(?:1st|first)[ -]?place)\b|冠军", re.IGNORECASE)
ARTIFACT_TYPES = (
    "official", "paper", "repository", "code", "issue", "benchmark", "postmortem", "writeup"
)
SEARCH_PROVIDERS = ("auto", "direct", "anysearch", "hybrid")
GITHUB_ARTIFACTS = {"repository", "code", "writeup", "postmortem"}
SCOPE_STATE_FILE = ".deep-research-scope.json"
SCOPE_GENERIC_TERMS = {
    "benchmark", "challenge", "competition", "kaggle", "official", "prediction"
}
SCOPE_SIGNAL_PATTERNS = {
    "metric": re.compile(r"\b(?:metric|evaluation(?:algorithm)?|scor(?:e|ing))s?\b|评测|评估|评分|指标", re.IGNORECASE),
    "schema": re.compile(r"\b(?:schema|columns?|fields?)\b|字段|数据结构|列名", re.IGNORECASE),
    "train": re.compile(r"\btrain(?:ing)?(?:[ _-]?(?:set|data))?\b|训练集|训练数据", re.IGNORECASE),
    "test": re.compile(r"\btest(?:ing)?(?:[ _-]?(?:set|data))?\b|测试集|测试数据", re.IGNORECASE),
    "target": re.compile(r"\b(?:label|target|response)s?\b|标签|预测目标", re.IGNORECASE),
    "submission": re.compile(r"\bsubmi(?:t|ssion)s?\b|提交", re.IGNORECASE),
    "split": re.compile(r"\b(?:split|fold|holdout)s?\b|切分|划分", re.IGNORECASE),
    "data": re.compile(r"\b(?:dataset|data|csv)s?\b|数据集", re.IGNORECASE),
}
KAGGLE_GET_COMPETITION = (
    "https://www.kaggle.com/api/i/competitions.CompetitionService/GetCompetition"
)
KAGGLE_LIST_COMPETITIONS = (
    "https://www.kaggle.com/api/i/competitions.CompetitionService/ListCompetitions"
)
KAGGLE_LIST_PAGES = (
    "https://www.kaggle.com/api/i/competitions.PageService/ListPages"
)
KAGGLE_TOPICS_ROOT = "https://www.kaggle.com/api/v1/competitions"
KAGGLE_QUALIFIER_RE = re.compile(
    r"\b(?:official|metric|evaluation|schema|rules?|leaderboard|solution|"
    r"writeup|repository|postmortem|ranking|ranked)\b",
    re.IGNORECASE,
)
KAGGLE_TARGET_NOISE = SCOPE_GENERIC_TERMS | {
    "code", "evaluation", "high", "metric", "official", "rank", "ranked",
    "ranking", "repository", "schema", "score", "solution", "team", "writeup",
}
PIVOT_GENERIC_TERMS = SCOPE_GENERIC_TERMS | {
    "column", "columns", "data", "dataset", "evaluation", "field", "fields",
    "label", "metric", "sample", "schema", "score", "scoring", "submission",
    "target", "test", "train",
}
REF_STATE_FILE = ".deep-research-refs.json"
CITATION_STATE_FILE = ".deep-research-citations.json"
URL_TEXT_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
KAGGLE_TOPIC_VALUE_RE = re.compile(
    r"\b(?:\d+(?:st|nd|rd|th)[ -]?place|winner|winning|solution|write[ -]?up|"
    r"postmortem|approach|code|ablation|validation|takeaways?|shake[ -]?up)\b",
    re.IGNORECASE,
)
KAGGLE_OFFICIAL_NEED_RE = re.compile(
    r"\b(?:official|evaluation|metric|data|schema|rules?|submission)\b|"
    r"官方|评测|评估|指标|数据|规则|提交",
    re.IGNORECASE,
)


_ACTIVE_SEARCH = ContextVar("deep_research_search", default=None)


class _SearchExecution:
    def __init__(self, plan: list[dict], artifact: str | None, recovery: dict | None = None):
        self.plan = plan
        self.artifact = artifact
        self.previous = recovery
        self.providers = provider_cache.ProviderCache(
            _provider_config(plan), recovery["provider_execution"] if recovery is not None else None)


def _provider_config(plan: list[dict]) -> dict:
    # Cache replay remains unconditional. Only explicit recovery needs an exact
    # implementation/configuration match; budgets and source policy are not identity.
    digest = hashlib.sha256()
    for name in ("research.py", "search.py", "search_candidate.py", "anysearch_provider.py", "provider_cache.py"):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    config = {
        "implementation_sha256": digest.hexdigest(),
        "academic": _academic_search_enabled(),
        "practitioner": os.environ.get("DRBENCH_PRACTITIONER_SEARCH", "1") != "0",
        "blocked_url_substrings": sorted(set(base._policy_parts())),
        "timeout": base.TIMEOUT, "max_response_bytes": base.MAX_BYTES,
    }
    if any(row["provider"] == "anysearch" for row in plan):
        config["anysearch"] = {
            "endpoint": anysearch.ENDPOINT, "timeout": anysearch._timeout(),
            "client": anysearch.CLIENT_HEADER, "max_response_bytes": anysearch.MAX_RESPONSE_BYTES,
            "auth_sha256": hashlib.sha256(os.environ.get("ANYSEARCH_API_KEY", "").strip().encode()).hexdigest(),
        }
    return config


@contextmanager
def _search_execution(plan: list[dict], artifact: str | None = None, recovery: dict | None = None):
    active = _ACTIVE_SEARCH.get()
    if active is not None:
        yield active
        return
    execution = _SearchExecution(plan, artifact, recovery)
    base._reserve("search")
    token = _ACTIVE_SEARCH.set(execution)
    try:
        yield execution
    finally:
        _ACTIVE_SEARCH.reset(token)


def _require_web_search() -> None:
    if _source_policy() == "local_only":
        raise RuntimeError("web search is disabled by source_policy=local_only; use local files only")


class SearchError(RuntimeError):
    def __init__(self, provider_errors: list[dict]):
        self.provider_errors = provider_errors
        details = "; ".join(f"{row['provider']}: {row['error']}" for row in provider_errors)
        super().__init__(f"search incomplete; no usable results ({details})")


def _call_provider(name: str, operation, outcomes: list[dict], *, request: dict | None = None) -> list[dict]:
    execution = _ACTIVE_SEARCH.get()
    if execution is not None and request is not None:
        cards, outcome = execution.providers.call(name, request, operation)
        outcomes.append(outcome)
        return cards
    try:
        cards = operation()
    except Exception as exc:
        cards = []
        outcome = {"provider": name, "status": "error", "error": f"{type(exc).__name__}: {exc}"[:240]}
    else:
        outcome = {"provider": name, "status": "ok" if cards else "empty"}
    if request is not None:
        outcome["request"] = request
    outcomes.append(outcome)
    return list(cards)


def _outcome_errors(outcomes: list[dict]) -> list[dict]:
    return [{"provider": row["provider"], "error": row["error"]}
            for row in outcomes if row["status"] == "error" and not row.get("reused")]


def _github_candidates(query: str, limit: int, outcomes: list[dict]) -> list[dict]:
    return _call_provider("github", lambda: _github_repositories(query, limit), outcomes,
                          request={"query": _github_query(query), "limit": min(max(limit, 1), 10)})


def _provider_operation(row: dict):
    """Reconstruct only the recorded request, never a newly discovered dependency."""
    name, request = row["provider"], row["request"]
    if name == "anysearch":
        query, limit = request.get("query"), request.get("max_results")
        artifact = "paper" if request.get("domain") == "academic" else None
        if (isinstance(query, str) and query.strip() and type(limit) is int and 1 <= limit <= 10
                and request == anysearch._arguments(query, limit, artifact)):
            return lambda: _search_anysearch(query, limit, artifact)
    elif name in {"duckduckgo", "bing", "360", "arxiv", "openalex", "github"}:
        query, limit = request.get("query"), request.get("limit")
        if (set(request) == {"query", "limit"} and isinstance(query, str) and query.strip()
                and type(limit) is int and 1 <= limit <= 100):
            methods = {"duckduckgo": _duckduckgo, "arxiv": _arxiv,
                       "openalex": _openalex_exact, "github": _github_repositories,
                       "bing": lambda query, limit: base._bing(query, limit, strict=True),
                       "360": lambda query, limit: base._so360(query, limit, strict=True)}
            return lambda: methods[name](query, limit)
    elif name in {"kaggle-metadata", "kaggle-listing", "kaggle-topics"}:
        url, max_bytes = request.get("url"), request.get("max_bytes")
        roots = {"kaggle-metadata": KAGGLE_GET_COMPETITION + "?",
                 "kaggle-listing": KAGGLE_LIST_COMPETITIONS + "?",
                 "kaggle-topics": KAGGLE_TOPICS_ROOT + "/"}
        if (set(request) == {"url", "max_bytes"} and isinstance(url, str)
                and url.startswith(roots[name]) and type(max_bytes) is int
                and 1 <= max_bytes <= 3_000_000):
            return lambda: [_kaggle_json(url, max_bytes)]
    raise ValueError("unsupported cached provider request; use --refresh to create a new search")


def _retry_provider_failures() -> None:
    execution = _ACTIVE_SEARCH.get()
    if execution is not None:
        execution.providers.retry_failures(lambda row: _provider_operation(row)())


def _recovery_has(provider: str, request: dict | None = None) -> bool:
    execution = _ACTIVE_SEARCH.get()
    return bool(execution and execution.providers.recovering and execution.providers.has(provider, request))


def _complete_search(result: dict, query: str, limit: int, target: str | None) -> dict:
    execution = _ACTIVE_SEARCH.get()
    records = execution.providers
    if records.recovering:
        diagnostics = {}
        # A recovered specialized/aggregate lane can skip an old successful
        # branch. Keep its previous leads in the candidate pool in either case.
        result["results"] = _rank_discovery(
            result.get("results", []) + execution.previous.get("results", []),
            query, limit, target, diagnostics=diagnostics)
        result.setdefault("discovery_diagnostics", {})["recovery_ranking"] = diagnostics
        result["provider_results"] = records.outcomes() + list(records.deferred.values())
        result.pop("provider_errors", None)
        errors = _outcome_errors(records.outcomes())
        if errors:
            result["provider_errors"] = errors
        result["providers_used"] = list(dict.fromkeys(
            execution.previous.get("providers_used", []) + result.get("providers_used", [])))
        result.setdefault("recall_checks", {})["remaining_gaps"] = _recall_gaps(
            result["results"], query, limit, target)
        result["recovery"] = {
            "retried_requests": len(records.retried),
            "reused_requests": len(records.records) - len(records.retried),
            "deferred_new_requests": len(records.deferred),
            "note": "Only previously failed requests were retried; use --refresh for new discovery or changed settings.",
        }
    _search_status(result, allow_incomplete=True)
    base._record_event("search", result)
    result["provider_execution"] = records.snapshot()
    return _search_status(result)


def _search_status(result: dict, *, allow_incomplete: bool = False) -> dict:
    errors = result.get("provider_errors", [])
    if not result.get("results") and errors and not allow_incomplete:
        raise SearchError(errors)
    incomplete = errors or result.get("recovery", {}).get("deferred_new_requests")
    result["status"] = "partial" if incomplete else "ok" if result.get("results") else "empty"
    return result


def _query_words(query: str) -> list[str]:
    return [
        word for word in v1.WORD_RE.findall(query.casefold())
        if len(word) > 1 and word not in v1.STOPWORDS and word not in EXTRA_STOPWORDS
    ]


def _arxiv(query: str, limit: int) -> list[dict]:
    words = _query_words(query)[:5]
    if len(words) < 2:
        return []
    search_query = " AND ".join(f"all:{word}" for word in words)
    params = {
        "search_query": search_query,
        "start": "0",
        "max_results": str(limit),
        "sortBy": "relevance",
    }
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    body, _ = base._get(url)
    root = ET.fromstring(body)
    if root.tag != f"{{{ATOM['atom']}}}feed":
        raise ValueError("unexpected arXiv search response")
    cards = []
    for entry in root.findall("atom:entry", ATOM):
        target = (entry.findtext("atom:id", default="", namespaces=ATOM) or "").strip()
        target = re.sub(r"^http://", "https://", target)
        title = base._clean(
            entry.findtext("atom:title", default="", namespaces=ATOM) or "", 240
        )
        summary = base._clean(
            entry.findtext("atom:summary", default="", namespaces=ATOM) or "", 900
        )
        published = (
            entry.findtext("atom:published", default="", namespaces=ATOM) or ""
        )[:10]
        title_hits = len(set(words) & v1._tokens(title))
        if target and title and title_hits >= 2 and base._url_ok(target):
            cards.append(
                {
                    "title": title,
                    "url": target,
                    "snippet": base._clean(f"{published}. {summary}", 900),
                    "engine": "arxiv",
                    "source_type": "primary_or_academic",
                }
            )
    return cards


def _openalex_exact(query: str, limit: int) -> list[dict]:
    query_tokens = v1._tokens(query)
    cards = []
    for card in v1._openalex(query, limit, strict=True):
        if len(query_tokens & v1._tokens(card.get("title", ""))) >= 2:
            cards.append(dict(card, source_type="primary_or_academic"))
    return cards


def _academic_search_enabled() -> bool:
    return os.environ.get("DRBENCH_ACADEMIC_SEARCH", "1") != "0"


def _practitioner_search_enabled(query: str) -> bool:
    if os.environ.get("DRBENCH_PRACTITIONER_SEARCH", "1") == "0":
        return False
    has_artifact = bool(PRACTITIONER_ARTIFACT_RE.search(query))
    return has_artifact and bool(
        PRACTITIONER_CONTEXT_RE.search(query) or PRACTITIONER_OUTCOME_RE.search(query)
    )


def _github_query(query: str) -> str:
    query = _explicit_github_query(query)
    competition = bool(re.search(r"\b(?:kaggle|competition|hackathon)\b|竞赛|比赛|赛题", query, re.IGNORECASE)
                       or PRACTITIONER_OUTCOME_RE.search(query))
    if competition:
        anchor = PRACTITIONER_CONTEXT_RE.sub(" ", query)
        anchor = PRACTITIONER_OUTCOME_RE.sub(" ", anchor)
        anchor = PRACTITIONER_ARTIFACT_RE.sub(" ", anchor)
        anchor = re.sub(r"\bmedals?\b", " ", anchor, flags=re.IGNORECASE)
        return f"{_clean_query(anchor)} solution".strip()
    # Repository search ANDs its words. Adding 'solution' silently excludes
    # ordinary papers, datasets, tools and their official implementations.
    return _clean_query(re.sub(r"\b(?:repository|repo|code|implementation)\b|仓库|代码|实现", " ", query, flags=re.IGNORECASE))


def _github_repositories(query: str, limit: int) -> list[dict]:
    github_query = _github_query(query)
    params = {
        "q": github_query,
        "per_page": str(min(max(limit, 1), 10)),
    }
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(params)
    body, _ = base._get(url)
    rows = json.loads(body).get("items")
    if not isinstance(rows, list):
        raise ValueError("unexpected GitHub repository search response")

    query_tokens = set(_query_words(github_query)) - {"solution"}
    ranked = []
    for row in rows:
        target = row.get("html_url") or ""
        name = row.get("full_name") or row.get("name") or ""
        description = base._clean(row.get("description") or "", 500)
        homepage = row.get("homepage") or ""
        topics = [str(topic) for topic in (row.get("topics") or [])[:8]]
        searchable = " ".join((name, description, homepage, " ".join(topics)))
        overlap = len(query_tokens & set(_query_words(searchable)))
        if not target or not name or overlap < min(2, len(query_tokens)):
            continue
        outcome_proof = bool(PRACTITIONER_OUTCOME_RE.search(searchable))
        winner_claim = bool(WINNER_RE.search(searchable))
        placement_match = PLACEMENT_RE.search(searchable)
        placement = int(placement_match.group(1)) if placement_match else None
        stars = int(row.get("stargazers_count") or 0)
        details = [description, f"GitHub stars: {stars}"]
        if row.get("language"):
            details.append(f"language: {row['language']}")
        if topics:
            details.append("topics: " + ", ".join(topics))
        if homepage:
            details.append("linked page: " + homepage)
        ranked.append(
            (
                (
                    overlap,
                    int(winner_claim),
                    int(placement is not None),
                    -(placement or 10_000),
                    int(outcome_proof),
                    stars,
                ),
                {
                    "title": base._clean(f"{name}: {description}", 240),
                    "url": target,
                    "snippet": base._clean(" | ".join(details), 900),
                    "engine": "github-repositories",
                    "source_type": "practitioner_artifact",
                },
            )
        )
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [card for _, card in ranked[:limit]]


def _duckduckgo(query: str, limit: int) -> list[dict]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    body, _ = base._get(url)
    soup = v1.BeautifulSoup(body, "html.parser")
    if soup.select_one("#challenge-form, .anomaly-modal") is not None:
        raise ValueError("DuckDuckGo returned a challenge page")
    if (soup.select_one(".result__a") is None
            and soup.select_one(".no-results, .no-results__message") is None):
        raise ValueError("unrecognized DuckDuckGo search response; cannot confirm empty results")
    cards = []
    for row in soup.select(".result"):
        anchor = row.select_one("a.result__a")
        if anchor is None:
            continue
        target = str(anchor.get("href") or "").strip()
        parsed = urllib.parse.urlparse(target)
        if parsed.netloc.casefold().endswith("duckduckgo.com"):
            target = urllib.parse.parse_qs(parsed.query).get("uddg", [""])[0]
        title = base._clean(anchor.get_text(" "), 240)
        snippet_node = row.select_one(".result__snippet")
        snippet = base._clean(
            snippet_node.get_text(" ") if snippet_node else "", 700
        )
        if target and title and base._url_ok(target):
            cards.append(
                {
                    "title": title,
                    "url": target,
                    "snippet": snippet,
                    "engine": "duckduckgo-html",
                }
            )
        if len(cards) >= limit:
            break
    return cards


def _search_direct(query: str, limit: int = 8, target: str | None = None) -> dict:
    outcomes = []
    request = {"query": query, "limit": max(8, limit)}
    web_cards = _call_provider("duckduckgo", lambda: _duckduckgo(query, max(8, limit)), outcomes, request=request)
    if _recall_gaps(web_cards, query, limit, target) or _recovery_has("bing", request):
        web_cards.extend(_call_provider("bing", lambda: base._bing(query, max(8, limit), strict=True), outcomes,
                                        request=request))
    if not v1._is_english_query(query):
        web_cards.extend(_call_provider("360", lambda: base._so360(query, max(8, limit), strict=True), outcomes,
                                        request=request))
    practitioner = []
    topic_cards = []
    if _practitioner_search_enabled(query):
        topic_cards = _kaggle_topic_cards(query, max(4, limit // 2), outcomes=outcomes)
        practitioner.extend(topic_cards)
        practitioner.extend(_github_candidates(query, max(4, limit // 2), outcomes))
    scholarly = []
    if _academic_search_enabled():
        academic_limit = max(2, limit // 3)
        academic_request = {"query": query, "limit": academic_limit}
        if len(_query_words(query)) >= 2:
            scholarly.extend(_call_provider("arxiv", lambda: _arxiv(query, academic_limit), outcomes,
                                             request=academic_request))
        scholarly.extend(_call_provider("openalex", lambda: _openalex_exact(query, academic_limit), outcomes,
                                        request=academic_request))

    cards = practitioner + scholarly + web_cards
    diagnostics = {}
    ranked = _rank_discovery(cards, query, limit, target, diagnostics=diagnostics)
    if topic_cards:
        ranked = _merge_cards(topic_cards, ranked, limit)
    execution = _ACTIVE_SEARCH.get()
    if (not (execution and execution.artifact) and "kaggle" in query.casefold()
            and KAGGLE_OFFICIAL_NEED_RE.search(query)):
        metadata = _kaggle_official_card(query, query, outcomes=outcomes)
        page_card = _kaggle_pages_card(metadata) if metadata else None
        official_cards = [
            card for card in (page_card, metadata) if card is not None
        ]
        if official_cards:
            canonical = str(metadata.get("canonical_target") or query)
            _open_scope(canonical)
            scoped = {"results": official_cards}
            _remember_scope_urls(canonical, scoped)
            ranked = _merge_cards(official_cards, ranked, limit)
            base._record_event(
                "official_candidates",
                {"target": canonical, "need": query, "results": official_cards},
            )
    return {
        "query": query, "results": ranked, "provider_results": outcomes,
        "discovery_diagnostics": {"direct_ranking": diagnostics},
        "provider_errors": _outcome_errors(outcomes),
    }


def _source_policy() -> str:
    value = os.environ.get("DEEP_RESEARCH_SOURCE_POLICY") or os.environ.get(
        "DRBENCH_SOURCE_POLICY", "web_allowed"
    )
    value = value.strip().casefold()
    if value not in {"local_only", "web_allowed", "mixed"}:
        raise ValueError(f"unsupported source policy: {value}")
    return value


def _provider_mode(requested: str | None) -> str:
    value = requested or os.environ.get("DEEP_RESEARCH_SEARCH_PROVIDER") or os.environ.get(
        "DRBENCH_SEARCH_PROVIDER", "auto"
    )
    value = value.strip().casefold()
    if value not in SEARCH_PROVIDERS:
        raise ValueError(
            f"unsupported search provider: {value}; choose {', '.join(SEARCH_PROVIDERS)}"
        )
    return value


def _search_anysearch(query: str, limit: int, artifact: str | None) -> list[dict]:
    cards = []
    for card in anysearch.search(query, limit, artifact):
        url = str(card.get("url") or "").strip()
        title = base._clean(str(card.get("title") or ""), 240)
        if not url or not title or not base._url_ok(url):
            continue
        cards.append(
            {
                "title": title,
                "url": url,
                "snippet": base._clean(str(card.get("snippet") or ""), 900),
                "engine": "anysearch",
                "source_type": v1._source_type(url, "anysearch"),
            }
        )
    return cards


def _discovery_key(card: dict) -> str:
    url = str(card.get("url") or "").split("#", 1)[0]
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").casefold().removeprefix("www.")
    identifier = r"((?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v[1-9]\d*)?)"
    arxiv = None
    if host in {"arxiv.org", "export.arxiv.org"}:
        arxiv = re.fullmatch(r"/(?:abs|pdf|html)/" + identifier + r"(?:\.pdf)?/?", parsed.path, re.IGNORECASE)
    elif host == "doi.org":
        arxiv = re.fullmatch(r"/10\.48550/arxiv\." + identifier + r"/?", parsed.path, re.IGNORECASE)
    if arxiv:
        return f"arxiv:{arxiv[1].casefold()}"
    return f"url:{url}"


def _same_title_groups(cards: list[dict]) -> list[set[str]]:
    groups = {}
    for card in cards:
        title = re.sub(r"[\W_]+", " ", str(card.get("title") or "").casefold()).strip()
        if len(title) >= 32:
            groups.setdefault(title, set()).add(_discovery_key(card))
    return [keys for keys in groups.values() if len(keys) > 1]


def _rank_discovery(
    cards: list[dict], query: str, limit: int, target: str | None = None,
    *, diagnostics: dict | None = None,
) -> list[dict]:
    # Titles and snippets guide ranking; only document identities remove leads.
    ranked = v1._rank(cards, query, len(cards))
    if target:
        ranked.sort(key=lambda card: not _target_matched(card, target))
    selected, seen = [], set()
    for card in ranked:
        key = _discovery_key(card)
        if key in seen:
            continue
        seen.add(key)
        selected.append(card)
    page = selected[:limit]
    if diagnostics is not None:
        unique_urls = {str(card.get("url") or "").split("#", 1)[0] for card in cards}
        query_tokens = v1._tokens(query)
        low_overlap = sum(
            len(query_tokens & v1._tokens(" ".join(str(card.get(key) or "") for key in ("title", "snippet"))))
            < min(3, len(query_tokens)) and not (target and _target_matched(card, target))
            for card in page
        )
        diagnostics.update({
            "input_candidates": len(cards),
            "url_duplicates_removed": len(cards) - len(unique_urls),
            "url_policy_rejected": len(unique_urls) - len(ranked),
            "document_aliases_removed": len(ranked) - len(selected),
            "page_limit_omitted": len(selected) - len(page),
            "returned_candidates": len(page),
            "low_overlap_candidates_retained": low_overlap,
            "suspected_same_title_groups": len(_same_title_groups(ranked)),
            "relevance_policy": "ranking_only",
        })
    return page


def _recall_gaps(cards: list[dict], query: str, limit: int, target: str | None = None) -> list[str]:
    keys = [_discovery_key(card) for card in cards]
    gaps = []
    if len(set(keys)) < min(2, limit):
        gaps.append("too_few_distinct_documents")
    if len(keys) - len(set(keys)) >= 2:
        gaps.append("repeated_document_results")
    if _same_title_groups(cards):
        gaps.append("similar_titles_require_read")
    if target and not any(_target_matched(card, target) for card in cards):
        gaps.append("no_target_match")
    terms = v1._tokens(re.sub(r"\bsite:\S+", " ", query))
    if target:
        terms -= v1._tokens(target)
    terms -= {"official", "repository", "repo", "code", "implementation", "paper", "writeup", "postmortem"}
    covered = set().union(*(v1._tokens(" ".join(str(card.get(key) or "") for key in ("title", "snippet")))
                            for card in cards))
    if terms - covered:
        gaps.append("need_terms_missing_from_results")
    normalized = lambda value: " ".join(re.findall(r"\w+", value.casefold()))
    if any(not any(normalized(phrase) in normalized(" ".join(str(card.get(key) or "") for key in ("title", "snippet")))
                       for card in cards) for phrase in re.findall(r'"([^"]+)"', query)):
        gaps.append("quoted_phrase_missing_from_results")
    return gaps


def _search_plan(query: str, limit: int, mode: str, artifact: str | None = None,
                 target: str | None = None) -> list[dict]:
    plan = []
    effective_query = _route_query(query, artifact) if artifact else query
    if mode != "anysearch" and artifact in GITHUB_ARTIFACTS:
        plan.append({"provider": "github", "query": _github_query(query),
                     "limit": min(5, limit), "when": "first"})
        fallback = _scope_target(target or "")
        if fallback and _github_query(fallback) != _github_query(query):
            plan.append({"provider": "github", "query": _github_query(fallback),
                         "limit": min(5, limit), "when": "target_candidates_insufficient"})
        if "kaggle" in f"{target} {query}".casefold():
            plan.append({"provider": "kaggle-topics", "when": "always" if mode == "hybrid"
                         else "target_candidates_insufficient"})
    elif mode != "anysearch" and artifact == "official" and "kaggle" in f"{target} {query}".casefold():
        plan.append({"provider": "kaggle-metadata", "when": "first"})
    if mode in {"auto", "anysearch", "hybrid"}:
        plan.append({"provider": "anysearch", "query": effective_query,
                     "limit": min(max(limit, 1), 10),
                     "when": "specialized_recall_gap" if mode == "auto" and plan else "always"})
    if mode != "anysearch":
        plan.append({"provider": "direct", "query": effective_query, "limit": limit,
                     "when": "always" if mode == "hybrid" or not plan else "recall_gap"})
    return plan


def _scope_identity_matches(state: dict, value: str) -> bool:
    if state.get("status") != "closed":
        return False
    canonical = str(state.get("canonical_target") or state.get("target") or "")
    known_slug = str(state.get("competition_name") or "").casefold()
    known_years = set(re.findall(r"\b(?:19|20)\d{2}\b", f"{canonical} {known_slug}"))
    if set(re.findall(r"\b(?:19|20)\d{2}\b", value)) - known_years:
        return False
    explicit_slugs = []
    for url in URL_TEXT_RE.findall(value):
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname not in {"kaggle.com", "www.kaggle.com"}:
            continue
        slug = _kaggle_competition_slug(url)
        slug = slug or urllib.parse.parse_qs(parsed.query).get("competitionName", [""])[0]
        if slug:
            explicit_slugs.append(slug.casefold())
    if explicit_slugs:
        return bool(known_slug) and set(explicit_slugs) == {known_slug}
    terms = set(_query_words(value)) - SCOPE_GENERIC_TERMS
    for identity in (canonical, str(state.get("target") or "")):
        if set(re.findall(r"\b(?:19|20)\d{2}\b", identity)) - known_years:
            continue
        identity_terms = set(_query_words(identity)) - SCOPE_GENERIC_TERMS
        if identity_terms and identity_terms <= terms:
            return True
    return False


def _scope_target(target: str) -> str:
    state = _load_scope_state()
    if _scope_identity_matches(state, target):
        return str(state.get("canonical_target") or target)
    return target


def _search_base(
    query: str,
    limit: int = 8,
    provider: str | None = None,
    artifact: str | None = None,
    target: str | None = None,
    *, _recovery: dict | None = None,
) -> dict:
    _require_web_search()
    mode = _provider_mode(provider)
    plan = _search_plan(query, limit, mode, artifact, target)
    with _search_execution(plan, artifact, _recovery):
        result = _search_base_execute(query, limit, mode, artifact, target)
        return _complete_search(result, query, limit, target) if artifact is None else result


def _search_base_execute(query: str, limit: int, mode: str, artifact: str | None, target: str | None) -> dict:
    _retry_provider_failures()
    execution = _ACTIVE_SEARCH.get()
    provider_errors = []
    provider_results = []
    providers_used = []
    anysearch_cards = []
    direct_cards = []
    diagnostics = {}

    if mode in {"auto", "anysearch", "hybrid"}:
        anysearch_cards = _call_provider(
            "anysearch", lambda: _search_anysearch(query, limit, artifact), provider_results,
            request=anysearch._arguments(query.strip(), limit, artifact))
        if provider_results[-1]["status"] in {"ok", "empty"}:
            providers_used.append("anysearch")
        provider_errors.extend(_outcome_errors(provider_results))

    recall_gaps = _recall_gaps(anysearch_cards, query, limit, target)
    need_direct = (mode in {"direct", "hybrid"} or (mode == "auto" and bool(recall_gaps))
                   or _recovery_has("duckduckgo", {"query": query, "limit": max(8, limit)}))
    if need_direct:
        direct_result = _search_direct(query, limit, target=target)
        direct_cards = list(direct_result.get("results", []))
        provider_errors.extend(direct_result.get("provider_errors", []))
        provider_results.extend(direct_result.get("provider_results", []))
        diagnostics.update(direct_result.get("discovery_diagnostics", {}))
        providers_used.append("direct")

    diagnostics["combined_ranking"] = {}
    combined = _rank_discovery(anysearch_cards + direct_cards, query, limit, target,
                               diagnostics=diagnostics["combined_ranking"])
    result = {
        "query": query,
        "results": combined,
        "provider_mode": mode,
        "providers_used": providers_used,
        "providers_planned": execution.plan,
        "provider_results": provider_results,
        "discovery_diagnostics": diagnostics,
        "status": "partial" if provider_errors else "ok" if combined else "empty",
        "recall_checks": {"initial_gaps": recall_gaps if mode != "direct" else [],
                          "remaining_gaps": _recall_gaps(combined, query, limit, target),
                          "note": "Query overlap and duplicate checks guide discovery only; read sources to assess evidence."},
    }
    if provider_errors:
        result["provider_errors"] = provider_errors
    return _search_status(result, allow_incomplete=True)


def _clean_query(value: str) -> str:
    value = re.sub(r"\s+-\s+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _kaggle_json(url: str, max_bytes: int = 3_000_000) -> dict:
    if not base._url_ok(url):
        raise ValueError(f"blocked or unsupported URL: {url}")
    with base.metrics.request(base.requests.get,
        url,
        url_ok=base._url_ok,
        headers={"User-Agent": base.UA, "Accept": "application/json"},
        timeout=base.TIMEOUT,
    ) as response:
        response.raise_for_status()
        if len(response.content) > max_bytes:
            raise ValueError("Kaggle response exceeds size limit")
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("unexpected Kaggle JSON response; expected an object")
        return value


def _slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.casefold())).strip("-")


def _kaggle_slug_candidates(target: str, query: str) -> list[str]:
    candidates = []
    for value in (target, query):
        parsed = urllib.parse.urlparse(value)
        path_match = re.search(r"/(?:competitions|c)/([^/?#]+)", parsed.path)
        if path_match:
            candidates.append(path_match.group(1))
        api_name = urllib.parse.parse_qs(parsed.query).get("competitionName", [])
        candidates.extend(api_name[:1])

        phrase = re.sub(r"https?://\S+", " ", value)
        kaggle_match = re.search(r"\bkaggle\b", phrase, re.IGNORECASE)
        if kaggle_match:
            phrase = phrase[kaggle_match.end():]
        qualifier = KAGGLE_QUALIFIER_RE.search(phrase)
        if qualifier:
            phrase = phrase[:qualifier.start()]
        phrase = re.sub(r"\bcompetition\b\s*$", "", phrase, flags=re.IGNORECASE)
        slug = _slugify(phrase)
        if len(slug.split("-")) >= 2:
            candidates.append(slug)
    return list(dict.fromkeys(value for value in candidates if value))[:3]


def _kaggle_card(row: dict) -> dict | None:
    slug = str(row.get("competitionName") or "").strip()
    title = str(row.get("title") or "").strip()
    if not slug or not title:
        return None
    metric = row.get("evaluationAlgorithm") or {}
    details = [str(row.get("briefDescription") or "")]
    if isinstance(metric, dict) and metric.get("name"):
        details.append(f"official metric: {metric['name']}")
    if row.get("leaderboardPercentage") is not None:
        details.append(f"public leaderboard percentage: {row['leaderboardPercentage']}")
    if row.get("requiredSubmissionFilename"):
        details.append(f"submission file: {row['requiredSubmissionFilename']}")
    url = KAGGLE_GET_COMPETITION + "?" + urllib.parse.urlencode(
        {"competitionName": slug}
    )
    return {
        "title": f"{title} | Kaggle official competition metadata",
        "url": url,
        "snippet": base._clean(" | ".join(value for value in details if value), 900),
        "engine": "kaggle-official-api",
        "source_type": "official",
        "competition_name": slug,
        "competition_id": row.get("id") or row.get("competitionId"),
        "canonical_target": f"Kaggle {title}",
    }


def _kaggle_pages_card(metadata_card: dict) -> dict | None:
    competition_id = metadata_card.get("competition_id")
    slug = str(metadata_card.get("competition_name") or "").strip()
    canonical = str(metadata_card.get("canonical_target") or "").strip()
    if not competition_id or not slug or not canonical:
        return None
    url = KAGGLE_LIST_PAGES + "?" + urllib.parse.urlencode(
        {"competitionId": competition_id, "competitionName": slug}
    )
    return {
        "title": f"{canonical.removeprefix('Kaggle ')} | Kaggle official Evaluation and Data pages",
        "url": url,
        "snippet": "Official competition Overview, Evaluation, and Data page bodies.",
        "engine": "kaggle-official-pages",
        "source_type": "official",
        "competition_name": slug,
        "competition_id": competition_id,
        "canonical_target": canonical,
    }


def _kaggle_official_card(target: str, query: str, *, outcomes: list | None = None) -> dict | None:
    if "kaggle" not in f"{target} {query}".casefold():
        return None
    outcomes = outcomes if outcomes is not None else []
    for slug in _kaggle_slug_candidates(target, query):
        url = KAGGLE_GET_COMPETITION + "?" + urllib.parse.urlencode(
            {"competitionName": slug}
        )
        rows = _call_provider("kaggle-metadata", lambda: [_kaggle_json(url, 100_000)], outcomes,
                              request={"url": url, "max_bytes": 100_000})
        if not rows:
            continue
        row = rows[0]
        card = _kaggle_card(row)
        if card and _same_target(target, f"{card['title']} {slug}"):
            return card

    listing_url = KAGGLE_LIST_COMPETITIONS + "?pageSize=1000"
    listings = _call_provider("kaggle-listing", lambda: [_kaggle_json(listing_url)], outcomes,
                              request={"url": listing_url, "max_bytes": 3_000_000})
    if not listings:
        return None
    listing = listings[0]
    target_terms = set(_query_words(target)) - KAGGLE_TARGET_NOISE
    if len(target_terms) < 2:
        return None
    ranked = []
    for row in listing.get("competitions", []):
        if not isinstance(row, dict):
            continue
        searchable = " ".join(
            str(row.get(key) or "")
            for key in ("competitionName", "title", "briefDescription")
        )
        row_terms = set(_query_words(searchable))
        overlap = len(target_terms & row_terms)
        if overlap < min(3, len(target_terms)):
            continue
        ranked.append(((overlap / max(1, len(target_terms)), overlap), row))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return _kaggle_card(ranked[0][1])


def _kaggle_topic_cards(value: str, limit: int, *, outcomes: list | None = None) -> list[dict]:
    if "kaggle" not in value.casefold() or not (
        PRACTITIONER_ARTIFACT_RE.search(value) or PRACTITIONER_OUTCOME_RE.search(value)
    ):
        return []
    outcomes = outcomes if outcomes is not None else []
    state = _load_scope_state()
    if state.get("competition_name") and _scope_identity_matches(state, value):
        slug = str(state["competition_name"]).strip()
        canonical = str(state.get("canonical_target") or state["target"]).removeprefix("Kaggle ")
    else:
        metadata = _kaggle_official_card(value, value, outcomes=outcomes)
        if not metadata:
            return []
        slug = str(metadata.get("competition_name") or "").strip()
        canonical = str(metadata.get("canonical_target") or "").removeprefix("Kaggle ")
    if not slug or not canonical:
        return []
    ranked = []
    seen_topic_ids = set()
    for page in (1, 2):
        url = (
            f"{KAGGLE_TOPICS_ROOT}/{urllib.parse.quote(slug, safe='')}/topics?"
            + urllib.parse.urlencode({"page": page})
        )
        pages = _call_provider("kaggle-topics", lambda: [_kaggle_json(url, 500_000)], outcomes,
                               request={"url": url, "max_bytes": 500_000})
        if not pages:
            break
        payload = pages[0]
        for row in payload.get("topics", []):
            if not isinstance(row, dict):
                continue
            topic_id = row.get("id")
            title = str(row.get("title") or "").strip()
            if (
                not topic_id
                or topic_id in seen_topic_ids
                or not title
                or not KAGGLE_TOPIC_VALUE_RE.search(title)
            ):
                continue
            seen_topic_ids.add(topic_id)
            placement = PLACEMENT_RE.search(title)
            placement_value = int(placement.group(1)) if placement else 10_000
            votes = int(row.get("votes") or 0)
            comments = int(row.get("commentCount") or 0)
            topic_url = (
                f"https://www.kaggle.com/competitions/{urllib.parse.quote(slug, safe='')}/"
                f"discussion/{int(topic_id)}"
            )
            ranked.append(
                (
                    (
                        int(bool(placement)),
                        -placement_value,
                        int(bool(re.search(r"solution|write[ -]?up|winning|winner", title, re.IGNORECASE))),
                        votes,
                        comments,
                    ),
                    {
                        "title": title,
                        "url": topic_url,
                        "snippet": base._clean(
                            f"{canonical} | Kaggle competition topic | "
                            f"votes: {votes} | comments: {comments}",
                            900,
                        ),
                        "engine": "kaggle-competition-topics",
                        "source_type": "practitioner_artifact",
                    },
                )
            )
        if not payload.get("topics"):
            break
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [card for _, card in ranked[:limit]]


def _scope_state_path() -> Path:
    root = os.environ.get("DRBENCH_RUN_DIR") or os.environ.get(
        "DEEP_RESEARCH_RUN_DIR"
    )
    return Path(root or os.getcwd()) / SCOPE_STATE_FILE


def _load_scope_state() -> dict:
    try:
        value = json.loads(_scope_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_scope_state(state: dict) -> None:
    path = _scope_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _same_target(first: str, second: str) -> bool:
    first_terms = set(_query_words(first)) - SCOPE_GENERIC_TERMS
    second_terms = set(_query_words(second)) - SCOPE_GENERIC_TERMS
    return bool(first_terms and second_terms) and len(first_terms & second_terms) >= min(
        2, len(first_terms), len(second_terms)
    )


def _uses_scope_gate(query: str, target: str, artifact: str) -> bool:
    return artifact == "official" and bool(
        PRACTITIONER_CONTEXT_RE.search(f"{target} {query}")
    )


def _open_scope(target: str, *, record_search: bool = True) -> dict:
    state = _load_scope_state()
    if not _same_target(str(state.get("target") or ""), target):
        state = {"status": "open", "target": target, "searches": 0, "urls": []}
    elif state.get("status") == "closed":
        return state
    searches = int(state.get("searches") or 0)
    if searches >= 2:
        raise RuntimeError(
            "scope gate still open after two searches; Read the exact official "
            "source or stop this Target as Blocked"
        )
    if not record_search:
        return state
    state["status"] = "open"
    state["searches"] = searches + 1
    _save_scope_state(state)
    return state


def _normalised_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _query_uses_pivot(query: str, pivots: list[str]) -> str | None:
    normalised_query = f" {_normalised_phrase(query)} "
    for pivot in pivots:
        marker = _normalised_phrase(str(pivot))
        if marker and f" {marker} " in normalised_query:
            return str(pivot)
    return None


def _guard_scope_search(query: str, target: str, artifact: str, *, consume_pivot: bool = True) -> None:
    state = _load_scope_state()
    if (
        state.get("status") == "open"
        and artifact != "official"
        and _same_target(str(state.get("target") or ""), target)
    ):
        base._record_event(
            "scope_block",
            {"target": target, "artifact": artifact, "reason": "scope Read required"},
        )
        raise RuntimeError(
            "scope gate is open for this Target; Read the exact official source, "
            "or a scope-search candidate with verified body evidence, before "
            "requesting solution artifacts"
        )
    if (
        state.get("status") == "closed"
        and state.get("pivot_required")
        and artifact != "official"
        and _same_target(str(state.get("target") or ""), target)
    ):
        pivots = [str(value) for value in state.get("pivots") or []]
        used = _query_uses_pivot(query, pivots)
        if not used:
            base._record_event(
                "pivot_block",
                {
                    "target": target,
                    "query": query,
                    "pivots": pivots,
                    "reason": "first solution query must consume an official body Pivot",
                },
            )
            choices = ", ".join(pivots[:6]) or "an exact metric/schema phrase"
            raise RuntimeError(
                "first solution query must copy one scope_state.pivots value: "
                + choices
            )
        if not consume_pivot:
            return
        state["pivot_required"] = False
        state["pivot_used"] = used
        _save_scope_state(state)
        base._record_event(
            "pivot_state",
            {"status": "consumed", "target": target, "pivot": used, "query": query},
        )


def _remember_scope_urls(target: str, result: dict) -> None:
    state = _load_scope_state()
    if state.get("status") != "open" or not _same_target(
        str(state.get("target") or ""), target
    ):
        return
    urls = list(state.get("urls") or [])
    urls.extend(str(row.get("url") or "") for row in result.get("results", []))
    state["urls"] = list(dict.fromkeys(url for url in urls if url))[:24]
    for row in result.get("results", []):
        if row.get("engine") in {"kaggle-official-api", "kaggle-official-pages"}:
            state["canonical_target"] = row.get("canonical_target")
            state["competition_name"] = row.get("competition_name")
            state["competition_id"] = row.get("competition_id")
            break
    _save_scope_state(state)


def _scope_body_signals(result: dict) -> list[str]:
    body = " ".join(str(window) for window in result.get("windows", []))
    if not body.strip():
        return []
    return [
        name for name, pattern in SCOPE_SIGNAL_PATTERNS.items()
        if pattern.search(body)
    ]


def _scope_pivots(result: dict) -> list[str]:
    windows = [str(value) for value in result.get("windows", [])]
    evaluation = " ".join(
        value for value in windows if "page: evaluation" in value.casefold()
    )
    data_body = " ".join(
        value for value in windows if "page: data-description" in value.casefold()
    )
    body = " ".join(windows)
    candidates = []

    metric_patterns = (
        r"(?:evaluation|scoring)\s+metric(?:\s+for[^,.\n]{0,60})?\s+(?:is|:)\s+"
        r"([a-z0-9][a-z0-9 _+./()\-]{1,60}?)(?=,|\.|\n)",
        r"(?:evaluated|scored)\s+(?:with|using|by)\s+"
        r"([a-z0-9][a-z0-9 _+./()\-]{1,60}?)(?=,|\.|\n)",
    )
    for pattern in metric_patterns:
        candidates.extend(
            match.group(1).strip(" `*_-")
            for match in re.finditer(pattern, evaluation or body, re.IGNORECASE)
        )

    identifier_pattern = re.compile(
        r"`([a-z][a-z0-9_.-]{2,48})`|\b([a-z][a-z0-9]+(?:_[a-z0-9]+)+)\b",
        re.IGNORECASE,
    )
    for source in (data_body, evaluation):
        candidates.extend(
            next(value for value in match.groups() if value)
            for match in identifier_pattern.finditer(source)
        )

    unit_pattern = re.compile(
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
        r"[ -]?(?:second|minute|hour|day)s?\s+"
        r"(?:window|clip|recording|segment|horizon)s?\b",
        re.IGNORECASE,
    )
    candidates.extend(match.group(0) for match in unit_pattern.finditer(body))

    pivots = []
    for candidate in candidates:
        value = re.sub(r"\s+", " ", candidate).strip()
        words = set(_query_words(value))
        if not value or not words or words <= PIVOT_GENERIC_TERMS:
            continue
        if value.casefold() not in {item.casefold() for item in pivots}:
            pivots.append(value)
        if len(pivots) >= 8:
            break
    return pivots


def _scope_source_matches(state: dict, result: dict) -> str | None:
    url = str(result.get("url") or "")
    resolved_url = str(result.get("resolved_url") or url)
    searchable = " ".join(
        [url, resolved_url, str(result.get("title") or "")]
        + [str(window) for window in result.get("windows", [])]
    )
    signals = _scope_body_signals(result)
    if len(signals) < 2:
        return None
    parsed = urllib.parse.urlparse(resolved_url)
    target = str(state.get("target") or "").casefold()
    if "kaggle" in target:
        official_path = "/competitions/" in parsed.path or "/c/" in parsed.path
        official_api = (
            parsed.path.endswith("competitions.CompetitionService/GetCompetition")
            and bool(urllib.parse.parse_qs(parsed.query).get("competitionName"))
        )
        official_pages = parsed.path.endswith("competitions.PageService/ListPages")
        exact_page_identity = False
        if official_pages:
            request_url = urllib.parse.urlparse(url)
            requested_id = (urllib.parse.parse_qs(request_url.query).get("competitionId") or [""])[0]
            expected_id = str(state.get("competition_id") or "")
            body_ids = {
                str(value)
                for value in re.findall(
                    r'["\u0027]?competitionId["\u0027]?\s*:\s*["\u0027]?(\d+)',
                    " ".join(str(window) for window in result.get("windows", [])),
                )
            }
            result_id = str(result.get("competition_id") or "")
            if not (
                expected_id
                and requested_id == expected_id
                and (result_id == expected_id or expected_id in body_ids)
            ):
                return None
            exact_page_identity = True
        if not exact_page_identity and not _same_target(
            str(state.get("target") or ""), searchable
        ):
            return None
        if parsed.hostname in {"kaggle.com", "www.kaggle.com"} and (
            official_path or official_api or official_pages
        ):
            return "high"
    else:
        if not _same_target(str(state.get("target") or ""), searchable):
            return None
        if url in set(state.get("urls") or []):
            return "high"
    if url in set(state.get("urls") or []):
        return "low"
    return None


def _public_scope_state(state: dict) -> dict | None:
    confidence = str(state.get("confidence") or "")
    if state.get("status") != "closed" or confidence not in {"high", "low"}:
        return None
    payload = {
        "status": "closed",
        "confidence": confidence,
        "coverage": state.get("coverage") or "partial",
        "closed_by": state.get("closed_by"),
        "signals": list(state.get("signals") or []),
        "pivots": list(state.get("pivots") or []),
        "pivot_required": bool(state.get("pivot_required")),
        "report_required": True,
    }
    if confidence == "low":
        payload["unverified"] = "official scope claims remain unverified"
    elif payload["coverage"] == "partial":
        payload["unverified"] = (
            "official Evaluation formula and data semantics remain unread"
        )
    return payload


def _scope_coverage(result: dict, signals: list[str], confidence: str) -> str:
    if confidence != "high":
        return "partial"
    page_names = {
        str(name).casefold() for name in result.get("official_page_names", [])
    }
    has_evaluation = any("evaluation" in name for name in page_names)
    has_data = any("data" in name for name in page_names)
    has_data_semantics = bool(
        {"schema", "target", "train", "test", "data"} & set(signals)
    )
    if has_evaluation and has_data and "metric" in signals and has_data_semantics:
        return "detailed"
    return "partial"


def _has_term(query: str, *terms: str) -> bool:
    return any(
        re.search(rf"\b{re.escape(term)}\b", query, re.IGNORECASE)
        for term in terms
    )


def _route_query(query: str, artifact: str) -> str:
    if artifact in {"official", "paper"}:
        return query
    if artifact == "repository":
        return query if _has_term(query, "repository", "repo") else f"{query} repository"
    if artifact == "code":
        return query if _has_term(query, "code", "implementation") else f"{query} implementation code"
    if artifact == "issue":
        return query if _has_term(query, "issue") else f"{query} issue"
    if artifact == "benchmark":
        return query if _has_term(query, "benchmark") else f"{query} benchmark results"
    if artifact == "postmortem":
        return query if _has_term(query, "postmortem") else f"{query} postmortem"
    return query if _has_term(query, "writeup", "lessons learned") else f"{query} writeup"


def _explicit_github_query(query: str) -> str:
    value = re.sub(r"\bsite\s*:\s*github\.com\b", " ", query, flags=re.IGNORECASE)
    value = re.sub(r"\bgithub\b", " ", value, flags=re.IGNORECASE)
    return _clean_query(value)


def _target_matched(card: dict, target: str) -> bool:
    searchable = " ".join(
        str(card.get(key) or "") for key in ("title", "snippet", "url")
    )
    if any(name not in searchable for name in v1.HAN_RE.findall(target)):
        return False
    target_terms = set(_query_words(target))
    if not target_terms:
        # Han-only names have no Latin tokens; require the whole name rather
        # than accepting shared characters or short n-grams from another name.
        phrase = " ".join(target.casefold().split())
        return bool(v1.HAN_RE.search(phrase)) and phrase in " ".join(searchable.casefold().split())
    card_terms = set(_query_words(searchable))
    return len(target_terms & card_terms) >= min(2, len(target_terms))


def _merge_cards(primary: list[dict], fallback: list[dict], limit: int) -> list[dict]:
    merged = []
    seen = set()
    for card in primary + fallback:
        url = str(card.get("url") or "").split("#", 1)[0]
        if not url or url in seen:
            continue
        seen.add(url)
        merged.append(card)
        if len(merged) >= limit:
            break
    return merged


def search(
    query: str,
    limit: int = 8,
    target: str | None = None,
    artifact: str | None = None,
    proof: str | None = None,
    provider: str | None = None,
    *, _recovery: dict | None = None,
) -> dict:
    if target is None and artifact is None and proof is None:
        return _search_base(query, limit, provider=provider, _recovery=_recovery)
    query = _clean_query(query)
    target = _clean_query(target or "")
    proof = _clean_query(proof or "")
    if not query or not target or artifact not in ARTIFACT_TYPES or not proof:
        raise ValueError(
            "query, target, proof, and a supported artifact must be non-empty"
        )
    _require_web_search()
    mode = _provider_mode(provider)
    # Reject scope violations before reserving budget; consume a Pivot only after it succeeds.
    _guard_scope_search(query, target, artifact, consume_pivot=False)
    if _uses_scope_gate(query, target, artifact):
        _open_scope(target, record_search=False)
    plan = _search_plan(query, limit, mode, artifact, target)
    with _search_execution(plan, artifact, _recovery):
        result = _search_artifact(query, limit, target, artifact, proof, mode)
        return _complete_search(result, _route_query(query, artifact), limit, target)


def _search_artifact(query: str, limit: int, target: str, artifact: str, proof: str, mode: str) -> dict:
    _guard_scope_search(query, target, artifact)
    scope_gate = _uses_scope_gate(query, target, artifact)
    if scope_gate:
        _open_scope(target)
    _retry_provider_failures()
    effective_query = _route_query(query, artifact)
    extra_outcomes = []
    official_cards, artifact_cards, topic_cards = [], [], []
    if scope_gate and mode != "anysearch":
        official_card = _kaggle_official_card(target, query, outcomes=extra_outcomes)
        if official_card:
            page_card = _kaggle_pages_card(official_card)
            official_cards = [
                card for card in (page_card, official_card) if card is not None
            ]
            base._record_event(
                "official_candidates",
                {"target": target, "need": query, "results": official_cards},
            )
    if artifact in GITHUB_ARTIFACTS and mode != "anysearch":
        github_query = _explicit_github_query(query)
        artifact_queries = [github_query]
        artifact_cards = [
            card for card in _github_candidates(github_query, min(5, limit), extra_outcomes)
            if _target_matched(card, target)
        ]
        fallback_target = _scope_target(target)
        fallback_request = {"query": _github_query(fallback_target), "limit": min(5, limit)}
        if (_github_query(fallback_target) != _github_query(github_query)
                and (len(artifact_cards) < min(2, limit) or _recovery_has("github", fallback_request))):
            artifact_queries.append(fallback_target)
            fallback_cards = [
                card for card in _github_candidates(fallback_target, min(5, limit), extra_outcomes)
                if _target_matched(card, target)
            ]
            artifact_cards = _merge_cards(
                fallback_cards, artifact_cards, min(5, limit)
            )
        base._record_event(
            "artifact_candidates",
            {
                "target": target,
                "need": query,
                "artifact": artifact,
                "query": artifact_queries[-1],
                "queries": artifact_queries,
                "results": artifact_cards,
            },
        )
        if "kaggle" in f"{target} {query}".casefold() and (
                mode == "hybrid" or len(artifact_cards) < min(2, limit)
                or any(_recovery_has(name) for name in ("kaggle-topics", "kaggle-metadata", "kaggle-listing"))):
            topic_query = query if _same_target(target, query) else f"{_scope_target(target)} {query}"
            topic_cards = _kaggle_topic_cards(topic_query, min(5, limit), outcomes=extra_outcomes)
    specialized = _merge_cards(official_cards, _merge_cards(topic_cards, artifact_cards, limit), limit)
    initial_gaps = _recall_gaps(specialized, effective_query, limit, target)
    if (mode in {"anysearch", "hybrid"} or initial_gaps
            or _recovery_has("anysearch", anysearch._arguments(effective_query.strip(), limit, artifact))
            or _recovery_has("duckduckgo", {"query": effective_query, "limit": max(8, limit)})):
        result = _search_base(effective_query, limit, provider=mode, artifact=artifact, target=target)
    else:
        result = {"query": effective_query, "results": [], "provider_mode": mode,
                  "providers_used": [], "provider_results": [], "recall_checks": {"initial_gaps": initial_gaps}}
    outcomes = list(result.get("provider_results", []))
    original_errors = list(result.get("provider_errors", []))
    result = dict(result)
    result["results"] = _merge_cards(
        specialized, list(result.get("results", [])), limit
    )
    if scope_gate:
        _remember_scope_urls(target, result)
    base._record_event(
        "artifact_route",
        {
            "target": target,
            "need": query,
            "requested_query": query,
            "query": effective_query,
            "artifact": artifact,
            "proof": proof,
        },
    )
    result.update(
        {"target": target, "need": query, "artifact": artifact, "proof": proof}
    )
    result["providers_planned"] = _ACTIVE_SEARCH.get().plan
    if extra_outcomes and "direct" not in result.get("providers_used", []):
        result["providers_used"] = ["direct", *result.get("providers_used", [])]
    result["recall_checks"] = {
        **result.get("recall_checks", {}),
        "remaining_gaps": _recall_gaps(result["results"], effective_query, limit, target),
    }
    state = _load_scope_state()
    public_scope = _public_scope_state(state)
    if public_scope and _same_target(str(state.get("target") or ""), target):
        result["scope_state"] = public_scope
    result["provider_results"] = extra_outcomes + outcomes
    errors = _outcome_errors(extra_outcomes) + original_errors
    if errors:
        result["provider_errors"] = errors
    return _search_status(result, allow_incomplete=True)


def _kaggle_pages_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.hostname in {"kaggle.com", "www.kaggle.com"}
        and parsed.path.endswith("competitions.PageService/ListPages")
    )


def _kaggle_competition_slug(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in {"kaggle.com", "www.kaggle.com"}:
        return ""
    match = re.match(r"^/(?:competitions|c)/([^/?#]+)(?:/|$)", parsed.path)
    return urllib.parse.unquote(match.group(1)).strip() if match else ""


def _kaggle_topic_parts(url: str) -> tuple[str, int] | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in {"kaggle.com", "www.kaggle.com"}:
        return None
    match = re.match(
        r"^/(?:competitions|c)/([^/?#]+)/discussion/(\d+)(?:/|$)", parsed.path
    )
    if not match:
        return None
    return urllib.parse.unquote(match.group(1)).strip(), int(match.group(2))


def _kaggle_official_pages_url(url: str) -> str:
    slug = _kaggle_competition_slug(url)
    if not slug:
        return ""
    metadata_url = KAGGLE_GET_COMPETITION + "?" + urllib.parse.urlencode(
        {"competitionName": slug}
    )
    metadata = _kaggle_json(metadata_url, 100_000)
    competition_id = metadata.get("id") or metadata.get("competitionId")
    if not competition_id:
        return ""
    return KAGGLE_LIST_PAGES + "?" + urllib.parse.urlencode(
        {"competitionId": competition_id, "competitionName": slug}
    )


def _github_repository_parts(url: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlparse(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    return parts[0], parts[1].removesuffix(".git")


def _read_github_readme(
    url: str, terms: list[str], window: int, max_windows: int
) -> dict:
    parts = _github_repository_parts(url)
    if not parts:
        raise ValueError("not a GitHub repository root")
    base._reserve("read")
    owner, repository = parts
    api_url = "https://api.github.com/repos/" + urllib.parse.quote(
        f"{owner}/{repository}/readme", safe="/"
    )
    if not base._url_ok(api_url):
        raise ValueError(f"blocked or unsupported URL: {api_url}")
    with base.metrics.request(base.requests.get,
        api_url,
        url_ok=base._url_ok,
        headers={
            "User-Agent": base.UA,
            "Accept": "application/vnd.github.raw+json",
        },
        timeout=base.TIMEOUT,
    ) as response:
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("GitHub README exceeds size limit")
        text = v1._document_text(response.text)
    if not text:
        raise RuntimeError("GitHub repository has no readable README")
    # Compact windows keep the requested proof locator and its value together.
    radius = min(window, 280)
    result = {
        "url": url,
        "resolved_url": response.url,
        "title": f"GitHub README: {owner}/{repository}",
        "windows": v1._windows(text, terms, radius, max_windows),
        "content_type": "github_readme",
        "content_scope": "readme",
        "extracted_chars": len(text),
        "source_type": "practitioner_artifact",
        "fallback": "github_readme_api",
        "failed_attempts": 0,
    }
    base._record_event("read", result)
    result["_body"] = text
    return result


def _flatten_topic_messages(messages: list[dict]) -> list[str]:
    values = []
    for message in messages:
        if not isinstance(message, dict) or message.get("isDeleted"):
            continue
        body = str(message.get("rawMarkdown") or message.get("content") or "").strip()
        if body:
            values.append(body)
        replies = message.get("replies") or []
        if isinstance(replies, list):
            values.extend(_flatten_topic_messages(replies))
    return values


def _read_kaggle_topic(
    url: str, terms: list[str], window: int, max_windows: int
) -> dict:
    parts = _kaggle_topic_parts(url)
    if not parts:
        raise ValueError("not a Kaggle competition topic")
    slug, topic_id = parts
    detail_url = f"https://www.kaggle.com/api/v1/discussions/{topic_id}/get"
    messages_url = (
        f"{KAGGLE_TOPICS_ROOT}/{urllib.parse.quote(slug, safe='')}/topics/"
        f"{topic_id}/messages?" + urllib.parse.urlencode({"page_size": 30})
    )
    base._reserve("read")
    topic = _kaggle_json(detail_url, 500_000).get("topic") or {}
    topic_url = str(topic.get("url") or "")
    canonical_topic = _kaggle_topic_parts(
        urllib.parse.urljoin("https://www.kaggle.com", topic_url)
    )
    if canonical_topic != (slug, topic_id):
        raise RuntimeError("Kaggle topic identity did not match the requested competition")
    payload = _kaggle_json(messages_url, 2_000_000)
    messages = payload.get("messages", [])
    bodies = _flatten_topic_messages(messages if isinstance(messages, list) else [])
    title = str(topic.get("title") or f"Kaggle competition topic {topic_id}")
    author = str(topic.get("authorName") or "unknown")
    primary_html = str(topic.get("content") or "").strip()
    primary = v1._html_text(primary_html.encode("utf-8"), base_url=url)[1] if primary_html else ""
    # The first message may be a reply or retain Markdown links absent from the
    # rendered post. Only exact duplicate bodies can be discarded safely.
    sections = []
    for value in [primary, *bodies]:
        if value and value not in sections:
            sections.append(value)
    text = v1._document_text(
        f"Kaggle competition topic: {title}\nAuthor: {author}\n\n"
        + "\n\n".join(sections)
    )
    if not text.strip():
        raise RuntimeError("Kaggle competition topic has no readable body")
    result = {
        "url": url,
        "resolved_url": messages_url,
        "title": title,
        "windows": v1._windows(text, terms, window, max_windows),
        "content_type": "kaggle_competition_topic",
        "content_scope": "discussion",
        "extracted_chars": len(text),
        "source_type": "practitioner_artifact",
        "fallback": "kaggle_topics_api",
        "failed_attempts": 0,
        "topic_id": topic_id,
        "author": author,
    }
    base._record_event("read", result)
    result["_body"] = text
    return result


def _kaggle_page_windows(
    pages: list[dict], terms: list[str], window: int, max_windows: int
) -> tuple[list[str], list[str]]:
    priority = {"evaluation": 0, "data-description": 1, "description": 2}
    selected = sorted(
        (page for page in pages if str(page.get("content") or "").strip()),
        key=lambda page: (priority.get(str(page.get("name") or "").casefold(), 9), int(page.get("id") or 0)),
    )
    selected = [
        page for page in selected
        if str(page.get("name") or "").casefold() in priority
    ] or selected[:3]
    windows = []
    names = []
    for page in selected:
        name = str(page.get("name") or "official")
        content = str(page.get("content") or "")
        chunks = v1._windows(content, terms, window, max(1, min(2, max_windows)))
        for chunk in chunks:
            windows.append(f"Kaggle official page: {name}\n{chunk}")
            names.append(name)
            if len(windows) >= max_windows:
                return windows, names
    return windows, names


def _read_kaggle_pages(
    url: str, terms: list[str], window: int, max_windows: int
) -> dict:
    base._reserve("read")
    errors = []
    try:
        payload = _kaggle_json(url)
        pages = [row for row in payload.get("pages", []) if isinstance(row, dict)]
        windows, names = _kaggle_page_windows(pages, terms, window, max_windows)
        if not windows:
            raise RuntimeError("official page response contained no readable page body")
        competition_ids = {
            str(row.get("competitionId"))
            for row in pages
            if row.get("competitionId") is not None
        }
        result = {
            "url": url,
            "resolved_url": url,
            "title": "Kaggle official competition pages",
            "windows": windows,
            "content_type": "kaggle_official_pages",
            "content_scope": "official_pages",
            "extracted_chars": sum(len(str(row.get("content") or "")) for row in pages),
            "source_type": "official",
            "fallback": "none",
            "failed_attempts": 0,
            "official_page_names": sorted(set(names)),
            "competition_id": next(iter(competition_ids), ""),
        }
        body = "\n\n".join(
            f"Kaggle official page: {row.get('name') or 'official'}\n{row.get('content') or ''}"
            for row in pages
        )
    except Exception as exc:
        errors.append(str(exc))
        # Keep the scope gate usable when the page service is unavailable, but
        # label this as metadata-only so the Evaluation/Data gaps stay visible.
        parsed = urllib.parse.urlparse(url)
        slug = (urllib.parse.parse_qs(parsed.query).get("competitionName") or [""])[0]
        if not slug:
            raise RuntimeError("; ".join(errors))
        metadata_url = KAGGLE_GET_COMPETITION + "?" + urllib.parse.urlencode(
            {"competitionName": slug}
        )
        try:
            metadata = _kaggle_json(metadata_url, 100_000)
            text = json.dumps(metadata, ensure_ascii=False)
            title = str(metadata.get("title") or "Kaggle official competition metadata")
            result = {
                "url": url,
                "resolved_url": metadata_url,
                "title": title,
                "windows": [f"Kaggle official metadata\n{text[:window * 2]}"],
                "content_type": "kaggle_official_metadata",
                "content_scope": "metadata",
                "extracted_chars": len(text),
                "source_type": "official",
                "fallback": "kaggle_competition_metadata",
                "failed_attempts": len(errors),
                "official_page_names": [],
                "competition_id": metadata.get("id") or metadata.get("competitionId") or "",
            }
            body = f"Kaggle official metadata\n{text}"
        except Exception as fallback_exc:
            raise RuntimeError("; ".join(errors + [str(fallback_exc)]))
    base._record_event("read", result)
    result["_body"] = body
    return result


def _read_local(path: Path) -> dict:
    with path.open("rb") as handle:
        raw = handle.read(v1.MAX_DOWNLOAD_BYTES + 1)
    if len(raw) > v1.MAX_DOWNLOAD_BYTES:
        raise ValueError("local document exceeds size limit")
    details = {}
    if raw.startswith(b"%PDF"):
        title, text = v1._pdf_text(raw, details)
        content_type = "pdf"
    elif path.suffix.casefold() in {".html", ".htm"}:
        title, text = v1._html_text(raw)
        content_type = "html"
    else:
        title, text = path.name, v1._document_text(raw.decode("utf-8", errors="replace"))
        content_type = "text"
    return {
        "url": str(path), "title": title or path.name,
        "content_type": content_type, "content_scope": "local_file",
        "source_type": "local", "truncated": details.get("truncated", False),
        "pages": details.get("pages", []), "_body": text,
    }


def read_page(
    url: str, terms: list[str], window: int = 650, max_windows: int = 5,
    *, refresh: bool = False,
) -> dict:
    policy = _source_policy()
    ref = _source_ref(url)
    cached = None if refresh else workspace.load_source(ref)
    if cached:
        result = workspace.excerpts(*cached, terms, window, max_windows)
    elif not url.startswith(("http://", "https://")):
        path = Path(url).expanduser().resolve()
        if not path.is_file():
            raise ValueError("source must be a known REF, HTTP(S) URL, or local file")
        result = _read_local(path)
    elif policy == "local_only":
        raise RuntimeError("web reading is disabled by source_policy=local_only; use a saved source or local file")
    elif _kaggle_topic_parts(url):
        result = _read_kaggle_topic(url, terms, window, max_windows)
    elif _kaggle_pages_url(url):
        result = _read_kaggle_pages(url, terms, window, max_windows)
    elif _kaggle_competition_slug(url):
        try:
            pages_url = _kaggle_official_pages_url(url)
            if not pages_url:
                raise RuntimeError("official competition metadata has no id")
        except Exception:
            result = v1.read_page(url, terms, window, max_windows, include_body=True)
        else:
            result = _read_kaggle_pages(pages_url, terms, window, max_windows)
            result = dict(result)
            result["url"] = url
            result["resolved_url"] = pages_url
            result["fallback"] = "kaggle_competition_url_to_official_pages"
    elif _github_repository_parts(url):
        try:
            result = _read_github_readme(url, terms, window, max_windows)
        except Exception:
            result = v1.read_page(url, terms, window, max_windows, include_body=True)
    else:
        result = v1.read_page(url, terms, window, max_windows, include_body=True)
    body = result.pop("_body", "")
    state = _load_scope_state()
    confidence = None
    if state.get("status") == "open":
        confidence = _scope_source_matches(state, result)
    if confidence:
        signals = _scope_body_signals(result)
        state["status"] = "closed"
        state["confidence"] = confidence
        state["coverage"] = _scope_coverage(result, signals, confidence)
        state["pivots"] = _scope_pivots(result)
        state["pivot_required"] = bool(state["pivots"])
        state["closed_by"] = str(
            result.get("resolved_url") or result.get("url") or url
        )
        state["signals"] = signals
        _save_scope_state(state)
        base._record_event(
            "scope_state",
            {
                "status": "closed",
                "confidence": confidence,
                "coverage": state["coverage"],
                "target": state.get("target"),
                "url": state["closed_by"],
                "signals": signals,
                "pivots": state["pivots"],
                "pivot_required": state["pivot_required"],
            },
        )
        result = dict(result)
        result["scope_state"] = _public_scope_state(state)
    if not cached:
        result.setdefault("content_scope", "full_text")
        metadata, text = workspace.save_source(ref, result, body)
        saved = workspace.excerpts(metadata, text, terms, window, max_windows, cache_hit=False)
        if "scope_state" in result:
            saved["scope_state"] = result["scope_state"]
        return saved
    return result


def _join_terms(groups: list[list[str]]) -> list[str]:
    return [" ".join(group).strip() for group in groups if " ".join(group).strip()]


def _ref_state_path() -> Path:
    root = os.environ.get("DRBENCH_RUN_DIR") or os.environ.get(
        "DEEP_RESEARCH_RUN_DIR"
    )
    return Path(root or os.getcwd()) / REF_STATE_FILE


@contextmanager
def _ref_lock():
    path = _ref_state_path().with_name(f"{REF_STATE_FILE}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_refs() -> dict[str, str]:
    try:
        value = json.loads(_ref_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(ref): str(url)
        for ref, url in value.items()
        if str(ref).startswith("REF-") and (
            str(url).startswith(("http://", "https://")) or Path(str(url)).is_absolute()
        )
    }


def _save_refs(refs: dict[str, str]) -> None:
    path = _ref_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _citation_state_path() -> Path:
    return _ref_state_path().with_name(CITATION_STATE_FILE)


def _load_citations() -> list[str]:
    try:
        value = json.loads(_citation_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(value, list):
        return []
    return [
        str(url)
        for url in value
        if str(url).startswith(("http://", "https://"))
    ]


def _save_citations(urls: list[str]) -> None:
    path = _citation_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    temporary.write_text(json.dumps(urls, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _source_ref(url: str) -> str:
    value = url.strip()
    if value.startswith(("http://", "https://")):
        value = value.split("#", 1)[0]
    return "REF-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16].upper()


def _hide_urls(value):
    if isinstance(value, dict):
        return {
            key: _hide_urls(item)
            for key, item in value.items()
            if key not in {"url", "resolved_url", "openalex_id", "homepage"}
        }
    if isinstance(value, list):
        return [_hide_urls(item) for item in value]
    if isinstance(value, str):
        return URL_TEXT_RE.sub("[URL hidden until Read]", value)
    return value


def _search_for_need(
    query: str,
    limit: int,
    target: str | None,
    artifact: str | None,
    proof: str | None,
    provider: str | None,
    *, need: str | None = None, refresh: bool = False, retry_failed: bool = False,
    gap_id: str | None = None, corpus_path: str | None = None,
) -> dict:
    if refresh and retry_failed:
        raise ValueError("--refresh and --retry-failed are mutually exclusive")
    if not query.strip():
        raise ValueError("search query must be non-empty")
    query = query.strip()
    if need is not None:
        workspace.require_need(need)
    mode = "fixed_corpus" if corpus_path else _provider_mode(provider)
    _source_policy()
    request = {
        "query": query.strip(), "limit": limit, "target": target,
        "artifact": artifact, "proof": proof, "provider": mode,
    }
    if corpus_path:
        identity, _ = fixed_corpus.load_case(corpus_path)
        request.update(corpus_path=str(Path(corpus_path).resolve()), corpus_identity=identity)
    cached = None if refresh else workspace.load_search(request)
    cache_hit = cached is not None
    try:
        recovery = None
        if retry_failed:
            _require_web_search()
            if corpus_path:
                raise ValueError("--retry-failed applies to web search; fixed-corpus search has no provider requests")
            if cached is None:
                raise ValueError("--retry-failed requires a cached search; run search first or use --refresh")
            saved = cached["result"].get("provider_execution")
            if saved is None:
                raise ValueError("cached search has no provider execution records; use --refresh")
            plan = _search_plan(query, limit, mode, artifact, target)
            records = provider_cache.ProviderCache(_provider_config(plan), saved)
            for row in records.records.values():
                _provider_operation(row)
            if any(row["status"] == "error" for row in records.records.values()):
                recovery = cached["result"]
                cache_hit = False
        if cached is None or recovery is not None:
            if corpus_path:
                result = fixed_corpus.search(workspace, corpus_path, query, limit)
            elif recovery is not None:
                result = search(query, limit, target, artifact, proof, mode, _recovery=recovery)
            else:
                result = search(query, limit, target, artifact, proof, mode)
            cached = workspace.save_search(request, result)
    except Exception as exc:
        if need is not None:
            workspace.record_search_attempt(
                need, query, workspace.search_id(request), error=str(exc)[:240], gap=gap_id,
            )
        raise
    public = _public_leads(cached["result"])
    public.update(
        cache_hit=cache_hit, search_id=cached["id"], searched_at=cached["searched_at"],
    )
    if need is not None:
        public["need"] = need
        workspace.record_search_attempt(
            need, query, cached["id"], cache_hit=cache_hit, new_ref_count=public["new_ref_count"], gap=gap_id,
        )
    return public


def _ref_search(query, limit, target, artifact, proof, provider, *, need=None, refresh=False,
                retry_failed=False, gap=None, corpus_path=None):
    if gap is None:
        return _search_for_need(query, limit, target, artifact, proof, provider, need=need, refresh=refresh,
                                retry_failed=retry_failed,
                                corpus_path=corpus_path)
    need, attempt = claim_review.reserve_gap_search(workspace, gap, query, need)
    try:
        result = _search_for_need(query, limit, target, artifact, proof, provider, need=need, refresh=refresh,
                                  retry_failed=retry_failed,
                                  gap_id=gap, corpus_path=corpus_path)
    except Exception as exc:
        claim_review.finish_gap_search(workspace, gap, attempt, error=exc)
        raise
    result.update(claim_review.finish_gap_search(workspace, gap, attempt, result=result))
    return result


def _search_batch(plan_path: str, limit: int = 5, provider: str | None = None, *,
                  workers: int = 3, refresh: bool = False, retry_failed: bool = False) -> dict:
    if refresh and retry_failed:
        raise ValueError("--refresh and --retry-failed are mutually exclusive")
    if workers not in (1, 2, 3) or not 1 <= limit <= 12:
        raise ValueError("search-batch requires 1-3 workers and 1-12 results per query")
    with Path(plan_path).open("rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("search plan exceeds 65536 bytes")
    plan = json.loads(raw)
    feedback = None
    if isinstance(plan, dict) and "feedback" in plan:
        if set(plan) != {"feedback"}:
            raise ValueError("use either searches or feedback in a search plan")
        plan = search_feedback.build_plan(workspace, plan["feedback"], _source_policy(), ARTIFACT_TYPES)
        feedback = plan["feedback"]
    searches = plan.get("searches") if isinstance(plan, dict) else None
    if not isinstance(searches, list) or not 1 <= len(searches) <= 6:
        raise ValueError("search plan must contain 1-6 searches")
    items, seen = [], set()
    for row in searches:
        if not isinstance(row, dict) or set(row) - {"query", "reason", "need", "target", "artifact", "proof"}:
            raise ValueError("each search accepts query, reason, optional need, and target/artifact/proof")
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ("query", "reason")):
            raise ValueError("each search requires a non-empty query and a short purpose in reason")
        item = {key: value.strip() if isinstance(value, str) else value for key, value in row.items()}
        route = search_feedback.route_fields(item, ARTIFACT_TYPES)
        item.update(route)
        identity = (item["query"], tuple(route.values()))
        if identity in seen:
            raise ValueError("duplicate query and route in search plan; reuse its results instead")
        seen.add(identity)
        if "need" in item:
            if not isinstance(item["need"], str) or not item["need"]:
                raise ValueError("need must be a non-empty registered Need ID")
            workspace.require_need(item["need"])
        items.append(item)
    mode = _provider_mode(provider)
    _source_policy()
    # Competition discovery shares mutable scope state within the run.
    if any("kaggle" in f"{item['query']} {item.get('target', '')}".casefold()
           or _practitioner_search_enabled(f"{item['query']} {item.get('target', '')}") for item in items):
        workers = 1
    workers = min(workers, len(items))

    def retrieve(item):
        try:
            result = _ref_search(item["query"], limit, item.get("target"), item.get("artifact"), item.get("proof"), mode,
                                 need=item.get("need"), refresh=refresh, retry_failed=retry_failed)
            return {**item, "status": result.get("status", "ok" if result.get("results") else "empty"), "result": result}
        except Exception as exc:
            return {**item, "status": "error", "error": str(exc)[:240],
                    **({"provider_errors": exc.provider_errors} if isinstance(exc, SearchError) else {})}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(retrieve, items))
    failed = sum(row["status"] == "error" for row in results)
    partial = sum(row["status"] == "partial" for row in results)
    public = _hide_urls({
        "status": "failed" if failed == len(items) else "partial" if failed or partial else "complete",
        "query_count": len(items), "completed": len(items) - failed, "failed": failed,
        "partial": partial,
        "empty": sum(row["status"] == "empty" for row in results),
        "workers": workers, "results": results,
        "next_action": "Read promising REFs, then plan follow-up queries from the remaining gaps. "
                       "Completed searches and summaries do not prove that a Need is answered.",
        **({"search_feedback": feedback} if feedback is not None else {}),
    })
    record = workspace.save_search({"operation": "search-batch", "searches": items,
                                    "limit": limit, "provider": mode,
                                    **({"feedback": feedback} if feedback is not None else {})}, public)
    public["batch_id"] = record["id"]
    return public


def _public_leads(result: dict) -> dict:
    rows = []
    discovered = []
    for row in result.get("results", []):
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        ref = _source_ref(url)
        discovered.append((ref, url))
        rows.append(
            {
                **_hide_urls(row),
                "ref": ref,
                "evidence_status": "unread_lead",
                "citable": False,
            }
        )
    with _ref_lock():
        refs = _load_refs()
        new_refs = {ref for ref, _ in discovered if ref not in refs}
        for row in rows:
            row["seen_before"] = row["ref"] in refs
        refs.update(discovered)
        _save_refs(refs)
    public = _hide_urls({key: value for key, value in result.items()
                         if key not in {"results", "provider_execution"}})
    public["results"] = rows
    public["new_ref_count"] = len(new_refs)
    public["next_action"] = (
        "These cards are unread leads, not evidence. Read a matching REF. "
        "Only literal values returned in allowed_citation_urls may be cited."
    )
    if public.get("recall_checks", {}).get("remaining_gaps"):
        public["next_action"] += (
            " Recall gaps remain. Keep the problem's subject in the next query, use one missing concept "
            "or an exact domain phrase, and inspect a primary source; do not treat this page as sufficient coverage."
        )
    if rows and not new_refs:
        public["next_action"] += (
            " All candidate REFs were seen before; revisit saved text or pivot to the remaining gap."
        )
    return public


def _follow_papers(source: str, direction: str, offset: int, limit: int, *,
                   need: str | None = None, refresh: bool = False) -> dict:
    if need is not None:
        workspace.require_need(need)
    if source.startswith("REF-"):
        with _ref_lock():
            source = _load_refs().get(source, "")
    identifier = citation_graph.paper_id(source)
    if direction not in {"references", "citations"} or offset < 0 or not 1 <= limit <= 20:
        raise ValueError("direction must be references/citations, offset nonnegative, and limit 1-20")
    policy = _source_policy()
    request = {"operation": "follow", "paper": identifier, "direction": direction,
               "offset": offset, "limit": limit, "provider": "semantic_scholar"}
    cached = None if refresh else workspace.load_search(request)
    cache_hit = cached is not None
    action = f"follow {identifier} {direction} offset={offset}"
    if cached is None:
        try:
            if policy == "local_only":
                raise RuntimeError("citation discovery is disabled by source_policy=local_only")
            base._reserve("search")
            result = citation_graph.fetch_papers(identifier, direction, offset, limit, v1._fetch_bytes)
        except Exception as exc:
            if need is not None:
                workspace.record_search_attempt(need, action, workspace.search_id(request), error=str(exc)[:240])
            raise
        cached = workspace.save_search(request, result)
    public = _public_leads(cached["result"])
    public.update(cache_hit=cache_hit, search_id=cached["id"], searched_at=cached["searched_at"])
    if need is not None:
        public["need"] = need
        workspace.record_search_attempt(need, action, cached["id"], cache_hit=cache_hit,
                                        new_ref_count=public["new_ref_count"])
    return public


def _with_citation(result: dict) -> dict:
    result = dict(result)
    source = str(result.get("url") or "")
    if source.startswith(("http://", "https://")):
        result["citation_url"] = source
    else:
        result["citation_path"] = source
    with _ref_lock():
        citations = _load_citations()
        if source.startswith(("http://", "https://")) and source not in citations:
            citations.append(source)
            _save_citations(citations)
        result["allowed_citation_urls"] = citations
    result["citation_note"] = "Reading admits the source URL/path; check whether its saved excerpt supports each claim."
    return result


def _ref_read(ref: str, terms: list[str], window: int, *, refresh: bool = False) -> dict:
    source = ref
    with _ref_lock():
        url = _load_refs().get(ref, "")
    if not url:
        if ref.startswith(("http://", "https://")):
            url = ref
        elif Path(ref).expanduser().is_file():
            url = str(Path(ref).expanduser().resolve())
        else:
            raise ValueError("Read requires a known REF, HTTP(S) URL, or local file")
        ref = _source_ref(url)
    try:
        result = read_page(url, terms, window, refresh=refresh)
    except Exception as exc:
        base._record_event(
            "ref_read_failed", {"ref": ref, "error_type": type(exc).__name__}
        )
        raise RuntimeError(f"Read failed for {ref}: {str(exc)[:240]}") from None
    result = dict(result)
    result["ref"] = ref
    with _ref_lock():
        refs = _load_refs()
        refs[ref] = url
        _save_refs(refs)
    result["discovery"] = "registered_ref" if source.startswith("REF-") else "direct_input"
    return _with_citation(result)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    search_parser = sub.add_parser("search")
    search_parser.add_argument("query")
    # Keep the structured route for the Skill, while allowing this script to
    # serve as a drop-in read-only search adapter when only a query is known.
    search_parser.add_argument("--target")
    search_parser.add_argument("--artifact", choices=ARTIFACT_TYPES)
    search_parser.add_argument("--proof")
    search_parser.add_argument(
        "--provider",
        choices=SEARCH_PROVIDERS,
        help=(
            "Candidate retriever: auto tries artifact APIs first when applicable, "
            "then AnySearch with direct fallback; hybrid attempts all lanes; "
            "anysearch has no direct calls. Environment: DEEP_RESEARCH_SEARCH_PROVIDER."
        ),
    )
    search_parser.add_argument("--limit", type=int, default=8)
    search_parser.add_argument("--need", help="Associate this search with a registered Need")
    search_parser.add_argument("--gap", help="Search for a reviewed claim gap within its explicit bounds")
    search_parser.add_argument("--corpus", help="Search only a case-pack manifest with local FTS5 and measured budgets")
    search_refresh = search_parser.add_mutually_exclusive_group()
    search_refresh.add_argument("--refresh", action="store_true", help="Bypass saved search results")
    search_refresh.add_argument("--retry-failed", action="store_true", help="Retry only recorded failed provider requests")
    batch_parser = sub.add_parser("search-batch", help="Run independent query/reason searches with bounded concurrency")
    batch_parser.add_argument("plan", help="JSON object with 1-6 searches or saved-source feedback")
    batch_parser.add_argument("--limit", type=int, choices=range(1, 13), default=5)
    batch_parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=3)
    batch_parser.add_argument("--provider", choices=SEARCH_PROVIDERS)
    batch_refresh = batch_parser.add_mutually_exclusive_group()
    batch_refresh.add_argument("--refresh", action="store_true")
    batch_refresh.add_argument("--retry-failed", action="store_true")
    read_parser = sub.add_parser("read")
    read_parser.add_argument("ref")
    read_parser.add_argument("--term", action="append", nargs="+", default=[])
    read_parser.add_argument("--window", type=int, default=650)
    read_parser.add_argument("--refresh", action="store_true", help="Retrieve a new source snapshot")
    read_parser.add_argument("--corpus", help="Read a fixed-corpus source ID or REF under the case budget")
    open_parser = sub.add_parser("open", help="Read saved source lines without network access")
    saved_source_help = "REF returned by Read, or a fixed-corpus source ID already read in this run (e.g. S1)"
    open_parser.add_argument("ref", help=saved_source_help)
    open_parser.add_argument("--start-line", type=int, default=1)
    open_parser.add_argument("--count", type=int, default=40)
    open_parser.add_argument("--version", help="Read a historical snapshot_id (legacy content_hash also accepted)")
    find_parser = sub.add_parser("find", help="Find terms in a saved source")
    find_parser.add_argument("ref", help=saved_source_help)
    find_parser.add_argument("--term", action="append", nargs="+", required=True)
    find_parser.add_argument("--window", type=int, default=650)
    find_parser.add_argument("--offset", type=int, default=0, help="Skip this many matching passage windows")
    find_parser.add_argument("--limit", type=int, choices=range(1, 11), default=5)
    find_parser.add_argument("--version", help="Read a historical snapshot_id or legacy content_hash")
    evidence_parser = sub.add_parser("evidence", help="Save a claim and verify its verbatim quote")
    evidence_parser.add_argument("ref", help=saved_source_help)
    evidence_parser.add_argument("--need", required=True)
    evidence_parser.add_argument("--claim", required=True)
    evidence_parser.add_argument("--quote", required=True)
    evidence_parser.add_argument("--relation", choices=("supports", "refutes", "context"), required=True)
    evidence_parser.add_argument("--start-line", type=int, default=1)
    evidence_parser.add_argument("--version", help="Use a historical snapshot_id or legacy content_hash")
    review_parser = sub.add_parser("review", help="Review saved evidence, identical text and mixed relations offline")
    review_parser.add_argument("--need")
    review_parser.add_argument("--claim", help="Match an exact saved claim")
    review_parser.add_argument("--offset", type=int, default=0)
    review_parser.add_argument("--limit", type=int, default=5)
    follow_parser = sub.add_parser("follow", help="Discover a paper's references or citing papers as unread leads")
    follow_parser.add_argument("paper")
    follow_parser.add_argument("--direction", choices=("references", "citations"), default="references")
    follow_parser.add_argument("--offset", type=int, default=0)
    follow_parser.add_argument("--limit", type=int, default=10)
    follow_parser.add_argument("--need")
    follow_parser.add_argument("--refresh", action="store_true")
    section_parser = sub.add_parser("section", help="Register or revise a section's Needs and perspective")
    section_parser.add_argument("id")
    section_parser.add_argument("--title", required=True)
    section_parser.add_argument("--need", action="append", required=True, dest="needs")
    section_parser.add_argument("--perspective")
    section_parser.add_argument("--order", type=int)
    section_parser.add_argument("--reason", default="")
    sub.add_parser("outline", help="Show section coverage, remaining gaps and unassigned Needs")
    synthesis_parser = sub.add_parser("synthesis", help="Save caller-authored synthesis separately from raw evidence")
    synthesis_parser.add_argument("section")
    synthesis_parser.add_argument("--summary", required=True)
    synthesis_parser.add_argument("--evidence", action="append", required=True, dest="evidence_ids")
    synthesis_parser.add_argument("--inference", action="append", default=[])
    synthesis_parser.add_argument("--limitation", action="append", default=[])
    packet_parser = sub.add_parser("packet", help="Get bounded section evidence, counterevidence and synthesis")
    packet_parser.add_argument("section")
    packet_parser.add_argument("--offset", type=int, default=0)
    packet_parser.add_argument("--limit", type=int, default=5)
    audit_parser = sub.add_parser("audit", help="Bind draft evidence markers to sentence candidates; flag structural issues")
    audit_parser.add_argument("report")
    audit_parser.add_argument("--offset", type=int, default=0)
    audit_parser.add_argument("--limit", type=int, default=10)
    claims_parser = sub.add_parser("claims", help="Prepare, save or inspect explicit per-claim semantic judgments")
    claims_parser.add_argument("report")
    claims_parser.add_argument("--judgments")
    claims_parser.add_argument("--schema-version", type=int, choices=(1, 2))
    claims_parser.add_argument("--offset", type=int, default=0)
    claims_parser.add_argument("--limit", type=int, default=5)
    gap_parser = sub.add_parser("gap", help="Create or inspect a bounded follow-up for a reviewed claim")
    gap_parser.add_argument("report")
    gap_parser.add_argument("--claim", required=True)
    gap_parser.add_argument("--need", required=True)
    gap_parser.add_argument("--max-attempts", type=int, default=3)
    outcome_parser = sub.add_parser("gap-result", help="Record new evidence, no progress, resolution or a stop")
    outcome_parser.add_argument("gap")
    outcome_parser.add_argument("--outcome", choices=("progress", "no_progress", "resolved", "stop"), required=True)
    outcome_parser.add_argument("--reason", required=True)
    outcome_parser.add_argument("--evidence", action="append", default=[])
    case_parser = sub.add_parser("case-pack", help="Export researcher-only task and corpus without evaluator references")
    case_parser.add_argument("case")
    case_parser.add_argument("--output", required=True)
    compare_parser = sub.add_parser("compare", help="Prepare a blind comparison using a fixed case and two reports")
    compare_parser.add_argument("case")
    compare_parser.add_argument("--a", required=True)
    compare_parser.add_argument("--b", required=True)
    compare_parser.add_argument("--a-run")
    compare_parser.add_argument("--b-run")
    compare_parser.add_argument("--output", required=True)
    compare_parser.add_argument("--seed", type=int, default=0)
    score_parser = sub.add_parser("score", help="Validate a reviewer's scores and reveal the comparison labels")
    score_parser.add_argument("comparison")
    score_parser.add_argument("--judgments", required=True)
    calibration_parser = sub.add_parser("calibrate", help="Freeze two blind reviewer-calibration packets with swapped labels")
    calibration_parser.add_argument("suite")
    calibration_parser.add_argument("--output", required=True)
    calibration_parser.add_argument("--seed", type=int, default=0)
    calibration_score_parser = sub.add_parser("calibration-score", help="Check external judgments against frozen calibration expectations")
    calibration_score_parser.add_argument("calibration")
    calibration_score_parser.add_argument("--judgments", action="append", required=True)
    paired_prepare = sub.add_parser("paired-prepare", help="Freeze resources, cases, configuration and the planned paired-run denominator")
    paired_prepare.add_argument("config")
    paired_prepare.add_argument("--output", required=True)
    paired_start = sub.add_parser("paired-start", help="Assign a planned offline run to a fresh researcher context")
    paired_start.add_argument("study")
    paired_start.add_argument("pair")
    paired_start.add_argument("--variant", choices=("a", "b"), required=True)
    paired_start.add_argument("--context", required=True)
    paired_start.add_argument("--config", required=True)
    paired_tool = sub.add_parser("paired-tool", help="Record an offline command against the assigned frozen skill and corpus")
    paired_tool.add_argument("study")
    paired_tool.add_argument("pair")
    paired_tool.add_argument("variant", choices=("a", "b"))
    paired_tool.add_argument("arguments", nargs=argparse.REMAINDER)
    paired_finish = sub.add_parser("paired-finish", help="Seal a run's report, stop reason, trace and available measurements")
    paired_finish.add_argument("study")
    paired_finish.add_argument("pair")
    paired_finish.add_argument("--variant", choices=("a", "b"), required=True)
    paired_finish.add_argument("--status", choices=("completed", "budget_exhausted", "failed"), required=True)
    paired_finish.add_argument("--reason", required=True)
    paired_finish.add_argument("--report")
    paired_finish.add_argument("--measurements")
    paired_blind = sub.add_parser("paired-blind", help="Prepare separate atomic-fact blind packets for the two planned reviewers")
    paired_blind.add_argument("study")
    paired_blind.add_argument("pair")
    paired_review = sub.add_parser("paired-review", help="Retain one immutable independent judgment per reviewer slot")
    paired_review.add_argument("study")
    paired_review.add_argument("pair")
    paired_review.add_argument("--reviewer", required=True)
    paired_review.add_argument("--judgments", required=True)
    paired_summary = sub.add_parser("paired-summary", help="Summarize all planned pairs, missing assessments, disagreements and measured costs")
    paired_summary.add_argument("study")
    init_parser = sub.add_parser("init", help="Create a durable task contract, or return the matching one")
    init_parser.add_argument("--deliverable", required=True)
    init_parser.add_argument("--target", required=True)
    init_parser.add_argument("--constraint", action="append", default=[])
    need_parser = sub.add_parser("need", help="Register a checklist item or update its explicit progress")
    need_parser.add_argument("id")
    need_parser.add_argument("--question")
    need_parser.add_argument("--proof")
    need_parser.add_argument("--priority", type=int, choices=range(1, 6))
    need_parser.add_argument("--state", choices=("open", "partial", "blocked", "answered"))
    need_parser.add_argument("--note")
    need_parser.add_argument("--evidence", action="append", dest="evidence_ids")
    failure_parser = sub.add_parser("failure", help="Record a task-wide or Need-specific failed attempt")
    failure_parser.add_argument("--need", help="Registered Need ID; omit for a task-wide tool or workflow failure")
    failure_parser.add_argument("--action", required=True)
    failure_parser.add_argument("--reason", required=True)
    stop_parser = sub.add_parser("stop", help="Save a stop reason; incomplete unless --complete is supplied")
    stop_parser.add_argument("--reason", required=True)
    stop_parser.add_argument("--complete", action="store_true")
    sub.add_parser("resume", help="Reactivate the existing task without clearing progress or failures")
    sub.add_parser("status", help="Show task progress, remaining Needs, sources and evidence")
    args = parser.parse_args()
    try:
        if args.command == "search":
            result = _ref_search(
                args.query,
                max(1, min(args.limit, 12)),
                args.target,
                args.artifact,
                args.proof,
                args.provider,
                need=args.need, refresh=args.refresh, retry_failed=args.retry_failed,
                gap=args.gap, corpus_path=args.corpus,
            )
        elif args.command == "search-batch":
            result = _search_batch(args.plan, args.limit, args.provider, workers=args.workers, refresh=args.refresh,
                                   retry_failed=args.retry_failed)
        elif args.command == "read":
            if args.corpus:
                with _ref_lock():
                    source = _load_refs().get(args.ref, args.ref)
                result = fixed_corpus.read(workspace, args.corpus, source, lambda value: _ref_read(
                    value, _join_terms(args.term), max(100, min(args.window, 2_000)), refresh=True))
            else:
                result = _ref_read(args.ref, _join_terms(args.term), max(100, min(args.window, 2_000)), refresh=args.refresh)
        elif args.command == "open":
            ref, version = fixed_corpus.saved_source(workspace, args.ref, args.version)
            result = _with_citation(workspace.open_source(
                ref, args.start_line, args.count, version
            ))
        elif args.command == "find":
            ref, version = fixed_corpus.saved_source(workspace, args.ref, args.version)
            result = _with_citation(workspace.excerpts(
                *workspace.require_source(ref, version),
                _join_terms(args.term), max(100, min(args.window, 2_000)),
                max_windows=args.limit, offset=args.offset, intro_if_missing=False,
            ))
        elif args.command == "evidence":
            ref, version = fixed_corpus.saved_source(workspace, args.ref, args.version)
            result = workspace.record_evidence(
                ref, args.need, args.claim, args.quote, args.relation,
                start_line=args.start_line, version=version,
            )
        elif args.command == "review":
            result = workspace.review_evidence(
                need=args.need, claim=args.claim, offset=args.offset, limit=args.limit,
            )
        elif args.command == "follow":
            result = _follow_papers(args.paper, args.direction, args.offset, args.limit,
                                    need=args.need, refresh=args.refresh)
        elif args.command == "section":
            result = reporting.set_section(workspace, args.id, args.title, args.needs,
                                            perspective=args.perspective, order=args.order, reason=args.reason)
        elif args.command == "outline":
            result = reporting.outline(workspace)
        elif args.command == "synthesis":
            result = reporting.save_synthesis(workspace, args.section, args.summary, args.evidence_ids,
                                               inferences=args.inference, limitations=args.limitation)
        elif args.command == "packet":
            result = reporting.packet(workspace, args.section, offset=args.offset, limit=args.limit)
        elif args.command == "audit":
            result = reporting.audit_report(workspace, args.report, offset=args.offset, limit=args.limit)
        elif args.command == "claims":
            result = claim_review.claims(workspace, reporting, args.report, judgments_path=args.judgments,
                                         schema_version=args.schema_version, offset=args.offset, limit=args.limit)
        elif args.command == "gap":
            result = claim_review.new_gap(workspace, reporting, args.report, args.claim, args.need,
                                          max_attempts=args.max_attempts)
        elif args.command == "gap-result":
            result = claim_review.gap_result(workspace, args.gap, args.outcome, args.reason, args.evidence)
        elif args.command == "compare":
            result = evaluation.prepare(workspace, reporting, args.case, args.a, args.b, args.output,
                                         seed=args.seed, a_run=args.a_run, b_run=args.b_run, semantic_review=claim_review)
        elif args.command == "case-pack":
            result = evaluation.pack_case(workspace, args.case, args.output)
        elif args.command == "score":
            result = evaluation.score(workspace, args.comparison, args.judgments)
        elif args.command == "calibrate":
            result = calibration.prepare(workspace, evaluation, args.suite, args.output, seed=args.seed)
        elif args.command == "calibration-score":
            result = calibration.score(workspace, args.calibration, args.judgments)
        elif args.command == "paired-prepare":
            result = paired_evaluation.prepare(workspace, evaluation, calibration, args.config, args.output)
        elif args.command == "paired-start":
            result = paired_evaluation.start(workspace, calibration, args.study, args.pair, args.variant, args.context, args.config)
        elif args.command == "paired-tool":
            result = paired_evaluation.run_tool(workspace, calibration, args.study, args.pair, args.variant, args.arguments)
        elif args.command == "paired-finish":
            result = paired_evaluation.finish(workspace, calibration, args.study, args.pair, args.variant,
                status=args.status, reason=args.reason, report_path=args.report, measurements_path=args.measurements)
        elif args.command == "paired-blind":
            result = paired_evaluation.blind(workspace, calibration, args.study, args.pair)
        elif args.command == "paired-review":
            result = paired_evaluation.review(workspace, calibration, args.study, args.pair, args.reviewer, args.judgments)
        elif args.command == "paired-summary":
            result = paired_evaluation.summary(workspace, calibration, args.study)
        elif args.command == "init":
            result = workspace.init_task(args.deliverable, args.target, args.constraint)
        elif args.command == "need":
            result = workspace.set_need(
                args.id, question=args.question, proof=args.proof, priority=args.priority,
                state=args.state, note=args.note, evidence_ids=args.evidence_ids,
            )
        elif args.command == "failure":
            result = workspace.record_failure(args.need, args.action, args.reason)
        elif args.command == "stop":
            result = workspace.stop_task(args.reason, complete=args.complete)
        elif args.command == "resume":
            workspace.resume_task()
            result = workspace.status()
        else:
            result = workspace.status()
        usage = base.metrics.snapshot()
        if usage is not None:
            result["run_usage"] = usage
        print(json.dumps(result, ensure_ascii=False))
        return 1 if (args.command == "search-batch" and result["status"] == "failed"
                     or args.command == "paired-tool" and result["returncode"] != 0) else 0
    except Exception as exc:
        print(json.dumps(_hide_urls({"error": str(exc), "run_usage": base.metrics.snapshot(),
                                    **({"provider_errors": exc.provider_errors} if isinstance(exc, SearchError) else {})}),
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())

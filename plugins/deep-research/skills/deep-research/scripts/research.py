#!/usr/bin/env python3
"""Conservative retriever: web search plus narrow academic and artifact lanes."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path


def _load_v1():
    path = Path(__file__).with_name("search_candidate.py")
    spec = importlib.util.spec_from_file_location("drbench_retriever_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reader helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v1 = _load_v1()
base = v1.base
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
    try:
        body, _ = base._get(url)
        root = ET.fromstring(body)
    except Exception:
        return []
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
    for card in v1._openalex(query, limit):
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
    anchor = PRACTITIONER_CONTEXT_RE.sub(" ", query)
    anchor = PRACTITIONER_OUTCOME_RE.sub(" ", anchor)
    anchor = PRACTITIONER_ARTIFACT_RE.sub(" ", anchor)
    anchor = re.sub(r"\bmedals?\b", " ", anchor, flags=re.IGNORECASE)
    anchor = re.sub(r"\s+", " ", anchor).strip(" -")
    return f"{anchor} solution".strip()


def _github_repositories(query: str, limit: int) -> list[dict]:
    params = {
        "q": _github_query(query),
        "sort": "stars",
        "order": "desc",
        "per_page": str(min(max(limit, 1), 10)),
    }
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(params)
    try:
        body, _ = base._get(url)
        rows = json.loads(body).get("items", [])
    except Exception:
        return []

    query_tokens = set(_query_words(query))
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
                    int(winner_claim),
                    int(placement is not None),
                    -(placement or 10_000),
                    int(outcome_proof),
                    overlap,
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
    try:
        body, _ = base._get(url)
    except Exception:
        return []
    soup = v1.BeautifulSoup(body, "html.parser")
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


def _search_base(query: str, limit: int = 8) -> dict:
    base._reserve("search")
    web_cards = _duckduckgo(query, max(8, limit))
    if len(web_cards) < 2:
        web_cards.extend(base._bing(query, max(8, limit)))
    if not v1._is_english_query(query):
        web_cards.extend(base._so360(query, max(8, limit)))
    practitioner = []
    topic_cards = []
    if _practitioner_search_enabled(query):
        topic_query = query
        state = _load_scope_state()
        if (
            state.get("status") == "closed"
            and state.get("competition_name")
            and _same_target(str(state.get("target") or ""), query)
        ):
            topic_query = f"{state.get('canonical_target') or state['target']} {query}"
        topic_cards = _kaggle_topic_cards(topic_query, max(4, limit // 2))
        practitioner.extend(topic_cards)
        practitioner.extend(_github_repositories(query, max(4, limit // 2)))
    scholarly = []
    if _academic_search_enabled():
        academic_limit = max(2, limit // 3)
        scholarly.extend(_arxiv(query, academic_limit))
        scholarly.extend(_openalex_exact(query, academic_limit))

    unique_scholarly = OrderedDict()
    for card in scholarly:
        title_key = re.sub(r"\W+", " ", card.get("title", "").casefold()).strip()
        if title_key and title_key not in unique_scholarly:
            unique_scholarly[title_key] = card
    scholarly = list(unique_scholarly.values())

    query_tokens = v1._tokens(query)
    cards = practitioner + scholarly + web_cards
    relevant = []
    for card in cards:
        searchable = " ".join(
            str(card.get(key) or "") for key in ("title", "snippet")
        )
        overlap = len(query_tokens & v1._tokens(searchable))
        if overlap >= min(3, len(query_tokens)):
            relevant.append(card)
    ranked = v1._rank(relevant, query, limit)
    if topic_cards:
        ranked = _merge_cards(topic_cards, ranked, limit)
    if "kaggle" in query.casefold() and KAGGLE_OFFICIAL_NEED_RE.search(query):
        metadata = _kaggle_official_card(query, query)
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
    result = {"query": query, "results": ranked}
    base._record_event("search", result)
    return result


def _clean_query(value: str) -> str:
    value = re.sub(r"\s+-\s+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _kaggle_json(url: str, max_bytes: int = 3_000_000) -> dict:
    if not base._url_ok(url):
        raise ValueError(f"blocked or unsupported URL: {url}")
    response = base.requests.get(
        url,
        headers={"User-Agent": base.UA, "Accept": "application/json"},
        timeout=base.TIMEOUT,
        allow_redirects=True,
    )
    if not base._url_ok(response.url):
        raise ValueError(f"redirected to blocked URL: {response.url}")
    response.raise_for_status()
    if len(response.content) > max_bytes:
        raise ValueError("Kaggle response exceeds size limit")
    value = response.json()
    return value if isinstance(value, dict) else {}


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


def _kaggle_official_card(target: str, query: str) -> dict | None:
    if "kaggle" not in f"{target} {query}".casefold():
        return None
    for slug in _kaggle_slug_candidates(target, query):
        url = KAGGLE_GET_COMPETITION + "?" + urllib.parse.urlencode(
            {"competitionName": slug}
        )
        try:
            row = _kaggle_json(url, 100_000)
        except Exception:
            continue
        card = _kaggle_card(row)
        if card and _same_target(target, f"{card['title']} {slug}"):
            return card

    try:
        listing = _kaggle_json(KAGGLE_LIST_COMPETITIONS + "?pageSize=1000")
    except Exception:
        return None
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


def _kaggle_topic_cards(value: str, limit: int) -> list[dict]:
    if "kaggle" not in value.casefold() or not (
        PRACTITIONER_ARTIFACT_RE.search(value) or PRACTITIONER_OUTCOME_RE.search(value)
    ):
        return []
    metadata = _kaggle_official_card(value, value)
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
        try:
            payload = _kaggle_json(url, 500_000)
        except Exception:
            break
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


def _open_scope(target: str) -> dict:
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


def _guard_scope_search(query: str, target: str, artifact: str) -> None:
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
    return query if _has_term(query, "writeup", "lessons learned") else f"{query} participant writeup lessons learned"


def _explicit_github_query(query: str) -> str:
    value = re.sub(r"\bsite\s*:\s*github\.com\b", " ", query, flags=re.IGNORECASE)
    value = re.sub(r"\bgithub\b", " ", value, flags=re.IGNORECASE)
    return _clean_query(value)


def _target_matched(card: dict, target: str) -> bool:
    target_terms = set(_query_words(target))
    if not target_terms:
        return False
    card_terms = set(
        _query_words(
            " ".join(
                str(card.get(key) or "") for key in ("title", "snippet", "url")
            )
        )
    )
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
) -> dict:
    if target is None and artifact is None and proof is None:
        return _search_base(query, limit)
    query = _clean_query(query)
    target = _clean_query(target or "")
    proof = _clean_query(proof or "")
    if not query or not target or artifact not in ARTIFACT_TYPES or not proof:
        raise ValueError(
            "query, target, proof, and a supported artifact must be non-empty"
        )
    _guard_scope_search(query, target, artifact)
    scope_gate = _uses_scope_gate(query, target, artifact)
    if scope_gate:
        _open_scope(target)
    effective_query = _route_query(query, artifact)
    result = _search_base(effective_query, limit)
    if scope_gate:
        official_card = _kaggle_official_card(target, query)
        if official_card:
            page_card = _kaggle_pages_card(official_card)
            official_cards = [
                card for card in (page_card, official_card) if card is not None
            ]
            result = dict(result)
            result["results"] = _merge_cards(
                official_cards, list(result.get("results", [])), limit
            )
            base._record_event(
                "official_candidates",
                {"target": target, "need": query, "results": official_cards},
            )
        _remember_scope_urls(target, result)
    artifact_cards = []
    topic_cards = [
        card for card in result.get("results", [])
        if card.get("engine") == "kaggle-competition-topics"
    ]
    if artifact in GITHUB_ARTIFACTS and not topic_cards:
        state = _load_scope_state()
        topic_target = target
        if (
            state.get("status") == "closed"
            and _same_target(str(state.get("target") or ""), target)
            and state.get("competition_name")
        ):
            topic_target = str(state.get("canonical_target") or target)
        topic_cards = _kaggle_topic_cards(
            f"{topic_target} {query}", min(5, limit)
        )
    if artifact in GITHUB_ARTIFACTS:
        github_query = _explicit_github_query(query)
        artifact_queries = [github_query]
        artifact_cards = [
            card for card in _github_repositories(github_query, min(5, limit))
            if _target_matched(card, target)
        ]
        state = _load_scope_state()
        fallback_target = str(state.get("canonical_target") or target)
        if len(artifact_cards) < 2 and fallback_target.casefold() != github_query.casefold():
            artifact_queries.append(fallback_target)
            fallback_cards = [
                card for card in _github_repositories(fallback_target, min(5, limit))
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
    result = dict(result)
    result["results"] = _merge_cards(
        topic_cards, _merge_cards(artifact_cards, list(result.get("results", [])), limit), limit
    )
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
    state = _load_scope_state()
    public_scope = _public_scope_state(state)
    if public_scope and _same_target(str(state.get("target") or ""), target):
        result["scope_state"] = public_scope
    return result


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
    owner, repository = parts
    api_url = "https://api.github.com/repos/" + urllib.parse.quote(
        f"{owner}/{repository}/readme", safe="/"
    )
    if not base._url_ok(api_url):
        raise ValueError(f"blocked or unsupported URL: {api_url}")
    response = base.requests.get(
        api_url,
        headers={
            "User-Agent": base.UA,
            "Accept": "application/vnd.github.raw+json",
        },
        timeout=base.TIMEOUT,
        allow_redirects=True,
    )
    if not base._url_ok(response.url):
        raise ValueError(f"redirected to blocked URL: {response.url}")
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("GitHub README exceeds size limit")
    text = v1._normalise_text(response.text)
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
        "extracted_chars": len(text),
        "source_type": "practitioner_artifact",
        "fallback": "github_readme_api",
        "failed_attempts": 0,
    }
    base._reserve("read")
    base._record_event("read", result)
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
    primary = (
        v1.BeautifulSoup(primary_html, "html.parser").get_text(" ", strip=True)
        if primary_html else ""
    )
    if primary and bodies:
        bodies = bodies[1:]
    sections = []
    for value in [primary, *bodies]:
        if value and value not in sections:
            sections.append(value)
    text = v1._normalise_text(
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
        "extracted_chars": len(text),
        "source_type": "practitioner_artifact",
        "fallback": "kaggle_topics_api",
        "failed_attempts": 0,
        "topic_id": topic_id,
        "author": author,
    }
    base._record_event("read", result)
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
            "extracted_chars": sum(len(str(row.get("content") or "")) for row in pages),
            "source_type": "official",
            "fallback": "none",
            "failed_attempts": 0,
            "official_page_names": sorted(set(names)),
            "competition_id": next(iter(competition_ids), ""),
        }
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
                "extracted_chars": len(text),
                "source_type": "official",
                "fallback": "kaggle_competition_metadata",
                "failed_attempts": len(errors),
                "official_page_names": [],
                "competition_id": metadata.get("id") or metadata.get("competitionId") or "",
            }
        except Exception as fallback_exc:
            raise RuntimeError("; ".join(errors + [str(fallback_exc)]))
    base._record_event("read", result)
    return result


def read_page(
    url: str, terms: list[str], window: int = 650, max_windows: int = 5
) -> dict:
    if _kaggle_topic_parts(url):
        result = _read_kaggle_topic(url, terms, window, max_windows)
    elif _kaggle_pages_url(url):
        result = _read_kaggle_pages(url, terms, window, max_windows)
    elif _kaggle_competition_slug(url):
        try:
            pages_url = _kaggle_official_pages_url(url)
            if not pages_url:
                raise RuntimeError("official competition metadata has no id")
        except Exception:
            result = v1.read_page(url, terms, window, max_windows)
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
            result = v1.read_page(url, terms, window, max_windows)
    else:
        result = v1.read_page(url, terms, window, max_windows)
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
        if str(ref).startswith("REF-") and str(url).startswith(("http://", "https://"))
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
    value = url.strip().split("#", 1)[0]
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


def _ref_search(
    query: str,
    limit: int,
    target: str | None,
    artifact: str | None,
    proof: str | None,
) -> dict:
    result = search(query, limit, target, artifact, proof)
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
        refs.update(discovered)
        _save_refs(dict(list(refs.items())[-256:]))
    public = _hide_urls({key: value for key, value in result.items() if key != "results"})
    public["results"] = rows
    public["next_action"] = (
        "These cards are unread leads, not evidence. Read a matching REF. "
        "Only literal values returned in allowed_citation_urls may be cited."
    )
    return public


def _ref_read(ref: str, terms: list[str], window: int) -> dict:
    with _ref_lock():
        url = _load_refs().get(ref, "")
    if not url:
        raise ValueError("Read requires a REF returned by Search in this run")
    try:
        result = read_page(url, terms, window)
    except Exception as exc:
        base._record_event(
            "ref_read_failed", {"ref": ref, "error_type": type(exc).__name__}
        )
        raise RuntimeError(f"Read failed for {ref}; choose another REF") from None
    result = dict(result)
    result["citation_url"] = str(result.get("url") or url)
    result["ref"] = ref
    with _ref_lock():
        citations = _load_citations()
        if result["citation_url"] not in citations:
            citations.append(result["citation_url"])
            _save_citations(citations[-64:])
        result["allowed_citation_urls"] = citations[-64:]
    return result


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
    search_parser.add_argument("--limit", type=int, default=8)
    read_parser = sub.add_parser("read")
    read_parser.add_argument("ref")
    read_parser.add_argument("--term", action="append", nargs="+", default=[])
    read_parser.add_argument("--window", type=int, default=650)
    args = parser.parse_args()
    try:
        if args.command == "search":
            result = _ref_search(
                args.query,
                max(1, min(args.limit, 12)),
                args.target,
                args.artifact,
                args.proof,
            )
        else:
            result = _ref_read(
                args.ref, _join_terms(args.term), max(100, min(args.window, 2_000))
            )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())

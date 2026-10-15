"""Build bounded follow-up queries from caller-assessed, saved source passages."""

from __future__ import annotations


def _text(value, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{field} must be non-empty text of at most {limit} characters")
    return value.strip()


def route_fields(row: dict, artifact_types) -> dict:
    fields = {key for key in ("target", "artifact", "proof") if key in row}
    if not fields:
        return {}
    if len(fields) != 3:
        raise ValueError("target, artifact, and proof must be supplied together")
    route = {key: _text(row[key], key, 1_000) for key in ("target", "artifact", "proof")}
    if route["artifact"] not in artifact_types:
        raise ValueError("unsupported search artifact")
    return route


def _join_query(parts: list[str]) -> str:
    words = []
    for part in parts:
        incoming = part.split()
        current = [word.casefold() for word in words]
        candidate = [word.casefold() for word in incoming]
        if not words:
            words = incoming
        elif any(current[start:start + len(candidate)] == candidate
                 for start in range(len(current) - len(candidate) + 1)):
            continue
        elif any(candidate[start:start + len(current)] == current
                 for start in range(len(candidate) - len(current) + 1)):
            words = incoming
        else:
            # Whole tokens keep distinct technical names such as C and C++ intact.
            overlap = next((size for size in range(min(len(current), len(candidate)), 0, -1)
                            if current[-size:] == candidate[:size]), 0)
            words.extend(incoming[overlap:])
    return " ".join(words)


def build_plan(workspace, feedback: dict, source_policy: str, artifact_types) -> dict:
    if not isinstance(feedback, dict) or set(feedback) - {"original_query", "gap", "need", "sources"}:
        raise ValueError("feedback accepts only original_query, gap, optional need, and sources")
    original = _text(feedback.get("original_query"), "original_query", 1_000)
    gap = _text(feedback.get("gap"), "gap", 500)
    need = None
    if "need" in feedback:
        need = _text(feedback["need"], "need", 64)
        workspace.require_need(need)
    sources = feedback.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 5:
        raise ValueError("feedback requires 1-5 saved source passages")

    searches = [{"query": original, "reason": "Retain the original query as a retrieval anchor"}]
    checked, seen = [], {(original.casefold(), ())}
    for source in sources:
        if not isinstance(source, dict) or set(source) - {
            "ref", "version", "quote", "terms", "role", "reason", "target", "artifact", "proof",
        }:
            raise ValueError("each feedback source accepts ref, version, quote, terms, role, reason, "
                             "and optional target/artifact/proof")
        ref = _text(source.get("ref"), "ref", 64)
        role = source.get("role")
        if role not in ("relevant", "bridge", "misleading"):
            raise ValueError("feedback role must be relevant, bridge, or misleading")
        reason = _text(source.get("reason"), "reason", 500)
        quote = _text(source.get("quote"), "quote", 4_000)
        route = route_fields(source, artifact_types)
        if route and route["target"] not in quote:
            raise ValueError("feedback target must occur verbatim in its quoted passage")
        terms = source.get("terms")
        if not isinstance(terms, list) or not 1 <= len(terms) <= 4:
            raise ValueError("each feedback source requires 1-4 verbatim terms")
        terms = list(dict.fromkeys(_text(term, "term", 160) for term in terms))
        if any(term not in quote for term in terms):
            raise ValueError("feedback terms must occur verbatim in their quoted passage")
        version = source.get("version")
        if "version" in source:
            version = _text(version, "version", 64)
        metadata, body = workspace.require_source(ref, version)
        start = body.find(quote)
        if start < 0:
            raise ValueError("feedback quote is not present verbatim in the saved source")
        if source_policy == "mixed" and not str(metadata.get("url", "")).startswith(("https://", "http://")):
            raise ValueError("mixed policy cannot automatically expand queries from local source text; "
                             "rewrite a public search query instead")
        record = {
            "ref": ref, "version": metadata.get("snapshot_id", metadata["content_hash"]),
            "quote": quote, "terms": terms, "role": role, "reason": reason,
            "quote_verified": True, "relevance_assessed_by": "caller",
            "content_scope": metadata.get("content_scope"), "source_truncated": metadata["truncated"],
            "locator": workspace._locator(body, start, start + len(quote), metadata),
            **route,
        }
        # Bridge queries may need to drop the original entity to expose a missing relation.
        if role != "misleading":
            anchor = route.get("target", original)
            parts = ([anchor] if role == "relevant" else []) + terms + [gap]
            query = _join_query(parts)
            record["query"] = query
            identity = (query.casefold(), tuple(route.values()))
            if identity not in seen:
                searches.append({"query": query, "reason": reason, **route})
                seen.add(identity)
        checked.append(record)

    if need is not None:
        for search in searches:
            search["need"] = need
    return {
        "searches": searches,
        "feedback": {
            "original_query": original, "gap": gap, **({"need": need} if need else {}),
            "sources": checked,
            "note": "Quotes and term origins are checked; relevance is caller-assessed. "
                    "Bridge passages guide discovery and do not establish the missing claim. "
                    "Read results against the original Need before judging progress.",
        },
    }

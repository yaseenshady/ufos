"""
UFO/UAP Article Fact-Checker & News Labeler
============================================
Processes UFO/UAP news articles at scale using the GitHub Copilot SDK.

For every article the script produces:
  - A structured JSON label file   →  reports/labels/<slug>.json
  - A Markdown fact-check report   →  reports/articles/<slug>.md

After all articles are processed an aggregate summary is written to:
  reports/summary_<timestamp>.md

Usage:
    # Process the default sources list (sources.txt)
    python labeler.py

    # Process specific URLs
    python labeler.py --urls https://example.com/story https://other.com/article

    # Use a custom sources file
    python labeler.py --sources my_urls.txt

    # Control concurrency (default: 2 parallel sessions)
    python labeler.py --concurrency 3

    # Skip articles that already have a label file
    python labeler.py --skip-existing

Authentication (choose one):
    - Sign in via the Copilot CLI beforehand  (gh copilot auth login)
    - Set COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN env var
    - BYOK: configure a custom provider in the SDK
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from copilot import CopilotClient, SubprocessConfig
from copilot.generated.session_events import AssistantMessageData, SessionIdleData
from copilot.session import PermissionHandler

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_SOURCES = Path("sources.txt")
DEFAULT_REPORTS_DIR = Path("reports")

# ---------------------------------------------------------------------------
# Fact-check / labeling prompt
# ---------------------------------------------------------------------------

LABEL_PROMPT = """\
You are an investigative fact-checker and research analyst specialising in
UFO/UAP (Unidentified Anomalous Phenomena) news coverage.

Produce both outputs below NOW. Do NOT ask clarifying questions or wait for
confirmation. Proceed immediately with the best available information.

Article URL: {url}

──────────────────────────────────────────────────────────────────────────────
INSTRUCTIONS (execute without pausing)
──────────────────────────────────────────────────────────────────────────────
STEP 1  Fetch and read the full article at the URL above.
        If the URL is inaccessible, search the web for the article title or
        related coverage and use the best available public sources. Do not stop.
STEP 2  Identify every factual claim, image, and piece of evidence.
STEP 3  Cross-reference claims against official records
        (war.gov/ufo, aaro.mil, nasa.gov, congressional testimony, news archives).
STEP 4  Write the JSON block and the Markdown report immediately.
        Even if the URL is unreachable, complete the full analysis using web search.

──────────────────────────────────────────────────────────────────────────────
OUTPUT FORMAT
──────────────────────────────────────────────────────────────────────────────

```json
{{
  "url": "<article URL>",
  "title": "<article headline>",
  "source": "<publication name>",
  "date_published": "<ISO-8601 date or null>",
  "credibility_score": <integer 1-10>,
  "credibility_label": "<High|Medium|Low|Unverified>",
  "categories": [
    "<Government Disclosure|Eyewitness Report|Image/Video Analysis|Scientific Study|Hoax|Speculation|Historical Record|Other>"
  ],
  "evidence_types": [
    "<Official Documents|Images|Video|Radar/Sensor Data|Witness Accounts|Expert Opinion|None>"
  ],
  "image_count": <integer>,
  "claims": [
    {{
      "claim": "<exact claim from article>",
      "verdict": "<Verified|Plausible|Unverified|Contested|False>",
      "explanation": "<one-sentence rationale with source>"
    }}
  ],
  "summary": "<2-3 sentence neutral summary>",
  "key_findings": ["<finding 1>", "<finding 2>"]
}}
```

## Fact-Check Report: <article headline>

**URL:** <article URL>
**Source:** <publication>
**Date:** <date>
**Credibility:** <label> (<score>/10)

### Summary
<Neutral 2-3 sentence summary of the article>

### Evidence & Images
<Describe each image and piece of evidence found in the article, including
 any visible metadata, caption text, or context provided by the author>

### Claim-by-Claim Analysis
<For each claim: state it verbatim, give the verdict, and explain the reasoning
 with reference to supporting or contradicting sources>

### Cross-Reference & Context
<How does this article relate to other known sources, official disclosures,
 the war.gov/ufo portal, or the broader UAP research record?>

### Verdict
<Overall assessment of the article's accuracy and journalistic quality>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(url: str) -> str:
    """Convert a URL into a safe, readable filename slug (max 120 chars)."""
    parsed = urlparse(url)
    raw = (parsed.netloc + parsed.path).strip("/")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-")
    return slug[:120]


def _build_client() -> CopilotClient:
    import os

    token = (
        os.environ.get("COPILOT_GITHUB_TOKEN")
        or os.environ.get("GITHUB_COPILOT_API_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
    )
    return CopilotClient(SubprocessConfig(github_token=token or None))


def _model() -> str:
    import os

    return os.environ.get("COPILOT_MODEL", "gpt-4.1")


def _extract_json(text: str) -> dict | None:
    """Pull the first ```json … ``` block from a model response."""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Per-article processing
# ---------------------------------------------------------------------------


async def process_article(
    url: str,
    client: CopilotClient,
    labels_dir: Path,
    articles_dir: Path,
    skip_existing: bool,
) -> dict:
    """Fact-check and label a single article. Returns the label dict."""
    slug = _slug(url)
    label_path = labels_dir / f"{slug}.json"
    report_path = articles_dir / f"{slug}.md"

    if skip_existing and label_path.exists():
        print(f"  [skip]       {url}")
        return json.loads(label_path.read_text(encoding="utf-8"))

    print(f"  [processing] {url}")

    parts: list[str] = []

    async with await client.create_session(
        on_permission_request=PermissionHandler.approve_all,
        model=_model(),
    ) as session:
        idle = asyncio.Event()

        def on_event(event) -> None:
            match event.data:
                case AssistantMessageData() as data:
                    parts.append(data.content)
                case SessionIdleData():
                    idle.set()

        session.on(on_event)
        await session.send(LABEL_PROMPT.format(url=url))
        await idle.wait()

    full_response = "".join(parts)

    # Persist the Markdown fact-check report
    report_path.write_text(full_response, encoding="utf-8")

    # Extract and persist JSON labels
    label_data = _extract_json(full_response)
    if label_data is None:
        label_data = {
            "url": url,
            "title": None,
            "source": None,
            "date_published": None,
            "credibility_score": None,
            "credibility_label": "Unverified",
            "categories": [],
            "evidence_types": [],
            "image_count": 0,
            "claims": [],
            "summary": full_response[:500],
            "key_findings": [],
            "_parse_error": "JSON block not found in model response",
        }

    label_data["_report_path"] = str(report_path)
    label_data["_processed_at"] = datetime.now(tz=timezone.utc).isoformat()

    label_path.write_text(
        json.dumps(label_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"  [done]       {url}  →  {label_path.name}")
    return label_data


# ---------------------------------------------------------------------------
# Aggregate summary report
# ---------------------------------------------------------------------------


def _write_summary(labels: list[dict], summary_path: Path) -> None:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total = len(labels)

    credibility_counts: dict[str, int] = {}
    all_categories: dict[str, int] = {}
    all_evidence: dict[str, int] = {}
    verified = unverified = 0

    for label in labels:
        cl = label.get("credibility_label") or "Unverified"
        credibility_counts[cl] = credibility_counts.get(cl, 0) + 1

        for cat in label.get("categories", []):
            all_categories[cat] = all_categories.get(cat, 0) + 1

        for ev in label.get("evidence_types", []):
            all_evidence[ev] = all_evidence.get(ev, 0) + 1

        for claim in label.get("claims", []):
            verdict = claim.get("verdict", "")
            if verdict in ("Verified", "Plausible"):
                verified += 1
            elif verdict in ("Unverified", "Contested", "False"):
                unverified += 1

    def _bar(count: int, total: int, width: int = 20) -> str:
        filled = round(width * count / total) if total else 0
        return "█" * filled + "░" * (width - filled)

    lines = [
        "# UFO/UAP News Labeling — Aggregate Summary",
        "",
        f"*Generated:* {now}  ",
        f"*Articles processed:* {total}  ",
        "",
        "---",
        "",
        "## Credibility Distribution",
        "",
    ]

    for label_name in ("High", "Medium", "Low", "Unverified"):
        count = credibility_counts.get(label_name, 0)
        pct = round(100 * count / total) if total else 0
        lines.append(f"- **{label_name}**: {count} ({pct}%) `{_bar(count, total)}`")

    lines += [
        "",
        "## Top Categories",
        "",
    ]
    for cat, count in sorted(all_categories.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"- {cat}: **{count}**")

    lines += [
        "",
        "## Evidence Types Seen",
        "",
    ]
    for ev, count in sorted(all_evidence.items(), key=lambda x: -x[1]):
        lines.append(f"- {ev}: **{count}**")

    lines += [
        "",
        "## Claims Overview",
        "",
        f"- ✅ Verified / Plausible: **{verified}**",
        f"- ❌ Unverified / Contested / False: **{unverified}**",
        "",
        "---",
        "",
        "## Article Index",
        "",
        "| # | Title | Source | Credibility | Score |",
        "|---|-------|--------|-------------|-------|",
    ]

    for i, label in enumerate(labels, 1):
        title = (label.get("title") or label.get("url") or "—")[:80]
        source = label.get("source") or "—"
        cred = label.get("credibility_label") or "—"
        score = label.get("credibility_score")
        score_str = f"{score}/10" if score is not None else "—"
        lines.append(f"| {i} | {title} | {source} | {cred} | {score_str} |")

    summary_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------


async def run_batch(
    urls: list[str],
    reports_dir: Path,
    concurrency: int,
    skip_existing: bool,
) -> list[dict]:
    """Process all URLs, writing per-article files and an aggregate summary."""
    labels_dir = reports_dir / "labels"
    articles_dir = reports_dir / "articles"
    labels_dir.mkdir(parents=True, exist_ok=True)
    articles_dir.mkdir(parents=True, exist_ok=True)

    print(f"[*] {len(urls)} article(s) queued  |  concurrency={concurrency}\n")

    sem = asyncio.Semaphore(concurrency)
    results: list[dict | None] = [None] * len(urls)

    async with _build_client() as client:

        async def bounded(i: int, url: str) -> None:
            async with sem:
                results[i] = await process_article(
                    url, client, labels_dir, articles_dir, skip_existing
                )

        await asyncio.gather(*(bounded(i, u) for i, u in enumerate(urls)))

    collected = [r for r in results if r is not None]

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    summary_path = reports_dir / f"summary_{ts}.md"
    _write_summary(collected, summary_path)
    print(f"\n[+] Aggregate summary  →  {summary_path}")

    return collected


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="UFO/UAP article fact-checker and news labeler — GitHub Copilot SDK",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--urls",
        nargs="+",
        metavar="URL",
        help="One or more article URLs (overrides --sources)",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        default=DEFAULT_SOURCES,
        metavar="FILE",
        help=f"Text file with one URL per line (default: {DEFAULT_SOURCES})",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=DEFAULT_REPORTS_DIR,
        metavar="DIR",
        help=f"Output directory (default: {DEFAULT_REPORTS_DIR})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        metavar="N",
        help="Max parallel Copilot sessions (default: 2)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip articles whose label file already exists",
    )
    return parser.parse_args()


def _load_urls(args: argparse.Namespace) -> list[str]:
    if args.urls:
        return list(args.urls)

    sources_file: Path = args.sources
    if not sources_file.exists():
        print(f"[!] Sources file not found: {sources_file}", file=sys.stderr)
        sys.exit(1)

    urls = [
        line.strip()
        for line in sources_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not urls:
        print(f"[!] No URLs found in {sources_file}", file=sys.stderr)
        sys.exit(1)

    return urls


def main() -> None:
    args = _parse_args()
    urls = _load_urls(args)
    asyncio.run(
        run_batch(
            urls=urls,
            reports_dir=args.reports_dir,
            concurrency=args.concurrency,
            skip_existing=args.skip_existing,
        )
    )
    print("Done.\n")


if __name__ == "__main__":
    main()

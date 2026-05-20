# UFO/UAP Research & Fact-Check System

A two-script toolkit that uses the **GitHub Copilot SDK** to programmatically
research and fact-check UFO/UAP content at scale.

---

## Scripts

### `research.py` — Deep research on war.gov/ufo

Runs a single, in-depth Copilot session that:

- Browses the entire [war.gov/ufo](https://www.war.gov/ufo/) portal
- Inventories every document and image
- Fact-checks each image and key claim
- Writes a comprehensive Markdown report to `reports/`

```bash
python research.py
```

### `labeler.py` — Batch fact-checker & news labeler (at scale)

Processes many UFO/UAP article URLs concurrently. For each article it:

- Fetches and reads the full article via the Copilot agent
- Extracts all images, evidence, and factual claims
- Assigns structured labels (credibility score/label, categories, evidence types)
- Cross-references claims against official sources
- Writes a per-article JSON label file and Markdown fact-check report
- Generates an aggregate summary across all articles

```bash
# Process the default sources list (sources.txt)
python labeler.py

# Process specific URLs
python labeler.py --urls https://example.com/story https://other.com/article

# Use a custom sources file
python labeler.py --sources my_urls.txt

# Run 3 sessions in parallel and skip already-processed articles
python labeler.py --concurrency 3 --skip-existing
```

**Output layout:**

```
reports/
├── labels/
│   └── <slug>.json          # structured labels for each article
├── articles/
│   └── <slug>.md            # full Markdown fact-check report
└── summary_<timestamp>.md   # aggregate credibility & category summary
```

---

## Installation

```bash
pip install github-copilot-sdk
```

## Authentication

Choose one of:

| Method | How |
|--------|-----|
| GitHub Copilot CLI login | `gh copilot auth login` before running |
| Environment variable | `export COPILOT_GITHUB_TOKEN=<token>` |
| BYOK | Set `COPILOT_MODEL` + configure provider in SDK |

## Adding more sources

Edit `sources.txt` — one URL per line, `#` for comments — then re-run
`labeler.py`. Already-processed articles are skipped with `--skip-existing`.

## Label schema

Each `reports/labels/<slug>.json` file contains:

```json
{
  "url": "...",
  "title": "...",
  "source": "...",
  "date_published": "...",
  "credibility_score": 7,
  "credibility_label": "Medium",
  "categories": ["Government Disclosure"],
  "evidence_types": ["Official Documents", "Images"],
  "image_count": 3,
  "claims": [
    {
      "claim": "...",
      "verdict": "Verified",
      "explanation": "..."
    }
  ],
  "summary": "...",
  "key_findings": ["..."]
}
```
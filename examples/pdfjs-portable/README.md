# PDF.js Find reference

This developer-authored Pipeline uses the pinned PDF.js viewer in a fresh 1280 × 720 Chromium context. It finds the toolbar's magnifying glass, enters the search term, waits for the first selected highlight to appear, then presses Enter to advance. The grader checks that the second result is visibly selected on the release notes page in two differently paginated PDFs.

```bash
uv run python integration/agent/regrade.py --target pdfjs \
  --project examples/pdfjs-portable --output work/pdfjs-replay
```

The example uses vibe-maa's browser controller. The Pipeline nodes and image are standard MaaFramework resources.

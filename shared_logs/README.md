Place raw trade log CSV files here (one per token) to generate summary reports. You can also nest them inside subfolders (for example `shared_logs/logs/XRPUSDC.csv`).

Use `python scripts/review_latest.py shared_logs --output-dir docs/reviews --combined-output docs/shared_logs_review.md` to refresh the Markdown reports for every CSV within this directory tree.

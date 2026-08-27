# flight-watch

Checks Naver domestic flight availability (CJU→GMP 2026-09-26, 2 adults, YC)
every 10 minutes via GitHub Actions and sends an ntfy.sh push when seats appear.

- Watch conditions: constants at the top of `check.py`
- Push channel: repository secret `NTFY_TOPIC`
- To stop: disable the `flight-watch` workflow in the Actions tab (or delete this repo)

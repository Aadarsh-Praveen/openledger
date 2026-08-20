"""Thin Socrata HTTP client: retry-with-backoff, count(), and a paginated fetch."""

import logging
import time

import requests

from ingest.config import BASE_URL, HEADERS, MAX_RETRIES, PAGE_SIZE, RETRY_BACKOFF_BASE_SECONDS

log = logging.getLogger("ingest.socrata")


def get(params, max_retries=MAX_RETRIES):
    """GET with retry-with-backoff on non-200. Logs every retry."""
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=60)
        except requests.RequestException as e:
            if attempt > max_retries:
                raise
            wait = RETRY_BACKOFF_BASE_SECONDS ** attempt
            log.warning(f"Request exception (attempt {attempt}/{max_retries}): {e}. Retrying in {wait}s.")
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            return resp

        if attempt > max_retries:
            resp.raise_for_status()

        wait = RETRY_BACKOFF_BASE_SECONDS ** attempt
        log.warning(
            f"Non-200 response (status={resp.status_code}, attempt {attempt}/{max_retries}): "
            f"{resp.text[:200]}. Retrying in {wait}s."
        )
        time.sleep(wait)


def count(where_clause):
    """$select=count(*) for a given $where clause."""
    resp = get({"$select": "count(*)", "$where": where_clause})
    return int(resp.json()[0]["count"])


def fetch_page(where_clause, order_clause, limit, offset, select_clause):
    # select_clause is required, not defaulted: Socrata's response to a bare query
    # silently omits :updated_at and other fields rather than erroring, so every
    # production call must pass it explicitly. See ingest/schema.py select_clause().
    resp = get({
        "$select": select_clause,
        "$where": where_clause,
        "$order": order_clause,
        "$limit": limit,
        "$offset": offset,
    })
    return resp.json()


def paginate(where_clause, order_clause, select_clause, page_size=PAGE_SIZE):
    """Yields successive pages (lists of row dicts) for a $where-bounded query,
    walking $offset within the window. Caller is responsible for keeping each
    window's offset range small (chunk by date range upstream)."""
    offset = 0
    while True:
        page = fetch_page(where_clause, order_clause, page_size, offset, select_clause)
        if not page:
            return
        yield page
        if len(page) < page_size:
            return
        offset += page_size

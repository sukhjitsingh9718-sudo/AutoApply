"""LinkedIn job search engine using Playwright.

Implements: FR-044 (LinkedIn search).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Iterator
from urllib.parse import quote_plus

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from bot.search.base import BaseSearcher, RawJob

if TYPE_CHECKING:
    from config.settings import SearchCriteria

logger = logging.getLogger(__name__)

LINKEDIN_SEARCH_URL = "https://www.linkedin.com/jobs/search/"


class LinkedInSearcher(BaseSearcher):
    """Search LinkedIn jobs via browser automation."""

    def search(self, criteria: SearchCriteria, page=None) -> Iterator[RawJob]:
        """Search LinkedIn for jobs matching criteria.

        Args:
            criteria: Search parameters.
            page: Playwright Page instance.

        Yields:
            RawJob objects for each job found.
        """
        if page is None:
            logger.warning("LinkedInSearcher requires a Playwright page")
            return

        max_results = getattr(criteria, "max_results_per_search", 100)
        found = 0

        for title in criteria.job_titles:
            for location in criteria.locations:
                if found >= max_results:
                    return

                try:
                    yield from self._search_page(
                        page, title, location, criteria, max_results - found
                    )
                except Exception as e:
                    logger.error(
                        "LinkedIn search failed for '%s' in '%s': %s",
                        title, location, e,
                    )

    def _search_page(
        self, page, title: str, location: str, criteria, remaining: int
    ) -> Iterator[RawJob]:
        """Search a single title+location combination."""
        url = (
            f"{LINKEDIN_SEARCH_URL}"
            f"?keywords={quote_plus(title)}"
            f"&location={quote_plus(location)}"
        )
        if criteria.remote_only:
            url += "&f_WT=2"  # LinkedIn remote filter

        logger.info("LinkedIn: searching '%s' in '%s'", title, location)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)

        # LinkedIn may redirect to login/authwall asynchronously after the
        # initial DOMContentLoaded event. Wait for either authentication
        # redirect or usable search-result markup. This check is intentionally
        # non-destructive: it does not consume job-card query results.
        try:
            page.wait_for_function(
                """() => {
                    const url = window.location.href.toLowerCase();
                    if (url.includes("login") || url.includes("authwall")) {
                        return true;
                    }

                    return Boolean(
                        document.querySelector(
                            ".jobs-search-results__list-item, "
                            + ".job-card-container, "
                            + "[data-occludable-job-id]"
                        )
                    );
                }""",
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            # A timeout simply means neither condition became ready.
            # The normal no-results handling below will deal with that case.
            pass

        final_url = page.url.lower()
        if "login" in final_url or "authwall" in final_url:
            logger.warning(
                "LinkedIn requires login. Please log in via the browser "
                "and restart the bot."
            )
            return

        found = 0
        page_num = 0

        while found < remaining:
            job_cards = page.query_selector_all(
                ".jobs-search-results__list-item, "
                ".job-card-container, "
                "[data-occludable-job-id]"
            )

            if not job_cards:
                logger.info("LinkedIn: no job cards found on page %d", page_num)
                break

            for card in job_cards:
                if found >= remaining:
                    return

                try:
                    raw_job = self._extract_job(page, card)
                    if raw_job:
                        found += 1
                        yield raw_job
                except Exception as e:
                    logger.debug("LinkedIn: failed to extract job card: %s", e)

            # Try next page
            page_num += 1
            next_btn = page.query_selector(
                "button[aria-label='Next'], "
                ".artdeco-pagination__button--next"
            )
            if not next_btn or not next_btn.is_enabled():
                break

            next_btn.click()
            time.sleep(2)

    def _extract_job(self, page, card) -> RawJob | None:
        """Extract job details from a LinkedIn job card."""
        # Click the card to load the detail panel
        try:
            card.click()
            time.sleep(1)
        except Exception as e:
            logger.debug("LinkedIn: failed to click job card: %s", e)
            return None

        # Extract from the detail panel
        title_el = page.query_selector(
            ".job-details-jobs-unified-top-card__job-title, "
            ".jobs-unified-top-card__job-title, "
            "h2.t-24"
        )
        company_el = page.query_selector(
            ".job-details-jobs-unified-top-card__company-name, "
            ".jobs-unified-top-card__company-name, "
            "a.ember-view.t-black.t-normal"
        )
        location_el = page.query_selector(
            ".job-details-jobs-unified-top-card__primary-description-container "
            ".tvm__text, "
            ".jobs-unified-top-card__bullet"
        )
        desc_el = page.query_selector(
            ".jobs-description__content, "
            ".jobs-box__html-content, "
            "#job-details"
        )

        title = title_el.inner_text().strip() if title_el else None
        company = company_el.inner_text().strip() if company_el else None
        location_text = location_el.inner_text().strip() if location_el else ""
        description = desc_el.inner_text().strip() if desc_el else ""

        if not title or not company:
            return None

        # Get job ID from card or URL
        job_id = card.get_attribute("data-occludable-job-id") or ""
        if not job_id:
            # Try extracting from URL
            current_url = page.url
            if "currentJobId=" in current_url:
                job_id = current_url.split("currentJobId=")[1].split("&")[0]

        if not job_id:
            return None

        # Salary (if shown)
        salary_el = page.query_selector(
            ".job-details-jobs-unified-top-card__job-insight--highlight, "
            ".salary-main-rail__compensation-value"
        )
        salary = salary_el.inner_text().strip() if salary_el else None

        # Apply URL
        apply_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

        return RawJob(
            title=title,
            company=company,
            location=location_text,
            salary=salary,
            description=description,
            apply_url=apply_url,
            platform="linkedin",
            external_id=f"linkedin-{job_id}",
            posted_at=None,
        )

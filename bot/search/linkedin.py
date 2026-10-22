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
                    for raw_job in self._search_page(
                        page, title, location, criteria, max_results - found
                    ):
                        found += 1
                        yield raw_job

                        if found >= max_results:
                            return
                except Exception as e:
                    logger.error(
                        "LinkedIn search failed for '%s' in '%s': %s",
                        title, location, e,
                    )

    def _search_page(
        self, page, title: str, location: str, criteria, remaining: int
    ) -> Iterator[RawJob]:
        """Search a single title+location combination across LinkedIn result pages."""
        base_url = (
            f"{LINKEDIN_SEARCH_URL}"
            f"?keywords={quote_plus(title)}"
            f"&location={quote_plus(location)}"
        )
        if criteria.remote_only:
            base_url += "&f_WT=2"

        logger.info("LinkedIn: searching '%s' in '%s'", title, location)

        found = 0
        page_num = 0
        seen_job_ids: set[str] = set()

        # LinkedIn job search pages conventionally advance in batches of 25.
        # Use the URL offset instead of depending on the pagination button being
        # present in the currently rendered/virtualized DOM.
        while found < remaining:
            offset = page_num * 25
            url = f"{base_url}&start={offset}"

            logger.info(
                "LinkedIn: loading results page %d (offset=%d)",
                page_num + 1,
                offset,
            )

            page.goto(url, wait_until="domcontentloaded", timeout=30000)

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
                pass

            final_url = page.url.lower()
            if "login" in final_url or "authwall" in final_url:
                logger.warning(
                    "LinkedIn requires login. Please log in via the browser "
                    "and restart the bot."
                )
                return

            # Give LinkedIn's virtualized result list a chance to populate.
            for _ in range(6):
                page.evaluate(
                    """() => {
                        const el =
                            document.querySelector('.jobs-search-results-list') ||
                            document.querySelector('.scaffold-layout__list') ||
                            document.scrollingElement;
                        if (el) {
                            el.scrollTop = el.scrollHeight;
                        }
                    }"""
                )
                time.sleep(0.5)

            job_cards = page.query_selector_all(
                ".jobs-search-results__list-item, "
                ".job-card-container, "
                "[data-occludable-job-id]"
            )

            if not job_cards:
                logger.info(
                    "LinkedIn: no job cards found on results page %d",
                    page_num + 1,
                )
                break

            new_jobs_on_page = 0

            for card in job_cards:
                if found >= remaining:
                    return

                try:
                    raw_job = self._extract_job(page, card)
                    if not raw_job:
                        continue

                    if raw_job.external_id in seen_job_ids:
                        continue

                    seen_job_ids.add(raw_job.external_id)
                    found += 1
                    new_jobs_on_page += 1
                    yield raw_job

                except Exception as e:
                    logger.debug(
                        "LinkedIn: failed to extract job card: %s",
                        e,
                    )

            logger.info(
                "LinkedIn: page %d yielded %d new jobs (%d total)",
                page_num + 1,
                new_jobs_on_page,
                found,
            )

            # If LinkedIn returned a page but none of its jobs were new,
            # continuing offsets would normally just repeat the same results.
            if new_jobs_on_page == 0:
                break

            page_num += 1

            # Safety bound: enough pages to satisfy the normal 100-result cap
            # without risking an endless loop if LinkedIn ignores `start`.
            if page_num >= 10:
                logger.info("LinkedIn: pagination safety limit reached")
                break

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

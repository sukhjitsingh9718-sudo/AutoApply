"""LinkedIn Easy Apply automation.

Implements: FR-047 (LinkedIn Easy Apply).
"""

from __future__ import annotations

import logging

from bot.apply.base import ApplyResult, BaseApplier

logger = logging.getLogger(__name__)


class LinkedInApplier(BaseApplier):
    """Automate LinkedIn Easy Apply submissions."""

    def _do_apply(
        self, job, resume_pdf_path, cover_letter_text, profile
    ) -> ApplyResult:
        logger.info("LinkedIn: applying to %s at %s", job.raw.title, job.raw.company)
        self._safe_goto(job.raw.apply_url)
        self._random_pause(1, 3)

        if self._detect_captcha():
            return ApplyResult(
                success=False, captcha_detected=True,
                error_message="CAPTCHA detected",
            )

        # Find and click Easy Apply button with explicit wait
        easy_apply_btn = self._wait_and_query(
            "button.jobs-apply-button, "
            "button[aria-label*='Easy Apply'], "
            ".jobs-apply-button--top-card",
            timeout=8000,
        )

        if not easy_apply_btn:
            try:
                buttons = self.page.locator("button")
                visible_buttons = []

                for i in range(min(buttons.count(), 80)):
                    btn = buttons.nth(i)
                    try:
                        if not btn.is_visible():
                            continue

                        text = (btn.inner_text() or "").strip().replace("\n", " ")
                        aria = btn.get_attribute("aria-label") or ""
                        classes = btn.get_attribute("class") or ""

                        if text or aria:
                            visible_buttons.append(
                                f"text={text[:100]!r} "
                                f"aria={aria[:100]!r} "
                                f"class={classes[:140]!r}"
                            )
                    except Exception:
                        pass

                logger.warning(
                    "LinkedIn: Easy Apply button not found. Current URL: %s",
                    self.page.url,
                )

                if visible_buttons:
                    logger.warning(
                        "LinkedIn: visible buttons: %s",
                        " || ".join(visible_buttons[:30]),
                    )
                else:
                    logger.warning(
                        "LinkedIn: no visible buttons detected on job page"
                    )

            except Exception as diag_error:
                logger.debug(
                    "LinkedIn: failed to inspect job-page buttons: %s",
                    diag_error,
                )

            return ApplyResult(
                success=False,
                manual_required=True,
                error_message="Easy Apply button not found — external application required",
            )

        easy_apply_btn.click()
        self._random_pause(1, 2)

        logger.info("LinkedIn: Easy Apply modal opened")

        # Process multi-step Easy Apply modal
        max_steps = 10
        for step in range(max_steps):
            logger.info("LinkedIn: processing Easy Apply step %d/%d", step + 1, max_steps)

            if self._detect_captcha():
                return ApplyResult(
                    success=False, captcha_detected=True,
                    error_message="CAPTCHA detected in application form",
                )

            self._fill_form_fields(profile)

            if resume_pdf_path:
                self._upload_resume(resume_pdf_path)

            self._fill_cover_letter(cover_letter_text)

            # Check for submit button (final step)
            if self._safe_click(
                "button[aria-label*='Submit application'], "
                "button[aria-label*='Submit']",
                timeout=2000,
            ):
                self._random_pause(2, 4)

                # Close confirmation modal if present
                self._safe_click(
                    "button[aria-label*='Dismiss'], "
                    "[data-test-modal-close-btn]",
                    timeout=3000,
                )
                return ApplyResult(success=True)

            # Click "Next" or "Review" to advance
            if self._safe_click(
                "button[aria-label*='Continue'], "
                "button[aria-label*='Next'], "
                "button[aria-label*='Review']",
                timeout=2000,
            ):
                self._random_pause(1, 2)
                continue

            # We are stuck on this step. Log only field labels/types and
            # validation messages; never log entered values.
            try:
                required = self.page.locator(
                    "input[required], select[required], textarea[required], "
                    "[aria-required='true']"
                )
                logger.warning(
                    "LinkedIn: Easy Apply stuck on step %d; visible required fields=%d",
                    step + 1,
                    required.count(),
                )

                labels = self.page.locator(
                    ".artdeco-text-input--label, "
                    ".fb-dash-form-element__label, "
                    "label"
                )
                visible_labels = []
                for i in range(min(labels.count(), 20)):
                    item = labels.nth(i)
                    if item.is_visible():
                        text = (item.inner_text() or "").strip()
                        if text:
                            visible_labels.append(text[:120])

                if visible_labels:
                    logger.warning(
                        "LinkedIn: visible form labels: %s",
                        " | ".join(visible_labels),
                    )

                errors = self.page.locator(
                    ".artdeco-inline-feedback__message, "
                    "[role='alert'], "
                    ".fb-dash-form-element__error"
                )
                visible_errors = []
                for i in range(min(errors.count(), 10)):
                    item = errors.nth(i)
                    if item.is_visible():
                        text = (item.inner_text() or "").strip()
                        if text:
                            visible_errors.append(text[:160])

                if visible_errors:
                    logger.warning(
                        "LinkedIn: form validation messages: %s",
                        " | ".join(visible_errors),
                    )

            except Exception as diag_error:
                logger.debug(
                    "LinkedIn: could not inspect blocked Easy Apply step: %s",
                    diag_error,
                )

            return ApplyResult(
                success=False,
                manual_required=True,
                error_message=(
                    f"Easy Apply needs manual input on step {step + 1}"
                ),
            )

        return ApplyResult(
            success=False,
            manual_required=True,
            error_message="Easy Apply exceeded maximum form steps",
        )

    def _fill_form_fields(self, profile) -> None:
        """Fill common form fields if they are empty."""
        self._safe_fill(
            "input[name*='phone'], input[id*='phone']",
            profile.phone_full,
        )

    def _upload_resume(self, resume_pdf_path) -> bool:
        """Upload a resume using LinkedIn Easy Apply file selectors."""
        return self._safe_upload(
            resume_pdf_path,
            [
                "input[type='file'][name*='resume']",
                "input[type='file']",
            ],
        )

    def _fill_cover_letter(self, text: str) -> None:
        """Fill cover letter textarea if visible."""
        if not text:
            return
        textarea = self.page.query_selector(
            "textarea[name*='cover'], "
            "textarea[id*='cover'], "
            "textarea[aria-label*='cover']"
        )
        if textarea and textarea.is_visible() and not textarea.input_value():
            textarea.fill(text)
            self._random_pause(0.5, 1)

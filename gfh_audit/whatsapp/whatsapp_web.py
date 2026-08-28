"""WhatsApp Web automation — 100% Selenium, zero OS-level UI automation.

Session persistence comes from the browser profile directory managed by
:class:`~gfh_audit.whatsapp.driver_manager.DriverManager`."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .driver_manager import DriverManager

logger = logging.getLogger("gfh.audit.whatsapp.web")

# BeautifulSoup for fast HTML parsing of WhatsApp Web pages: one page_source
# fetch parsed locally beats N Selenium DOM round-trips (VidaPay pattern).
try:
    from bs4 import BeautifulSoup

    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

WHATSAPP_URL = "https://web.whatsapp.com"

# --- resilient selectors (WhatsApp Web changes markup frequently) -----------
SEARCH_BOX_SELECTORS = [
    (By.CSS_SELECTOR, "div[contenteditable='true'][data-tab='3']"),
    (By.CSS_SELECTOR, "div[title='Search input textbox']"),
    (By.CSS_SELECTOR, "div[role='textbox'][data-tab='3']"),
    (By.XPATH, "//div[@contenteditable='true'][@data-tab='3']"),
]
MESSAGE_BOX_SELECTORS = [
    (By.CSS_SELECTOR, "footer div[contenteditable='true']"),
    (By.CSS_SELECTOR, "div[contenteditable='true'][data-tab='6']"),
    (By.CSS_SELECTOR, "div[title='Type a message']"),
    (By.CSS_SELECTOR, "div[role='textbox'][data-tab='6']"),
]
CHAT_LIST_SELECTORS = [
    (By.CSS_SELECTOR, "div[aria-label='Chat list']"),
    (By.CSS_SELECTOR, "#pane-side div[role='listitem']"),
    (By.CSS_SELECTOR, "#side div[role='row']"),
    (By.CSS_SELECTOR, "#main"),  # logged-in fallback: main pane exists
]
QR_SELECTORS = [
    (By.CSS_SELECTOR, "canvas[aria-label*='Scan']"),
    (By.XPATH, "//div[contains(text(),'Scan code') or contains(text(),'Log in to WhatsApp Web')]"),
    (By.XPATH, "//canvas"),
]
ATTACH_SELECTORS = [
    (By.CSS_SELECTOR, "span[data-icon='plus']"),
    (By.CSS_SELECTOR, "span[data-icon='clip']"),
    (By.XPATH, "//button[contains(@title,'Attach') or @title='Attach']"),
    (By.XPATH, "//div[@title='Attach']"),
]
FILE_INPUT_SELECTORS = [
    (By.CSS_SELECTOR, "input[type='file'][accept*='image']"),
    (By.CSS_SELECTOR, "input[type='file']"),
]
SEND_BUTTON_SELECTORS = [
    (By.CSS_SELECTOR, "span[data-icon='send']"),
    (By.CSS_SELECTOR, "button[aria-label='Send']"),
    (By.XPATH, "//div[@role='button'][@aria-label='Send']"),
]
CAPTION_SELECTORS = [
    (By.CSS_SELECTOR, "div[contenteditable='true'][data-tab='9']"),
    (By.CSS_SELECTOR, "footer div[contenteditable='true']"),
    (By.CSS_SELECTOR, "div[role='textbox'][data-tab='9']"),
]


@dataclass
class WhatsAppMessage:
    """One incoming message observed in a group conversation."""

    message_id: str = ""
    group: str = ""
    sender: str = ""
    text: str = ""
    has_image: bool = False
    timestamp: str = ""
    element_ref: object = None
    seen_at: float = field(default_factory=time.time)


class WhatsAppWeb:
    """High-level WhatsApp Web driver: session, groups, sending, polling."""

    def __init__(self, driver_manager: DriverManager, status_callback=None):
        self.dm = driver_manager
        self.status_callback = status_callback
        self._seen_message_ids: dict = {}  # group -> set(message ids)
        self._notif_checked = False        # WhatsApp notification settings checked once per session

    # -- helpers ---------------------------------------------------------------
    def _notify(self, text: str) -> None:
        logger.info(text)
        if self.status_callback:
            try:
                self.status_callback(text)
            except Exception:
                pass

    @property
    def driver(self) -> Optional[WebDriver]:
        return self.dm.driver

    def _find_first(self, selectors, timeout: int = 8) -> Optional[WebElement]:
        for by, value in selectors:
            try:
                return WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((by, value))
                )
            except Exception:
                continue
        return None

    # -- session -----------------------------------------------------------------
    def open_session(self, wait_seconds: int = 180) -> bool:
        """Open WhatsApp Web and wait until the chat list is available.

        First run: the user scans the QR code inside the automated browser
        window. Later runs: the persistent profile restores the session."""
        if not self.dm.is_valid and not self.dm.initialize():
            return False
        if not self.dm.navigate(WHATSAPP_URL, timeout=45):
            return False

        self._notify("Waiting for WhatsApp Web session (scan QR if prompted)...")
        deadline = time.time() + max(30, wait_seconds)
        while time.time() < deadline:
            try:
                if self.dm._validate_session():
                    # logged in when the main pane or chat list is present
                    for by, value in CHAT_LIST_SELECTORS:
                        try:
                            if self.driver.find_elements(by, value):
                                self._notify("WhatsApp Web session active")
                                return True
                        except Exception:
                            continue
            except WebDriverException:
                pass
            time.sleep(2)
        self._notify("WhatsApp Web session wait timed out")
        return False

    def is_logged_in(self) -> bool:
        try:
            if not self.dm._validate_session():
                return False
            for by, value in CHAT_LIST_SELECTORS:
                if self.driver.find_elements(by, value):
                    return True
            return False
        except Exception:
            return False

    # -- groups --------------------------------------------------------------------
    def open_group(self, group_name: str, wait_seconds: int = 25) -> bool:
        """Search and open a group chat by (partial) title."""
        try:
            if not self.is_logged_in():
                self._notify("WhatsApp Web session lost — attempting re-open")
                if not self.open_session(wait_seconds=60):
                    return False

            search = self._find_first(SEARCH_BOX_SELECTORS, timeout=10)
            if search is None:
                self._notify("WhatsApp search box not found")
                return False
            search.click()
            time.sleep(0.4)
            search.clear() if hasattr(search, "clear") else None
            search.send_keys(Keys.CONTROL, "a")
            search.send_keys(Keys.DELETE)
            for chunk in group_name:
                search.send_keys(chunk)
                time.sleep(0.01)
            time.sleep(1.6)

            # Click the first matching chat result
            result_xpath = (
                f"//div[@role='listitem']//span[@title='{group_name}'] | "
                f"//span[@title='{group_name}'] | "
                f"//div[@role='option']//span[@title='{group_name}']"
            )
            result = self.dm.find_element_safe(By.XPATH, result_xpath, timeout=8)
            if result is None:
                # fallback: click first listitem row in search results
                result = self.dm.find_element_safe(
                    By.CSS_SELECTOR, "#side div[role='listitem']", timeout=4
                )
            if result is None:
                self._notify(f"Group not found in search: {group_name}")
                return False
            result.click()

            # verify the conversation header opened
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                try:
                    header = self.driver.find_elements(
                        By.XPATH,
                        f"//header//span[@title='{group_name}'] | //header//span[contains(@title,'{group_name[:30]}')]",
                    )
                    if header:
                        self._notify(f"Group opened: {group_name}")
                        return True
                except Exception:
                    pass
                time.sleep(0.5)
            self._notify(f"Group opened (header not verified): {group_name}")
            return True
        except (WebDriverException, TimeoutException) as exc:
            logger.error("open_group(%s) failed: %s", group_name, exc)
            self.dm._recover_driver()
            return False

    # -- sending ---------------------------------------------------------------------
    def send_text(self, group_name: str, message: str, retry: int = 2) -> bool:
        """Send a plain text message (multi-line safe) into a group."""
        for attempt in range(retry + 1):
            try:
                if not self.open_group(group_name):
                    continue
                box = self._find_first(MESSAGE_BOX_SELECTORS, timeout=10)
                if box is None:
                    self._notify(f"Message box not found for {group_name}")
                    continue
                box.click()
                time.sleep(0.3)
                lines = message.split("\n")
                for i, line in enumerate(lines):
                    if i:
                        box.send_keys(Keys.SHIFT, Keys.ENTER)
                    if line:
                        box.send_keys(line)
                time.sleep(0.3)
                box.send_keys(Keys.ENTER)
                time.sleep(0.8)
                self._notify(f"Text sent to {group_name} ({len(message)} chars)")
                return True
            except (WebDriverException, TimeoutException) as exc:
                logger.warning("send_text attempt %s failed: %s", attempt + 1, exc)
                self.dm._recover_driver()
                time.sleep(2)
        return False

    def send_text_with_mentions(self, group_name: str, message: str, retry: int = 2) -> bool:
        """Send text; for each '@<phone>' token try a real contact selection.

        WhatsApp Web only creates a true mention via the contact dropdown that
        pops up after typing '@'. When the dropdown cannot be matched, the
        literal '@<phone>' text is left in the message (explicit tag format).
        """
        for attempt in range(retry + 1):
            try:
                if not self.open_group(group_name):
                    continue
                box = self._find_first(MESSAGE_BOX_SELECTORS, timeout=10)
                if box is None:
                    continue
                box.click()
                time.sleep(0.3)

                segments = self._split_mentions(message)
                for kind, value in segments:
                    if kind == "text":
                        self._type_lines(box, value)
                    else:  # mention
                        if not self._select_mention(box, value):
                            box.send_keys("@" + value)
                time.sleep(0.4)
                box.send_keys(Keys.ENTER)
                time.sleep(0.8)
                self._notify(f"Message with mentions sent to {group_name}")
                return True
            except (WebDriverException, TimeoutException) as exc:
                logger.warning("send_text_with_mentions attempt %s failed: %s", attempt + 1, exc)
                self.dm._recover_driver()
                time.sleep(2)
        return False

    @staticmethod
    def _split_mentions(message: str):
        """Split into ('text', str) and ('mention', phone) segments."""
        import re

        segments = []
        last = 0
        for match in re.finditer(r"@(\+?\d[\d\s\-().]{7,})", message):
            text = message[last:match.start()]
            if text:
                segments.append(("text", text))
            segments.append(("mention", re.sub(r"\D", "", match.group(1))))
            last = match.end()
        tail = message[last:]
        if tail:
            segments.append(("text", tail))
        return segments

    @staticmethod
    def _type_lines(box: WebElement, text: str) -> None:
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i:
                box.send_keys(Keys.SHIFT, Keys.ENTER)
            if line:
                box.send_keys(line)

    def _select_mention(self, box: WebElement, phone_digits: str) -> bool:
        """Type @phone and try to pick the matching contact from the dropdown."""
        try:
            box.send_keys("@")
            time.sleep(0.4)
            for digit in phone_digits:
                box.send_keys(digit)
                time.sleep(0.03)
            time.sleep(1.2)
            options = self.driver.find_elements(
                By.XPATH,
                "//div[@role='listbox']//li[@role='option'] | "
                "//ul[@role='listbox']//li[@role='option'] | "
                "//div[contains(@class,'mention')]//div[@role='option']",
            )
            for option in options[:12]:
                try:
                    opt_text = (option.text or "").replace(" ", "")
                except Exception:
                    continue
                if phone_digits[-9:] and phone_digits[-9:] in opt_text:
                    option.click()
                    time.sleep(0.4)
                    return True
            # no match — remove typed @digits so the fallback can write literal text
            for _ in range(len(phone_digits) + 1):
                box.send_keys(Keys.BACK_SPACE)
                time.sleep(0.01)
            return False
        except Exception:
            return False

    def send_image(self, group_name: str, image_path: Path, caption: str = "", retry: int = 2) -> bool:
        """Attach a PNG/JPG through the file input (pure Selenium — no clipboard)."""
        image_path = Path(image_path)
        if not image_path.exists():
            self._notify(f"Image not found: {image_path}")
            return False
        for attempt in range(retry + 1):
            try:
                if not self.open_group(group_name):
                    continue
                attach = self._find_first(ATTACH_SELECTORS, timeout=8)
                if attach is None:
                    self._notify(f"Attach button not found for {group_name}")
                    continue
                attach.click()
                time.sleep(1.0)
                file_input = self._find_first(FILE_INPUT_SELECTORS, timeout=6)
                if file_input is None:
                    self._notify("File input not found after attach click")
                    continue
                file_input.send_keys(str(image_path.resolve()))
                time.sleep(2.5)  # media preview upload

                if caption:
                    caption_box = self._find_first(CAPTION_SELECTORS, timeout=8)
                    if caption_box is not None:
                        caption_box.click()
                        time.sleep(0.2)
                        self._type_lines(caption_box, caption)
                        time.sleep(0.3)

                send_btn = self._find_first(SEND_BUTTON_SELECTORS, timeout=8)
                if send_btn is not None:
                    send_btn.click()
                else:
                    # fall back to keyboard send
                    (caption_box or attach).send_keys(Keys.ENTER)
                time.sleep(2.0)
                self._notify(f"Image sent to {group_name}: {image_path.name}")
                return True
            except (WebDriverException, TimeoutException) as exc:
                logger.warning("send_image attempt %s failed: %s", attempt + 1, exc)
                self.dm._recover_driver()
                time.sleep(2)
        return False

    # -- polling ------------------------------------------------------------------------
    # -- notification-driven monitoring (VidaPay Transfer Bot pattern) ---------
    def check_group_notifications(self) -> List[str]:
        """Return the names of chats/groups that currently show an unread badge.

        Reads the WhatsApp Web chat list directly in the browser (green unread
        badge circles, aria-label fallback) instead of opening every group.
        The monitor uses this to only open groups that actually have new
        messages."""
        self._ensure_notification_settings_on()
        try:
            unread = self.driver.execute_script("""
                const groupsWithUnread = [];

                // Strategy 1: spans containing small numbers inside elements
                // with a green background (the unread badge circle).
                const allSpans = document.querySelectorAll('span');
                for (const span of allSpans) {
                    if (span.offsetWidth === 0 || span.offsetHeight === 0) continue;
                    const text = span.textContent.trim();
                    if (!text || !/^\\d{1,2}$/.test(text)) continue;
                    let el = span;
                    for (let i = 0; i < 3 && el; i++) {
                        const bg = getComputedStyle(el).backgroundColor;
                        if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'rgb(0, 0, 0)'
                            && bg !== 'rgb(255, 255, 255)' && bg !== 'transparent') {
                            const match = bg.match(/\\d+/g);
                            if (match && match.length >= 3) {
                                const r = parseInt(match[0]);
                                const g = parseInt(match[1]);
                                const b = parseInt(match[2]);
                                if (g > r && g > b) {
                                    let parent = el.parentElement;
                                    let group_name = '';
                                    for (let j = 0; j < 15 && parent; j++) {
                                        const nameEl = parent.querySelector(
                                            'span[title], span[aria-label], '
                                          + 'div[title], div[aria-label]'
                                        );
                                        if (nameEl) {
                                            group_name = nameEl.getAttribute('title')
                                                || nameEl.getAttribute('aria-label')
                                                || nameEl.textContent || '';
                                            if (group_name.trim() && group_name.length > 1) break;
                                        }
                                        parent = parent.parentElement;
                                    }
                                    if (group_name.trim()) {
                                        groupsWithUnread.push(group_name.trim());
                                    }
                                    break;
                                }
                            }
                        }
                        el = el.parentElement;
                    }
                }

                // Strategy 2: fallback - aria-label containing "unread".
                const ariaUnread = document.querySelectorAll(
                    '[aria-label*="unread" i], [aria-label*="message" i]'
                );
                for (const el of ariaUnread) {
                    if (el.offsetWidth === 0 || el.offsetHeight === 0) continue;
                    const label = el.getAttribute('aria-label') || '';
                    if (label.toLowerCase().includes('unread')) {
                        let parent = el.parentElement;
                        for (let j = 0; j < 10 && parent; j++) {
                            const nameEl = parent.querySelector('span[title], div[title]');
                            if (nameEl && nameEl.getAttribute('title')) {
                                groupsWithUnread.push(nameEl.getAttribute('title').trim());
                                break;
                            }
                            parent = parent.parentElement;
                        }
                    }
                }

                return [...new Set(groupsWithUnread)];
            """)
            return list(unread or [])
        except Exception as exc:
            logger.debug("Notification check error: %s", exc)
            return []

    def _ensure_notification_settings_on(self) -> None:
        """Turn WhatsApp Web notification settings ON (once per session)."""
        if self._notif_checked:
            return
        self._notif_checked = True
        try:
            settings_clicked = False
            for selector in [
                'span[data-testid="menu"]',
                'div[aria-label="Menu"]',
                'button[aria-label="Menu"]',
                'span[aria-label="Settings"]',
                'div[role="button"][aria-label*="Menu"]',
                '#side > header div[role="button"]',
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        settings_clicked = True
                        break
                except Exception:
                    continue
            if not settings_clicked:
                return
            for selector in [
                'li[role="menuitem"] div[title="Settings"]',
                'div[role="menuitem"][title="Settings"]',
                'li span[title="Settings"]',
                'div[aria-label="Settings"]',
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        break
                except Exception:
                    continue
            for selector in [
                'div[role="listitem"] span[title="Notifications"]',
                'div[role="listitem"] div[title="Notifications"]',
                'span[title="Notifications"]',
                'div[aria-label="Notifications"]',
            ]:
                try:
                    btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if btn.is_displayed():
                        self.driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1)
                        break
                except Exception:
                    continue
            toggles = self.driver.find_elements(
                By.CSS_SELECTOR,
                'div[role="checkbox"], span[role="button"][aria-checked], '
                'input[type="checkbox"], div[role="switch"]'
            )
            turned_on = 0
            for toggle in toggles:
                try:
                    if not toggle.is_displayed():
                        continue
                    checked = toggle.get_attribute("aria-checked")
                    is_selected = toggle.is_selected()
                    if checked == "false" or (checked is None and not is_selected):
                        self.driver.execute_script("arguments[0].click();", toggle)
                        time.sleep(0.5)
                        turned_on += 1
                except Exception:
                    continue
            if turned_on:
                self._notify(f"Turned ON {turned_on} WhatsApp notification setting(s)")
            # navigate back to the chats view
            try:
                self.driver.back()
                time.sleep(1)
            except Exception:
                pass
        except Exception as exc:
            logger.debug("Notification settings check failed: %s", exc)

    def fetch_new_messages(self, group_name: str, limit: int = 12) -> List[WhatsAppMessage]:
        """Read the most recent incoming messages of the currently open group.

        Primary path parses ``driver.page_source`` once with BeautifulSoup
        (fast); falls back to Selenium DOM traversal. Only messages whose
        data-id was not observed before are returned (duplicate scanning of
        images/IMEIs is prevented here)."""
        if not self.is_logged_in():
            return []
        raw = self._extract_messages_bs4(group_name, limit)
        if raw is None:
            raw = self._extract_messages_selenium(group_name, limit)

        seen = self._seen_message_ids.setdefault(group_name, set())
        messages: List[WhatsAppMessage] = []
        for message in raw:
            if not message.message_id or message.message_id in seen:
                continue
            seen.add(message.message_id)
            if len(seen) > 500:
                newest_ids = {m.message_id for m in raw}
                self._seen_message_ids[group_name] = {
                    i for i in seen if i in newest_ids
                } | {message.message_id}
                seen = self._seen_message_ids[group_name]
            messages.append(message)
        return messages

    def _extract_messages_bs4(self, group_name: str, limit: int) -> Optional[List[WhatsAppMessage]]:
        """Parse the visible conversation with BeautifulSoup.

        Returns None when BeautifulSoup is unavailable or parsing fails so the
        caller can fall back to Selenium extraction."""
        if not BS4_AVAILABLE:
            return None
        try:
            html = self.driver.page_source
        except Exception:
            return None
        if not html:
            return None
        try:
            soup = BeautifulSoup(html, "html.parser")
            rows = soup.select("div[data-id]")
            if not rows:
                rows = soup.select("div[role='row']")
            if not rows:
                return None
            messages: List[WhatsAppMessage] = []
            for row in rows[-max(limit * 3, limit):]:
                try:
                    row_class = " ".join(row.get("class") or [])
                    if "message-in" not in row_class:
                        continue
                    msg_id = row.get("data-id") or ""
                    if not msg_id:
                        continue
                    text_nodes = row.select("span.selectable-text")
                    text = "\n".join(
                        node.get_text("\n", strip=True)
                        for node in text_nodes
                        if node.get_text(strip=True)
                    )
                    sender = ""
                    sender_node = row.select_one("span[aria-label], span[class*='sender']")
                    if sender_node:
                        sender = sender_node.get("aria-label") or sender_node.get_text(strip=True)
                    has_image = bool(row.select("img[src^='blob:']"))
                    if not text and not has_image:
                        continue
                    messages.append(
                        WhatsAppMessage(
                            message_id=msg_id,
                            group=group_name,
                            sender=sender,
                            text=text,
                            has_image=has_image,
                        )
                    )
                except Exception:
                    continue
            return messages[-limit:]
        except Exception as exc:
            logger.debug("BS4 message extraction failed: %s", exc)
            return None

    def _extract_messages_selenium(self, group_name: str, limit: int) -> List[WhatsAppMessage]:
        """Selenium fallback: walk message rows via WebDriver round-trips."""
        messages: List[WhatsAppMessage] = []
        try:
            elements = self.driver.find_elements(
                By.XPATH, "//div[@data-id and contains(@class,'message-in')]"
            )
            if not elements:
                elements = self.driver.find_elements(
                    By.XPATH, "//div[contains(@class,'message-in')][@data-id]"
                )
            for element in elements[-limit:]:
                try:
                    msg_id = element.get_attribute("data-id") or ""
                    if not msg_id:
                        continue
                    sender, text = self._extract_sender_and_text(element)
                    has_image = bool(element.find_elements(By.CSS_SELECTOR, "img[src^='blob:']"))
                    messages.append(
                        WhatsAppMessage(
                            message_id=msg_id,
                            group=group_name,
                            sender=sender,
                            text=text,
                            has_image=has_image,
                            element_ref=element,
                        )
                    )
                except Exception:
                    continue
        except WebDriverException:
            self.dm._recover_driver()
        except Exception as exc:
            logger.debug("fetch_new_messages error: %s", exc)
        return messages

    @staticmethod
    def _extract_sender_and_text(element: WebElement):
        sender, text = "", ""
        try:
            sender_nodes = element.find_elements(By.CSS_SELECTOR, "span[aria-label], span[class*='sender']")
            for node in sender_nodes:
                label = node.get_attribute("aria-label") or ""
                if label:
                    sender = label
                    break
        except Exception:
            pass
        try:
            text_nodes = element.find_elements(By.CSS_SELECTOR, "span.selectable-text")
            parts = []
            for node in text_nodes:
                value = (node.text or "").strip()
                if value:
                    parts.append(value)
            text = "\n".join(parts)
        except Exception:
            pass
        return sender, text

    def download_message_image(self, message: WhatsAppMessage) -> Optional[bytes]:
        """Fetch the image bytes of a message via its blob URL.

        Clicks the thumbnail to open the media viewer for the full-size blob,
        falling back to the inline thumbnail blob."""
        blob_urls = self._collect_blob_urls(message)
        for blob_url in blob_urls:
            data = self._fetch_blob(blob_url)
            if data:
                return data
        return None

    def _collect_blob_urls(self, message: WhatsAppMessage) -> List[str]:
        urls: List[str] = []
        element = message.element_ref
        if element is None and message.message_id:
            # BS4-extracted messages carry no live element — re-locate it by
            # its data-id before touching blob URLs.
            try:
                element = self.driver.find_element(
                    By.XPATH, f"//div[@data-id=\"{message.message_id}\"]"
                )
                message.element_ref = element
            except Exception:
                return urls
        try:
            if element is not None:
                thumbs = element.find_elements(By.CSS_SELECTOR, "img[src^='blob:']")
                for thumb in thumbs:
                    src = thumb.get_attribute("src")
                    if src:
                        urls.append(src)
                # try to open the media viewer for full resolution
                try:
                    thumbs[0].click()
                    time.sleep(1.6)
                    viewer_imgs = self.driver.find_elements(
                        By.CSS_SELECTOR, "div[role='dialog'] img[src^='blob:']"
                    )
                    for img in viewer_imgs:
                        src = img.get_attribute("src")
                        if src:
                            urls.insert(0, src)  # full-size first
                except Exception:
                    pass
                # close viewer if it opened
                try:
                    self.driver.find_element(By.CSS_SELECTOR, "div[role='dialog'] span[data-icon='x']").click()
                    time.sleep(0.6)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("collect blob urls error: %s", exc)
        return urls

    def _fetch_blob(self, blob_url: str) -> Optional[bytes]:
        script = """
          const done = arguments[arguments.length - 1];
          fetch(arguments[0]).then(r => r.blob()).then(b => {
            const reader = new FileReader();
            reader.onload = () => done(reader.result);
            reader.onerror = () => done('');
            reader.readAsDataURL(b);
          }).catch(() => done(''));
        """
        try:
            self.driver.set_script_timeout(20)
            result = self.driver.execute_async_script(script, blob_url)
            if not result or not isinstance(result, str) or "," not in result:
                return None
            import base64

            return base64.b64decode(result.split(",", 1)[1])
        except Exception as exc:
            logger.debug("blob fetch failed: %s", exc)
            return None

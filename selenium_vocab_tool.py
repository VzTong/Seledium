from __future__ import annotations
# Cho phép dùng kiểu dữ liệu forward reference (Python 3.7+)

# ================= IMPORT =================
import argparse          # Xử lý CLI arguments
import csv               # Ghi file CSV
import json              # Xử lý JSON (__NEXT_DATA__)
import re                # Regex
import time              # Sleep, delay
import traceback         # In lỗi chi tiết

from dataclasses import dataclass, asdict  # Model dữ liệu
from pathlib import Path                  # Xử lý path cross-platform
from typing import Callable, Iterable, Optional  # Type hint
from urllib.parse import urljoin, urlparse       # Xử lý URL

from bs4 import BeautifulSoup  # Parse HTML fallback

# Selenium core
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeWebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

# ================= REGEX =================
# Regex kiểm tra text có chứa chữ/số (dùng filter vocab)
WORD_RE = re.compile(r"[A-Za-zÀ-ỹà-ỹ0-9]")

# Chuẩn hóa whitespace (multiple spaces → 1 space)
MULTISPACE_RE = re.compile(r"\s+")

# Dùng để tạo filename safe
SLUG_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]+")

# Bắt số lượng từ trong text kiểu "24 words" (hoặc "1 word").
TOPIC_WORDS_RE = re.compile(r"\b(\d{1,3}(?:,\d{3})*)\s+words?\b", re.IGNORECASE)

# Loại bỏ chữ Trung, Nhật, Hàn, Nga, Ả Rập (filter noise)
DISALLOWED_SCRIPT_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u0400-\u04ff\u0600-\u06ff]"
)

@dataclass
class TopicResult:
    topic_title: str
    topic_url: str
    topic_file: str
    articles: list[dict[str, str]]
    entries: list[dict[str, str]]
    vocabulary: list[str]


def normalize_text(text: str) -> str:
    return MULTISPACE_RE.sub(" ", text or "").strip()


def is_valid_href(href: str | None) -> bool:
    if not href:
        return False
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
        return False
    return True


def same_domain(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()


def slugify(value: str, default: str = "item", max_len: int = 100) -> str:
    value = normalize_text(value).lower()
    value = value.replace("https://", "").replace("http://", "")
    value = value.replace("/", "_")
    value = SLUG_SAFE_RE.sub("_", value)
    value = value.strip("._-")
    if not value:
        value = default
    return value[:max_len]


def link_output_folder(start_url: str, base_output_dir: Path) -> Path:
    parsed = urlparse(start_url)
    combined = parsed.netloc + parsed.path
    if parsed.query:
        combined += "_" + parsed.query
    folder_name = slugify(combined, default="link")
    return base_output_dir / folder_name


def output_mode_suffix(include_phonetic: bool, include_example: bool) -> str:
    if include_phonetic and include_example:
        return "full"
    if (not include_phonetic) and (not include_example):
        return "word_only"
    if include_phonetic and (not include_example):
        return "word_phonetic"
    return "word_example"


def guess_driver_path(driver_path: Optional[str]) -> Optional[str]:
    if driver_path:
        path = Path(driver_path).expanduser().resolve()
        return str(path) if path.exists() else None

    script_dir = Path(__file__).resolve().parent
    candidates = [
        script_dir / "chromedriver.exe",
        Path.cwd() / "chromedriver.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def setup_driver(headless: bool = True, driver_path: Optional[str] = None) -> ChromeWebDriver:
    options = Options()
    options.page_load_strategy = "eager"
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--lang=vi-VN")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
        },
    )

    local_driver = guess_driver_path(driver_path)
    if local_driver:
        service = ChromeService(executable_path=local_driver)
        return ChromeWebDriver(service=service, options=options)

    try:
        # Selenium Manager (cần internet lần đầu để tải driver nếu máy chưa có)
        return ChromeWebDriver(options=options)
    except WebDriverException as exc:
        msg = str(exc)
        if "Unable to obtain driver for chrome" in msg:
            raise RuntimeError(
                "Không tìm thấy ChromeDriver. Nếu đang offline, hãy tải chromedriver.exe và đặt vào cùng thư mục code, "
                "hoặc truyền --driver-path tới file chromedriver.exe."
            ) from exc
        raise


def wait_for_page(driver: webdriver.Chrome, timeout: int = 20) -> bool:
    def is_ready(d: webdriver.Chrome) -> bool:
        try:
            state = d.execute_script("return document.readyState")
            has_body = bool(d.execute_script("return !!document.body"))
            return has_body and state in ("interactive", "complete")
        except Exception:
            return False

    try:
        WebDriverWait(driver, timeout).until(is_ready)
        return True
    except TimeoutException:
        return False


def scroll_to_bottom(driver: webdriver.Chrome, pause: float = 0.5, max_rounds: int = 8) -> None:
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(max_rounds):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def open_page(driver: webdriver.Chrome, url: str, timeout: int = 20) -> bool:
    driver.get(url)
    first_ready = wait_for_page(driver, timeout=timeout)
    if not first_ready:
        try:
            driver.execute_script("window.stop();")
        except Exception:
            pass

        # Nếu trang đã có body thì vẫn xử lý tiếp thay vì fail cứng.
        has_body = False
        try:
            has_body = bool(driver.execute_script("return !!document.body"))
        except Exception:
            has_body = False

        if not has_body:
            return False

    scroll_to_bottom(driver)
    wait_for_page(driver, timeout=min(8, timeout))
    return True


def collect_anchor_items(driver: webdriver.Chrome, base_url: str, selector: str = "a") -> list[dict]:
    script = """
    const selector = arguments[0] || 'a';
    return Array.from(document.querySelectorAll(selector)).map(a => ({
        text: (a.innerText || a.textContent || '').trim(),
        href: a.href || a.getAttribute('href') || '',
        title: (a.title || '').trim()
    }));
    """
    items = driver.execute_script(script, selector) or []
    cleaned: list[dict] = []
    for item in items:
        text = normalize_text(item.get("text", ""))
        href = normalize_text(item.get("href", ""))
        if not is_valid_href(href) or not text:
            continue
        abs_url = urljoin(base_url, href)
        cleaned.append({"text": text, "url": abs_url, "title": normalize_text(item.get("title", ""))})
    return cleaned


def unique_by_url(items: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        url = item["url"]
        if url in seen:
            continue
        seen.add(url)
        result.append(item)
    return result


def is_langeek_url(url: str) -> bool:
    return "langeek.co" in urlparse(url).netloc.lower()


def discover_langeek_topics(driver: webdriver.Chrome, start_url: str) -> list[dict]:
    script = """
const startLinks = Array.from(document.querySelectorAll('a[href*="/vocab/subcategory/"]'))
    .filter(a => ((a.innerText || a.textContent || '').trim().toLowerCase() === 'start'));
const topics = [];
for (const link of startLinks) {
    let node = link;
    let title = '';
    let rawText = '';
    for (let i = 0; i < 8 && node; i++) {
        node = node.parentElement;
        if (!node) break;
        const heading = node.querySelector('h1,h2,h3,h4,strong,[class*="title"],[class*="heading"]');
        const headingText = (heading?.innerText || '').trim();
        if (headingText) {
            title = headingText;
            rawText = (node.innerText || '').trim();
            break;
        }
    }
    const href = link.href || link.getAttribute('href') || '';
    if (!title || !href) continue;
    topics.push({ title, url: href, rawText });
}
return topics;
"""

    rows = driver.execute_script(script) or []
    topics: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        title = normalize_text(row.get("title", ""))
        if DISALLOWED_SCRIPT_RE.search(title):
            continue

        url = normalize_text(row.get("url", ""))
        if not url:
            continue

        full_url = urljoin(start_url, url)
        if full_url in seen:
            continue
        seen.add(full_url)

        expected_words = 0
        match = TOPIC_WORDS_RE.search(normalize_text(row.get("rawText", "")))
        if match:
            expected_words = int(match.group(1).replace(",", ""))

        topics.append({"text": title, "url": full_url, "expected_words": expected_words})

    return topics


def try_click_start_on_page(driver: webdriver.Chrome) -> bool:
    try:
        buttons = driver.find_elements(By.XPATH, "//a|//button")
    except Exception:
        return False

    for btn in buttons:
        text = normalize_text(btn.text).lower()
        if "start" not in text:
            continue
        try:
            driver.execute_script("arguments[0].click();", btn)
            return True
        except Exception:
            continue
    return False


def get_langeek_word_tabs(driver: webdriver.Chrome) -> list[str]:
    script = r"""
    const items = [];
        const nodes = Array.from(document.querySelectorAll('button, a, div, span, li'));
    for (const n of nodes) {
            const text = (n.textContent || n.innerText || '').replace(/\s+/g, ' ').trim();
      if (!/^\d+\s*-\s*/.test(text)) continue;
      if (!n.offsetParent) continue;
            const cleaned = text.trim();
      items.push(cleaned);
    }
    return Array.from(new Set(items));
    """
    tabs = driver.execute_script(script) or []
    return [normalize_text(item) for item in tabs if normalize_text(item)]


def click_word_tab(driver: webdriver.Chrome, tab_text: str) -> bool:
    xpath = f"//*[self::button or self::a or self::div or self::span][normalize-space()='{tab_text}']"
    try:
        elements = driver.find_elements(By.XPATH, xpath)
    except Exception:
        return False

    for element in elements:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except Exception:
            continue
    return False


def click_word_tab_by_index(driver: webdriver.Chrome, index: int) -> bool:
        script = r"""
const idx = arguments[0];
const nodes = Array.from(document.querySelectorAll('button, a, div, span, li'))
    .filter(n => {
        const text = (n.textContent || n.innerText || '').replace(/\s+/g, ' ').trim();
        return /^\d+\s*-\s*/.test(text) && n.offsetParent;
    });
if (idx < 0 || idx >= nodes.length) return false;
nodes[idx].click();
return true;
"""
        try:
                return bool(driver.execute_script(script, index))
        except Exception:
                return False


def extract_langeek_word_entry(driver: webdriver.Chrome) -> dict[str, str]:
    script = r"""
    const textOf = (sel) => {
      const el = document.querySelector(sel);
      return (el?.innerText || el?.textContent || '').trim();
    };

    let word = textOf('h1');
    if (!word) {
      const maybe = Array.from(document.querySelectorAll('h2,h3,strong')).find(e => (e.innerText || '').trim().length <= 40);
      word = (maybe?.innerText || '').trim();
    }

    const fullText = document.body.innerText || '';
    const phoneticMatch = fullText.match(/\/[A-Za-zˈˌəʊɪæɔɑɒʌɛɜθðŋ\.\-\s]+\//);
    const phonetic = phoneticMatch ? phoneticMatch[0].trim() : '';

    let example = '';
    const nodes = Array.from(document.querySelectorAll('p,li,div,span'));
    const exNode = nodes.find(n => /example/i.test((n.innerText || '').trim()));
    if (exNode) {
      const parent = exNode.closest('section,div,article') || exNode.parentElement;
      const lines = (parent?.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
      const candidate = lines.find(s => s.length >= 10 && /[A-Za-z]/.test(s) && !/example/i.test(s));
      example = candidate || '';
    }

    return { word, phonetic, example };
    """
    raw = driver.execute_script(script) or {}
    return {
        "word": normalize_text(raw.get("word", "")),
        "phonetic": normalize_text(raw.get("phonetic", "")),
        "example": normalize_text(raw.get("example", "")),
    }


def extract_langeek_entries_from_next_data(driver: webdriver.Chrome) -> list[dict[str, str]]:
    raw = driver.execute_script("const el=document.getElementById('__NEXT_DATA__'); return el ? el.textContent : '';")
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except Exception:
        return []

    cards = (
        data.get("props", {})
        .get("pageProps", {})
        .get("initialState", {})
        .get("static", {})
        .get("subcategory", {})
        .get("cards", [])
    )
    if not isinstance(cards, list):
        return []

    entries: list[dict[str, str]] = []

    for idx, card in enumerate(cards, start=1):
        mt = card.get("mainTranslation", {}) if isinstance(card, dict) else {}
        if not isinstance(mt, dict):
            continue

        word = normalize_text(mt.get("title", ""))
        if not word:
            continue

        pronunciation = normalize_text(mt.get("pronunciation", ""))
        if pronunciation and not pronunciation.startswith("/"):
            pronunciation = f"/{pronunciation}/"

        part_of_speech = ""
        pos_payload = mt.get("partOfSpeech", {})
        if isinstance(pos_payload, dict):
            part_of_speech = normalize_text(str(pos_payload.get("partOfSpeechType", "")))

        note = normalize_text(mt.get("descriptionTitle", ""))
        if not note:
            note = part_of_speech

        definition_en = normalize_text(mt.get("description", ""))

        example = ""
        examples = mt.get("examples", [])
        if isinstance(examples, list) and examples:
            first = examples[0]
            if isinstance(first, dict):
                example = normalize_text(first.get("example", ""))

        entries.append(
            {
                "order": str(idx),
                "word": word,
                "phonetic": pronunciation,
                "example": example,
                "note": note,
                "definition_en": definition_en,
            }
        )

    return entries


def extract_langeek_entries_with_retry(
    driver: webdriver.Chrome,
    topic_url: str,
    expected_words: int,
    log: Callable[[str], None],
    attempts: int = 3,
) -> list[dict[str, str]]:
    for attempt in range(1, attempts + 1):
        entries = extract_langeek_entries_from_next_data(driver)
        if entries:
            return entries

        log(f"WARN: Không đọc được __NEXT_DATA__ (lần {attempt}/{attempts})")
        if attempt >= attempts:
            break

        time.sleep(0.6)
        open_page(driver, topic_url)
        if "/learn/" not in driver.current_url and "/review" not in driver.current_url:
            clicked = try_click_start_on_page(driver)
            if clicked:
                time.sleep(0.9)
                wait_for_page(driver, timeout=20)

    if expected_words > 0:
        log(f"WARN: Retry __NEXT_DATA__ vẫn thất bại, expected_words={expected_words}")
    return []


def apply_entry_field_options(
    entries: list[dict[str, str]],
    include_phonetic: bool,
    include_example: bool,
) -> list[dict[str, str]]:
    adjusted: list[dict[str, str]] = []
    for row in entries:
        adjusted.append(
            {
                "order": str(row.get("order", "")),
                "word": normalize_text(row.get("word", "")),
                "phonetic": normalize_text(row.get("phonetic", "")) if include_phonetic else "",
                "example": normalize_text(row.get("example", "")) if include_example else "",
                "note": normalize_text(row.get("note", "")),
                "definition_en": normalize_text(row.get("definition_en", "")),
            }
        )
    return adjusted


def apply_note_definition_policy(
    entries: list[dict[str, str]],
    include_note_definition_all: bool,
) -> list[dict[str, str]]:
    # Mặc định: chỉ giữ note/definition cho các từ bị trùng chữ (thường là đa nghĩa).
    # Nếu include_note_definition_all=True thì giữ note/definition cho toàn bộ từ.
    if include_note_definition_all:
        return entries

    word_counts: dict[str, int] = {}
    for row in entries:
        key = normalize_text(row.get("word", "")).lower()
        if not key:
            continue
        word_counts[key] = word_counts.get(key, 0) + 1

    trimmed: list[dict[str, str]] = []
    for row in entries:
        key = normalize_text(row.get("word", "")).lower()
        keep_note = word_counts.get(key, 0) > 1

        new_row = dict(row)
        if not keep_note:
            new_row["note"] = ""
            new_row["definition_en"] = ""
        trimmed.append(new_row)

    return trimmed


def merge_entries_by_order(existing_entries: list[dict[str, str]], new_entries: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}

    for row in existing_entries:
        order = normalize_text(str(row.get("order", "")))
        if order:
            merged[order] = row

    for row in new_entries:
        order = normalize_text(str(row.get("order", "")))
        if order:
            merged[order] = row

    if merged:
        ordered = sorted(merged.values(), key=lambda x: int((x.get("order") or "0") or "0"))
        return ordered

    return list(new_entries or existing_entries)


def click_next_word(driver: webdriver.Chrome) -> bool:
    script = """
const candidates = Array.from(document.querySelectorAll('button,a,div'));
for (const el of candidates) {
  const rect = el.getBoundingClientRect();
  if (rect.width < 20 || rect.height < 20) continue;
  const txt = ((el.innerText || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
  const cls = (el.className || '').toLowerCase();
  const onRight = rect.left > (window.innerWidth * 0.8);
  const looksLikeNext = txt.includes('next') || txt.includes('continue') || cls.includes('next') || txt.trim() === '>';
  if (onRight && looksLikeNext) {
    el.click();
    return true;
  }
}
return false;
"""
    try:
        return bool(driver.execute_script(script))
    except Exception:
        return False


def collect_langeek_entries(
    driver: webdriver.Chrome,
    expected_words: int,
    output_dir: Path,
    topic_index: int,
    topic_title: str,
    log: Callable[[str], None],
    initial_entries: Optional[list[dict[str, str]]] = None,
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = list(initial_entries or [])
    seen: set[tuple[str, str, str]] = {
        (
            normalize_text(item.get("word", "")).lower(),
            normalize_text(item.get("phonetic", "")).lower(),
            normalize_text(item.get("example", "")).lower(),
        )
        for item in entries
        if item.get("word", "")
    }

    tab_labels = get_langeek_word_tabs(driver)
    if tab_labels:
        log(f"INFO: phát hiện {len(tab_labels)} tab từ")

    # Ưu tiên click từng tab theo index để lấy đủ từ của topic.
    for idx in range(len(tab_labels)):
        clicked = click_word_tab_by_index(driver, idx)
        if not clicked:
            continue

        # Chờ ngắn để nội dung từ hiện xong.
        entry: dict[str, str] = {"word": "", "phonetic": "", "example": "", "note": "", "definition_en": ""}
        for _ in range(4):
            time.sleep(0.18)
            entry = extract_langeek_word_entry(driver)
            if entry.get("word", ""):
                break

        word = entry.get("word", "")
        if not word:
            continue

        key = (
            normalize_text(word).lower(),
            normalize_text(entry.get("phonetic", "")).lower(),
            normalize_text(entry.get("example", "")).lower(),
        )
        if key in seen:
            continue

        seen.add(key)
        entry["order"] = str(len(entries) + 1)
        entries.append(entry)
        temp_file = save_topic_entries_csv(output_dir, topic_index, topic_title, entries)
        log(f"SAVE: {temp_file} ({len(entries)} từ)")

    target = expected_words if expected_words > 0 else max(40, len(tab_labels))
    stable_rounds = 0
    max_steps = max(30, target * 3)

    # Fallback: nếu tab không đủ/không click được thì chạy Next để nhặt thêm.
    while len(entries) < target and max_steps > 0:
        max_steps -= 1
        entry = extract_langeek_word_entry(driver)
        word = entry.get("word", "")

        if word:
            key = (
                normalize_text(word).lower(),
                normalize_text(entry.get("phonetic", "")).lower(),
                normalize_text(entry.get("example", "")).lower(),
            )
            if key not in seen:
                seen.add(key)
                entry["order"] = str(len(entries) + 1)
                entries.append(entry)
                temp_file = save_topic_entries_csv(output_dir, topic_index, topic_title, entries)
                log(f"SAVE: {temp_file} ({len(entries)} từ)")
                stable_rounds = 0
            else:
                stable_rounds += 1
        else:
            stable_rounds += 1

        if stable_rounds >= 5:
            break

        moved = click_next_word(driver)
        if not moved:
            break
        time.sleep(0.2)

    return entries


def save_topic_entries_csv(output_dir: Path, topic_index: int, topic_title: str, entries: list[dict[str, str]]) -> str:
    file_name = topic_csv_name(topic_index, topic_title)
    file_path = output_dir / file_name
    with file_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["order", "word", "phonetic", "example", "note", "definition_en"])
        writer.writeheader()
        for i, row in enumerate(entries, start=1):
            writer.writerow(
                {
                    "order": row.get("order", str(i)),
                    "word": row.get("word", ""),
                    "phonetic": row.get("phonetic", ""),
                    "example": row.get("example", ""),
                    "note": row.get("note", ""),
                    "definition_en": row.get("definition_en", ""),
                }
            )
    return file_name


def topic_csv_name(topic_index: int, topic_title: str) -> str:
    return f"{topic_index:02d}_{slugify(topic_title, default='topic')}.csv"


def load_topic_entries_csv(output_dir: Path, topic_index: int, topic_title: str) -> list[dict[str, str]]:
    file_path = output_dir / topic_csv_name(topic_index, topic_title)
    if not file_path.exists():
        return []

    rows: list[dict[str, str]] = []
    with file_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            order = normalize_text((row.get("order") or ""))
            word = normalize_text((row.get("word") or ""))
            phonetic = normalize_text((row.get("phonetic") or ""))
            example = normalize_text((row.get("example") or ""))
            note = normalize_text((row.get("note") or ""))
            definition_en = normalize_text((row.get("definition_en") or ""))
            if not word:
                continue
            rows.append({"order": order, "word": word, "phonetic": phonetic, "example": example, "note": note, "definition_en": definition_en})
    return rows


def is_probable_vocab_term(text: str) -> bool:
    text = normalize_text(text)
    if not text or len(text) > 80:
        return False
    if DISALLOWED_SCRIPT_RE.search(text):
        return False
    if not WORD_RE.search(text):
        return False
    word_count = len(text.split())
    if word_count > 6:
        return False
    if any(marker in text.lower() for marker in ["cookie", "privacy", "login", "sign up", "subscribe"]):
        return False
    if text.endswith((".", ";", ":")) and word_count > 1:
        return False
    return True


def is_probable_article_link(text: str, url: str) -> bool:
    text = normalize_text(text)
    if not text:
        return False
    if DISALLOWED_SCRIPT_RE.search(text):
        return False
    if len(text) > 120:
        return False
    lower = (text + " " + url).lower()
    for bad in ["login", "sign in", "register", "privacy", "terms", "cookie", "contact", "about", "facebook", "twitter"]:
        if bad in lower:
            return False
    return True


def extract_vocab_from_html(html: str, selectors: Optional[list[str]] = None) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    default_selectors = selectors or [
        "main li",
        "article li",
        "section li",
        "table td",
        "table th",
        "li",
        "a",
        "span",
        "p",
    ]

    candidates: list[str] = []
    for selector in default_selectors:
        for element in soup.select(selector):
            text = normalize_text(element.get_text(" ", strip=True))
            if is_probable_vocab_term(text):
                candidates.append(text)

    unique: list[str] = []
    seen: set[str] = set()
    for text in candidates:
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique


def pick_topic_links(
    anchors: list[dict],
    start_url: str,
    topic_selector: Optional[str] = None,
) -> list[dict]:
    filtered_anchors = [a for a in anchors if not DISALLOWED_SCRIPT_RE.search(a["text"])]

    if topic_selector:
        return filtered_anchors

    same_domain_anchors = [a for a in filtered_anchors if same_domain(a["url"], start_url)]

    def score(item: dict) -> tuple[int, int]:
        text = item["text"]
        url = item["url"]
        # ưu tiên link ngắn, không phải điều hướng kiểu "contact / login"
        penalty = 0
        lower = (text + " " + url).lower()
        for bad in ["login", "sign in", "register", "privacy", "terms", "contact", "about", "home", "facebook", "twitter"]:
            if bad in lower:
                penalty += 5
        return (penalty, len(text))

    ranked = sorted(same_domain_anchors, key=score)
    return ranked


def extract_topic_articles(topic_anchors: list[dict], topic_url: str, start_url: str) -> list[dict[str, str]]:
    unique = unique_by_url(topic_anchors)
    results: list[dict[str, str]] = []
    for item in unique:
        url = item["url"]
        title = item["text"]
        if url == topic_url:
            continue
        if not same_domain(url, start_url):
            continue
        if not is_probable_article_link(title, url):
            continue
        results.append({"title": title, "url": url})
    return results


def scrape_vocabulary(
    start_url: str,
    output_dir: Path,
    headless: bool = True,
    topic_selector: Optional[str] = None,
    vocab_selectors: Optional[list[str]] = None,
    logger: Optional[Callable[[str], None]] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    driver_path: Optional[str] = None,
    resume: bool = True,
    include_phonetic: bool = True,
    include_example: bool = True,
    include_note_definition_all: bool = False,
) -> list[TopicResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    mode_suffix = output_mode_suffix(include_phonetic=include_phonetic, include_example=include_example)
    base_run_output_dir = link_output_folder(start_url, output_dir)
    run_output_dir = base_run_output_dir.with_name(f"{base_run_output_dir.name}__{mode_suffix}")
    run_output_dir.mkdir(parents=True, exist_ok=True)

    driver: webdriver.Chrome | None = None
    results: list[TopicResult] = []

    def log(message: str) -> None:
        if logger is not None:
            logger(message)
        else:
            print(message)

    def format_exception(exc: Exception) -> str:
        text = str(exc).strip()
        if not text:
            text = repr(exc)
        return f"{exc.__class__.__name__}: {text}"

    try:
        log("Bắt đầu phiên cào dữ liệu...")
        if driver_path:
            log(f"Driver path được cung cấp: {driver_path}")
        else:
            guessed = guess_driver_path(None)
            if guessed:
                log(f"Tìm thấy chromedriver local: {guessed}")
            else:
                log("Không thấy chromedriver local, Selenium Manager sẽ tự xử lý (có thể chậm nếu mạng yếu).")

        log("Đang khởi tạo ChromeDriver...")
        driver = setup_driver(headless=headless, driver_path=driver_path)
        log("Khởi tạo ChromeDriver thành công.")

        opened = open_page(driver, start_url)
        if not opened:
            log("WARN: Lần mở đầu chưa sẵn sàng, đang retry trang gốc...")
            time.sleep(1.0)
            opened = open_page(driver, start_url)
        if not opened:
            raise RuntimeError(f"Không thể mở ổn định trang gốc: {start_url}")
        log(f"Đã mở trang gốc: {start_url}")
        selector = topic_selector or "a"
        if is_langeek_url(start_url):
            topic_links = discover_langeek_topics(driver, start_url)
            if not topic_links:
                anchors = unique_by_url(collect_anchor_items(driver, start_url, selector=selector))
                topic_links = pick_topic_links(anchors, start_url, topic_selector=topic_selector)
        else:
            anchors = unique_by_url(collect_anchor_items(driver, start_url, selector=selector))
            topic_links = pick_topic_links(anchors, start_url, topic_selector=topic_selector)

        log(f"Tìm thấy {len(topic_links)} chủ đề cần xử lý")
        total_topics = len(topic_links)
        if progress_callback:
            progress_callback(0, total_topics)

        for index, topic in enumerate(topic_links, start=1):
            topic_url = topic["url"]
            topic_title = re.sub(r"^\d+\.?\s*", "", topic["text"]).strip()
            expected_words = int(topic.get("expected_words", 0)) if isinstance(topic, dict) else 0
            try:
                log(f"[{index}/{total_topics}] Đang xử lý: {topic_title}")
                entries: list[dict[str, str]] = []
                vocab: list[str] = []
                articles: list[dict[str, str]] = []
                existing_entries: list[dict[str, str]] = []

                if resume:
                    existing_entries = load_topic_entries_csv(run_output_dir, index, topic_title)
                    if existing_entries:
                        existing_entries = apply_entry_field_options(
                            existing_entries,
                            include_phonetic=include_phonetic,
                            include_example=include_example,
                        )
                        existing_entries = apply_note_definition_policy(
                            existing_entries,
                            include_note_definition_all=include_note_definition_all,
                        )
                        log(f"RESUME: đã có {len(existing_entries)} từ cho topic '{topic_title}'")
                        if expected_words > 0 and len(existing_entries) >= expected_words:
                            entries = existing_entries
                            vocab = [item.get("word", "") for item in entries if item.get("word", "")]
                            topic_file = topic_csv_name(index, topic_title)
                            results.append(
                                TopicResult(
                                    topic_title=topic_title,
                                    topic_url=topic_url,
                                    topic_file=topic_file,
                                    articles=articles,
                                    entries=entries,
                                    vocabulary=vocab,
                                )
                            )
                            log(f"OK: {topic_title} -> {len(vocab)} từ (resume-skip)")
                            continue

                if is_langeek_url(start_url):
                    open_page(driver, topic_url)
                    if "/learn/" not in driver.current_url and "/review" not in driver.current_url:
                        clicked = try_click_start_on_page(driver)
                        if clicked:
                            time.sleep(1.0)
                            wait_for_page(driver, timeout=20)

                    if expected_words > 0:
                        log(f"INFO: expected_words={expected_words}")

                    entries = extract_langeek_entries_with_retry(
                        driver=driver,
                        topic_url=topic_url,
                        expected_words=expected_words,
                        log=log,
                    )
                    entries = apply_entry_field_options(
                        entries,
                        include_phonetic=include_phonetic,
                        include_example=include_example,
                    )
                    entries = apply_note_definition_policy(
                        entries,
                        include_note_definition_all=include_note_definition_all,
                    )
                    if entries:
                        log(f"INFO: __NEXT_DATA__ cards={len(entries)}")
                        # merge với entries cũ nếu resume
                        if existing_entries:
                            entries = merge_entries_by_order(existing_entries, entries)
                            entries = apply_entry_field_options(
                                entries,
                                include_phonetic=include_phonetic,
                                include_example=include_example,
                            )
                            entries = apply_note_definition_policy(
                                entries,
                                include_note_definition_all=include_note_definition_all,
                            )

                        save_topic_entries_csv(run_output_dir, index, topic_title, entries)
                        log(f"SAVE: {topic_csv_name(index, topic_title)} ({len(entries)} từ)")
                    else:
                        log("WARN: Không đọc được __NEXT_DATA__, fallback sang click UI")
                        entries = collect_langeek_entries(
                            driver=driver,
                            expected_words=expected_words,
                            output_dir=run_output_dir,
                            topic_index=index,
                            topic_title=topic_title,
                            log=log,
                            initial_entries=existing_entries,
                        )
                        entries = apply_entry_field_options(
                            entries,
                            include_phonetic=include_phonetic,
                            include_example=include_example,
                        )
                        entries = apply_note_definition_policy(
                            entries,
                            include_note_definition_all=include_note_definition_all,
                        )
                    vocab = [item.get("word", "") for item in entries if item.get("word", "")]
                    if expected_words > 0 and len(vocab) < expected_words:
                        log(f"WARN: Topic '{topic_title}' thiếu từ: got={len(vocab)} expected={expected_words}")
                else:
                    try:
                        open_page(driver, topic_url)
                    except Exception:
                        time.sleep(1.0)
                        open_page(driver, topic_url)

                    html = driver.page_source
                    vocab = extract_vocab_from_html(html, selectors=vocab_selectors)
                    entries = [{"word": w, "phonetic": "", "example": "", "note": "", "definition_en": ""} for w in vocab]
                    entries = apply_entry_field_options(
                        entries,
                        include_phonetic=include_phonetic,
                        include_example=include_example,
                    )
                    entries = apply_note_definition_policy(
                        entries,
                        include_note_definition_all=include_note_definition_all,
                    )
                    topic_anchors = collect_anchor_items(driver, topic_url, selector="a")
                    articles = extract_topic_articles(topic_anchors, topic_url=topic_url, start_url=start_url)
                    save_topic_entries_csv(run_output_dir, index, topic_title, entries)

                topic_file = topic_csv_name(index, topic_title)
                results.append(
                    TopicResult(
                        topic_title=topic_title,
                        topic_url=topic_url,
                        topic_file=topic_file,
                        articles=articles,
                        entries=entries,
                        vocabulary=vocab,
                    )
                )
                log(f"OK: {topic_title} -> {len(vocab)} từ")
            except Exception as exc:
                message = str(exc)
                log(f"SKIP: {topic_title} ({topic_url}) - {exc.__class__.__name__}: {message}")
                if "Failed to establish a new connection" in message or "MaxRetryError" in message:
                    log("WARN: Driver có vẻ đã chết, đang khởi tạo lại driver...")
                    try:
                        if driver is not None:
                            driver.quit()
                    except Exception:
                        pass
                    driver = setup_driver(headless=headless, driver_path=driver_path)
            finally:
                if progress_callback:
                    progress_callback(index, total_topics)
    except Exception as exc:
        log(f"ERROR: {format_exception(exc)}")
        tb = traceback.format_exc()
        log(tb)
        raise
    finally:
        if driver is not None:
            driver.quit()

    save_results(results, run_output_dir, start_url=start_url)
    return results


def save_results(results: list[TopicResult], output_dir: Path, start_url: str) -> None:
    csv_path = output_dir / "all_vocabulary.csv"
    json_path = output_dir / "all_topics.json"
    summary_path = output_dir / "summary.json"

    flat_rows: list[dict] = []
    for topic_idx, result in enumerate(results, start=1):
        if result.entries:
            ordered_entries = sorted(
                result.entries,
                key=lambda x: int((x.get("order") or "0").strip() or "0"),
            )
            for row in ordered_entries:
                word = row.get("word", "")
                if not word:
                    continue
                flat_rows.append(
                    {
                        "topic_index": topic_idx,
                        "topic_title": result.topic_title,
                        "topic_url": result.topic_url,
                        "order": row.get("order", ""),
                        "word": word,
                        "phonetic": row.get("phonetic", ""),
                        "example": row.get("example", ""),
                        "note": row.get("note", ""),
                        "definition_en": row.get("definition_en", ""),
                    }
                )
        else:
            for order_idx, word in enumerate(result.vocabulary, start=1):
                if not word:
                    continue
                flat_rows.append(
                    {
                        "topic_index": topic_idx,
                        "topic_title": result.topic_title,
                        "topic_url": result.topic_url,
                        "order": str(order_idx),
                        "word": word,
                        "phonetic": "",
                        "example": "",
                        "note": "",
                        "definition_en": "",
                    }
                )

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["topic_index", "topic_title", "topic_url", "order", "word", "phonetic", "example", "note", "definition_en"],
        )
        writer.writeheader()
        writer.writerows(flat_rows)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump([asdict(item) for item in results], f, ensure_ascii=False, indent=2)

    summary = {
        "start_url": start_url,
        "topics_count": len(results),
        "total_words": sum(len(item.vocabulary) for item in results),
        "output_dir": str(output_dir),
        "structure": {
            "root_files": ["all_vocabulary.csv", "all_topics.json", "summary.json"],
            "topic_files": ["NN_topic-name.csv (word, phonetic, example, note, definition_en)"],
        },
    }
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def parse_selectors(raw: Optional[str]) -> Optional[list[str]]:
    if not raw:
        return None
    selectors = [normalize_text(item) for item in raw.split(",")]
    return [selector for selector in selectors if selector]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cào danh sách chủ đề và từ vựng bằng Selenium, rồi lưu ra CSV/JSON/TXT."
    )
    parser.add_argument("--url", required=True, help="Link trang gốc chứa danh sách chủ đề.")
    parser.add_argument("--output", default=str(Path(__file__).resolve().parent / "output"), help="Thư mục gốc lưu kết quả.")
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--topic-selector",
        default=None,
        help="CSS selector để lấy link chủ đề. Nếu không có, dùng heuristic tự động.",
    )
    parser.add_argument(
        "--vocab-selectors",
        default=None,
        help="Danh sách CSS selector để lấy từ vựng, ngăn cách bằng dấu phẩy.",
    )
    parser.add_argument(
        "--driver-path",
        default=None,
        help="Đường dẫn tới chromedriver.exe (hữu ích khi chạy offline).",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Tiếp tục từ dữ liệu đã cào trước đó (mặc định bật).",
    )
    parser.add_argument(
        "--include-phonetic",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Có lưu phiên âm hay không (mặc định bật).",
    )
    parser.add_argument(
        "--include-example",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Có lưu câu ví dụ hay không (mặc định bật).",
    )
    parser.add_argument(
        "--include-note-definition-all",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Giữ note/definition cho toàn bộ từ. Mặc định chỉ giữ cho các từ trùng chữ (đa nghĩa).",
    )
    return parser


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()
    output_dir = Path(args.output).resolve()
    vocab_selectors = parse_selectors(args.vocab_selectors)

    scrape_vocabulary(
        start_url=args.url,
        output_dir=output_dir,
        headless=args.headless,
        topic_selector=args.topic_selector,
        vocab_selectors=vocab_selectors,
        driver_path=args.driver_path,
        resume=args.resume,
        include_phonetic=args.include_phonetic,
        include_example=args.include_example,
        include_note_definition_all=args.include_note_definition_all,
    )
    print(f"Đã lưu kết quả trong thư mục gốc: {output_dir}")


if __name__ == "__main__":
    main()
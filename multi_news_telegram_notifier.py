#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
=============================================================================
🤖 AUTO BINANCE ANNOUNCEMENT TO WORDPRESS PUBLISHER BOT (24/7 PRODUCTION)
=============================================================================
- Module 1: BinanceScanner & NewsExtractor (Quét Catalog 93 & 49, bóc tách AST, Tables, Hình ảnh)
- Module 2: AIProcessor (Gemini 3.1 Flash Lite - Biên tập bài viết tiếng Anh SEO + Tables chuẩn WP)
- Module 2.5: AICheckerModule (Gemma 4 31B chấm điểm AI + Gemini Humanize viết lại)
- Module 3: HFImageEngine (Hugging Face sinh ảnh bìa 16:9 + Watermark Logo CP_white_logo.png)
- Module 4: WordPressPublisher (Playwright tự động hóa đăng bài nháp Draft lên chainplay.gg)
- Module 5: CSVLogger (Ghi lịch sử dữ liệu vào file CSV)
- Module 6: TelegramNotifier (Gửi báo cáo tức thì về Telegram khi lên bài thành công)
- Module 7: Periodic Scheduler (Chạy định kỳ 10 phút / lần)
=============================================================================
"""

import asyncio
import csv
from datetime import datetime
import html
import io
import json
import os
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types
from huggingface_hub import InferenceClient
from PIL import Image, ImageOps
try:
  from json_repair import repair_json
except ImportError:
  def repair_json(text: str) -> str:
    cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned
from playwright.async_api import async_playwright
from pydantic import BaseModel
import requests

# =====================================================================
# ⚙️ 1. NẠP BIẾN MÔI TRƯỜNG & CẤU HÌNH HỆ THỐNG
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Nạp các file .env
load_dotenv(os.path.join(BASE_DIR, "key.env"))
load_dotenv(os.path.join(BASE_DIR, "tk_key.env"))

GEMINI_KEY = os.getenv("gemini_api_key") or os.getenv("gemini_api_key_2")
# Toàn bộ key Gemini khả dụng (đã loại trùng) để xoay vòng khi key chính lỗi/hết quota
GEMINI_KEYS = list(dict.fromkeys(
    k.strip() for k in (
        os.getenv("gemini_api_key"),
        os.getenv("gemini_api_key_2"),
        os.getenv("gemini_api_key_3"),
    ) if k and k.strip()
))
# Danh sách model dự phòng (thử lần lượt sau model chính)
GEMINI_FALLBACK_MODELS = [
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-2.5-flash",
]
HF_KEY = os.getenv("hf_api")

def _env(*names: str, default: str = "") -> str:
  """Đọc biến môi trường, chấp nhận cả tên viết thường lẫn VIẾT HOA (tk_key.env dùng cả 2 kiểu)."""
  for n in names:
    v = os.getenv(n) or os.getenv(n.upper()) or os.getenv(n.lower())
    if v:
      return v.strip()
  return default


CP_WP_USER = _env("cp_wp_user", default="trungnn")
CP_WP_PASSWORD = _env("cp_wp_password", default="hO3teW@smHd&uoPj52o4@Qru")

VB_WP_USER = _env("vb_wp_user", default="trungnn41299@gmail.com")
VB_WP_PASSWORD = _env("vb_wp_password", default="$7RloCF&%vdbpV&e")

TELEGRAM_BOT_TOKEN = (
    os.getenv("news_bot_token")
    or os.getenv("trading_bot_token")
)
TELEGRAM_CHAT_ID = os.getenv("tk_news_chatid", "-1004422031564")


def get_platform_config(platform: str = "VB") -> dict:
  """Tạo dictionary cấu hình đồng bộ cho từng nền tảng (VB hoặc CP)."""
  p = platform.upper()
  is_vb = p == "VB"

  logo_file = "VB_white_logo.png" if is_vb else "CP_white_logo.png"
  logo_path = os.path.join(BASE_DIR, logo_file)
  if not os.path.exists(logo_path):
    # Thử tìm ở thư mục cha hoặc tk_bot nếu đang chạy trong for_agent
    alt_path = os.path.join(os.path.dirname(BASE_DIR), "tk_bot", logo_file)
    if os.path.exists(alt_path):
      logo_path = alt_path

  return {
      "PLATFORM": p,
      # 1. AI Models & Threshold
      "GEMINI_API_KEY": GEMINI_KEY,
      "MODELS": {
          "MAIN_AI": "gemini-3.5-flash-lite",
          "CHECK_AI": "gemini-3.5-flash-lite",
          "REWRITE_AI": "gemini-3.5-flash-lite",
      },
      "AI_THRESHOLD": 35,
      # 2. Tạo ảnh Hugging Face (Bộ màu thương hiệu & Logo)
      "IMAGE": {
          "ENABLE_GENERATION": True,
          "PLATFORM": p,
          "HF_API_TOKEN": HF_KEY,
          "HF_MODEL_ID": "krea/Krea-2-Turbo",
          "OUTPUT_DIR": os.path.join(BASE_DIR, "downloads"),
          "LOGO_PATH": logo_path,
      },
      # 3. Cấu hình WordPress
      "WORDPRESS": {
          "PLATFORM": p,
          "SITE_URL": "https://ventureburn.com" if is_vb else "https://chainplay.gg",
          "LOGIN_URL": "https://ventureburn.com/tklogin/" if is_vb else "https://chainplay.gg/tklogin/",
          "USERNAME": VB_WP_USER if is_vb else CP_WP_USER,
          "PASSWORD": VB_WP_PASSWORD if is_vb else CP_WP_PASSWORD,
          "PROFILE_DIR": os.path.join(BASE_DIR, "wp_profile_vb" if is_vb else "wp_profile"),
          "HEADLESS": True,
      },
      # 4. Quản lý File CSV
      "CSV_LOG_PATH": os.path.join(BASE_DIR, "vb_articles_log.csv" if is_vb else "cp_articles_log.csv"),
      # 5. Cấu hình Telegram Notification
      "TELEGRAM_NOTIFY": {
          "ENABLE": True,
          "BOT_TOKEN": TELEGRAM_BOT_TOKEN,
          "CHAT_ID": TELEGRAM_CHAT_ID,
          "BRAND_NAME": "VENTUREBURN BOT" if is_vb else "CHAINPLAY BOT",
      },
      # 6. Cấu hình Quét Binance Announcements
      "BINANCE": {
          "CATALOG_IDS": [93, 49, 48],
          "LANG": "en",
          "BASE_ANNOUNCEMENT_URL": "https://www.binance.com/en/support/announcement",
          "KEYWORD_WHITELIST": [
              "List",
              "Listing",
              "Binance Will List",
              "Tournament",
              "Trading Tournament",
              "Airdrop",
              "Introduce",
              "Introduces",
              "Introducing",
              "Trading Festival",
              "Festival",
              "Competition",
              "Compete",
              "Rewards",
              "Prize",
              "Will Feature",
              "Feature",
              "Launch",
              "Launches",
              "Launching",
          ],
          "KEYWORD_BLACKLIST": [
              "Prediction Market",
          ],
          "POLL_INTERVAL_SECONDS": 600,
      },
  }


CONFIG_VB = get_platform_config("VB")
CONFIG_CP = get_platform_config("CP")
CONFIG = CONFIG_VB  # Mặc định cấu hình Ventureburn (VB)


# =====================================================================
# 🛟 GEMINI FALLBACK CLIENT (XOAY VÒNG KEY + MODEL DỰ PHÒNG)
# =====================================================================
class GeminiAllFailedError(RuntimeError):
  """Tất cả tổ hợp (model, key) đều lỗi."""


class GeminiFallbackClient:
  """Gọi Gemini với cơ chế fallback.

  Thứ tự thử: với từng model (model chính trước, sau đó các model dự phòng),
  thử lần lượt từng API key. Phân loại lỗi:
    - 429 (hết quota/rate limit), 401/403 (key hỏng)  ➔ chuyển sang key kế tiếp.
    - 404 / 400 do model không tồn tại/không hỗ trợ   ➔ bỏ qua model này.
    - 5xx, timeout, lỗi mạng                           ➔ retry có backoff, rồi chuyển tiếp.
    - Response rỗng hoặc validate() thất bại          ➔ chuyển tổ hợp kế tiếp.
  """

  def __init__(
      self,
      api_key: str | None = None,
      model_id: str | None = None,
      fallback_models: list[str] | None = None,
      api_keys: list[str] | None = None,
      retries_per_combo: int = 2,
      base_delay: float = 2.0,
      timeout_ms: int = 120_000,
  ):
    keys = [api_key] + list(api_keys if api_keys is not None else GEMINI_KEYS)
    self.api_keys = list(dict.fromkeys(k for k in keys if k))
    if not self.api_keys:
      raise ValueError("Không tìm thấy Gemini API key nào (gemini_api_key*).")

    models = [model_id] + list(
        fallback_models if fallback_models is not None else GEMINI_FALLBACK_MODELS
    )
    self.models = list(dict.fromkeys(m for m in models if m))
    self.retries_per_combo = max(1, retries_per_combo)
    self.base_delay = base_delay
    self.timeout_ms = timeout_ms
    self._clients: dict[str, genai.Client] = {}
    self.last_model_used: str | None = None

  def _client(self, key: str) -> genai.Client:
    if key not in self._clients:
      self._clients[key] = genai.Client(
          api_key=key, http_options=types.HttpOptions(timeout=self.timeout_ms)
      )
    return self._clients[key]

  @staticmethod
  def _classify(err: Exception) -> str:
    code = getattr(err, "code", None)
    msg = str(err).lower()
    if code == 429 or "resource_exhausted" in msg or "quota" in msg:
      return "next_key"
    if code in (401, 403) or "api key" in msg or "permission" in msg:
      return "next_key"
    if code == 404 or (code == 400 and ("model" in msg or "not supported" in msg)):
      return "next_model"
    if isinstance(code, int) and 400 <= code < 500:
      # Lỗi request khác (prompt/config) — thử model khác có thể vẫn chạy được
      return "next_model"
    return "retry"  # 5xx, timeout, lỗi mạng, lỗi không xác định

  @staticmethod
  def _mask(key: str) -> str:
    return f"...{key[-4:]}" if len(key) > 4 else "***"

  def generate(self, contents, config=None, validate=None, label: str = "Gemini"):
    """Trả về (response, result) — result là output của validate(response) nếu có.

    validate(response) nên raise Exception khi output không dùng được
    (ví dụ JSON hỏng) để kích hoạt fallback sang tổ hợp kế tiếp.
    """
    errors_log: list[str] = []
    for model in self.models:
      skip_model = False
      for key in self.api_keys:
        for attempt in range(1, self.retries_per_combo + 1):
          try:
            resp = self._client(key).models.generate_content(
                model=model, contents=contents, config=config
            )
            text = getattr(resp, "text", None)
            if not text or not text.strip():
              finish = None
              try:
                finish = resp.candidates[0].finish_reason
              except Exception:
                pass
              raise ValueError(f"Response rỗng (finish_reason={finish})")
            result = validate(resp) if validate else None
            if model != self.models[0] or key != self.api_keys[0]:
              print(f"🛟 [{label}] Fallback thành công: model={model}, key={self._mask(key)}")
            self.last_model_used = model
            return resp, result
          except Exception as e:
            action = self._classify(e)
            short = str(e).replace("\n", " ")[:200]
            errors_log.append(f"{model}/{self._mask(key)}#{attempt}: {short}")
            print(
                f"⚠️ [{label}] Lỗi model={model} key={self._mask(key)}"
                f" (lần {attempt}/{self.retries_per_combo}) ➔ {action}: {short}"
            )
            if action == "next_model":
              skip_model = True
              break
            if action == "next_key":
              break
            if attempt < self.retries_per_combo:
              time.sleep(self.base_delay * (2 ** (attempt - 1)))
        if skip_model:
          break
    raise GeminiAllFailedError(
        f"[{label}] Tất cả {len(self.models)} model x {len(self.api_keys)} key"
        f" đều thất bại. Lỗi cuối: {errors_log[-3:]}"
    )



# =====================================================================
# 📦 HELPER: XỬ LÝ ẢNH CỤC BỘ & METADATA
# =====================================================================
class ImageHelper:

  @staticmethod
  def download_and_sanitize_image(
      image_url: str, slug: str, output_dir: str, index: int = 1
  ) -> str | None:
    """Tải ảnh về máy, xóa EXIF/Metadata và đổi tên file theo slug."""
    try:
      headers = {
          "User-Agent": (
              "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
              " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
          )
      }
      resp = requests.get(image_url, headers=headers, timeout=20)
      if resp.status_code != 200:
        return None

      clean_slug = re.sub(r"[^a-zA-Z0-9_-]", "", slug.replace(" ", "-"))[:40]
      filename = f"{clean_slug}-img-{index}-{int(time.time())}.jpg"
      file_path = os.path.join(output_dir, filename)

      image = Image.open(io.BytesIO(resp.content)).convert("RGB")
      image.save(file_path, "JPEG", quality=90, optimize=True)
      return file_path
    except Exception as e:
      print(f"⚠️ Lỗi khi tải/xử lý ảnh ({image_url}): {e}")
      return None


# =====================================================================
# 📦 MODULE 1: BINANCE SCANNER & EXTRACTOR
# =====================================================================
class BinanceScanner:
  """Quét danh sách thông báo chính thức từ Binance API và bóc tách nội dung chi tiết."""

  def __init__(self, config: dict):
    self.catalog_ids = config.get("CATALOG_IDS", [93, 49, 48])
    self.lang = config.get("LANG", "en")
    self.base_url = config.get(
        "BASE_ANNOUNCEMENT_URL",
        "https://www.binance.com/en/support/announcement",
    )
    self.headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "lang": self.lang,
        "clienttype": "web",
    }

  def get_latest_articles(self) -> list[dict]:
    """Quét tất cả các danh mục được chỉ định và trả về danh sách bài viết mới nhất."""
    all_articles = []
    seen_codes = set()

    for cat_id in self.catalog_ids:
      try:
        url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId={cat_id}&pageNo=1&pageSize=15"
        resp = requests.get(url, headers=self.headers, timeout=15)
        if resp.status_code != 200:
          print(f"⚠️ Binance API Catalog {cat_id} HTTP: {resp.status_code}")
          continue

        res_data = resp.json().get("data", {})
        cat_articles = res_data.get("articles", [])
        for sub_cat in res_data.get("catalogs", []):
          cat_articles.extend(sub_cat.get("articles", []))

        for art in cat_articles:
          code = art.get("code")
          if not code or code in seen_codes:
            continue
          seen_codes.add(code)

          title = art.get("title", "").strip()
          release_date = art.get("releaseDate")
          article_url = f"{self.base_url}/{code}"

          all_articles.append({
              "code": code,
              "title": title,
              "catalog_id": cat_id,
              "release_date": release_date,
              "url": article_url,
          })
      except Exception as e:
        print(f"⚠️ Lỗi khi quét Catalog {cat_id}: {e}")

    return all_articles

  def _parse_binance_ast_node(self, node) -> str:
    """Đệ quy chuyển đổi cây cấu trúc JSON AST của Binance thành chuỗi HTML/Table hoàn chỉnh."""
    if not isinstance(node, dict):
      return ""
    if node.get("node") == "text":
      return node.get("text", "")

    tag = node.get("tag", "")
    children_html = "".join(
        self._parse_binance_ast_node(c) for c in node.get("child", [])
    )

    if tag:
      attrs = node.get("attr", {})
      attr_parts = []
      if "href" in attrs:
        attr_parts.append(f'href="{attrs["href"]}"')
      if "src" in attrs:
        attr_parts.append(f'src="{attrs["src"]}"')

      attr_str = (" " + " ".join(attr_parts)) if attr_parts else ""

      if tag in ["img", "br", "hr"]:
        return f"<{tag}{attr_str} />"
      return f"<{tag}{attr_str}>{children_html}</{tag}>"

    return children_html

  def extract_article_detail(self, article_code: str, default_url: str) -> dict:
    """Lấy chi tiết toàn bộ nội dung, cấu trúc bảng và hình ảnh của một bài thông báo Binance."""
    print(f"\n🌐 [Module 1] Đang trích xuất chi tiết Binance: [{article_code}]")
    fallback_data = {
        "url": default_url,
        "title": "Binance Announcement Update",
        "content": "",
        "source_images": [],
    }

    try:
      detail_url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/detail/query?articleCode={article_code}"
      resp = requests.get(detail_url, headers=self.headers, timeout=15)
      if resp.status_code != 200:
        return fallback_data

      data = resp.json().get("data", {})
      title = data.get("title") or "Binance Official Announcement"
      raw_body = data.get("body", "")

      html_content = ""
      if raw_body.startswith("{") and '"node":' in raw_body:
        try:
          ast_tree = json.loads(raw_body)
          html_content = self._parse_binance_ast_node(ast_tree)
        except Exception:
          html_content = raw_body
      else:
        html_content = raw_body

      soup = BeautifulSoup(html_content, "html.parser")

      # 1. Trích xuất hình ảnh
      extracted_images = []
      seen_img = set()
      for img in soup.find_all("img"):
        src = img.get("src", "").strip()
        if (
            src
            and src.startswith("http")
            and src not in seen_img
            and not re.search(r"(icon|avatar|logo|\.svg)", src, re.I)
        ):
          seen_img.add(src)
          extracted_images.append({
              "url": src,
              "caption": img.get("alt", "").strip(),
              "alt": img.get("alt", "").strip(),
          })

      # 2. Trích xuất văn bản & Bảng dữ liệu (Tables)
      paragraphs = []
      for elem in soup.find_all(["p", "h2", "h3", "li", "table"]):
        if elem.name == "table":
          rows = []
          for tr in elem.find_all("tr"):
            cells = [
                th_td.get_text(separator=" ", strip=True)
                for th_td in tr.find_all(["th", "td"])
            ]
            if cells and any(c for c in cells):
              rows.append(" | ".join(cells))
          if rows:
            paragraphs.append("\n[DATA TABLE]:\n" + "\n".join(rows) + "\n")
        else:
          text = elem.get_text(strip=True)
          if (
              len(text) > 20
              and not elem.find_parent("table")
              and not elem.find_parent("figcaption")
          ):
            paragraphs.append(text)

      content = "\n\n".join(paragraphs)
      print(
          f"✅ Trích xuất thành công {len(paragraphs)} đoạn/bảng (Độ dài:"
          f" {len(content)} ký tự)."
      )
      print(f"📷 Tìm thấy {len(extracted_images)} ảnh gốc.")

      return {
          "url": default_url,
          "title": title,
          "content": content,
          "source_images": extracted_images,
      }

    except Exception as e:
      print(f"⚠️ Lỗi bóc tách bài {article_code}: {e}")
      return fallback_data


# =====================================================================
# 🤖 MODULE 2: AI PROCESSOR (GEMINI + WORDPRESS SEO + TABLES)
# =====================================================================
class ArticleOutputSchema(BaseModel):
  editorial_plan: str
  title: str
  slug: str
  meta_description: str
  categories: str
  key_takeaways: list[str]
  image_prompt: str
  content_html: str


class AIProcessor:

  def __init__(
      self,
      api_key: str,
      model_id: str,
      csv_log_path: str,
      site_url: str,
      platform: str = "VB",
      fallback_models: list[str] | None = None,
  ):
    self.gemini = GeminiFallbackClient(
        api_key=api_key, model_id=model_id, fallback_models=fallback_models
    )
    self.model_id = model_id
    self.csv_log_path = os.path.abspath(csv_log_path)
    self.site_url = site_url.rstrip("/")
    self.platform = platform.upper()

  def _get_dynamic_internal_links(self, max_links: int = 10) -> list[dict]:
    """Đọc file CSV bài viết cũ, trích xuất slug và title."""
    links = []
    if not os.path.exists(self.csv_log_path):
      return links

    try:
      with open(self.csv_log_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
          if not isinstance(row, dict):
            continue
          clean_row = {
              str(k).strip(): str(v).strip()
              for k, v in row.items()
              if k is not None
          }
          slug = (
              clean_row.get("Post Slug")
              or clean_row.get("slug")
              or clean_row.get("Post slug")
              or ""
          )
          title = (
              clean_row.get("Post Title")
              or clean_row.get("title")
              or clean_row.get("Post title")
              or ""
          )

          if (
              slug
              and slug.lower() != "none"
              and slug != "post-thumb"
              and not slug.startswith("http")
          ):
            clean_slug = slug.strip("/")
            full_url = f"{self.site_url}/{clean_slug}"
            clean_title = (
                title if title else clean_slug.replace("-", " ").title()
            )
            links.append(
                {"title": clean_title, "slug": clean_slug, "url": full_url}
            )

      return links[-max_links:] if len(links) > max_links else links
    except Exception as e:
      print(f"⚠️ Lỗi đọc CSV: {e}")
      return []

  def _force_inject_latest_links(
      self, html_content: str, links: list[dict]
  ) -> str:
    """Đảm bảo bài viết luôn có link theo format 'More Update: title'."""
    if not links:
      return html_content

    target_link = links[-1]
    target_url = target_link["url"]
    target_title = target_link["title"]

    link_html = (
        f"<p style='margin-top: 15px;'><em>More Update: <a href='{target_url}'"
        f" style='color: #00AF61; font-weight:"
        f" 600;'>{target_title}</a></em></p>"
    )

    if target_url in html_content:
      return html_content

    paragraphs = re.findall(r"<p.*?>.*?</p>", html_content, flags=re.DOTALL)
    if len(paragraphs) >= 3:
      mid_idx = len(paragraphs) // 2
      replacement = paragraphs[mid_idx] + "\n" + link_html
      html_content = html_content.replace(paragraphs[mid_idx], replacement, 1)
    elif "<p><em>Source:" in html_content:
      html_content = html_content.replace(
          "<p><em>Source:", f"{link_html}\n<p><em>Source:"
      )
    else:
      html_content += f"\n{link_html}"

    print(f"✅ Đã chèn Internal Link: More Update: {target_title}")
    return html_content

  def process(self, raw_data: dict) -> dict:
    platform_name = "Ventureburn" if self.platform == "VB" else "Chainplay"
    print(f"🤖 [Module 2] Đang gửi nội dung sang Gemini biên tập ({platform_name} SEO English)...")
    internal_links = self._get_dynamic_internal_links()
    internal_links_json = json.dumps(
        internal_links, ensure_ascii=False, indent=2
    )
    source_images_json = json.dumps(
        raw_data.get("source_images", []), ensure_ascii=False, indent=2
    )

    if self.platform == "VB":
      persona = (
          "You are a senior financial & technology SEO editor for Ventureburn (a premier global digital publication covering tech innovation, Web3, cryptocurrency, startups, and capital markets). "
          "Your task is to analyze the extracted official Binance announcement, structure a comprehensive editorial outline, and write an authoritative, highly engaging news story in ENGLISH for investors, tech founders, and crypto traders."
      )
      source_citation = f"<p><em>Source: <a href='{raw_data.get('url', '')}' target='_blank'>Binance Official Announcement</a> via Ventureburn</em></p>"
      image_prompt_rule = """
6. IMAGE PROMPT RULE (16:9 TECH NEWS BRANDED POSTER):
   Synthesize an Image Generation Prompt strictly following these visual styling rules:

   - ASPECT RATIO: 16:9 widescreen landscape (1600x900 / 1312x736).

   - OVERALL AESTHETIC & RENDERING STYLE:
     * STYLE: High-end 3D tech diorama illustration, sleek Octane/Redshift 3D render, miniature architectural tech studio aesthetic. Smooth clay-matte materials combined with glossy reflective dark floor surfaces.
     * ENVIRONMENT & SCENE: A futuristic dark interior space (e.g., high-tech lab, control room, server corridor, or conceptual trading floor). Sharp linear neon light strips tracing along the ceiling, perspective walls, and floor boundaries.
     * THEMATIC CHARACTERS & OBJECTS: 
       - Stylized faceless or low-poly 3D miniature human figures in professional suits or lab coats, interacting with floating holographic HUD screens, digital charts, and monitors.
       - A prominent 3D symbolic hero object placed in the foreground/table center (e.g., glowing tech sphere, metallic bull, digital node, cyber hardware device).

   - TEXT RENDERING & POSITIONING (PROMINENT SHORT HEADLINE ONLY):
     * POSITION: Top-left or top-center quadrant, clean 2-4 lines multiline headline layout.
     * CONDENSED HEADLINE RULE (CRITICAL):
       - DO NOT use the full article title. 
       - Condense the main event into an ultra-short, high-impact headline of STRICTLY 3 TO 5 WORDS MAXIMUM (Format: "[SUBJECT] [VERB] [METRIC/KEYWORD]").
       - Examples of ideal length: "BINANCE LISTS TOKEN", "AIRDROP POOL LIVE", "TRADING REWARDS LAUNCH".
     * EXACT TEXT CONTENT: Wrap this exact 3-5 word condensed title in double quotes (e.g., "SHORT PUNCHY TITLE").
     * TYPOGRAPHY STYLE: Massive, ultra-bold, modern geometric sans-serif (Inter / Helvetica Black style), flat fill, perfectly legible.
     * TEXT COLOR: High-contrast pure stark white (#FFFFFF) for the primary headline, and vivid radiant electric lime (#00FF66 / #25F47E) for framed highlight numbers. Solid flat vector fill, razor-sharp vector borders, zero 3D bevels.
     * STRICT ZERO-TOLERANCE TEXT NEGATIVE: Strictly NO extra unrequested text, no secondary subtitles, no body paragraphs, no watermarks, no illegible gibberish. ONLY the 3-5 word headline and the single central framed metric text.
     
   - COLOR PALETTE & LIGHTING (VIBRANT ULTRA-SATURATED NEON TONE):
     * DOMINANT GREEN THEME: A vivid, hyper-saturated radioactive emerald and electric jade environment (#00FF66 to #044421 rich dynamic gradient), fully illuminated with intense photoluminescent green glow, zero muddy darkness.
     * BRIGHT GRID PERSPECTIVE: Ultra-luminous cyber-lime perspective grid floor (#00FF88 / #39FF14) with brilliant glowing neon tiles reflecting laser-sharp specular highlights across mirror-polished dark obsidian surfaces.
     * MULTI-COLOR NEON ACCENTS: Saturated, blazing neon conduit tubes and glowing cable loops framing the center, clashing electric cyber lime (#00FF66) with intense fluorescent laser cyan (#00F0FF) and radiant vivid ultraviolet (#D946EF) for extreme chromatic vibrancy.
     * WARM CHROMATIC ACCENTS: Blazing, high-intensity neon amber-orange (#FF5500) and golden solar-yellow (#FFD700) illuminated beacons that violently pop with sharp complementary contrast against the vibrant green atmosphere.
     * LIGHTING & RENDERING AESTHETIC: Hyper-saturated 3D Octane render, heavy neon bloom glow, crisp volumetric light rays, intense chromatic bounce lighting, glossy wet-reflective specular highlights, razor-sharp vector clarity.
     * STRICT NEGATIVE FILTER: NO monochromatic dull tones, NO dark muddy shadows, NO pitch-black dead space, NO washed-out desaturated pastels, NO flat dim lighting, NO dusty or grainy textures, NO photorealistic gritty noise.

   - ZERO-TOLERANCE TEXT & ARTIFACT RESTRICTIONS:
     * MANDATE: The ONLY permitted text on the canvas is the quoted headline in the top-left.
     * NEGATIVE FILTER: Strictly NO corner logos, NO tiny brand icons, NO watermarks, NO badges, NO subtitles, NO micro-text, NO pseudocode/runes, NO surface model/serial numbers, NO blurry clutter, NO UI HUD elements.
"""
    else:
      persona = (
          "You are a senior financial & cryptocurrency journalist and SEO editor for Chainplay (a leading Web3 & Crypto publication). "
          "Your task is to analyze the extracted official Binance announcement, structure a comprehensive editorial outline, and write an authoritative, highly engaging news story in ENGLISH for crypto traders, Web3 gamers, and investors."
      )
      source_citation = f"<p><em>Source: <a href='{raw_data.get('url', '')}' target='_blank'>Binance Official Announcement</a> via Chainplay</em></p>"
      image_prompt_rule = """
6. IMAGE PROMPT RULE (16:9 MINIMALIST SILHOUETTE & PULP SCI-FI POSTER):
    Synthesize an Image Generation Prompt strictly following these visual specifications:

    - ASPECT RATIO: 16:9 widescreen landscape.

    - ART STYLE & AESTHETIC (CRITICAL):
      * Minimalist Silhouette Poster Art combining Retro Pulp Sci-Fi and Western Graphic Novel aesthetics (inspired by vintage pulp sci-fi and Olly Moss minimalist sunset posters).
      * Clean 2D vector graphic art, bold flat shapes, sharp silhouette cutouts, and crisp geometric precision (STRICTLY NOT photorealistic, NOT 3D render/CGI).

    - FOREGROUND & SILHOUETTES:
      * Solid pitch-black silhouettes (flat solid black shapes) for all foreground subjects, terrain, characters, cybernetic figures, crypto hardware, retro vehicles, or architectural structures matching the announcement theme.
      * Subtle warm rim lighting/edge glow reflecting from the sunset background onto silhouette edges.

    - COMPOSITION & BACKGROUND:
      * MINIMALIST GRAPHIC COMPOSITION: Clean flat horizon with a giant, majestic glowing sun / celestial circle dominating the center or backdrop, framing the silhouette subject.
      * Atmospheric depth created through layered flat silhouettes (distant dunes, futuristic towers, mountains, or landscape).

    - COLOR PALETTE & LIGHTING:
      * WARM MONOCHROMATIC GRADIENT: High-contrast warm sunset sky transitioning smoothly from radiant bright golden yellow -> blazing fiery orange -> deep crimson / brick red -> dark twilight dusk.
      * High contrast, dramatic atmospheric glow, clean vintage poster tones.

    - TEXT RENDERING & POSITIONING (PROMINENT SHORT HEADLINE ONLY):
      * POSITION: Upper sky quadrant or top-center, clean multiline headline layout.
      * CONDENSED HEADLINE RULE (CRITICAL):
        - DO NOT use the full article title. 
        - Condense the main event into an ultra-short, high-impact headline of STRICTLY 3 TO 5 WORDS MAXIMUM (Format: "[SUBJECT] [VERB] [METRIC/KEYWORD]").
        - Examples of ideal length: "BINANCE LISTS NEW TOKEN", "AIRDROP REWARD POOL OPEN", "TRADING TOURNAMENT LAUNCHES".
      * EXACT TEXT CONTENT: Wrap this exact 3-5 word condensed title in double quotes (e.g., "SHORT PUNCHY TITLE").
      * TYPOGRAPHY STYLE: Massive, ultra-bold, modern geometric sans-serif (Inter / Helvetica Black / Bold Display style), perfectly crisp and legible.
      * TEXT COLOR: High-contrast solid pure white (#FFFFFF) or warm golden yellow (#FFDD55). Completely flat.
      * STRICT ZERO-TOLERANCE TEXT NEGATIVE: Strictly NO extra unrequested text, no secondary subtitles, no body paragraphs, no watermarks, no illegible gibberish. ONLY the 3-5 word headline in double quotes.

    - STRICT NEGATIVE FILTER:
      * No photorealistic CGI, no 3D octane render, no muddy textures, no washed-out pastel tones, no cluttered backgrounds.
"""

    prompt = f"""
{persona}

======================================================================
INPUT DATA
======================================================================
ORIGINAL TITLE: {raw_data.get('title', '')}
ORIGINAL URL: {raw_data.get('url', '')}
ORIGINAL CONTENT:
{raw_data.get('content', '')}

AVAILABLE SOURCE IMAGES:
{source_images_json}

DYNAMIC INTERNAL LINKS REPOSITORY:
{internal_links_json}

======================================================================
PHASE 1: EDITORIAL PLANNING & VERIFICATION (`editorial_plan`)
======================================================================
1. FACT-CHECK & METRIC AUDIT: Extract all exact token names, ticker symbols, listing dates/times (UTC), trading pairs, prize pools, deposit schedules, and fee details.
2. ANGLE & HOOK FORMULATION: Identify the primary news angle that matters most to active traders and community members.
3. BLUEPRINT & PACING: Outline sections with clear talking points, natural burstiness, and accurate data preservation.

======================================================================
PHASE 2: ARTICLE DRAFTING RULES (`content_html`)
======================================================================
1. TARGET ARTICLE LENGTH (~400 WORDS / 380 - 450 WORDS):
   - Provide a comprehensive, engaging, professional crypto news article matching top publication standards (CoinDesk, Cointelegraph).
   - Structure logically into 3 distinct sections with 2 to 3 topic-specific <h2> tags:
     a) Journalistic Lead (No H2 heading, ~60-80 words): A punchy, hook-driven intro answering Who, What, When, and Key Reward/Metric numbers.
     b) Core Mechanics & Feature Breakdown (~150-180 words under <h2>): Thoroughly explain the background of the token/event, promotion phases, prize pool allocation, and special boost multipliers.
     c) Participation Guide & Eligibility (~120-150 words under <h2>): Step-by-step practical guide on how traders can participate via Binance Wallet / Alpha / Web3, exact qualifying rules (e.g. buy volume criteria, timeframes, snapshot dates, reward distribution timeline).

2. ADAPTIVE LENGTH & ZERO FLUFF:
   - Rely EXCLUSIVELY on verified facts from source. Zero hallucinations.
   - BANNED CLICHÉS: "delve into", "testament to", "ever-evolving", "tapestry", "pivotal", "game-changer", "moreover", "in conclusion", "beacon", "underscores".
   - Maintain natural sentence cadence and burstiness (mix 3-5 word sentences with 15-20 words).
   - Use single quotes (') for all HTML attributes.
   - DATA TABLES: If the source contains tables or structured metrics (e.g., listing schedule, trading pairs, reward pools, fee tiers, tokenomics, multipliers), format them into clean, responsive WordPress HTML tables:
     <table class='wp-block-table' style='width: 100%; border-collapse: collapse; margin: 15px 0;'><thead><tr><th>Column 1</th><th>Column 2</th></tr></thead><tbody><tr><td>Data 1</td><td>Data 2</td></tr></tbody></table>
   - Embed original source figures under relevant <h2> if available:
     <figure class='wp-block-image'><img src='[IMAGE_URL]' alt='[ALT_TEXT]' /><figcaption>[ORIGINAL_CAPTION]</figcaption></figure>
   - Append source citation at the very end:
     {source_citation}

3. KEY TAKEAWAYS: Strictly 3 concise bullet points in `key_takeaways` array (Plain text only, NO HTML).
4. METADATA:
   - slug: 3-5 words, lowercase, hyphen-separated.
   - meta_description: STRICTLY SHORT (100 - 130 characters maximum). A single punchy, high-impact sentence summarizing the core hook without truncation.
   - categories: 3-5 relevant comma-separated tags (e.g., Binance, Crypto Listing, Airdrop, Trading).

{image_prompt_rule}
"""

    def _parse_article(resp) -> dict:
      try:
        parsed = json.loads(resp.text)
      except Exception:
        parsed = json.loads(repair_json(resp.text))
      if not isinstance(parsed, dict) or not parsed.get("content_html"):
        raise ValueError("JSON bài viết thiếu content_html")
      return parsed

    _, result = self.gemini.generate(
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ArticleOutputSchema,
            max_output_tokens=9192,
            temperature=0.6,
        ),
        validate=_parse_article,
        label="Module 2",
    )

    if not isinstance(result.get("key_takeaways"), list):
      result["key_takeaways"] = []

    result["content_html"] = self._force_inject_latest_links(
        result.get("content_html", ""), internal_links
    )
    return result


# =====================================================================
# 🔍 MODULE 2.5: AI DETECTOR & CONTENT HUMANIZER
# =====================================================================
class AICheckResult(BaseModel):
  ai_percentage: int
  verdict: str
  flagged_phrases: list[str]


class AICheckerModule:

  def __init__(
      self,
      api_key: str,
      check_model: str,
      rewrite_model: str,
      fallback_models: list[str] | None = None,
  ):
    self.check_gemini = GeminiFallbackClient(
        api_key=api_key, model_id=check_model, fallback_models=fallback_models
    )
    self.rewrite_gemini = GeminiFallbackClient(
        api_key=api_key, model_id=rewrite_model, fallback_models=fallback_models
    )
    self.check_model = check_model
    self.rewrite_model = rewrite_model

  def _extract_plain_text(self, html_content: str) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in soup.find_all(["figcaption", "em"]):
      tag.decompose()
    return soup.get_text(separator=" ", strip=True)

  def evaluate_score(self, html_content: str) -> dict:
    plain_text = self._extract_plain_text(html_content)
    prompt = f"""
You are an expert AI detection classifier calibrated to detect LLM writing patterns.
Analyze the following text and assign an estimated AI generation probability percentage (0 to 100).

EVALUATION CRITERIA:
- Uniform sentence cadence and lack of structural variation (high AI score).
- Overuse of transitional clichés: "delve", "testament", "pivotal", "landscape", "crucial role", "furthermore".
- Organic sentence mix with rich gaming/finance terminology and varying lengths (low AI score / human).

TEXT SAMPLE:
\"\"\"{plain_text[:2500]}\"\"\"
"""
    try:
      def _parse_check(resp) -> dict:
        try:
          parsed = json.loads(resp.text)
        except Exception:
          parsed = json.loads(repair_json(resp.text))
        if not isinstance(parsed, dict) or "ai_percentage" not in parsed:
          raise ValueError("JSON chấm điểm thiếu ai_percentage")
        return parsed

      _, result = self.check_gemini.generate(
          contents=prompt,
          config=types.GenerateContentConfig(
              response_mime_type="application/json",
              response_schema=AICheckResult,
              temperature=0.1,
          ),
          validate=_parse_check,
          label="Module 2.5 Check",
      )
      return result
    except Exception as e:
      print(f"⚠️ [Module 2.5] Lỗi khi dùng {self.check_model} chấm điểm: {e}")
      return {"ai_percentage": 0, "verdict": "Check Failed", "flagged_phrases": []}

  def humanize_content(
      self, html_content: str, flagged_phrases: list[str]
  ) -> str:
    prompt = f"""
Rewrite the following article to sound 100% human-written in ENGLISH.

CURRENT HTML CONTENT:
{html_content}

CRITICAL REWRITE RULES:
1. STRUCTURAL INTEGRITY: Preserve ALL HTML tags (<h2>, <figure>, <figcaption>, <img>, <a>, <table>, <thead>, <tbody>, <tr>, <th>, <td>). NEVER drop tables, internal links (<p><em>More Update: ...</em></p>), or source links.
2. PURGE CLICHÉS: Completely remove these flagged robotic words: {json.dumps(flagged_phrases)}.
3. DISTRIBUTED BURSTINESS: Interweave ultra-short sentences (3-5 words), medium sentences (10-15 words), and complex compound clauses (25+ words).
4. VOCABULARY: Use authentic natural journalistic flow. Never start consecutive sentences with the same subject.
5. LENGTH RETENTION: Maintain the full informative depth and ~380-450 word length. Do NOT summarize or condense sections.

Output ONLY valid HTML body string.
"""
    try:
      resp, _ = self.rewrite_gemini.generate(
          contents=prompt,
          config=types.GenerateContentConfig(temperature=0.85),
          label="Module 2.5 Humanize",
      )
      return (
          resp.text.strip()
          .replace("```html", "")
          .replace("```json", "")
          .replace("```", "")
      )
    except Exception as e:
      print(f"⚠️ Humanize thất bại trên mọi model/key, giữ nội dung gốc: {e}")
      return html_content

  def process(self, article_data: dict, threshold: int = 35) -> dict:
    html_content = article_data.get("content_html", "")
    if not html_content:
      return article_data

    eval_data = self.evaluate_score(html_content)
    score = eval_data.get("ai_percentage", 0)
    flagged = eval_data.get("flagged_phrases", [])

    print(
        f"🔍 [Module 2.5] Model [{self.check_model}] chấm AI Score: {score}% |"
        f" Đánh giá: {eval_data.get('verdict')}"
    )

    if score > threshold:
      print(
          f"⚠️ AI Score ({score}%) > {threshold}%. Đang dùng"
          f" [{self.rewrite_model}] Humanize lại..."
      )
      article_data["content_html"] = self.humanize_content(
          html_content, flagged
      )
      recheck = self.evaluate_score(article_data["content_html"])
      final_score = recheck.get("ai_percentage", 0)
      article_data["ai_score"] = final_score
      print(
          f"✨ [Module 2.5] Điểm sau Humanize: {final_score}%"
          f" ({recheck.get('verdict')})"
      )
    else:
      print(
          f"✅ [Module 2.5] Bài viết đạt chuẩn an toàn (<={threshold}% AI). Sẵn"
          " sàng đăng tải!"
      )
      article_data["ai_score"] = score

    return article_data


# =====================================================================
# 📦 MODULE 3: IMAGE GENERATOR (Hugging Face + Watermark Logo)
# =====================================================================
class HFImageEngine:

  def __init__(self, config: dict):
    self.enabled = config.get("ENABLE_GENERATION", True)
    self.platform = config.get("PLATFORM", "VB").upper()
    self.api_token = config.get("HF_API_TOKEN")
    self.model_id = config.get("HF_MODEL_ID")
    self.output_dir = config.get("OUTPUT_DIR", os.path.abspath("./downloads"))
    
    # Tự động gán logo theo thương hiệu
    default_logo_file = "VB_white_logo.png" if self.platform == "VB" else "CP_white_logo.png"
    logo_candidate = config.get("LOGO_PATH") or os.path.join(BASE_DIR, default_logo_file)
    if not os.path.exists(logo_candidate):
      alt_candidate = os.path.join(os.path.dirname(BASE_DIR), "tk_bot", default_logo_file)
      if os.path.exists(alt_candidate):
        logo_candidate = alt_candidate
    self.logo_path = logo_candidate
    os.makedirs(self.output_dir, exist_ok=True)

    # Cấu hình color_enforcement_suffix chuẩn thương hiệu
    if "COLOR_ENFORCEMENT_SUFFIX" in config:
      self.color_enforcement_suffix = config["COLOR_ENFORCEMENT_SUFFIX"]
    elif self.platform == "VB":
      # Bộ màu thương hiệu Ventureburn chuẩn trích xuất từ vb_wp_poster.ipynb
      self.color_enforcement_suffix = (
          " glowing radioactive electric lime #00FF66, vivid laser cyan #00F0FF, radiant hot magenta accents, "
          "glossy mirror-like floor reflections, heavy neon bloom glow cold color temperature, cinematic Octane render, "
          "razor-sharp contrast, volumetric lighting, 2k resolution, photorealistic, no red orange, no watermarks, no extra text"
      )
    else:
      # Bộ màu thương hiệu Chainplay chuẩn (Pulp Sci-Fi Sunset)
      self.color_enforcement_suffix = (
          ",minimalist silhouette poster art style, retro pulp sci-fi and western graphic novel aesthetic,"
          " solid flat black silhouettes, crisp vector shapes, giant glowing circular sunset sun backdrop,"
          " warm monochromatic sunset gradient from golden yellow to fiery orange to deep crimson red,"
          " minimalist graphic composition, flat 2D vector art, no 3D render, no photorealistic"
      )

    if self.enabled and self.api_token:
      self.client = InferenceClient(
          model=self.model_id, token=self.api_token, timeout=90
      )

  def _apply_logo(self, base_image: Image.Image) -> Image.Image:
    if not self.logo_path or not os.path.exists(self.logo_path):
      print(f"ℹ️ [Module 3] Bỏ qua chèn logo (không tìm thấy file: {self.logo_path})")
      return base_image

    try:
      logo = Image.open(self.logo_path).convert("RGBA")
      target_logo_width = int(base_image.width * 0.14)
      w_percent = target_logo_width / float(logo.size[0])
      target_logo_height = int(float(logo.size[1]) * float(w_percent))
      logo = logo.resize(
          (target_logo_width, target_logo_height), Image.Resampling.LANCZOS
      )

      pos_x = int((base_image.width - target_logo_width) / 2)
      pos_y = int(base_image.height - target_logo_height - 25)
      position = (pos_x, pos_y)

      base_rgba = base_image.convert("RGBA")
      base_rgba.paste(logo, position, logo)
      return base_rgba.convert("RGB")
    except Exception as e:
      print(f"⚠️ Không thể chèn logo: {e}")
      return base_image

  def generate_and_download(self, prompt: str, slug: str) -> tuple[str | None, str]:
    if not self.enabled:
      return None, "Skipped (Disabled by user)"

    final_prompt = prompt + self.color_enforcement_suffix

    brand_label = "Ventureburn (VB)" if self.platform == "VB" else "Chainplay (CP)"
    print(f"🎨 [Module 3] Đang tạo ảnh 16:9 phong cách {brand_label} với {self.model_id}...")
    try:
      raw_image: Image.Image = self.client.text_to_image(
          prompt=final_prompt, width=1600, height=900
      )
      target_size = (1600, 900)
      fitted_image = ImageOps.fit(
          raw_image, target_size, method=Image.Resampling.LANCZOS
      )
      final_image = self._apply_logo(fitted_image)

      file_name = f"{slug[:50]}_{int(time.time())}.jpg"
      file_path = os.path.join(self.output_dir, file_name)
      final_image.save(file_path, "JPEG", quality=95)

      print(f"✅ Đã lưu ảnh hoàn chỉnh tại: {file_path}")
      return file_path, f"Success ({file_name})"
    except Exception as e:
      err_msg = str(e)
      print(f"⚠️ Lỗi kết nối tạo ảnh Hugging Face: {err_msg}")
      return None, f"Failed ({err_msg[:60]})"


# =====================================================================
# 📦 MODULE 4: WORDPRESS PUBLISHER (Playwright Headless / Server Ready)
# =====================================================================
class WordPressPublisher:

  def __init__(self, config: dict):
    self.site_url = config.get("SITE_URL", "").rstrip("/")
    self.login_url = config.get("LOGIN_URL", f"{self.site_url}/wp-login.php")
    self.username = config.get("USERNAME", "")
    self.password = config.get("PASSWORD", "")
    self.profile_dir = config.get("PROFILE_DIR", os.path.abspath("./wp_profile"))
    self.headless = config.get("HEADLESS", True)
    self.output_dir = os.path.abspath("./downloads")
    os.makedirs(self.profile_dir, exist_ok=True)
    os.makedirs(self.output_dir, exist_ok=True)

  # Trang chặn bot (Cloudflare / WAF của host) - KHÔNG phải trang đăng nhập
  _CHALLENGE_MARKERS = ("attention required", "just a moment", "checking your browser", "verify you are human")
  _ADMIN_SELECTOR = "#wpadminbar, .edit-post-layout, .editor-header, .block-editor, #wpbody"

  async def _is_challenge(self, page) -> bool:
    # Trang có thể đang chuyển hướng (Cloudflare/WP redirect) -> chờ ổn định rồi thử lại
    for _ in range(3):
      try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
        title = (await page.title()).lower()
        if any(m in title for m in self._CHALLENGE_MARKERS) or "wsidchk=" in page.url:
          return True
        return await page.query_selector("#challenge-form, #cf-wrapper, .cf-error-details") is not None
      except Exception:
        await asyncio.sleep(2)
    return "wsidchk=" in page.url

  async def _is_logged_in_admin(self, page, wait_ms: int = 8000) -> bool:
    """Chỉ coi là đã đăng nhập khi THẤY giao diện admin (Gutenberg load chậm nên chờ một chút)."""
    try:
      await page.wait_for_selector(self._ADMIN_SELECTOR, timeout=wait_ms)
      return True
    except Exception:
      return False

  async def _wait_out_challenge(self, page) -> bool:
    """Trang Cloudflare: headless thì báo thất bại; có cửa sổ thì chờ người dùng tự xác nhận (tối đa 3 phút)."""
    if not await self._is_challenge(page):
      return True
    if getattr(self, "_headless_now", True):
      print(f"🛡️ Bị Cloudflare chặn tại {page.url[:80]}... (headless không vượt được).")
      return False
    print("🛡️ Bị Cloudflare chặn. Vui lòng tự xác nhận trên cửa sổ trình duyệt (chờ tối đa 3 phút)...")
    for _ in range(90):
      await asyncio.sleep(2)
      if not await self._is_challenge(page):
        print("✅ Đã qua trang Cloudflare.")
        return True
    print("❌ Hết thời gian chờ xác nhận Cloudflare.")
    return False

  async def _needs_login(self, page, response=None) -> bool:
    """True nếu CHƯA vào được trang admin: 404, trang login, trang chặn bot, hoặc bất kỳ trang nào không có giao diện admin."""
    if await self._is_challenge(page):
      return True
    if response is not None and response.status == 404:
      return True
    if "login" in page.url.lower():
      return True
    return not await self._is_logged_in_admin(page)

  async def _do_login_form(self, page) -> bool:
    """Điền form đăng nhập trên trang hiện tại. Trả về True nếu đã submit."""
    try:
      await page.wait_for_selector("#user_login, input[name='log']", timeout=15000)
    except Exception:
      print(f"⚠️ Không thấy form đăng nhập tại {page.url}")
      return False
    await page.fill("#user_login, input[name='log']", self.username)
    await page.fill("#user_pass, input[name='pwd']", self.password)
    remember = await page.query_selector("#rememberme, input[name='rememberme']")
    if remember:
      await remember.check()
    submit_btn = await page.query_selector(
        "#wp-submit, button[type='submit'], input[type='submit']"
    )
    if not submit_btn:
      print("⚠️ Không thấy nút đăng nhập.")
      return False
    await submit_btn.click()
    try:
      await page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
      pass
    await asyncio.sleep(2)
    return True

  async def _ensure_login(self, page, response=None, target_url: str | None = None) -> bool:
    """
    Fallback đăng nhập:
      1. Nếu trang trả 404 / trang login / không có admin bar ➔ coi như chưa đăng nhập.
      2. Mở LOGIN_URL (vd: https://ventureburn.com/tklogin/) và đăng nhập bằng tài khoản trong tk_key.env.
      3. Quay lại target_url (trang tạo bài).
    Trả về True nếu đã đăng nhập (hoặc vốn đã đăng nhập).
    """
    if not await self._wait_out_challenge(page):
      return False
    if not await self._needs_login(page, response):
      return True

    status = response.status if response is not None else "?"
    print(f"🔑 Chưa đăng nhập (HTTP {status}, URL: {page.url}). Chuyển sang trang đăng nhập: {self.login_url}")
    if not self.username or not self.password:
      print("⚠️ Thiếu VB_WP_USER/VB_WP_PASSWORD hoặc CP_WP_USER/CP_WP_PASSWORD trong tk_key.env, bỏ qua tự động đăng nhập.")
      return False

    for attempt in range(1, 3):
      try:
        if "login" not in page.url.lower() or attempt > 1:
          await page.goto(self.login_url, timeout=60000, wait_until="domcontentloaded")
          await asyncio.sleep(1)
        if not await self._wait_out_challenge(page):
          return False
        if not await self._do_login_form(page):
          continue
        if "login" in page.url.lower() and await page.query_selector("#login_error"):
          err = (await page.inner_text("#login_error")).strip()
          print(f"❌ WordPress từ chối đăng nhập: {err}")
          return False
        if target_url:
          resp = await page.goto(target_url, timeout=60000, wait_until="domcontentloaded")
          await asyncio.sleep(2)
          if not await self._needs_login(page, resp):
            print("✅ Đăng nhập thành công, đã quay lại trang tạo bài.")
            return True
          print(f"⚠️ Lần {attempt}: vẫn chưa vào được {target_url} sau khi đăng nhập.")
        else:
          print("✅ Đã gửi form đăng nhập.")
          return True
      except Exception as e:
        print(f"⚠️ Lỗi tự động đăng nhập (lần {attempt}): {e}")
    return False

  async def _upload_file_to_wp(self, page, file_path: str) -> dict | None:
    """Upload ảnh lên WP Media Library qua REST API sử dụng Session Nonce."""
    try:
      nonce = await page.evaluate("""() => {
                if (window.wpApiSettings && window.wpApiSettings.nonce) return window.wpApiSettings.nonce;
                if (window._wpNonce) return window._wpNonce;
                return '';
            }""")

      filename = os.path.basename(file_path)
      with open(file_path, "rb") as f:
        file_bytes = f.read()

      upload_url = f"{self.site_url}/wp-json/wp/v2/media"
      headers = {
          "Content-Disposition": f'attachment; filename="{filename}"',
          "Content-Type": "image/jpeg",
          "Cache-Control": "no-cache",
      }
      if nonce:
        headers["X-WP-Nonce"] = nonce

      response = await page.request.post(
          upload_url, headers=headers, data=file_bytes
      )
      raw_text = await response.text()

      if response.status in [200, 201]:
        try:
          res_json = json.loads(raw_text)
          media_id = res_json.get("id")
          source_url = res_json.get("source_url")
          print(f"  📤 Upload Media ID: {media_id} -> {source_url}")
          return {"id": media_id, "url": source_url}
        except Exception:
          return None
      return None
    except Exception as e:
      print(f"  ⚠️ Lỗi upload media: {e}")
      return None

  async def _set_featured_image(self, page, media_id: int):
    """Gán Featured Image vào bài viết."""
    print(f"🖼️ Đang gán Featured Image (ID: {media_id})...")
    try:
      set_res = await page.evaluate(f"""() => {{
                let results = [];
                // 1. Gutenberg Editor
                if (window.wp && window.wp.data && window.wp.data.dispatch('core/editor')) {{
                    try {{
                        window.wp.data.dispatch('core/editor').editPost({{ featured_media: {media_id} }});
                        results.push('gutenberg');
                    }} catch(e) {{}}
                }}
                // 2. Classic WPSetThumbnailID function
                if (typeof window.WPSetThumbnailID === 'function') {{
                    try {{
                        window.WPSetThumbnailID({media_id});
                        results.push('WPSetThumbnailID');
                    }} catch(e) {{}}
                }}
                // 3. Classic Hidden Input + events
                let thumbInput = document.getElementById('_thumbnail_id');
                if (thumbInput) {{
                    thumbInput.value = '{media_id}';
                    thumbInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    thumbInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    results.push('input_value');
                }}
                return results;
            }}""")
      print(f"  ✅ Đã gán Featured Image qua các phương thức: {set_res}")
    except Exception as e:
      print(f"  ⚠️ Không thể set Featured Image: {e}")

  async def replace_external_images(
      self, page, html_content: str, slug: str
  ) -> str:
    soup = BeautifulSoup(html_content, "html.parser")
    images = soup.find_all("img")

    for idx, img in enumerate(images, 1):
      src = img.get("src")
      if not src or not src.startswith("http") or self.site_url in src:
        continue

      local_path = ImageHelper.download_and_sanitize_image(
          image_url=src, slug=slug, output_dir=self.output_dir, index=idx
      )
      if local_path:
        upload_res = await self._upload_file_to_wp(page, local_path)
        if upload_res and upload_res.get("url"):
          img["src"] = upload_res["url"]
          for attr in [
              "srcset",
              "data-src",
              "data-original-src",
              "data-lazy-src",
              "sizes",
          ]:
            if img.has_attr(attr):
              del img[attr]

    return str(soup)

  async def _execute_create_draft(
      self, article_data: dict, local_image_path: str = None, headless_mode: bool = False
  ) -> tuple[str, str]:
    print(
        f"\n🚀 [Module 4] Đang khởi chạy trình duyệt Playwright (Headless:"
        f" {headless_mode})..."
    )
    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=self.profile_dir,
        headless=headless_mode,
        viewport={"width": 1366, "height": 850},
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    )

    page = context.pages[0] if context.pages else await context.new_page()
    self._headless_now = headless_mode

    try:
      new_post_url = f"{self.site_url}/wp-admin/post-new.php"
      print(f"📝 Mở trang tạo bài: {new_post_url}")
      resp = await page.goto(
          new_post_url, timeout=60000, wait_until="domcontentloaded"
      )
      await asyncio.sleep(2)

      # Kiểm tra và login tự động nếu chưa đăng nhập (kể cả khi wp-admin trả 404)
      logged_in = await self._ensure_login(page, response=resp, target_url=new_post_url)
      if not logged_in and headless_mode:
        raise RuntimeError(f"Chưa vào được wp-admin (Cloudflare chặn hoặc đăng nhập thất bại). URL: {page.url[:100]}")

      # Nếu ở chế độ mở giao diện và bị kẹt trang Login / Cloudflare, chờ người dùng thao tác
      if not headless_mode and not logged_in:
        print("\n👉 Cửa sổ trình duyệt đang mở. Vui lòng đăng nhập hoặc xác nhận Cloudflare trên màn hình...")
        for _ in range(60):
          await asyncio.sleep(2)
          if not await self._is_challenge(page) and await self._is_logged_in_admin(page, wait_ms=1000):
            print("🎉 Đăng nhập thành công, tiếp tục quy trình tạo bài...")
            await page.goto(new_post_url, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            break

      if "login" in page.url.lower():
        await page.goto(
            new_post_url, timeout=60000, wait_until="domcontentloaded"
        )
        await asyncio.sleep(2)

      # Đóng popup Gutenberg modal nếu có
      try:
        close_btn = await page.query_selector(
            "button[aria-label='Close dialog'], button[aria-label='Đóng'],"
            " .components-modal__header button"
        )
        if close_btn:
          await close_btn.click()
          await asyncio.sleep(1)
      except Exception:
        pass

      # 1. Tải và thay thế link ảnh trong bài
      slug_val = article_data.get("slug", "crypto-update").strip()
      clean_html = await self.replace_external_images(
          page, article_data.get("content_html", ""), slug_val
      )
      article_data["content_html"] = clean_html

      # 2. Upload và gán Featured Image
      if local_image_path and os.path.exists(local_image_path):
        print(f"📷 Tải Featured Image lên: {local_image_path}")
        feat_upload = await self._upload_file_to_wp(page, local_image_path)
        if feat_upload and feat_upload.get("id"):
          await self._set_featured_image(page, feat_upload["id"])

      # 3. Điền Tiêu đề
      print(f"✍️ Điền tiêu đề: {article_data['title']}")
      title_selector = (
          "h1.wp-block-post-title, textarea.editor-post-title__input,"
          " #post-title-0, #title"
      )
      try:
        title_elem = await page.wait_for_selector(title_selector, timeout=15000)
      except Exception:
        # Phiên đăng nhập có thể hết hạn giữa chừng ➔ thử đăng nhập lại 1 lần
        print("⚠️ Không thấy ô tiêu đề, thử đăng nhập lại qua trang login...")
        if not await self._ensure_login(page, target_url=new_post_url):
          raise RuntimeError(f"Chưa đăng nhập được WordPress ({self.login_url}). URL hiện tại: {page.url}")
        title_elem = await page.wait_for_selector(title_selector, timeout=20000)
      if title_elem:
        await title_elem.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await page.keyboard.type(article_data["title"])

      # 4. Điền Nội dung bài viết
      print("📄 Điền nội dung bài viết...")
      classic_text_tab = await page.query_selector("#content-html")
      classic_iframe = page.frame(name="content_ifr")

      if classic_text_tab:
        await classic_text_tab.click()
        await page.fill("#content", article_data["content_html"])
      elif classic_iframe:
        await classic_iframe.evaluate(
            f"document.getElementById('tinymce').innerHTML ="
            f" `{article_data['content_html']}`"
        )
      else:
        block = await page.query_selector(
            ".block-editor-default-block-appender__content,"
            " .editor-styles-wrapper p, p.wp-block-paragraph"
        )
        if block:
          await block.click()
          await page.keyboard.insert_text(article_data["content_html"])
        else:
          await page.click(".editor-styles-wrapper")
          await page.keyboard.insert_text(article_data["content_html"])

      await asyncio.sleep(1.5)

      # 5. Gán Slug & Meta Description (Yoast SEO & WordPress Core Excerpt)
      meta_desc_val = article_data.get("meta_description", "").strip()
      focus_kw = slug_val.replace("-", " ").title()
      print(f"⚙️ Gán Slug: '{slug_val}' & Meta Description: '{meta_desc_val[:60]}...'")

      # 5.1. Cập nhật qua Gutenberg Dispatch (Slug, Excerpt & Custom Meta)
      try:
        await page.evaluate(
            """({ slug, excerpt, focusKw }) => {
                if (window.wp && wp.data && wp.data.dispatch) {
                    const editor = wp.data.dispatch('core/editor');
                    if (editor && editor.editPost) {
                        editor.editPost({
                            slug: slug,
                            excerpt: excerpt,
                            meta: {
                                _yoast_wpseo_metadesc: excerpt,
                                yoast_wpseo_metadesc: excerpt,
                                _yoast_wpseo_focuskw: focusKw
                            }
                        });
                    }
                }
            }""",
            {"slug": slug_val, "excerpt": meta_desc_val, "focusKw": focus_kw},
        )
        await page.wait_for_timeout(300)
      except Exception as e:
        print(f"⚠️ Dispatch Slug & Meta: {e}")

      # 5.2. Điền Slug vào giao diện Yoast / WordPress UI
      try:
        yoast_slug_loc = page.locator(
            "input#yoast-slug-input, input#yoast-seo-slug, input#edit-slug-box input,"
            " input[id*='yoast'][id*='slug']"
        ).first
        if await yoast_slug_loc.count() > 0:
          await yoast_slug_loc.scroll_into_view_if_needed(timeout=3000)
          await yoast_slug_loc.fill(slug_val)
          await yoast_slug_loc.evaluate(
              """(el, val) => {
                  const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
                  nativeSetter.call(el, val);
                  el.dispatchEvent(new Event('input', { bubbles: true }));
                  el.dispatchEvent(new Event('change', { bubbles: true }));
                  el.dispatchEvent(new Event('blur', { bubbles: true }));
              }""",
              slug_val,
          )
      except Exception as e:
        print(f"⚠️ Yoast Slug UI: {e}")

      # 5.3. Điền Meta Description vào Yoast SEO Metabox & Excerpt UI
      try:
        filled_desc = await page.evaluate(
            """({ excerpt, focusKw }) => {
                let filled = [];
                let descSelectors = [
                    '#yoast-google-preview-description-metabox',
                    '#yoast_wpseo_metadesc',
                    'textarea[name="yoast_wpseo_metadesc"]',
                    '#yoast-description-input',
                    'textarea[id*="yoast"][id*="desc"]',
                    'textarea[name*="yoast"][name*="desc"]',
                    '.yoast-field-group textarea',
                    'textarea.yoast-field-group__textarea',
                    'div[data-testid*="meta-description"] textarea',
                    '#excerpt',
                    'textarea[name="excerpt"]',
                    'textarea.editor-post-excerpt__textarea'
                ];

                for (let sel of descSelectors) {
                    let elems = document.querySelectorAll(sel);
                    elems.forEach(el => {
                        if (el) {
                            try {
                                el.value = excerpt;
                                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set;
                                if (nativeSetter) nativeSetter.call(el, excerpt);
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                el.dispatchEvent(new Event('blur', { bubbles: true }));
                                filled.push(sel);
                            } catch(e) {}
                        }
                    });
                }

                let kwSelectors = [
                    '#yoast-focus-keyword',
                    'input[name="yoast_wpseo_focuskw"]',
                    '#yoast_wpseo_focuskw',
                    '#yoast-focus-keyword-input',
                    'input[id*="yoast"][id*="focus"]'
                ];
                for (let sel of kwSelectors) {
                    let elems = document.querySelectorAll(sel);
                    elems.forEach(el => {
                        if (el) {
                            try {
                                el.value = focusKw;
                                const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                                if (nativeSetter) nativeSetter.call(el, focusKw);
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                el.dispatchEvent(new Event('change', { bubbles: true }));
                                el.dispatchEvent(new Event('blur', { bubbles: true }));
                            } catch(e) {}
                        }
                    });
                }

                return filled;
            }""",
            {"excerpt": meta_desc_val, "focusKw": focus_kw},
        )
        if filled_desc:
          print(f"  ✅ Đã điền Meta Description qua DOM: {filled_desc}")
        else:
          desc_loc = page.locator(
              "#yoast-google-preview-description-metabox, #yoast_wpseo_metadesc, textarea[name='yoast_wpseo_metadesc'], #excerpt"
          ).first
          if await desc_loc.count() > 0:
            await desc_loc.scroll_into_view_if_needed(timeout=3000)
            await desc_loc.fill(meta_desc_val)
            print("  ✅ Đã điền Meta Description qua Playwright Locator")
      except Exception as e:
        print(f"⚠️ Yoast Meta Description UI: {e}")

      # 6. Điền ACF Key Takeaways (Kèm cơ chế Fallback đa tầng)
      key_takeaways = article_data.get("key_takeaways", [])
      if key_takeaways and isinstance(key_takeaways, list):
        print(f"📌 Đang xử lý {len(key_takeaways)} Key Takeaways...")
        acf_filled = False
        try:
          # TẦNG 1: Sử dụng JavaScript DOM để tìm và điền vào ACF Repeater
          await page.evaluate("window.scrollTo(0, document.body.scrollHeight || document.documentElement.scrollHeight);")
          await asyncio.sleep(0.5)

          takeaways_json = json.dumps(key_takeaways)
          acf_filled = await page.evaluate(f"""() => {{
              let takeaways = {takeaways_json};
              let repeater = document.querySelector('.acf-field-repeater[data-name="key_takeaways"], .acf-field[data-name="key_takeaways"], .acf-field-repeater:has(.acf-label)');
              if (!repeater) return false;

              let addBtn = repeater.querySelector('.acf-actions a.acf-button, a[data-event="add-row"], .acf-repeater-add-row, a.acf-button');
              let existingRows = repeater.querySelectorAll('tbody > tr.acf-row:not(.acf-clone)');
              
              // Bấm thêm dòng nếu còn thiếu
              let attempts = 0;
              while (existingRows.length < takeaways.length && addBtn && attempts < 10) {{
                  addBtn.click();
                  existingRows = repeater.querySelectorAll('tbody > tr.acf-row:not(.acf-clone)');
                  attempts++;
              }}

              // Tìm các ô nhập dữ liệu
              let inputs = repeater.querySelectorAll('tbody > tr.acf-row:not(.acf-clone) input[type="text"], tbody > tr.acf-row:not(.acf-clone) textarea');
              if (inputs.length === 0) return false;

              takeaways.forEach((text, idx) => {{
                  if (idx < inputs.length) {{
                      let input = inputs[idx];
                      input.value = text;
                      try {{
                          let isTextarea = input.tagName.toLowerCase() === 'textarea';
                          let proto = isTextarea ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                          let setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                          if (setter) setter.call(input, text);
                      }} catch(e) {{}}
                      input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                      input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                      input.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                  }}
              }});
              return true;
          }}""")

          if acf_filled:
            print("  ✅ Đã điền xong Key Takeaways vào ACF qua DOM Script!")
          else:
            # TẦNG 2: Sử dụng Playwright Locator với Timeout an toàn
            repeater_box = page.locator(
                ".acf-field-repeater:has(.acf-label:has-text('Key Takeaways')),"
                " .acf-field:has(.acf-label:has-text('Key Takeaways')),"
                " [data-name='key_takeaways'], .acf-field-repeater"
            ).first

            if await repeater_box.count() > 0:
              try:
                await repeater_box.scroll_into_view_if_needed(timeout=2000)
              except Exception:
                pass

              add_btn = repeater_box.locator(
                  ".acf-actions a.acf-button, a[data-event='add-row'], a:has-text('Add Item'), a:has-text('Add Row')"
              ).first

              target_total = len(key_takeaways)
              for _ in range(target_total + 1):
                current_rows = await repeater_box.locator("tbody > tr.acf-row:not(.acf-clone)").count()
                if current_rows < target_total and await add_btn.count() > 0:
                  await add_btn.click(force=True, timeout=2000)
                  await asyncio.sleep(0.3)
                else:
                  break

              input_locators = await repeater_box.locator(
                  "tbody > tr.acf-row:not(.acf-clone) input[type='text'], tbody > tr.acf-row:not(.acf-clone) textarea"
              ).all()

              for i, takeaway_text in enumerate(key_takeaways):
                if i < len(input_locators):
                  target_input = input_locators[i]
                  try:
                    await target_input.scroll_into_view_if_needed(timeout=1500)
                  except Exception:
                    pass
                  await target_input.fill(takeaway_text, timeout=2000)
                  await target_input.evaluate("""el => {
                      el.dispatchEvent(new Event('input', { bubbles: true }));
                      el.dispatchEvent(new Event('change', { bubbles: true }));
                      el.dispatchEvent(new Event('blur', { bubbles: true }));
                  }""")
              print("  ✅ Đã điền xong Key Takeaways vào ACF qua Playwright!")
              acf_filled = True
        except Exception as e:
          print(f"⚠️ Không thể điền vào ACF UI: {e}")

        # TẦNG 3 (FALLBACK): Nếu ACF không thể điền, tự động chèn Key Takeaways Box vào nội dung bài viết HTML
        if not acf_filled:
          print("ℹ️ [Fallback] Tự động chèn khối Key Takeaways Box vào HTML nội dung bài viết...")
          try:
            takeaways_html = (
                "<div class='wp-block-group key-takeaways-box' style='background: #f8f9fa; border-left: 4px solid #F0B90B; padding: 15px 20px; margin: 20px 0; border-radius: 4px;'>"
                "<h3 style='margin-top: 0; color: #1e2329; font-size: 1.1rem; font-weight: bold;'>📌 Key Takeaways</h3>"
                "<ul style='margin-bottom: 0; padding-left: 20px; color: #474d57;'>"
                + "".join(f"<li style='margin-bottom: 6px;'>{html.escape(t)}</li>" for t in key_takeaways)
                + "</ul></div>"
            )
            current_content = article_data.get("content_html", "")
            if "<h2" in current_content:
              updated_content = current_content.replace("<h2", f"{takeaways_html}\n<h2", 1)
            else:
              updated_content = f"{takeaways_html}\n{current_content}"

            # Cập nhật an toàn không gây call stack recursion
            await page.evaluate("""(newHtml) => {
                let classicEditor = document.getElementById('content');
                if (classicEditor) {
                    classicEditor.value = newHtml;
                    classicEditor.dispatchEvent(new Event('input', { bubbles: true }));
                    classicEditor.dispatchEvent(new Event('change', { bubbles: true }));
                }
                if (typeof tinyMCE !== 'undefined' && tinyMCE.get('content')) {
                    tinyMCE.get('content').setContent(newHtml);
                }
            }""", updated_content)
            article_data["content_html"] = updated_content
            print("  ✅ Đã chèn Key Takeaways Box vào nội dung bài viết!")
          except Exception as e:
            print(f"⚠️ Lỗi chèn Fallback Key Takeaways Box: {e}")


      # 7. Bấm Lưu bản nháp (Save draft)
      try:
        save_btn = await page.query_selector(
            "button.editor-post-save-draft, button:has-text('Save draft'),"
            " #save-post"
        )
        if save_btn:
          await save_btn.click()
          await asyncio.sleep(4)
          print("💾 Đã bấm Save Draft thành công.")
      except Exception as e:
        print(f"⚠️ Bỏ qua lỗi click Save Draft: {e}")

      draft_url = page.url
      await context.close()
      await playwright.stop()
      return draft_url, "Success"

    except Exception as e:
      err_msg = str(e)
      print(f"⚠️ Lỗi tạo bài viết: {err_msg}")
      try:
        await context.close()
        await playwright.stop()
      except Exception:
        pass
      return "Failed", err_msg

  async def create_draft(
      self, article_data: dict, local_image_path: str = None
  ) -> tuple[str, str]:
    # 1. Thử tạo bài với cấu hình hiện tại
    draft_url, status = await self._execute_create_draft(
        article_data=article_data,
        local_image_path=local_image_path,
        headless_mode=self.headless,
    )
    if status == "Success":
      return draft_url, status

    # 2. Nếu thất bại ở chế độ headless, tự động mở cửa sổ trình duyệt (Headless: False)
    if self.headless:
      print("\n⚠️ Đăng bài Headless chưa thành công. Tự động mở cửa sổ trình duyệt (Headless: False) để xử lý...")
      draft_url, status = await self._execute_create_draft(
          article_data=article_data,
          local_image_path=local_image_path,
          headless_mode=False,
      )
    return draft_url, status


# =====================================================================
# 📦 MODULE 5: CSV LOGGER (Ghi lịch sử dữ liệu)
# =====================================================================
class CSVLogger:

  def __init__(self, file_path: str):
    self.file_path = file_path
    self.headers = [
        "STT",
        "Date",
        "Source URL",
        "Post Title",
        "Post Slug",
        "Meta Description",
        "Categories",
        "Draft URL",
        "Image Status",
        "Status",
    ]
    self._init_csv()

  def _init_csv(self):
    if not os.path.exists(self.file_path):
      with open(
          self.file_path, mode="w", newline="", encoding="utf-8-sig"
      ) as f:
        writer = csv.writer(f)
        writer.writerow(self.headers)

  def _get_next_stt(self) -> int:
    try:
      with open(
          self.file_path, mode="r", newline="", encoding="utf-8-sig"
      ) as f:
        return len(list(csv.reader(f)))
    except Exception:
      return 1

  def log_article(
      self,
      source_url: str,
      article_data: dict,
      draft_url: str,
      img_status: str,
      status: str,
  ):
    stt = self._get_next_stt()
    row = [
        stt,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        source_url,
        article_data.get("title", ""),
        article_data.get("slug", ""),
        article_data.get("meta_description", ""),
        article_data.get("categories", ""),
        draft_url,
        img_status,
        status,
    ]
    with open(self.file_path, mode="a", newline="", encoding="utf-8-sig") as f:
      writer = csv.writer(f)
      writer.writerow(row)
    print(f"📁 [Module 5] Đã ghi dòng #{stt} vào: {self.file_path}")


# =====================================================================
# 📦 MODULE 6: TELEGRAM NOTIFIER (Gửi báo cáo khi lên bài xong)
# =====================================================================
class TelegramNotifier:

  def __init__(self, config: dict):
    self.enabled = config.get("ENABLE", True)
    self.bot_token = config.get("BOT_TOKEN")
    self.chat_id = config.get("CHAT_ID")
    self.brand_name = config.get("BRAND_NAME")

  def send_post_notification(
      self,
      title: str,
      source_url: str,
      draft_url: str,
      ai_score: int,
      img_status: str,
      brand_name: str = None,
  ):
    if not self.enabled or not self.bot_token or not self.chat_id:
      return

    brand = (
        brand_name
        or self.brand_name
        or ("VENTUREBURN BOT" if "ventureburn" in draft_url.lower() else "CHAINPLAY BOT")
    )
    print(f"📢 [Module 6] Đang gửi thông báo kết quả ({brand}) về Telegram...")
    try:
      now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      msg_text = (
          f"🚀 <b>[{brand}] ĐÃ TẠO BÀI NHÁP (DRAFT) THÀNH CÔNG!</b>\n\n"
          f"📌 <b>Tiêu đề:</b> {html.escape(title)}\n"
          f"🔗 <b>Link gốc Binance:</b> <a href='{source_url}'>Xem thông báo gốc</a>\n"
          f"📝 <b>Link WP Draft:</b> <a href='{draft_url}'>Xem bài nháp</a>\n"
          f"🔍 <b>AI Score:</b> {ai_score}%\n"
          f"🎨 <b>Ảnh bìa:</b> {img_status}\n"
          f"⏰ <b>Thời gian:</b> {now_str}\n"
      )

      api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
      payload = {
          "chat_id": self.chat_id,
          "text": msg_text,
          "parse_mode": "HTML",
          "disable_web_page_preview": False,
      }
      resp = requests.post(api_url, json=payload, timeout=15)
      if resp.status_code == 200:
        print(f"✅ Đã gửi thông báo Telegram thành công (Chat ID: {self.chat_id})")
      else:
        print(f"⚠️ Gửi Telegram thất bại (HTTP {resp.status_code}): {resp.text}")
    except Exception as e:
      print(f"⚠️ Lỗi kết nối Telegram Notifier: {e}")


# =====================================================================
# 🚀 PIPELINE ORCHESTRATOR
# =====================================================================
async def run_news_automation_pipeline(
    news_url: str,
    article_code: str,
    default_title: str = None,
    platform: str = "VB",
):
  platform_mode = platform.upper()
  if platform_mode == "BOTH":
    print("🚀 [Multi-Platform] Kích hoạt chế độ đăng kép: Ventureburn (VB) ➔ Chainplay (CP)...")
    print("\n" + "=" * 60 + "\n--- [BƯỚC 1/2] ĐĂNG BÀI LÊN VENTUREBURN (VB) ---\n" + "=" * 60)
    await run_news_automation_pipeline(news_url, article_code, default_title, platform="VB")
    print("\n⏳ Nghỉ 3 giây trước khi chuyển sang Chainplay...")
    await asyncio.sleep(3)
    print("\n" + "=" * 60 + "\n--- [BƯỚC 2/2] ĐĂNG BÀI LÊN CHAINPLAY (CP) ---\n" + "=" * 60)
    await run_news_automation_pipeline(news_url, article_code, default_title, platform="CP")
    print("\n🏁 [Multi-Platform] Đã hoàn thành toàn bộ quy trình cho cả 2 nền tảng!")
    return

  cfg = get_platform_config(platform_mode)
  scanner = BinanceScanner(config=cfg.get("BINANCE", {}))
  ai_processor = AIProcessor(
      api_key=cfg["GEMINI_API_KEY"],
      model_id=cfg["MODELS"]["MAIN_AI"],
      csv_log_path=cfg["CSV_LOG_PATH"],
      site_url=cfg["WORDPRESS"]["SITE_URL"],
      platform=platform_mode,
  )
  ai_checker = AICheckerModule(
      api_key=cfg["GEMINI_API_KEY"],
      check_model=cfg["MODELS"]["CHECK_AI"],
      rewrite_model=cfg["MODELS"]["REWRITE_AI"],
  )
  image_engine = HFImageEngine(config=cfg.get("IMAGE", {}))
  wp_publisher = WordPressPublisher(config=cfg.get("WORDPRESS", {}))
  csv_logger = CSVLogger(file_path=cfg["CSV_LOG_PATH"])
  tg_notifier = TelegramNotifier(config=cfg.get("TELEGRAM_NOTIFY", {}))

  try:
    # 1. Cào tin chi tiết & Bảng số liệu
    raw_data = scanner.extract_article_detail(
        article_code=article_code, default_url=news_url
    )
    if not raw_data.get("content"):
      print("❌ Dừng pipeline do không lấy được nội dung nguồn.")
      return

    if default_title and (
        not raw_data.get("title")
        or raw_data.get("title") == "Binance Announcement Update"
    ):
      raw_data["title"] = default_title

    # 2. Main AI biên tập sang tiếng Anh chuẩn SEO theo nền tảng
    article_data = ai_processor.process(raw_data)

    # 2.5. Check điểm AI & Humanize nếu cần
    article_data = ai_checker.process(
        article_data, threshold=cfg["AI_THRESHOLD"]
    )

    # 3. Tạo poster/banner 16:9 kèm Logo & Màu thương hiệu
    img_path, img_status = image_engine.generate_and_download(
        prompt=article_data.get("image_prompt", ""),
        slug=article_data.get("slug", "crypto-update"),
    )

    # 4. Đăng WordPress Draft
    draft_url, post_status = await wp_publisher.create_draft(
        article_data=article_data, local_image_path=img_path
    )

    # 5. Ghi Log CSV
    csv_logger.log_article(
        source_url=news_url,
        article_data=article_data,
        draft_url=draft_url,
        img_status=img_status,
        status=post_status,
    )

    # 6. Gửi Thông Báo Telegram
    ai_score_val = article_data.get("ai_score", 0)
    tg_notifier.send_post_notification(
        title=article_data.get("title", default_title),
        source_url=news_url,
        draft_url=draft_url,
        ai_score=ai_score_val,
        img_status=img_status,
        brand_name=cfg["TELEGRAM_NOTIFY"].get("BRAND_NAME"),
    )

  except Exception as e:
    print(f"❌ Lỗi Pipeline ({platform_mode}): {e}")


# =====================================================================
# 📡 MODULE 7: BINANCE ANNOUNCEMENT TRIGGER & KEYWORD FILTER
# =====================================================================
def matches_keywords(
    title: str,
    whitelist: list[str],
    blacklist: list[str] = None,
) -> tuple[bool, str]:
  search_target = title.lower()

  if blacklist:
    for b_kw in blacklist:
      clean_b = b_kw.strip().lower()
      if clean_b and clean_b in search_target:
        return False, f"Chứa từ khóa cấm: '{b_kw}'"

  clean_whitelist = [w.strip().lower() for w in (whitelist or []) if w.strip()]
  if clean_whitelist:
    for w_kw in clean_whitelist:
      if w_kw in search_target:
        return True, f"Khớp từ khóa mục tiêu: '{w_kw}'"
    return (
        False,
        f"Không chứa từ khóa yêu cầu ({', '.join(clean_whitelist[:5])}...)",
    )

  return True, "Chấp nhận tất cả"


async def check_binance_for_new_announcements(
    scanner: BinanceScanner,
    seen_articles: set,
    keyword_whitelist: list[str],
    keyword_blacklist: list[str],
):
  print(f"\n🔍 [{datetime.now().strftime('%H:%M:%S')}] Đang quét các danh mục thông báo Binance...")
  try:
    latest_articles = scanner.get_latest_articles()
    if not latest_articles:
      print("ℹ️ Không tìm thấy bài viết nào trong lần quét này.")
      return

    # Lần đầu chạy: Khởi tạo mốc các bài hiện có
    if not seen_articles:
      for art in latest_articles:
        seen_articles.add(art["code"])
      print(
          f"📌 Đã khởi tạo mốc cơ sở: Ghi nhớ {len(seen_articles)} bài viết hiện"
          " có trên Binance."
      )
      return

    # Duyệt các bài mới phát sinh
    for art in latest_articles:
      code = art["code"]
      if code in seen_articles:
        continue

      title = art["title"]
      article_url = art["url"]

      print("\n" + "=" * 70)
      print(f"⚡ [THÔNG BÁO BINANCE MỚI] Code: {code}")
      print(f"🎯 Tiêu đề: {title}")
      print(f"🔗 Link: {article_url}")

      # Kiểm tra bộ lọc từ khóa
      is_matched, reason = matches_keywords(
          title=title,
          whitelist=keyword_whitelist,
          blacklist=keyword_blacklist,
      )

      seen_articles.add(code)

      if not is_matched:
        print(f"⏭️ [BỎ QUA DO LỌC TỪ KHÓA] {reason}")
        continue

      print(f"🎉 [ĐỦ ĐIỀU KIỆN TRIGGER] {reason}")
      print("🚀 BẮT ĐẦU CHẠY PIPELINE BIÊN TẬP & ĐĂNG BÀI (MODULES 1 -> 6)...")
      print("=" * 70)

      await run_news_automation_pipeline(
          news_url=article_url, article_code=code, default_title=title
      )
      print(f"\n✅ Hoàn tất xử lý thông báo [{code}]! Tiếp tục theo dõi...\n")

  except Exception as e:
    print(f"⚠️ Lỗi quét Binance Announcements: {e}")


async def main():
  binance_cfg = CONFIG["BINANCE"]
  poll_interval = binance_cfg["POLL_INTERVAL_SECONDS"]
  scanner = BinanceScanner(config=binance_cfg)

  print("=" * 75)
  print("🤖 AUTO BINANCE ANNOUNCEMENTS TO WORDPRESS BOT (24/7 PRODUCTION)")
  print("=" * 75)
  print(f"📋 Danh mục Binance quét: {binance_cfg['CATALOG_IDS']} (List 93, 49, 48)")
  print(f"🔍 Từ khóa mục tiêu: {binance_cfg['KEYWORD_WHITELIST']}")
  print(f"🚫 Từ khóa cấm: {binance_cfg['KEYWORD_BLACKLIST']}")
  print(f"📢 Telegram Báo Cáo: Chat ID {CONFIG['TELEGRAM_NOTIFY']['CHAT_ID']}")
  print(f"⏱️ Chu kỳ quét: {poll_interval} giây ({poll_interval // 60} phút / lần)")
  print("=" * 75)

  seen_articles = set()

  # Quét lần đầu để thiết lập mốc
  await check_binance_for_new_announcements(
      scanner=scanner,
      seen_articles=seen_articles,
      keyword_whitelist=binance_cfg["KEYWORD_WHITELIST"],
      keyword_blacklist=binance_cfg["KEYWORD_BLACKLIST"],
  )

  print(
      "\n🟢 HỆ THỐNG ĐANG HOẠT ĐỘNG. SẼ TỰ ĐỘNG QUÉT MỖI"
      f" {poll_interval // 60} PHÚT...\n"
  )

  while True:
    try:
      await asyncio.sleep(poll_interval)
      await check_binance_for_new_announcements(
          scanner=scanner,
          seen_articles=seen_articles,
          keyword_whitelist=binance_cfg["KEYWORD_WHITELIST"],
          keyword_blacklist=binance_cfg["KEYWORD_BLACKLIST"],
      )
    except asyncio.CancelledError:
      print("\n🛑 Bot đã nhận lệnh dừng.")
      break
    except Exception as e:
      print(f"⚠️ Lỗi vòng lặp chính: {e}")
      await asyncio.sleep(10)


if __name__ == "__main__":
  try:
    asyncio.run(main())
  except KeyboardInterrupt:
    print("\n🛑 Đã dừng bot an toàn bằng phím tắt Ctrl+C.")
    sys.exit(0)

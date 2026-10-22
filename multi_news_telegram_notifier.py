#!/usr/bin/env python3
"""
=============================================================================
🤖 MULTI-NEWS TELEGRAM NOTIFIER (24/7 PRODUCTION DAEMON)
=============================================================================
Hệ thống giám sát và bắn tin tức tự động 24/7 qua Telegram:
1. Nguồn Binance Announcements:
   - Catalog 49: Latest Binance News (Tin niêm yết, tính năng, chính sách)
   - Catalog 93: Latest Activities (Sự kiện giao dịch, Airdrop, Giải thưởng)
2. Nguồn Dealroom Venture Intelligence (⛔ TẠM TẮT từ 2026-10-15):
   - dealroom.co đã bật Cloudflare Managed Challenge (HTTP 403 "Just a moment...")
     chặn mọi HTTP client kể cả headless browser => không fetch được nữa.
   - Giữ nguyên scanner để tái bật khi có giải pháp (stealth browser / scraper API).
3. Nguồn Telegram Channel @cryptorank_fundraising (CryptoRank Fundraising):
   - Quét web preview công khai https://t.me/s/cryptorank_fundraising (không cần API).
   - Tin vòng gọi vốn: Seed, Series A-E, Post-IPO, kèm nhà đầu tư và link dự án.
4. Nguồn TechCrunch Fundraising (bổ sung 2026-10-15, thay thế vai trò Dealroom):
   - WP-JSON REST API category "Fundraising" (id 577234943) của techcrunch.com.
   - Lọc từ khóa tiêu đề để loại bỏ bài sự kiện/quảng cáo lẫn trong category.
5. Cơ chế hoạt động:
   - Lưu vết bài viết bằng SQLite (không cần cài đặt server DB, tự sinh file .db)
   - Lần đầu chạy: Tự động ghi nhớ các bài hiện có làm mốc, KHÔNG bị spam tin cũ
   - Khi có bài mới: Bắn thông báo Telegram tức thì (Định dạng HTML đẹp mắt)
   - Tự động phục hồi khi mất mạng, xử lý tín hiệu dừng an toàn (Graceful Shutdown)
=============================================================================
"""

from __future__ import annotations

import os
import sys
import time
import json
import html
import re
import signal
import sqlite3
import asyncio
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from typing import Optional, Union, List, Dict, Tuple

# =====================================================================
# ⚙️ 1. CẤU HÌNH HỆ THỐNG & NẠP BIẾN MÔI TRƯỜNG
# =====================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Ưu tiên đọc cấu hình từ file tk_key.env và key.env trong cùng thư mục
load_dotenv(os.path.join(BASE_DIR, "tk_key.env"))
load_dotenv(os.path.join(BASE_DIR, "key.env"))

# Token bot và Chat ID Telegram (lấy cùng cấu hình với auto_telegram_wp_bot)
TELEGRAM_BOT_TOKEN = (
    os.getenv("news_bot_token")
    or os.getenv("trading_bot_token")
)
TELEGRAM_CHAT_ID = os.getenv("tk_chatid", "-1004422031564")

CONFIG = {
    # Đường dẫn file database SQLite (Tự động tạo ngay tại thư mục hiện tại)
    "DB_PATH": os.path.join(BASE_DIR, "seen_articles.db"),
    
    # Cấu hình Telegram
    "TELEGRAM": {
        "BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "CHAT_ID": TELEGRAM_CHAT_ID,
        "TIMEOUT": 15,
    },
    
    # Cấu hình gom tin theo đợt (Batching) - Ngăn ngừa spam tin nhắn khi nguồn có nhiều bài cùng lúc
    "BATCH_MODE": True,
    "BATCH_MAX_ITEMS": 15,

    # Cấu hình nguồn Binance Announcements
    "BINANCE": {
        "ENABLE": True,
        # 49: Tin tức chung/Listing, 93: Sự kiện/Airdrop/Giải đấu
        "CATALOGS": [
            {"id": 49, "name": "Binance News"},
            {"id": 93, "name": "Binance Activities"},
        ],
        "BASE_URL": "https://www.binance.com/en/support/announcement",
        "POLL_INTERVAL_SECONDS": 45,  # Quét mỗi 45 giây
    },
    
    # Cấu hình nguồn Dealroom.co
    "DEALROOM": {
        # ⛔ TẠM TẮT từ 2026-10-15: dealroom.co bật Cloudflare Managed Challenge
        # (HTTP 403 "Just a moment...") chặn mọi HTTP client (requests, curl_cffi,
        # Playwright headless) => không fetch được. Giữ scanner để tái bật khi có
        # giải pháp vượt CF (stealth browser / scraper API trả phí).
        "ENABLE": False,
        # funding: Vòng gọi vốn, ipo: Niêm yết cổ phiếu
        "LENSES": [
            {"lens": "funding", "name": "Vòng Gọi Vốn (Funding)"},
            {"lens": "ipo", "name": "Kế Hoạch IPO / Listing"},
        ],
        "BASE_URL": "https://dealroom.co/news",
        "POLL_INTERVAL_SECONDS": 120,  # Quét mỗi 2 phút (tránh rate limit)
    },

    # Cấu hình nguồn Telegram Channel @cryptorank_fundraising (thay thế tin vòng gọi vốn)
    "CRYPTORANK": {
        "ENABLE": True,
        # Username kênh (không có @): dùng cho cả web preview và link tin gốc
        "CHANNEL": "cryptorank_fundraising",
        "WEB_URL": "https://t.me/s/cryptorank_fundraising",
        "POLL_INTERVAL_SECONDS": 300,  # Quét mỗi 5 phút (kênh chỉ đăng vài tin/ngày)
    },

    # Cấu hình nguồn TechCrunch Fundraising (WP-JSON API, thay thế vai trò Dealroom)
    "TECHCRUNCH": {
        "ENABLE": True,
        "CATEGORY_NAME": "Fundraising",
        # id category fundraising của TechCrunch (tra cứu qua /wp-json/wp/v2/categories)
        "CATEGORY_ID": 577234943,
        "API_URL": (
            "https://techcrunch.com/wp-json/wp/v2/posts?categories=577234943"
            "&per_page=15&_fields=id,slug,link,title,date,excerpt"
        ),
        "PAGE_URL": "https://techcrunch.com/category/fundraising/",
        # Chỉ nhận bài có tiêu đề khớp ít nhất 1 từ khóa (regex word-boundary, không
        # phân biệt hoa thường) để loại bài sự kiện/quảng cáo lẫn trong category.
        "TITLE_KEYWORDS": [
            "raise", "funding", "fundrais", "seed", "series", "round", "ipo",
            "valuation", "bankrupt", "acquir", "shut down", "shuts down", "merger",
        ],
        "POLL_INTERVAL_SECONDS": 300,  # Quét mỗi 5 phút
    },

    # Cấu hình nguồn Beebom (Roblox Codes)
    "BEEBOM": {
        "ENABLE": True,
        "TAG_NAME": "Roblox Codes",
        "TAG_URL": "https://beebom.com/tag/roblox-codes/",
        "API_URL": "https://beebom.com/wp-json/wp/v2/posts?tags=253215&per_page=15",
        "POLL_INTERVAL_SECONDS": 180,  # Quét mỗi 3 phút
    },

    # Cấu hình nguồn Bizztor Startup & VC News (https://bizztor.com/news/)
    "BIZZTOR": {
        "ENABLE": True,
        "BASE_URL": "https://bizztor.com/news/",
        "POLL_INTERVAL_SECONDS": 300,  # Quét mỗi 5 phút
        "TIMEOUT": 15,
    },
}

# Biến cờ kiểm soát vòng lặp chạy ngầm (hỗ trợ tắt an toàn)
IS_RUNNING = True


def handle_shutdown_signal(sig, frame):
    """Bắt tín hiệu Ctrl+C hoặc kill để dừng bot an toàn, giải phóng tài nguyên."""
    global IS_RUNNING
    print(f"\n🛑 Nhận tín hiệu dừng ({sig}). Đang hoàn tất công việc và thoát an toàn...")
    IS_RUNNING = False


signal.signal(signal.SIGINT, handle_shutdown_signal)
signal.signal(signal.SIGTERM, handle_shutdown_signal)


# =====================================================================
# 🗄️ 2. LỚP LƯU TRỮ TRẠNG THÁI BẰNG SQLITE (ZERO-CONFIG)
# =====================================================================
class StorageManager:
    """
    Quản lý lưu vết các bài viết đã thông báo.
    Sử dụng SQLite tích hợp sẵn trong Python:
    - Không cần cài MySQL hay Redis
    - Tự sinh file seen_articles.db
    - Đảm bảo khởi động lại server không bao giờ bị bắn trùng bài cũ.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def _init_db(self):
        """Tạo bảng lưu trữ nếu chưa có."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_articles (
                    source TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    catalog_or_lens TEXT,
                    title TEXT,
                    url TEXT,
                    amount TEXT,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (source, article_id)
                )
                """
            )
            conn.commit()

    def is_seen(self, source: str, article_id: str) -> bool:
        """Kiểm tra xem mã bài viết này đã từng được lưu hay chưa."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM seen_articles WHERE source = ? AND article_id = ?",
                (source, str(article_id)),
            )
            return cursor.fetchone() is not None

    def mark_seen(
        self,
        source: str,
        article_id: str,
        catalog_or_lens: str = "",
        title: str = "",
        url: str = "",
        amount: str = "",
    ):
        """Ghi nhận mã bài viết vào database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO seen_articles 
                (source, article_id, catalog_or_lens, title, url, amount, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    str(article_id),
                    catalog_or_lens,
                    title,
                    url,
                    amount,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()

    def count_seen(self, source: str = None) -> int:
        """Đếm số bài viết đã ghi nhớ."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if source:
                cursor.execute(
                    "SELECT COUNT(*) FROM seen_articles WHERE source = ?", (source,)
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM seen_articles")
            row = cursor.fetchone()
            return row[0] if row else 0


# =====================================================================
# 📢 3. LỚP GỬI THÔNG BÁO QUA TELEGRAM BOT
# =====================================================================
class TelegramAlertService:
    """Đảm nhiệm việc gửi tin nhắn cảnh báo có định dạng HTML tới Telegram."""

    def __init__(self, config: dict):
        self.bot_token = config.get("BOT_TOKEN")
        self.chat_id = config.get("CHAT_ID")
        self.timeout = config.get("TIMEOUT", 15)

    def send_message(self, html_text: str) -> bool:
        """Gửi tin nhắn định dạng HTML qua Telegram Bot API."""
        if not self.bot_token or not self.chat_id:
            print("⚠️ Chưa cấu hình Telegram Bot Token hoặc Chat ID.")
            return False

        api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            resp = requests.post(api_url, json=payload, timeout=self.timeout)
            if resp.status_code == 200:
                return True
            else:
                print(f"⚠️ Gửi Telegram thất bại (HTTP {resp.status_code}): {resp.text}")
                return False
        except Exception as e:
            print(f"⚠️ Lỗi kết nối Telegram: {e}")
            return False

    def notify_binance_article(self, article: dict):
        """Format và gửi tin tức Binance."""
        catalog_name = article.get("catalog_name", "Binance Update")
        catalog_id = article.get("catalog_id", 0)
        title = html.escape(article.get("title", "No Title"))
        url = article.get("url", "")
        release_date = article.get("release_date_str", datetime.now().strftime("%Y-%m-%d %H:%M"))

        badge = "🚨 <b>[BINANCE NEWS]</b>" if catalog_id == 49 else "🎁 <b>[BINANCE ACTIVITIES & REWARDS]</b>"

        msg = (
            f"{badge}\n\n"
            f"📌 <b>Tiêu đề:</b> {title}\n"
            f"📂 <b>Chuyên mục:</b> {catalog_name} (Catalog #{catalog_id})\n"
            f"⏰ <b>Thời gian:</b> {release_date} (UTC+7)\n"
            f"🔗 <b>Link Binance:</b> <a href='{url}'>Xem thông báo gốc trên Binance</a>\n\n"
            f"🏷️ <i>#Binance #CryptoNews #{catalog_name.replace(' ', '')}</i>"
        )
        success = self.send_message(msg)
        if success:
            print(f"✅ [Telegram] Đã bắn thông báo Binance: {title[:50]}...")
        return success

    def notify_dealroom_deal(self, deal: dict):
        """Format và gửi tin tức vòng gọi vốn / IPO từ Dealroom."""
        lens = deal.get("lens", "news")
        lens_name = deal.get("lens_name", "Dealroom Intelligence")
        title = html.escape(deal.get("title", "No Title"))
        url = deal.get("url", "")
        amount_fmt = deal.get("amount_formatted", "N/A")
        sector = deal.get("sector", "Technology")
        geo = deal.get("geo", "Global")
        date_str = deal.get("date_str", datetime.now().strftime("%Y-%m-%d %H:%M"))

        badge = "💰 <b>[DEALROOM - VÒNG GỌI VỐN MỚI]</b>" if lens == "funding" else "🏛️ <b>[DEALROOM - KẾ HOẠCH IPO / LISTING]</b>"

        msg = (
            f"{badge}\n\n"
            f"📌 <b>Tiêu đề:</b> {title}\n"
            f"💵 <b>Quy mô Deal:</b> <b>{amount_fmt}</b>\n"
            f"🏢 <b>Lĩnh vực:</b> {sector.title()}\n"
            f"🌍 <b>Khu vực:</b> {geo.replace('-', ' ').title()}\n"
            f"⏰ <b>Thời gian:</b> {date_str} (UTC+7)\n"
            f"🔗 <b>Xem chi tiết:</b> <a href='{url}'>Mở bài viết trên Dealroom</a>\n\n"
            f"🏷️ <i>#Dealroom #{lens.upper()} #VentureCapital #StartupNews</i>"
        )
        success = self.send_message(msg)
        if success:
            print(f"✅ [Telegram] Đã bắn thông báo Dealroom: {title[:50]}...")
        return success

    def send_split_message(self, html_text: str, max_length: int = 3900) -> bool:
        """Tự động chia nhỏ tin nhắn nếu vượt giới hạn Telegram (4096 ký tự)."""
        if len(html_text) <= max_length:
            return self.send_message(html_text)

        blocks = html_text.split("\n\n")
        chunks = []
        current_chunk = []
        current_len = 0

        for block in blocks:
            if current_len + len(block) + 2 > max_length and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [block]
                current_len = len(block)
            else:
                current_chunk.append(block)
                current_len += len(block) + 2

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        total = len(chunks)
        all_success = True
        for idx, chunk in enumerate(chunks, 1):
            chunk_with_page = f"<i>(Phần {idx}/{total})</i>\n" + chunk if total > 1 else chunk
            ok = self.send_message(chunk_with_page)
            if not ok:
                all_success = False
            time.sleep(1)
        return all_success

    def notify_binance_batch(self, articles: list[dict]):
        """Gom nhiều thông báo Binance vào 1 tin nhắn tổng hợp."""
        if not articles:
            return
        if len(articles) == 1:
            return self.notify_binance_article(articles[0])

        count = len(articles)
        now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        header = (
            f"🚨 <b>[BINANCE - TỔNG HỢP {count} THÔNG BÁO MỚI NHẤT]</b>\n"
            f"⏰ <i>Thời gian quét: {now_str} (UTC+7)</i>\n"
            f"──────────────────────────\n\n"
        )

        items_text = []
        for i, art in enumerate(articles, 1):
            cat_id = art.get("catalog_id", 0)
            cat_name = art.get("catalog_name", "Binance Update")
            badge_icon = "📢" if cat_id == 49 else "🎁"
            title = html.escape(art.get("title", "No Title"))
            url = art.get("url", "")
            release_date = art.get("release_date_str", "")

            item = (
                f"<b>{i}️⃣ {badge_icon} [{cat_name}]</b>\n"
                f"   📌 {title}\n"
                f"   ⏰ {release_date} (UTC+7)\n"
                f"   🔗 <a href='{url}'>Xem bài gốc trên Binance</a>"
            )
            items_text.append(item)

        footer = "\n\n──────────────────────────\n🏷️ <i>#Binance #BatchDigest #CryptoNews #MultiSourceRadar</i>"
        full_msg = header + "\n\n".join(items_text) + footer
        success = self.send_split_message(full_msg)
        if success:
            print(f"✅ [Telegram] Đã bắn Batch Binance ({count} thông báo).")
        return success

    def notify_dealroom_batch(self, deals: list[dict]):
        """Gom nhiều deal vòng gọi vốn & IPO từ Dealroom vào 1 tin nhắn tổng hợp."""
        if not deals:
            return
        if len(deals) == 1:
            return self.notify_dealroom_deal(deals[0])

        count = len(deals)
        now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        header = (
            f"💰 <b>[DEALROOM - TỔNG HỢP {count} THƯƠNG VỤ MỚI NHẤT]</b>\n"
            f"⏰ <i>Thời gian quét: {now_str} (UTC+7)</i>\n"
            f"──────────────────────────\n\n"
        )

        items_text = []
        for i, deal in enumerate(deals, 1):
            lens = deal.get("lens", "funding")
            icon = "💵" if lens == "funding" else "🏛️"
            lens_tag = "Funding" if lens == "funding" else "IPO/Listing"
            title = html.escape(deal.get("title", "No Title"))
            amt = deal.get("amount_formatted", "N/A")
            sector = deal.get("sector", "Tech").title()
            geo = deal.get("geo", "Global").replace("-", " ").title()
            url = deal.get("url", "")

            item = (
                f"<b>{i}️⃣ {icon} [{lens_tag}] {title}</b>\n"
                f"   💵 Quy mô: <b>{amt}</b> | 🏢 {sector} | 🌍 {geo}\n"
                f"   🔗 <a href='{url}'>Xem chi tiết trên Dealroom</a>"
            )
            items_text.append(item)

        footer = "\n\n──────────────────────────\n🏷️ <i>#Dealroom #BatchDigest #VentureCapital #StartupNews #IPO</i>"
        full_msg = header + "\n\n".join(items_text) + footer
        success = self.send_split_message(full_msg)
        if success:
            print(f"✅ [Telegram] Đã bắn Batch Dealroom ({count} deals).")
        return success

    def notify_beebom_article(self, article: dict):
        """Format và gửi tin tức lẻ Beebom Roblox Codes."""
        title = html.escape(article.get("title", "No Title"))
        url = article.get("url", "")
        date_str = article.get("date_str", datetime.now().strftime("%Y-%m-%d %H:%M"))

        msg = (
            f"🎮 <b>[BEEBOM - ROBLOX CODES MỚI]</b>\n\n"
            f"📌 <b>Tiêu đề:</b> {title}\n"
            f"📂 <b>Chuyên mục:</b> Roblox Codes / Game Codes\n"
            f"⏰ <b>Thời gian:</b> {date_str} (UTC+7)\n"
            f"🔗 <b>Lấy mã Code:</b> <a href='{url}'>Xem bài hướng dẫn & nhận Giftcode</a>\n\n"
            f"🏷️ <i>#Beebom #Roblox #RobloxCodes #Giftcode #GamingNews</i>"
        )
        success = self.send_message(msg)
        if success:
            print(f"✅ [Telegram] Đã bắn thông báo Beebom: {title[:50]}...")
        return success

    def notify_beebom_batch(self, articles: list[dict]):
        """Gom nhiều bài Beebom Roblox Codes vào 1 tin nhắn tổng hợp."""
        if not articles:
            return
        if len(articles) == 1:
            return self.notify_beebom_article(articles[0])

        count = len(articles)
        now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        header = (
            f"🎮 <b>[BEEBOM - TỔNG HỢP {count} BÀI ROBLOX CODES MỚI]</b>\n"
            f"⏰ <i>Thời gian quét: {now_str} (UTC+7)</i>\n"
            f"──────────────────────────\n\n"
        )

        items_text = []
        for i, art in enumerate(articles, 1):
            title = html.escape(art.get("title", "No Title"))
            url = art.get("url", "")
            date_str = art.get("date_str", "")

            item = (
                f"<b>{i}️⃣ 🕹️ {title}</b>\n"
                f"   ⏰ {date_str} (UTC+7)\n"
                f"   🔗 <a href='{url}'>Xem mã Giftcode trên Beebom</a>"
            )
            items_text.append(item)

        footer = "\n\n──────────────────────────\n🏷️ <i>#Beebom #BatchDigest #RobloxCodes #GamingNews</i>"
        full_msg = header + "\n\n".join(items_text) + footer
        success = self.send_split_message(full_msg)
        if success:
            print(f"✅ [Telegram] Đã bắn Batch Beebom ({count} bài).")
        return success

    def notify_cryptorank_article(self, item: dict) -> bool:
        """Format và gửi 1 tin fundraising từ kênh Telegram CryptoRank."""
        title = html.escape(item.get("title", "No Title"))
        round_type = html.escape(item.get("round_type", "Fundraising"))
        amount_fmt = html.escape(item.get("amount_formatted", "Không công bố"))
        body = html.escape(item.get("full_text", ""))
        url = item.get("url", "")
        date_str = item.get("date_str", datetime.now().strftime("%Y-%m-%d %H:%M"))
        project_url = item.get("project_url", "")

        project_link = ""
        if project_url:
            project_link = (
                f"🔗 <b>Trang dự án:</b> <a href='{html.escape(project_url)}'>Mở trên CryptoRank.io</a>\n"
            )

        msg = (
            f"💼 <b>[CRYPTORANK - FUNDRAISING MỚI]</b>\n\n"
            f"📌 <b>Dự án:</b> {title}\n"
            f"💰 <b>Vòng vốn:</b> {round_type} | <b>Quy mô:</b> <b>{amount_fmt}</b>\n"
            f"⏰ <b>Thời gian:</b> {date_str} (UTC+7)\n"
            f"{project_link}"
            f"📄 <b>Nội dung tin:</b>\n{body}\n"
            f"🔗 <b>Tin gốc:</b> <a href='{url}'>Xem trên Telegram</a>\n\n"
            f"🏷️ <i>#CryptoRank #Fundraising #VentureCapital #StartupNews</i>"
        )
        success = self.send_message(msg)
        if success:
            print(f"✅ [Telegram] Đã bắn thông báo CryptoRank: {title[:50]}...")
        return success

    def notify_cryptorank_batch(self, items: list[dict]) -> bool:
        """Gom nhiều tin CryptoRank Fundraising vào 1 tin nhắn tổng hợp."""
        if not items:
            return True
        if len(items) == 1:
            return self.notify_cryptorank_article(items[0])

        count = len(items)
        now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        header = (
            f"💼 <b>[CRYPTORANK - TỔNG HỢP {count} TIN FUNDRAISING MỚI]</b>\n"
            f"⏰ <i>Thời gian quét: {now_str} (UTC+7)</i>\n"
            f"──────────────────────────\n\n"
        )

        items_text = []
        for i, item in enumerate(items, 1):
            title = html.escape(item.get("title", "No Title"))
            round_type = html.escape(item.get("round_type", "Fundraising"))
            amount_fmt = html.escape(item.get("amount_formatted", "Không công bố"))
            url = item.get("url", "")
            date_str = item.get("date_str", "")
            project_url = item.get("project_url", "")

            links = f"<a href='{url}'>Tin gốc</a>"
            if project_url:
                links += f" | <a href='{html.escape(project_url)}'>CryptoRank.io</a>"

            item_text = (
                f"<b>{i}️⃣ 💼 {title}</b>\n"
                f"   💰 {round_type} | Quy mô: <b>{amount_fmt}</b>\n"
                f"   ⏰ {date_str} (UTC+7)\n"
                f"   🔗 {links}"
            )
            items_text.append(item_text)

        footer = "\n\n──────────────────────────\n🏷️ <i>#CryptoRank #BatchDigest #Fundraising #VentureCapital</i>"
        full_msg = header + "\n\n".join(items_text) + footer
        success = self.send_split_message(full_msg)
        if success:
            print(f"✅ [Telegram] Đã bắn Batch CryptoRank ({count} tin).")
        return success


    def notify_techcrunch_article(self, article: dict) -> bool:
        """Format và gửi 1 bài fundraising từ TechCrunch."""
        title = html.escape(article.get("title", "No Title"))
        url = article.get("url", "")
        date_str = article.get("date_str", datetime.now().strftime("%Y-%m-%d %H:%M"))
        excerpt = html.escape(article.get("excerpt", ""))

        excerpt_block = f"📄 <i>{excerpt}</i>\n" if excerpt else ""

        msg = (
            f"📰 <b>[TECHCRUNCH - FUNDRAISING MỚI]</b>\n\n"
            f"📌 <b>Tiêu đề:</b> {title}\n"
            f"{excerpt_block}"
            f"⏰ <b>Thời gian:</b> {date_str} (UTC+7)\n"
            f"🔗 <b>Đọc bài gốc:</b> <a href='{url}'>Mở bài viết trên TechCrunch</a>\n\n"
            f"🏷️ <i>#TechCrunch #Fundraising #VentureCapital #StartupNews</i>"
        )
        success = self.send_message(msg)
        if success:
            print(f"✅ [Telegram] Đã bắn thông báo TechCrunch: {title[:50]}...")
        return success

    def notify_techcrunch_batch(self, articles: list[dict]) -> bool:
        """Gom nhiều bài TechCrunch Fundraising vào 1 tin nhắn tổng hợp."""
        if not articles:
            return True
        if len(articles) == 1:
            return self.notify_techcrunch_article(articles[0])

        count = len(articles)
        now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        header = (
            f"📰 <b>[TECHCRUNCH - TỔNG HỢP {count} BÀI FUNDRAISING MỚI]</b>\n"
            f"⏰ <i>Thời gian quét: {now_str} (UTC+7)</i>\n"
            f"──────────────────────────\n\n"
        )

        items_text = []
        for i, art in enumerate(articles, 1):
            title = html.escape(art.get("title", "No Title"))
            url = art.get("url", "")
            date_str = art.get("date_str", "")

            item = (
                f"<b>{i}️⃣ 📰 {title}</b>\n"
                f"   ⏰ {date_str} (UTC+7)\n"
                f"   🔗 <a href='{url}'>Đọc bài gốc trên TechCrunch</a>"
            )
            items_text.append(item)

        footer = "\n\n──────────────────────────\n🏷️ <i>#TechCrunch #BatchDigest #Fundraising #StartupNews</i>"
        full_msg = header + "\n\n".join(items_text) + footer
        success = self.send_split_message(full_msg)
        if success:
            print(f"✅ [Telegram] Đã bắn Batch TechCrunch ({count} bài).")
        return success

    def notify_bizztor_article(self, article: dict) -> bool:
        """Format và gửi tin tức gọi vốn / Startup từ Bizztor."""
        title = html.escape(article.get("title", "No Title"))
        url = article.get("url", "")
        amount = article.get("amount_formatted", "Không công bố")
        round_type = article.get("round_type", "Fundraising")
        excerpt = html.escape(article.get("excerpt", ""))
        date_str = article.get("date_str", datetime.now().strftime("%Y-%m-%d %H:%M"))

        badge = "💰 <b>[BIZZTOR - STARTUP & VC FUNDING]</b>"
        detail_lines = []
        if amount and amount != "Không công bố":
            detail_lines.append(f"💵 <b>Quy mô Deal:</b> <b>{amount}</b>")
        if round_type and round_type != "Fundraising":
            detail_lines.append(f"🎯 <b>Vòng gọi vốn:</b> {round_type}")

        details_str = ("\n" + "\n".join(detail_lines)) if detail_lines else ""
        excerpt_block = f"\n📝 <i>{excerpt[:200]}...</i>\n" if excerpt else "\n"

        msg = (
            f"{badge}\n\n"
            f"📌 <b>Tiêu đề:</b> {title}"
            f"{details_str}\n"
            f"{excerpt_block}"
            f"⏰ <b>Thời gian:</b> {date_str} (UTC+7)\n"
            f"🔗 <b>Xem chi tiết:</b> <a href='{url}'>Mở bài viết trên Bizztor</a>\n\n"
            f"🏷️ <i>#Bizztor #StartupFunding #VentureCapital #AI #TechNews</i>"
        )
        success = self.send_message(msg)
        if success:
            print(f"✅ [Telegram] Đã bắn thông báo Bizztor: {title[:50]}...")
        return success

    def notify_bizztor_batch(self, articles: list[dict]) -> bool:
        """Gom nhiều bài Bizztor vào 1 tin nhắn tổng hợp."""
        if not articles:
            return True
        if len(articles) == 1:
            return self.notify_bizztor_article(articles[0])

        count = len(articles)
        now_str = datetime.now().strftime("%H:%M:%S - %d/%m/%Y")
        header = (
            f"💼 <b>[BIZZTOR - TỔNG HỢP {count} TIN FUNDRAISING MỚI]</b>\n"
            f"⏰ <i>Thời gian quét: {now_str} (UTC+7)</i>\n"
            f"──────────────────────────\n\n"
        )

        items_text = []
        for i, art in enumerate(articles, 1):
            title = html.escape(art.get("title", "No Title"))
            url = art.get("url", "")
            amount = art.get("amount_formatted", "")
            round_type = art.get("round_type", "")
            date_str = art.get("date_str", "")

            deal_tag = ""
            if amount and amount != "Không công bố":
                deal_tag = f" | 💵 {amount}"
                if round_type and round_type != "Fundraising":
                    deal_tag += f" ({round_type})"

            item = (
                f"<b>{i}️⃣ 🚀 {title}</b>{deal_tag}\n"
                f"   ⏰ {date_str} (UTC+7)\n"
                f"   🔗 <a href='{url}'>Đọc bài gốc trên Bizztor</a>"
            )
            items_text.append(item)

        footer = "\n\n──────────────────────────\n🏷️ <i>#Bizztor #BatchDigest #VentureCapital #StartupNews</i>"
        full_msg = header + "\n\n".join(items_text) + footer
        success = self.send_split_message(full_msg)
        if success:
            print(f"✅ [Telegram] Đã bắn Batch Bizztor ({count} bài).")
        return success


# =====================================================================
# 🔍 4. BỘ QUÉT NGUỒN BINANCE ANNOUNCEMENTS
# =====================================================================
class BinanceScanner:
    """Truy vấn trực tiếp REST API thông báo chính thức của Binance."""

    def __init__(self, config: dict):
        self.catalogs = config.get("CATALOGS", [])
        self.base_url = config.get("BASE_URL", "https://www.binance.com/en/support/announcement")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "lang": "en",
            "clienttype": "web",
        }

    def fetch_latest_articles(self) -> list[dict]:
        """Lấy danh sách các bài viết mới nhất từ các danh mục Catalog đã cấu hình."""
        all_articles = []
        for cat in self.catalogs:
            cat_id = cat["id"]
            cat_name = cat["name"]
            url = f"https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId={cat_id}&pageNo=1&pageSize=10"
            try:
                resp = requests.get(url, headers=self.headers, timeout=12)
                if resp.status_code != 200:
                    print(f"⚠️ Binance API Catalog {cat_id} HTTP {resp.status_code}")
                    continue

                res_data = resp.json().get("data", {})
                articles = []
                # Dữ liệu của Binance có thể nằm ở data['articles'] hoặc data['catalogs'][0]['articles']
                if res_data.get("articles"):
                    articles.extend(res_data.get("articles", []))
                for sub_cat in res_data.get("catalogs", []):
                    articles.extend(sub_cat.get("articles", []))

                for art in articles:
                    code = art.get("code")
                    if not code:
                        continue

                    title = art.get("title", "").strip()
                    rel_date = art.get("releaseDate")
                    date_str = ""
                    if rel_date:
                        try:
                            # releaseDate là millisecond timestamp
                            dt = datetime.fromtimestamp(int(rel_date) / 1000)
                            date_str = dt.strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

                    article_url = f"{self.base_url}/{code}"
                    all_articles.append({
                        "source": "BINANCE",
                        "article_id": code,
                        "catalog_id": cat_id,
                        "catalog_name": cat_name,
                        "title": title,
                        "url": article_url,
                        "release_date_str": date_str,
                    })
            except Exception as e:
                print(f"⚠️ Lỗi quét Binance Catalog {cat_id}: {e}")

        return all_articles


# =====================================================================
# 🔍 5. BỘ QUÉT NGUỒN DEALROOM (FUNDING & IPO)
# =====================================================================
class DealroomScanner:
    """Quét và bóc tách dữ liệu bài viết vòng gọi vốn và IPO từ Dealroom.co."""

    def __init__(self, config: dict):
        self.lenses = config.get("LENSES", [])
        self.base_url = config.get("BASE_URL", "https://dealroom.co/news")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    @staticmethod
    def _format_deal_amount(raw_amt: str) -> str:
        """Quy đổi số tiền deal raw thành chuỗi dễ đọc ($2B, $150M,...)."""
        if not raw_amt:
            return "Không công bố"
        try:
            val = float(raw_amt)
            if val >= 1_000_000_000:
                return f"${val / 1_000_000_000:.2f} Billion"
            elif val >= 1_000_000:
                return f"${val / 1_000_000:.1f} Million"
            elif val >= 1_000:
                return f"${val / 1_000:.0f}K"
            return f"${val:,.0f}"
        except Exception:
            return raw_amt

    def fetch_latest_deals(self) -> list[dict]:
        """Tải trang tin Dealroom và trích xuất danh sách bài viết theo từng lens."""
        all_deals = []
        for item in self.lenses:
            lens = item["lens"]
            lens_name = item["name"]
            page_url = f"{self.base_url}/?lens={lens}"

            try:
                resp = requests.get(page_url, headers=self.headers, timeout=15)
                if resp.status_code != 200:
                    print(f"⚠️ Dealroom HTTP {resp.status_code} ({page_url})")
                    continue

                soup = BeautifulSoup(resp.text, "html.parser")
                # Thẻ bài viết trên Dealroom có thuộc tính data-lens tương ứng
                cards = soup.find_all("div", attrs={"data-lens": lens})

                for card in cards:
                    link_tag = card.find("a", href=True)
                    if not link_tag:
                        continue

                    href = link_tag.get("href", "").strip()
                    if not href.startswith("/news/"):
                        continue

                    # ID của bài viết được lấy từ slug đường dẫn (/news/149496-cognition-raises...)
                    slug = href.replace("/news/", "").strip("/")
                    if not slug or slug in ["archive"]:
                        continue

                    # Tìm tiêu đề bài viết
                    title_elem = (
                        card.find(["h2", "h3", "h4", "div"], class_=lambda cl: cl and any(x in cl for x in ["title", "headline", "name"]))
                        or link_tag
                    )
                    title = title_elem.get_text(strip=True) if title_elem else "No Title"

                    # Trích xuất số tiền, ngày giờ và lĩnh vực
                    raw_amt = card.get("data-amt", "")
                    amount_formatted = self._format_deal_amount(raw_amt)
                    sector = card.get("data-sector", "Technology")
                    geo = card.get("data-geo", "Global")
                    ts = card.get("data-ts")

                    date_str = ""
                    if ts:
                        try:
                            dt = datetime.fromtimestamp(int(ts) / 1000)
                            date_str = dt.strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                    else:
                        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

                    full_url = f"https://dealroom.co{href}" if href.startswith("/") else href

                    all_deals.append({
                        "source": "DEALROOM",
                        "article_id": slug,
                        "lens": lens,
                        "lens_name": lens_name,
                        "title": title,
                        "url": full_url,
                        "raw_amount": raw_amt,
                        "amount_formatted": amount_formatted,
                        "sector": sector,
                        "geo": geo,
                        "date_str": date_str,
                    })

            except Exception as e:
                print(f"⚠️ Lỗi quét Dealroom lens={lens}: {e}")

        return all_deals


# =====================================================================
# 🎮 5. BỘ QUÉT NGUỒN BEEBOM (ROBLOX CODES)
# =====================================================================
class BeebomScanner:
    """
    Bộ quét tự động trang Roblox Codes của Beebom:
    - Cơ chế kép (Dual-Engine):
      1. Ưu tiên truy vấn WP-JSON REST API: Siêu nhanh, cấu trúc JSON chính xác.
      2. Fallback sang BeautifulSoup HTML Parsing: Bóc tách DOM thẻ <article> nếu API bị lỗi.
    """

    def __init__(self, config: dict):
        self.tag_name = config.get("TAG_NAME", "Roblox Codes")
        self.tag_url = config.get("TAG_URL", "https://beebom.com/tag/roblox-codes/")
        self.api_url = config.get(
            "API_URL", "https://beebom.com/wp-json/wp/v2/posts?tags=253215&per_page=15"
        )
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    def fetch_latest_articles(self) -> list[dict]:
        """
        Lấy danh sách các bài viết cập nhật code Roblox mới nhất.
        Trả về danh sách dict chuẩn hóa.
        """
        # 1. Thử qua WordPress REST API (Nhanh và ổn định nhất)
        articles = self._fetch_via_api()
        if articles:
            return articles

        # 2. Fallback sang cào HTML nếu REST API thất bại
        print("  ℹ️ Thử fallback sang cào HTML Beebom...")
        return self._fetch_via_html()

    def _fetch_via_api(self) -> list[dict]:
        """Truy vấn WP-JSON API."""
        try:
            resp = requests.get(self.api_url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️ Beebom WP-JSON API trả về HTTP {resp.status_code}")
                return []

            posts = resp.json()
            if not isinstance(posts, list):
                return []

            results = []
            for post in posts:
                post_id = post.get("id")
                slug = post.get("slug", "")
                link = post.get("link", "")
                raw_title = post.get("title", {}).get("rendered", "")
                title = html.unescape(raw_title).strip()
                date_raw = post.get("date", "")

                # Chuẩn hóa thời gian
                try:
                    dt = datetime.fromisoformat(date_raw)
                    date_str = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    date_str = date_raw[:16].replace("T", " ") if date_raw else datetime.now().strftime("%Y-%m-%d %H:%M")

                article_id = slug if slug else f"beebom_{post_id}"

                results.append({
                    "source": "BEEBOM",
                    "article_id": article_id,
                    "title": title,
                    "url": link,
                    "date_str": date_str,
                    "tag": self.tag_name,
                })
            return results
        except Exception as e:
            print(f"⚠️ Lỗi kết nối Beebom API: {e}")
            return []

    def _fetch_via_html(self) -> list[dict]:
        """Cào HTML từ trang tag Beebom."""
        try:
            resp = requests.get(self.tag_url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️ Beebom HTML trả về HTTP {resp.status_code}")
                return []

            soup = BeautifulSoup(resp.text, "html.parser")
            articles = soup.find_all("article")
            results = []

            for art in articles:
                a_tag = art.find("a", href=True)
                if not a_tag:
                    continue

                url = a_tag["href"]
                slug = url.strip("/").split("/")[-1]
                if not slug:
                    continue

                h_tag = art.find(["h2", "h3"])
                title = h_tag.get_text(strip=True) if h_tag else slug.replace("-", " ").title()

                time_tag = art.find("time")
                date_str = time_tag.get_text(strip=True) if time_tag else datetime.now().strftime("%Y-%m-%d %H:%M")

                results.append({
                    "source": "BEEBOM",
                    "article_id": slug,
                    "title": title,
                    "url": url,
                    "date_str": date_str,
                    "tag": self.tag_name,
                })
            return results
        except Exception as e:
            print(f"⚠️ Lỗi cào HTML Beebom: {e}")
            return []


# =====================================================================
# 🔍 6. BỘ QUÉT NGUỒN TELEGRAM CHANNEL (CRYPTORANK FUNDRAISING)
# =====================================================================
class TelegramChannelScanner:
    """
    Quét web preview công khai của kênh Telegram (https://t.me/s/<channel>):
    - Không cần Bot API hay đăng nhập: t.me/s/ là trang web công khai của kênh.
    - Mỗi tin có data-post="<channel>/<số thứ tự>" tăng dần => khóa dedupe ổn định.
    - Lưu ý: post dạng album (nhiều ảnh) có thể render NHIỀU div trùng data-post,
      trong đó chỉ một div chứa .tgme_widget_message_text => phải dedupe mỗi lần fetch.
    """

    # Ký tự vô hình Telegram chèn vào đầu message, phải làm sạch trước khi xử lý
    _INVISIBLE_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff")

    # Dòng "quy mô + loại vòng" kiểu "$5.2M Seed Round", "$208M Post-IPO Round".
    # Loại vòng BẮT BUỘC viết hoa chữ đầu (đặc trưng format kênh) để tránh khớp sai
    # câu thường kiểu "raised $10M in a funding round" (sẽ trả về rỗng thay vì rác).
    _AMOUNT_ROUND_RE = re.compile(
        r"(\$ ?\d[\d.,]* ?[MBK]?)\s*(?:USD)?\s*([A-Z][A-Za-z0-9 ()/&-]*?)\s*Round"
    )

    def __init__(self, config: dict):
        self.channel = config.get("CHANNEL", "cryptorank_fundraising")
        self.web_url = config.get("WEB_URL", f"https://t.me/s/{self.channel}")
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    @classmethod
    def _clean_text(cls, raw: str) -> str:
        """Bỏ ký tự vô hình và các dòng trống dư thừa khỏi nội dung tin."""
        for ch in cls._INVISIBLE_CHARS:
            raw = raw.replace(ch, "")
        lines = [line.strip() for line in raw.split("\n")]
        return "\n".join(line for line in lines if line)

    @classmethod
    def _parse_amount_round(cls, text: str) -> tuple[str, str]:
        """
        Tách quy mô vốn và loại vòng từ nội dung tin.
        Trả về (amount_str, round_type), ví dụ ("$5.2M", "Seed"); ("", "") nếu không thấy.
        Giữ nguyên dạng chuỗi để tránh sai số float (tham khảo LESSONS_LEARNED).
        """
        # Chuẩn hóa toàn bộ whitespace (kể cả xuống dòng do get_text tách thẻ inline)
        flat_text = " ".join(text.split())
        match = cls._AMOUNT_ROUND_RE.search(flat_text)
        if not match:
            return "", ""
        return match.group(1).replace(" ", ""), match.group(2).strip()

    def _parse_message(self, msg) -> Optional[dict]:
        """Bóc tách 1 khối message của t.me/s thành dict chuẩn hóa, None nếu bỏ qua."""
        text_div = msg.select_one(".tgme_widget_message_text")
        if text_div is None:
            return None  # Tin media-only/service không có nội dung chữ

        full_text = self._clean_text(text_div.get_text("\n"))
        if not full_text:
            return None  # Tin chỉ toàn ký tự vô hình/emoji

        data_post = (msg.get("data-post") or "").strip()
        if not data_post or "/" not in data_post:
            return None  # Thiếu định danh => không thể dedupe an toàn
        msg_num = data_post.split("/", 1)[1]

        lines = full_text.split("\n")
        title = lines[0]

        # time[datetime] của t.me/s luôn là ISO UTC => quy đổi +7h hiển thị UTC+7
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        time_tag = msg.find("time")
        if time_tag:
            raw_dt = time_tag.get("datetime", "")
            try:
                # Python 3.8 không hỗ trợ hậu tố 'Z' trong fromisoformat
                clean_dt = raw_dt.replace("Z", "+00:00")
                dt_utc = datetime.fromisoformat(clean_dt)
                date_str = (dt_utc + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                print(f"⚠️ [CryptoRank] datetime lạ '{raw_dt}' ở tin {data_post}, dùng thời gian hiện tại.")

        amount, round_type = self._parse_amount_round(full_text)

        # Link dự án trên cryptorank.io (nếu có) nhúng trong nội dung tin
        project_url = ""
        for a_tag in text_div.find_all("a", href=True):
            if "cryptorank.io" in a_tag["href"]:
                project_url = a_tag["href"]
                break

        return {
            "source": "CRYPTORANK",
            "article_id": data_post,
            "title": title,
            "url": f"https://t.me/{self.channel}/{msg_num}",
            "full_text": full_text,
            "amount_formatted": amount if amount else "Không công bố",
            "round_type": round_type if round_type else "Fundraising",
            "project_url": project_url,
            "date_str": date_str,
        }

    def fetch_latest_messages(self) -> list[dict]:
        """Tải web preview kênh Telegram và bóc tách các tin gần nhất."""
        try:
            resp = requests.get(self.web_url, headers=self.headers, timeout=15)
            if resp.status_code == 429:
                print("⚠️ [CryptoRank] Telegram đang throttle (HTTP 429), đợi chu kỳ kế tiếp.")
                return []
            if resp.status_code != 200:
                print(f"⚠️ [CryptoRank] HTTP {resp.status_code} khi tải {self.web_url}")
                return []
        except requests.RequestException as e:
            print(f"⚠️ [CryptoRank] Lỗi kết nối t.me: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        # Dedupe theo data-post: album render nhiều div trùng nhau, giữ div đầu có text
        results: dict[str, dict] = {}
        for msg in soup.select(".tgme_widget_message"):
            parsed = self._parse_message(msg)
            if parsed is not None and parsed["article_id"] not in results:
                results[parsed["article_id"]] = parsed

        if not results:
            print("⚠️ [CryptoRank] Tải trang OK nhưng parse được 0 tin — DOM của t.me/s có thể đã đổi, cần kiểm tra lại parser!")
        return list(results.values())


# =====================================================================
# 🔍 7. BỘ QUÉT NGUỒN TECHCRUNCH FUNDRAISING (WP-JSON API)
# =====================================================================
class TechCrunchScanner:
    """
    Quét category Fundraising của TechCrunch qua WP-JSON REST API:
    - API chính xác, nhanh và ổn định hơn cào HTML (cùng cơ chế nguồn Beebom).
    - Category này bị TechCrunch lẫn bài sự kiện/quảng cáo (VD "Disrupt 2026 Day 2"),
      nên lọc thêm 1 lớp từ khóa tiêu đề để chỉ giữ tin vòng gọi vốn thật.
    """

    def __init__(self, config: dict):
        self.category_name = config.get("CATEGORY_NAME", "Fundraising")
        self.api_url = config.get(
            "API_URL",
            "https://techcrunch.com/wp-json/wp/v2/posts?categories=577234943&per_page=15",
        )
        self.title_keywords = [kw.lower() for kw in config.get("TITLE_KEYWORDS", [])]
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        }

    @staticmethod
    def _strip_html_tags(raw_html: str) -> str:
        """Bóc sạch thẻ HTML trong excerpt do WP-JSON trả về dạng rendered."""
        return re.sub(r"<[^>]+>", "", raw_html or "").strip()

    def _is_funding_article(self, title: str) -> bool:
        """Khớp từ khóa tiêu đề theo word-boundary (không phân biệt hoa thường)."""
        if not self.title_keywords:
            return True  # Không cấu hình từ khóa => nhận tất cả bài trong category
        lowered = title.lower()
        for keyword in self.title_keywords:
            # \b đầu từ khóa là prefix-stem match: "round" khớp "rounds" nhưng
            # KHÔNG khớp "background"/"surround" (trước 'r' là ký tự chữ).
            pattern = r"\b" + re.escape(keyword)
            if re.search(pattern, lowered):
                return True
        return False

    def fetch_latest_articles(self) -> list[dict]:
        """Lấy danh sách bài fundraising mới nhất từ WP-JSON API của TechCrunch."""
        try:
            resp = requests.get(self.api_url, headers=self.headers, timeout=15)
            if resp.status_code != 200:
                print(f"⚠️ [TechCrunch] WP-JSON API trả về HTTP {resp.status_code}")
                return []
            posts = resp.json()
            if not isinstance(posts, list):
                print(f"⚠️ [TechCrunch] Response không đúng định dạng danh sách: {type(posts)}")
                return []
        except (requests.RequestException, ValueError) as e:
            print(f"⚠️ [TechCrunch] Lỗi kết nối/phân tích API: {e}")
            return []

        results = []
        skipped = 0
        for post in posts:
            post_id = post.get("id")
            slug = post.get("slug", "")
            link = post.get("link", "")
            raw_title = (post.get("title") or {}).get("rendered", "")
            title = html.unescape(raw_title).strip()
            date_raw = post.get("date", "")
            raw_excerpt = (post.get("excerpt") or {}).get("rendered", "")
            excerpt = html.unescape(self._strip_html_tags(raw_excerpt))[:300]

            if not title or (not slug and not post_id):
                print(f"⚠️ [TechCrunch] Bỏ qua post thiếu title hoặc id: {post}")
                continue

            if not self._is_funding_article(title):
                skipped += 1
                continue

            try:
                # Python 3.8 không hỗ trợ hậu tố 'Z' trong fromisoformat
                clean_date = date_raw.replace("Z", "+00:00") if date_raw else ""
                dt = datetime.fromisoformat(clean_date)
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                date_str = date_raw[:16].replace("T", " ") if date_raw else datetime.now().strftime("%Y-%m-%d %H:%M")

            results.append({
                "source": "TECHCRUNCH",
                "article_id": slug if slug else f"techcrunch_{post_id}",
                "title": title,
                "url": link,
                "excerpt": excerpt,
                "date_str": date_str,
                "category": self.category_name,
            })

        if skipped > 0:
            print(f"  ℹ️ [TechCrunch] Đã lọc bỏ {skipped} bài không phải tin gọi vốn (theo từ khóa tiêu đề).")
        return results


# =====================================================================
# 🔍 8. BỘ QUÉT NGUỒN BIZZZTOR (STARTUP & VC FUNDING NEWS)
# =====================================================================
class BizztorScanner:
    """Cào bài viết mới nhất từ chuyên mục tin tức Bizztor (https://bizztor.com/news/)."""

    def __init__(self, config: dict):
        self.base_url = config.get("BASE_URL", "https://bizztor.com/news/")
        self.timeout = config.get("TIMEOUT", 15)
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
                " (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

    @staticmethod
    def _extract_amount_and_round(title: str) -> tuple[str, str]:
        """Trích xuất quy mô vốn ($/€/£) và vòng gọi vốn từ tiêu đề bài viết Bizztor."""
        # 1. Regex tìm số tiền: $125M, €60M, £4.6M, $2.1 Billion, £200,000, $50M...
        amount = "Không công bố"
        amount_match = re.search(
            r'([$€£]\s*[\d]+(?:\.[\d]+)?\s*(?:Billion|Million|B|M|k|K)?|[\d]+(?:\.[\d]+)?\s*(?:million|billion)\s*(?:pounds|euros|dollars)?)',
            title,
            re.IGNORECASE
        )
        if amount_match:
            amount = amount_match.group(1).strip()

        # 2. Regex tìm vòng gọi vốn: Pre-Seed, Seed, Series A-F...
        round_type = "Fundraising"
        round_match = re.search(
            r'\b(Pre-Seed|Seed|Series\s+[A-F](?:\s+Extension)?|Growth|First Close|Debt|Bridge|Grant)\b',
            title,
            re.IGNORECASE
        )
        if round_match:
            round_type = round_match.group(1).strip()

        return amount, round_type

    def _parse_html(self, html_text: str) -> list[dict]:
        """Tách riêng logic parse HTML để có thể kiểm thử (unit test) offline không cần mạng."""
        soup = BeautifulSoup(html_text, "html.parser")
        articles = []
        seen_slugs = set()

        # Bizztor đặt tiêu đề tin trong h2/h3 kèm thẻ a có href chứa /news/
        for h in soup.find_all(["h2", "h3"]):
            a = h.find("a")
            if not a:
                continue

            href = (a.get("href") or "").strip()
            if not href or href in ("https://bizztor.com/news/", "/news/", "https://bizztor.com/news"):
                continue

            # Chuẩn hóa URL tuyệt đối
            if href.startswith("/"):
                url = "https://bizztor.com" + href
            else:
                url = href

            # Trích xuất slug bài viết làm article_id
            clean_path = href.split("?")[0].rstrip("/")
            slug = clean_path.split("/")[-1]
            if not slug or slug in ("news", "page") or slug.startswith("page"):
                continue

            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            title = a.get_text(strip=True)
            if not title or len(title) < 15:
                continue

            amount, round_type = self._extract_amount_and_round(title)

            # Tìm đoạn văn tóm tắt lân cận (nếu có)
            excerpt = ""
            parent = h.find_parent()
            if parent:
                p_tag = parent.find("p")
                if p_tag:
                    excerpt = p_tag.get_text(strip=True)

            date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            articles.append({
                "source": "BIZZTOR",
                "article_id": slug,
                "title": title,
                "url": url,
                "excerpt": excerpt,
                "amount_formatted": amount,
                "round_type": round_type,
                "date_str": date_str,
            })

        return articles

    def fetch_latest_articles(self) -> list[dict]:
        """Gửi request cào danh sách bài viết từ https://bizztor.com/news/."""
        try:
            resp = requests.get(self.base_url, headers=self.headers, timeout=self.timeout)
            if resp.status_code == 200:
                articles = self._parse_html(resp.text)
                return articles
            elif resp.status_code == 429:
                print(f"⚠️ [Bizztor] Bị giới hạn tần suất (HTTP 429).")
                return []
            else:
                print(f"⚠️ [Bizztor] Lỗi HTTP {resp.status_code} khi tải {self.base_url}")
                return []
        except requests.Timeout:
            print(f"⚠️ [Bizztor] Hết thời gian chờ (Timeout {self.timeout}s) khi kết nối {self.base_url}")
            return []
        except Exception as e:
            print(f"⚠️ [Bizztor] Lỗi khi cào dữ liệu: {e}")
            return []


# =====================================================================
# 🚀 9. DAEMON CHÍNH CHẠY NGẦM 24/7 (ASYNC ENGINE)
# =====================================================================
class MultiNewsNotifierDaemon:
    """Điều phối toàn bộ vòng lặp kiểm tra và thông báo tin mới đa nguồn."""

    def __init__(self):
        self.storage = StorageManager(db_path=CONFIG["DB_PATH"])
        self.telegram = TelegramAlertService(config=CONFIG["TELEGRAM"])
        self.binance_scanner = BinanceScanner(config=CONFIG["BINANCE"])
        self.dealroom_scanner = DealroomScanner(config=CONFIG["DEALROOM"])
        self.beebom_scanner = BeebomScanner(config=CONFIG.get("BEEBOM", {}))
        self.cryptorank_scanner = TelegramChannelScanner(config=CONFIG["CRYPTORANK"])
        self.techcrunch_scanner = TechCrunchScanner(config=CONFIG.get("TECHCRUNCH", {}))
        self.bizztor_scanner = BizztorScanner(config=CONFIG.get("BIZZTOR", {}))

    def initialize_baseline(self):
        """
        Mốc cơ sở áp dụng LAZY theo từng nguồn (chống spam tin cũ khi mạng chưa sẵn sàng):
        - Nguồn nào trong DB chưa có bản ghi nào (chạy lần đầu hoặc vừa thêm nguồn mới),
          lần fetch THÀNH CÔNG ĐẦU TIÊN sẽ chỉ ghi nhớ mốc im lặng, KHÔNG bắn tin cũ.
        - Cơ chế nằm trong _process_source_items() được gọi bởi các hàm check_*_now().
        """
        print(
            f"📌 Trạng thái SQLite: {self.storage.count_seen()} tin đã ghi nhận "
            f"(Binance: {self.storage.count_seen('BINANCE')} | Dealroom: {self.storage.count_seen('DEALROOM')} | "
            f"Beebom: {self.storage.count_seen('BEEBOM')} | CryptoRank: {self.storage.count_seen('CRYPTORANK')} | "
            f"TechCrunch: {self.storage.count_seen('TECHCRUNCH')} | Bizztor: {self.storage.count_seen('BIZZTOR')})"
        )

    def _process_source_items(self, source: str, items: list[dict], notify_single, notify_batch, mark_one) -> int:
        """
        Xử lý chung cho mọi nguồn tin: lazy baseline -> lọc tin mới -> gửi Telegram
        (đơn hoặc gom batch) -> CHỈ mark_seen những tin gửi THÀNH CÔNG để không
        đánh mất tin khi Telegram API lỗi (tin chưa mark sẽ được thử lại chu kỳ sau).
        Trả về số tin đã được ghi nhận an toàn vào DB.
        """
        if not items:
            return 0

        # Lazy baseline: nguồn chưa từng có bản ghi nào -> seed mốc im lặng, KHÔNG notify
        if self.storage.count_seen(source) == 0:
            for it in items:
                mark_one(it)
            print(f"📌 [Baseline] Nguồn {source}: ghi nhớ im lặng {len(items)} tin hiện có (lần đầu tiên).")
            return len(items)

        new_items = [it for it in items if not self.storage.is_seen(source, it["article_id"])]
        if not new_items:
            return 0

        print(f"🔥 [TIN MỚI PHÁT HIỆN] {source}: Tìm thấy {len(new_items)} tin mới!")

        acknowledged = 0
        if CONFIG.get("BATCH_MODE", True) and len(new_items) > 1:
            if notify_batch(new_items):
                for it in new_items:
                    mark_one(it)
                acknowledged = len(new_items)
            else:
                print(f"⚠️ Gửi Telegram thất bại -> giữ {len(new_items)} tin {source} để thử lại chu kỳ sau.")
        else:
            for it in new_items:
                if notify_single(it):
                    mark_one(it)
                    acknowledged += 1
                else:
                    print(f"⚠️ Gửi 1 tin {source} thất bại -> sẽ thử lại chu kỳ sau.")
                time.sleep(1)
        return acknowledged

    def check_binance_now(self):
        """Quét và xử lý tin mới từ Binance (Hỗ trợ gom tin theo Batch)."""
        if not CONFIG["BINANCE"]["ENABLE"]:
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét thông báo Binance (Catalog 49 & 93)...")
        articles = self.binance_scanner.fetch_latest_articles()
        if not articles:
            print(f"[{now_str}] ℹ️ Binance: Không có dữ liệu.")
            return

        self._process_source_items(
            source="BINANCE",
            items=articles,
            notify_single=self.telegram.notify_binance_article,
            notify_batch=self.telegram.notify_binance_batch,
            mark_one=lambda art: self.storage.mark_seen(
                source="BINANCE",
                article_id=art["article_id"],
                catalog_or_lens=str(art["catalog_id"]),
                title=art["title"],
                url=art["url"],
            ),
        )

    def check_dealroom_now(self):
        """Quét và xử lý tin mới từ Dealroom (Hỗ trợ gom tin theo Batch)."""
        if not CONFIG["DEALROOM"]["ENABLE"]:
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét tin tức Dealroom (Funding & IPO)...")
        deals = self.dealroom_scanner.fetch_latest_deals()
        if not deals:
            print(f"[{now_str}] ℹ️ Dealroom: Không có dữ liệu.")
            return

        self._process_source_items(
            source="DEALROOM",
            items=deals,
            notify_single=self.telegram.notify_dealroom_deal,
            notify_batch=self.telegram.notify_dealroom_batch,
            mark_one=lambda deal: self.storage.mark_seen(
                source="DEALROOM",
                article_id=deal["article_id"],
                catalog_or_lens=deal["lens"],
                title=deal["title"],
                url=deal["url"],
                amount=deal.get("raw_amount", ""),
            ),
        )

    def check_beebom_now(self):
        """Quét và xử lý tin mới từ Beebom Roblox Codes (Hỗ trợ gom tin theo Batch)."""
        if not CONFIG.get("BEEBOM", {}).get("ENABLE", False):
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét bài viết Beebom (Roblox Codes)...")
        articles = self.beebom_scanner.fetch_latest_articles()
        if not articles:
            print(f"[{now_str}] ℹ️ Beebom: Không có dữ liệu.")
            return

        self._process_source_items(
            source="BEEBOM",
            items=articles,
            notify_single=self.telegram.notify_beebom_article,
            notify_batch=self.telegram.notify_beebom_batch,
            mark_one=lambda art: self.storage.mark_seen(
                source="BEEBOM",
                article_id=art["article_id"],
                catalog_or_lens="roblox-codes",
                title=art["title"],
                url=art["url"],
            ),
        )

    def check_cryptorank_now(self):
        """Quét kênh Telegram @cryptorank_fundraising và xử lý tin mới (Batch)."""
        if not CONFIG["CRYPTORANK"]["ENABLE"]:
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét kênh Telegram @{self.cryptorank_scanner.channel} (Fundraising)...")
        items = self.cryptorank_scanner.fetch_latest_messages()
        if not items:
            print(f"[{now_str}] ℹ️ CryptoRank: Không tải được tin hoặc không có tin nào.")
            return

        self._process_source_items(
            source="CRYPTORANK",
            items=items,
            notify_single=self.telegram.notify_cryptorank_article,
            notify_batch=self.telegram.notify_cryptorank_batch,
            mark_one=lambda it: self.storage.mark_seen(
                source="CRYPTORANK",
                article_id=it["article_id"],
                catalog_or_lens="fundraising",
                title=it["title"],
                url=it["url"],
                amount=it.get("amount_formatted", ""),
            ),
        )

    def check_techcrunch_now(self):
        """Quét bài TechCrunch Fundraising và xử lý tin mới (Batch)."""
        if not CONFIG.get("TECHCRUNCH", {}).get("ENABLE", False):
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét TechCrunch ({CONFIG['TECHCRUNCH']['CATEGORY_NAME']})...")
        articles = self.techcrunch_scanner.fetch_latest_articles()
        if not articles:
            print(f"[{now_str}] ℹ️ TechCrunch: Không có dữ liệu.")
            return

        self._process_source_items(
            source="TECHCRUNCH",
            items=articles,
            notify_single=self.telegram.notify_techcrunch_article,
            notify_batch=self.telegram.notify_techcrunch_batch,
            mark_one=lambda art: self.storage.mark_seen(
                source="TECHCRUNCH",
                article_id=art["article_id"],
                catalog_or_lens="fundraising",
                title=art["title"],
                url=art["url"],
            ),
        )

    def check_bizztor_now(self):
        """Quét bài viết Bizztor Startup News và xử lý tin mới (Batch)."""
        if not CONFIG.get("BIZZTOR", {}).get("ENABLE", False):
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét Bizztor Startup News...")
        articles = self.bizztor_scanner.fetch_latest_articles()
        if not articles:
            print(f"[{now_str}] ℹ️ Bizztor: Không có dữ liệu.")
            return

        self._process_source_items(
            source="BIZZTOR",
            items=articles,
            notify_single=self.telegram.notify_bizztor_article,
            notify_batch=self.telegram.notify_bizztor_batch,
            mark_one=lambda art: self.storage.mark_seen(
                source="BIZZTOR",
                article_id=art["article_id"],
                catalog_or_lens="news",
                title=art["title"],
                url=art["url"],
                amount=art.get("amount_formatted", ""),
            ),
        )

    async def run_daemon(self):
        """Vòng lặp chạy ngầm chính bất đồng bộ."""
        print("=" * 75)
        print("🤖 MULTI-NEWS TELEGRAM NOTIFIER DAEMON ĐANG KHỞI CHẠY (24/7)")
        print("=" * 75)
        print(f"📍 File Database SQLite: {CONFIG['DB_PATH']}")
        print(f"📢 Telegram Chat ID: {CONFIG['TELEGRAM']['CHAT_ID']}")
        print(f"📦 Chế độ gom tin Batch: {'BẬT (Khuyên dùng)' if CONFIG.get('BATCH_MODE') else 'TẮT'}")
        dealroom_status = (
            f"{CONFIG['DEALROOM']['POLL_INTERVAL_SECONDS']}s"
            if CONFIG["DEALROOM"]["ENABLE"]
            else "TẮT (Cloudflare chặn 403)"
        )
        print(
            f"⏱️ Chu kỳ quét: Binance ({CONFIG['BINANCE']['POLL_INTERVAL_SECONDS']}s) | "
            f"Dealroom ({dealroom_status}) | "
            f"Beebom ({CONFIG.get('BEEBOM', {}).get('POLL_INTERVAL_SECONDS', 180)}s) | "
            f"CryptoRank TG ({CONFIG['CRYPTORANK']['POLL_INTERVAL_SECONDS']}s) | "
            f"TechCrunch ({CONFIG.get('TECHCRUNCH', {}).get('POLL_INTERVAL_SECONDS', 300)}s) | "
            f"Bizztor ({CONFIG.get('BIZZTOR', {}).get('POLL_INTERVAL_SECONDS', 300)}s)"
        )
        print("=" * 75)

        # 1. In trạng thái mốc dữ liệu hiện có (baseline thực tế là LAZY per-source)
        self.initialize_baseline()

        binance_interval = CONFIG["BINANCE"]["POLL_INTERVAL_SECONDS"]
        dealroom_interval = CONFIG["DEALROOM"]["POLL_INTERVAL_SECONDS"]
        beebom_interval = CONFIG.get("BEEBOM", {}).get("POLL_INTERVAL_SECONDS", 180)
        cryptorank_interval = CONFIG["CRYPTORANK"]["POLL_INTERVAL_SECONDS"]
        techcrunch_interval = CONFIG.get("TECHCRUNCH", {}).get("POLL_INTERVAL_SECONDS", 300)
        bizztor_interval = CONFIG.get("BIZZTOR", {}).get("POLL_INTERVAL_SECONDS", 300)

        last_binance_check = 0.0
        last_dealroom_check = 0.0
        last_beebom_check = 0.0
        last_cryptorank_check = 0.0
        last_techcrunch_check = 0.0
        last_bizztor_check = 0.0

        print("🟢 HỆ THỐNG ĐÃ SẴN SÀNG VÀ BẮT ĐẦU LẮNG NGHE TIN TỨC 24/7...\n")

        while IS_RUNNING:
            now = time.time()

            # 1. Quét Binance theo chu kỳ
            if now - last_binance_check >= binance_interval:
                try:
                    self.check_binance_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ Binance: {e}")
                last_binance_check = time.time()

            # 2. Quét Dealroom theo chu kỳ
            if now - last_dealroom_check >= dealroom_interval:
                try:
                    self.check_dealroom_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ Dealroom: {e}")
                last_dealroom_check = time.time()

            # 3. Quét Beebom theo chu kỳ
            if now - last_beebom_check >= beebom_interval:
                try:
                    self.check_beebom_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ Beebom: {e}")
                last_beebom_check = time.time()

            # 4. Quét kênh Telegram CryptoRank theo chu kỳ
            if now - last_cryptorank_check >= cryptorank_interval:
                try:
                    self.check_cryptorank_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ CryptoRank: {e}")
                last_cryptorank_check = time.time()

            # 5. Quét TechCrunch Fundraising theo chu kỳ
            if now - last_techcrunch_check >= techcrunch_interval:
                try:
                    self.check_techcrunch_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ TechCrunch: {e}")
                last_techcrunch_check = time.time()

            # 6. Quét Bizztor Startup News theo chu kỳ
            if now - last_bizztor_check >= bizztor_interval:
                try:
                    self.check_bizztor_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ Bizztor: {e}")
                last_bizztor_check = time.time()

            # Sleep ngắn để kiểm tra cờ tắt chương trình thường xuyên
            await asyncio.sleep(2)

        print("👋 Bot đã dừng hoạt động an toàn. Hẹn gặp lại!")


if __name__ == "__main__":
    daemon = MultiNewsNotifierDaemon()
    asyncio.run(daemon.run_daemon())


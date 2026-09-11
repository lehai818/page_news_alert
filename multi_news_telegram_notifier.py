#!/usr/bin/env python3
"""
=============================================================================
🤖 MULTI-NEWS TELEGRAM NOTIFIER (24/7 PRODUCTION DAEMON)
=============================================================================
Hệ thống giám sát và bắn tin tức tự động 24/7 qua Telegram:
1. Nguồn Binance Announcements:
   - Catalog 49: Latest Binance News (Tin niêm yết, tính năng, chính sách)
   - Catalog 93: Latest Activities (Sự kiện giao dịch, Airdrop, Giải thưởng)
2. Nguồn Dealroom Venture Intelligence:
   - Lens Funding: Các vòng gọi vốn đầu tư mạo hiểm (Seed, Series A-E,...)
   - Lens IPO: Kế hoạch IPO, niêm yết sàn chứng khoán, SPAC
3. Cơ chế hoạt động:
   - Lưu vết bài viết bằng SQLite (không cần cài đặt server DB, tự sinh file .db)
   - Lần đầu chạy: Tự động ghi nhớ các bài hiện có làm mốc, KHÔNG bị spam tin cũ
   - Khi có bài mới: Bắn thông báo Telegram tức thì (Định dạng HTML đẹp mắt)
   - Tự động phục hồi khi mất mạng, xử lý tín hiệu dừng an toàn (Graceful Shutdown)
=============================================================================
"""

import os
import sys
import time
import json
import html
import signal
import sqlite3
import asyncio
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from dotenv import load_dotenv

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
    # or "8736373193:AAF_HBdPgpDPbrM-2PCgY5gZaZX4ly0SKLM"
)
TELEGRAM_CHAT_ID = os.getenv("tk_news_chatid") ###########################

CONFIG = {
    # Đường dẫn file database SQLite (Tự động tạo ngay tại thư mục hiện tại)
    "DB_PATH": os.path.join(BASE_DIR, "seen_articles.db"),
    
    # Cấu hình Telegram
    "TELEGRAM": {
        "BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "CHAT_ID": TELEGRAM_CHAT_ID,
        "TIMEOUT": 15,
    },
    
    # Cấu hình nguồn Binance Announcements
    "BINANCE": {
        "ENABLE": True,
        # 49: Tin tức chung/Listing, 93: Sự kiện/Airdrop/Giải đấu
        "CATALOGS": [
            {"id": 49, "name": "Binance News"},
            {"id": 93, "name": "Binance Activities"},
        ],
        "BASE_URL": "https://www.binance.com/en/support/announcement",
        "POLL_INTERVAL_SECONDS": 300,  # Quét mỗi 45 giây
    },
    
    # Cấu hình nguồn Dealroom.co
    "DEALROOM": {
        "ENABLE": True,
        # funding: Vòng gọi vốn, ipo: Niêm yết cổ phiếu
        "LENSES": [
            {"lens": "funding", "name": "Vòng Gọi Vốn (Funding)"},
            {"lens": "ipo", "name": "Kế Hoạch IPO / Listing"},
        ],
        "BASE_URL": "https://dealroom.co/news",
        "POLL_INTERVAL_SECONDS": 350,  # Quét mỗi 2 phút (tránh rate limit)
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
# 🚀 6. DAEMON CHÍNH CHẠY NGẦM 24/7 (ASYNC ENGINE)
# =====================================================================
class MultiNewsNotifierDaemon:
    """Điều phối toàn bộ vòng lặp kiểm tra và thông báo tin mới đa nguồn."""

    def __init__(self):
        self.storage = StorageManager(db_path=CONFIG["DB_PATH"])
        self.telegram = TelegramAlertService(config=CONFIG["TELEGRAM"])
        self.binance_scanner = BinanceScanner(config=CONFIG["BINANCE"])
        self.dealroom_scanner = DealroomScanner(config=CONFIG["DEALROOM"])

    def initialize_baseline(self):
        """
        Khởi tạo mốc cơ sở:
        Nếu database rỗng (chạy lần đầu tiên), ghi nhớ toàn bộ các bài viết
        hiện có để KHÔNG bị bắn ồ ạt hàng chục tin cũ lên Telegram.
        """
        total_seen = self.storage.count_seen()
        if total_seen > 0:
            print(f"📌 Đã tải mốc lịch sử từ SQLite: Ghi nhận {total_seen} bài viết đã lưu.")
            return

        print("⚡ Database mới khởi tạo lần đầu. Đang quét mốc tin tức cơ sở...")
        
        # 1. Nạp mốc Binance
        if CONFIG["BINANCE"]["ENABLE"]:
            binance_articles = self.binance_scanner.fetch_latest_articles()
            for art in binance_articles:
                self.storage.mark_seen(
                    source="BINANCE",
                    article_id=art["article_id"],
                    catalog_or_lens=str(art["catalog_id"]),
                    title=art["title"],
                    url=art["url"],
                )
            print(f"  -> Đã ghi nhớ mốc ban đầu: {len(binance_articles)} thông báo Binance.")

        # 2. Nạp mốc Dealroom
        if CONFIG["DEALROOM"]["ENABLE"]:
            dealroom_deals = self.dealroom_scanner.fetch_latest_deals()
            for deal in dealroom_deals:
                self.storage.mark_seen(
                    source="DEALROOM",
                    article_id=deal["article_id"],
                    catalog_or_lens=deal["lens"],
                    title=deal["title"],
                    url=deal["url"],
                    amount=deal.get("raw_amount", ""),
                )
            print(f"  -> Đã ghi nhớ mốc ban đầu: {len(dealroom_deals)} tin Dealroom (Funding/IPO).")

        print("✅ Đã thiết lập mốc khởi đầu thành công. Từ thời điểm này, chỉ bài MỚI PHÁT SINH mới gửi Telegram!\n")

    def check_binance_now(self):
        """Quét và xử lý tin mới từ Binance."""
        if not CONFIG["BINANCE"]["ENABLE"]:
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét thông báo Binance (Catalog 49 & 93)...")
        articles = self.binance_scanner.fetch_latest_articles()
        
        new_count = 0
        for art in articles:
            code = art["article_id"]
            if not self.storage.is_seen(source="BINANCE", article_id=code):
                print(f"🔥 [TIN MỚI PHÁT HIỆN] Binance: {art['title']}")
                # Gửi Telegram
                self.telegram.notify_binance_article(art)
                # Lưu vào DB
                self.storage.mark_seen(
                    source="BINANCE",
                    article_id=code,
                    catalog_or_lens=str(art["catalog_id"]),
                    title=art["title"],
                    url=art["url"],
                )
                new_count += 1
                time.sleep(1)  # Giãn cách nhỏ giữa các tin nhắn

        if new_count == 0:
            print(f"[{now_str}] ℹ️ Binance: Không có bài mới.")

    def check_dealroom_now(self):
        """Quét và xử lý tin mới từ Dealroom."""
        if not CONFIG["DEALROOM"]["ENABLE"]:
            return

        now_str = datetime.now().strftime("%H:%M:%S")
        print(f"[{now_str}] 🔍 Đang quét tin tức Dealroom (Funding & IPO)...")
        deals = self.dealroom_scanner.fetch_latest_deals()

        new_count = 0
        for deal in deals:
            slug = deal["article_id"]
            if not self.storage.is_seen(source="DEALROOM", article_id=slug):
                print(f"🔥 [TIN MỚI PHÁT HIỆN] Dealroom ({deal['lens'].upper()}): {deal['title']}")
                # Gửi Telegram
                self.telegram.notify_dealroom_deal(deal)
                # Lưu vào DB
                self.storage.mark_seen(
                    source="DEALROOM",
                    article_id=slug,
                    catalog_or_lens=deal["lens"],
                    title=deal["title"],
                    url=deal["url"],
                    amount=deal.get("raw_amount", ""),
                )
                new_count += 1
                time.sleep(1)

        if new_count == 0:
            print(f"[{now_str}] ℹ️ Dealroom: Không có bài mới.")

    async def run_daemon(self):
        """Vòng lặp chạy ngầm chính bất đồng bộ."""
        print("=" * 75)
        print("🤖 MULTI-NEWS TELEGRAM NOTIFIER DAEMON ĐANG KHỞI CHẠY (24/7)")
        print("=" * 75)
        print(f"📍 File Database SQLite: {CONFIG['DB_PATH']}")
        print(f"📢 Telegram Chat ID: {CONFIG['TELEGRAM']['CHAT_ID']}")
        print(f"⏱️ Chu kỳ quét: Binance ({CONFIG['BINANCE']['POLL_INTERVAL_SECONDS']}s) | Dealroom ({CONFIG['DEALROOM']['POLL_INTERVAL_SECONDS']}s)")
        print("=" * 75)

        # 1. Khởi tạo mốc cơ sở
        self.initialize_baseline()

        binance_interval = CONFIG["BINANCE"]["POLL_INTERVAL_SECONDS"]
        dealroom_interval = CONFIG["DEALROOM"]["POLL_INTERVAL_SECONDS"]

        last_binance_check = 0.0
        last_dealroom_check = 0.0

        print("🟢 HỆ THỐNG ĐÃ SẴN SÀNG VÀ BẮT ĐẦU LẮNG NGHE TIN TỨC 24/7...\n")

        while IS_RUNNING:
            now = time.time()

            # Quét Binance theo chu kỳ
            if now - last_binance_check >= binance_interval:
                try:
                    self.check_binance_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ Binance: {e}")
                last_binance_check = time.time()

            # Quét Dealroom theo chu kỳ
            if now - last_dealroom_check >= dealroom_interval:
                try:
                    self.check_dealroom_now()
                except Exception as e:
                    print(f"⚠️ Lỗi chu kỳ Dealroom: {e}")
                last_dealroom_check = time.time()

            # Sleep ngắn để kiểm tra cờ tắt chương trình thường xuyên
            await asyncio.sleep(2)

        print("👋 Bot đã dừng hoạt động an toàn. Hẹn gặp lại!")


if __name__ == "__main__":
    daemon = MultiNewsNotifierDaemon()
    asyncio.run(daemon.run_daemon())

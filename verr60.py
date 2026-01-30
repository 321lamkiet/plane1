import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from apify_client import ApifyClient
from datetime import datetime, time as dt_time
import time
import contextlib
import pytz
import google.generativeai as genai
import streamlit.components.v1 as components
import urllib.parse

# ==========================================
# 1. CẤU HÌNH HỆ THỐNG
# ==========================================
st.set_page_config(
    page_title="TikTok Intelligence OS v6.0",
    page_icon="💸",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
    div[data-testid="stMetric"] {
        background-color: #1F2937;
        border: 1px solid #374151;
        border-radius: 8px;
        padding: 15px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .status-badge {
        padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8em;
    }
   .badge-live { background-color: #065F46; color: #34D399; }
   .badge-sleep { background-color: #7F1D1D; color: #FCA5A5; }
    iframe { border-radius: 12px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. DATABASE ENGINE
# ==========================================
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v60_affiliate.db"): # Đổi tên DB mới
        self.db_name = db_name
        self.init_db()

    @contextlib.contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_name, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    author_name TEXT,
                    author_followers INTEGER, 
                    description TEXT,
                    video_url TEXT,
                    thumbnail_url TEXT,
                    music_title TEXT,
                    music_author TEXT,
                    is_saved INTEGER DEFAULT 0,
                    created_at INTEGER,
                    last_scraped_at TIMESTAMP,
                    current_views INTEGER,
                    current_shares INTEGER,
                    velocity_value REAL,
                    velocity_type TEXT,
                    ai_analysis TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT,
                    play_count INTEGER,
                    share_count INTEGER,
                    scraped_at TIMESTAMP,
                    FOREIGN KEY(video_id) REFERENCES videos(video_id)
                )
            """)
            conn.commit()

    def get_last_metric(self, video_id):
        with self.get_connection() as conn:
            cursor = conn.execute("""
                SELECT play_count, scraped_at FROM metrics 
                WHERE video_id =? ORDER BY scraped_at DESC LIMIT 1
            """, (video_id,))
            return cursor.fetchone()

    def upsert_video(self, data, velocity, v_type):
        now = datetime.now()
        vid_id = data.get('id')
        author = data.get('authorMeta', {}).get('name', 'Unknown')
        followers = data.get('authorMeta', {}).get('fans', 0)
        desc = data.get('text', '')
        url = data.get('webVideoUrl', '')
        thumb = data.get('videoMeta', {}).get('coverUrl', '')
        
        # [NEW] Lấy thông tin nhạc
        music_title = data.get('musicMeta', {}).get('musicName', 'Unknown')
        music_author = data.get('musicMeta', {}).get('musicAuthor', 'Unknown')

        created_at = data.get('createTime', int(time.time()))
        views = data.get('playCount', 0)
        shares = data.get('shareCount', 0)

        with self.get_connection() as conn:
            # Upsert nhưng KHÔNG ghi đè trường is_saved (để giữ bookmark)
            conn.execute("""
                INSERT INTO videos (
                    video_id, author_name, author_followers, description, video_url, thumbnail_url,
                    music_title, music_author,
                    created_at, last_scraped_at, current_views, current_shares,
                    velocity_value, velocity_type
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(video_id) DO UPDATE SET
                    last_scraped_at=excluded.last_scraped_at,
                    current_views=excluded.current_views,
                    current_shares=excluded.current_shares,
                    author_followers=excluded.author_followers,
                    velocity_value=excluded.velocity_value,
                    velocity_type=excluded.velocity_type,
                    music_title=excluded.music_title,
                    music_author=excluded.music_author
            """, (vid_id, author, followers, desc, url, thumb, music_title, music_author, 
                  created_at, now, views, shares, velocity, v_type))
            
            conn.execute("INSERT INTO metrics (video_id, play_count, share_count, scraped_at) VALUES (?,?,?,?)",
                         (vid_id, views, shares, now))
            conn.commit()
            
    def update_ai_analysis(self, video_id, analysis_text):
        with self.get_connection() as conn:
            conn.execute("UPDATE videos SET ai_analysis =? WHERE video_id =?", (analysis_text, video_id))
            conn.commit()
            
    def toggle_save_video(self, video_id, current_status):
        new_status = 0 if current_status == 1 else 1
        with self.get_connection() as conn:
            conn.execute("UPDATE videos SET is_saved =? WHERE video_id =?", (new_status, video_id))
            conn.commit()

    def fetch_data(self, only_saved=False):
        with self.get_connection() as conn:
            if only_saved:
                return pd.read_sql("SELECT * FROM videos WHERE is_saved = 1 ORDER BY last_scraped_at DESC", conn)
            else:
                return pd.read_sql("SELECT * FROM videos ORDER BY velocity_value DESC", conn)

db = DatabaseEngine()

# ==========================================
# 3. CORE LOGIC
# ==========================================
def calculate_velocity(video_item):
    video_id = video_item['id']
    current_views = video_item.get('playCount', 0)
    current_time = datetime.now()
    prev = db.get_last_metric(video_id)
    
    if prev:
        prev_views, prev_time_str = prev
        prev_time = pd.to_datetime(prev_time_str)
        hours_diff = (current_time - prev_time).total_seconds() / 3600.0
        if hours_diff > 0.05:
            view_diff = max(0, current_views - prev_views)
            velocity = view_diff / hours_diff
            return round(velocity, 1), "⚡ Thực"
    else:
        created_ts = video_item.get('createTime', 0)
        if created_ts:
            age_hours = max(1.0, (current_time - datetime.fromtimestamp(created_ts)).total_seconds() / 3600.0)
            velocity = current_views / age_hours
            return round(velocity, 1), "🆕 Dự báo"
    return 0.0, "⏳ Chờ"

def check_market_hours(country_code):
    try:
        if country_code == "VN":
            tz = pytz.timezone('Asia/Ho_Chi_Minh')
        else:
            tz = pytz.timezone('US/Eastern')
        now = datetime.now(tz)
        is_active = 8 <= now.hour < 23
        return is_active, now.strftime("%H:%M")
    except:
        return True, "Unknown"

def run_apify_scan(token, hashtags_str, limit, country_code, filter_mode, ai_keywords, custom_proxy=None):
    if not token: return False, "Thiếu API Token!"
    client = ApifyClient(token)
    
    if custom_proxy and custom_proxy.strip():
        proxy_config = {"useApifyProxy": False, "proxyUrls": [custom_proxy.strip()]}
    else:
        proxy_config = {"useApifyProxy": True, "apifyProxyCountry": country_code}

    hashtags_list = [h.strip() for h in hashtags_str.split(",") if h.strip()]
    if not hashtags_list: return False, "Nhập ít nhất 1 hashtag!"

    run_input = {
        "hashtags": hashtags_list,
        "resultsPerPage": limit,
        "proxyConfiguration": proxy_config,
        "searchSection": "", 
        "shouldDownloadCovers": False
    }
    
    try:
        run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
        if not run or 'defaultDatasetId' not in run:
             return False, "Lỗi Apify: Không trả về dữ liệu."

        dataset = client.dataset(run['defaultDatasetId']).list_items().items
        if not dataset: return False, "Không tìm thấy video nào."
        
        keywords = [k.strip().lower() for k in ai_keywords.split(",")] if ai_keywords else []
        valid_count = 0
        
        for item in dataset:
            desc = item.get('text', '').lower()
            is_ai_content = False
            for k in keywords:
                if k in desc:
                    is_ai_content = True
                    break
            
            should_save = False
            if filter_mode == "🌐 Hiển thị tất cả":
                should_save = True
            elif filter_mode == "🎯 Chỉ lấy Video AI":
                if is_ai_content: should_save = True
            elif filter_mode == "🚫 Chặn Video AI":
                if not is_ai_content: should_save = True
                
            if should_save:
                vel, v_type = calculate_velocity(item)
                db.upsert_video(item, vel, v_type)
                valid_count += 1
            
        return True, f"Quét xong! Lưu {valid_count} video."
    except Exception as e:
        return False, str(e)

def analyze_with_gemini(api_key, video_desc, author):
    if not api_key: return "⚠️ Thiếu Gemini API Key"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"Phân tích Marketing TikTok: Tác giả: {author}, Caption: {video_desc}. 3 ý ngắn gọn: Hook, Nỗi đau, Kịch bản remake."
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Lỗi AI: {str(e)}"

# ==========================================
# 5. GIAO DIỆN (FRONTEND)
# ==========================================
with st.sidebar:
    st.title("🦅 TikTok Intelligence OS")
    st.caption("v6.0 | Affiliate Suite")
    
    with st.expander("🔑 Cấu hình API", expanded=True):
        apify_token = st.text_input("Apify Token", type="password")
        gemini_key = st.text_input("Gemini API Key", type="password")
    
    with st.expander("⚙️ Cài đặt Quét", expanded=True):
        hashtags_str = st.text_area("Danh sách Hashtag", "shilajit, amazonfinds, gadgets")
        country_map = {"Mỹ (US)": "US", "Việt Nam (VN)": "VN", "Anh (UK)": "GB", "Pháp (FR)": "FR"}
        selected_country = st.selectbox("Quốc Gia", list(country_map.keys()))
        country_code = country_map[selected_country]
        custom_proxy = st.text_input("Proxy Riêng", placeholder="http://...", help="Tùy chọn")
        limit = st.slider("Limit/Quét", 10, 100, 30)

    st.subheader("🤖 Chiến lược & Lọc")
    filter_mode = st.radio("Chế độ lọc:", ["🌐 Hiển thị tất cả", "🎯 Chỉ lấy Video AI", "🚫 Chặn Video AI"])
    ai_keywords = st.text_area("Từ khóa AI", "ai generated, midjourney, chatgpt, #ai", height=70)
    use_viral_filter = st.checkbox("Ẩn kênh lớn view thấp", value=True)

    st.subheader("⚙️ Tự Động Hóa")
    auto_mode = st.checkbox("Bật chạy tự động")
    if auto_mode:
        scan_interval = st.number_input("Nghỉ (Phút)", 15, 120, 30, step=5)
        max_scans = st.number_input("Max lần/ngày", 1, 50, 10)
    else:
        scan_interval = 30; max_scans = 1
    
    if 'scan_count' not in st.session_state: st.session_state['scan_count'] = 0
    btn_label = "🚀 BẮT ĐẦU AUTO" if auto_mode else "🚀 QUÉT NGAY"
    
    if st.button(btn_label, type="primary", use_container_width=True):
        if st.session_state['scan_count'] >= max_scans and auto_mode:
            st.error("🛑 Đạt giới hạn ngày!")
        else:
            with st.status("Đang kết nối TikTok...") as status:
                success, msg = run_apify_scan(apify_token, hashtags_str, limit, country_code, filter_mode, ai_keywords, custom_proxy)
                if success:
                    st.session_state['scan_count'] += 1
                    status.update(label="✅ Xong!", state="complete")
                    time.sleep(1)
                    st.rerun()
                else:
                    status.update(label="❌ Lỗi", state="error"); st.error(msg)
    
    if st.button("📥 Tải Excel/CSV"):
        csv = db.fetch_data().to_csv(index=False).encode('utf-8')
        st.download_button("Bấm tải về", data=csv, file_name="tiktok_data.csv", mime="text/csv")

# --- DASHBOARD ---
market_active, local_time = check_market_hours(country_code)
status_html = f'<span class="status-badge badge-live">LIVE</span>' if market_active else f'<span class="status-badge badge-sleep">SLEEP</span>'
st.markdown(f"### 🏳️ {selected_country}: {local_time} | {status_html}", unsafe_allow_html=True)

# Lấy dữ liệu
df_all = db.fetch_data()
df_saved = db.fetch_data(only_saved=True)

# Lọc rác (chỉ áp dụng cho list chung, kho lưu trữ thì không lọc)
if use_viral_filter and not df_all.empty:
    df_all['safe_followers'] = df_all['author_followers'].replace(0, 1)
    df_all['viral_ratio'] = df_all['current_views'] / df_all['safe_followers']
    df_all = df_all[ (df_all['viral_ratio'] > 0.5) | (df_all['velocity_value'] > 500) ]

# --- METRICS ---
k1, k2, k3, k4 = st.columns(4)
k1.metric("Videos Mới", len(df_all))
k2.metric("Kho Lưu Trữ", len(df_saved))
if not df_all.empty:
    k3.metric("Tốc độ Đỉnh", f"{df_all['velocity_value'].max():.0f}/h")
k4.metric("Lần quét", f"{st.session_state['scan_count']}/{max_scans}")

st.divider()

# --- TABS GIAO DIỆN ---
main_tab1, main_tab2, main_tab3 = st.tabs(["🔥 Xu Hướng Mới", "❤️ Kho Của Tôi", "📊 Biểu Đồ"])

# Hàm hiển thị Video Card
def render_video_list(dataframe, is_saved_tab=False):
    if dataframe.empty:
        st.info("Chưa có video nào.")
        return

    for _, row in dataframe.head(50).iterrows(): # Giới hạn hiển thị 50 video để đỡ lag
        ratio = row['current_views'] / max(1, row['author_followers'])
        emoji = "💎" if ratio > 1.0 else "😐"
        
        # Tiêu đề Card
        card_title = f"{emoji} Vel: {row['velocity_value']:.0f}/h | {row['author_name']}"
        
        with st.expander(card_title, expanded=False):
            # Cột thông tin
            c1, c2 = st.columns([1.5, 1])
            with c1:
                st.caption(f"👁️ View: {row['current_views']:,} | 👤 Sub: {row['author_followers']:,}")
                st.caption(f"🎵 Nhạc: {row['music_title']} - {row['music_author']}")
            with c2:
                # Nút Lưu / Bỏ Lưu
                save_label = "💔 Bỏ Lưu" if row['is_saved'] == 1 else "❤️ Lưu Video"
                if st.button(save_label, key=f"save_{row['video_id']}_{is_saved_tab}"):
                    db.toggle_save_video(row['video_id'], row['is_saved'])
                    st.rerun()

            # Video Player
            components.iframe(f"https://www.tiktok.com/embed/v2/{row['video_id']}", height=450, scrolling=True)
            
            # [NEW] Khu vực Affiliate Sourcing
            st.markdown("---")
            st.markdown("##### 🛒 Tìm nguồn hàng:")
            
            # Tạo link tìm kiếm tự động
            search_query = urllib.parse.quote(row['description'][:50]) # Lấy 50 ký tự đầu làm từ khóa
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown(f"[🔍 Tìm trên Amazon](https://www.amazon.com/s?k={search_query})", unsafe_allow_html=True)
            with col_b:
                st.markdown(f"[🛍️ Tìm trên AliExpress](https://www.aliexpress.com/wholesale?SearchText={search_query})", unsafe_allow_html=True)
            
            st.markdown("---")
            
            # Nút AI
            if st.button(f"🧠 Phân tích Kịch bản", key=f"ai_{row['video_id']}_{is_saved_tab}"):
                with st.spinner("Gemini analyzing..."):
                    analysis = analyze_with_gemini(gemini_key, row['description'], row['author_name'])
                    db.update_ai_analysis(row['video_id'], analysis); st.rerun()
            if row['ai_analysis']: st.info(row['ai_analysis'])

with main_tab1:
    render_video_list(df_all, is_saved_tab=False)

with main_tab2:
    if df_saved.empty:
        st.warning("Bạn chưa lưu video nào. Hãy bấm '❤️ Lưu Video' ở tab Xu Hướng Mới nhé!")
    else:
        render_video_list(df_saved, is_saved_tab=True)

with main_tab3:
    if not df_all.empty:
        fig_scatter = px.scatter(
            df_all, x="author_followers", y="velocity_value", size="current_views", 
            color="viral_ratio", hover_name="author_name", log_x=True, 
            color_continuous_scale="Turbo", title="Rađa Săn Viral"
        )
        st.plotly_chart(fig_scatter, use_container_width=True)

import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from apify_client import ApifyClient
from datetime import datetime
import time
import contextlib
import requests # Thư viện để tải video
import streamlit.components.v1 as components
import urllib.parse

# ==========================================
# CẤU HÌNH (v10.0 Video Downloader)
# ==========================================
st.set_page_config(page_title="TikTok OS v10.0", page_icon="⬇️", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   /* Style cho nút download */
   .stDownloadButton button { width: 100%; background-color: #2563EB !important; color: white !important; border: none; }
</style>
""", unsafe_allow_html=True)

# DATABASE
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v10_downloader.db"): 
        self.db_name = db_name
        self.init_db()
    @contextlib.contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_name, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        try: yield conn
        finally: conn.close()
    def init_db(self):
        with self.get_connection() as conn:
            # Đã bỏ cột ai_analysis
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY, author_name TEXT, author_followers INTEGER, description TEXT, video_url TEXT, thumbnail_url TEXT, music_title TEXT, music_author TEXT, is_saved INTEGER DEFAULT 0, created_at INTEGER, last_scraped_at TIMESTAMP, current_views INTEGER, current_shares INTEGER, velocity_value REAL, velocity_type TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, video_id TEXT, play_count INTEGER, share_count INTEGER, scraped_at TIMESTAMP)""")
            conn.commit()
    def get_last_metric(self, video_id):
        with self.get_connection() as conn: return conn.execute("SELECT play_count, scraped_at FROM metrics WHERE video_id =? ORDER BY scraped_at DESC LIMIT 1", (video_id,)).fetchone()
    def upsert_video(self, data, vel, v_type):
        vid_id = data.get('id'); author = data.get('authorMeta', {}).get('name', 'Unknown')
        if not vid_id: return
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", 
                         (vid_id, author, data.get('authorMeta', {}).get('fans', 0), data.get('text', ''), data.get('webVideoUrl', ''), data.get('videoMeta', {}).get('coverUrl', ''), data.get('musicMeta', {}).get('musicName', ''), data.get('musicMeta', {}).get('musicAuthor', ''), 0, int(time.time()), datetime.now(), data.get('playCount', 0), data.get('shareCount', 0), vel, v_type))
            conn.execute("INSERT INTO metrics (video_id, play_count, share_count, scraped_at) VALUES (?,?,?,?)", (vid_id, data.get('playCount', 0), data.get('shareCount', 0), datetime.now()))
            conn.commit()
    def toggle_save(self, vid, status):
        with self.get_connection() as conn: conn.execute("UPDATE videos SET is_saved=? WHERE video_id=?", (0 if status==1 else 1, vid)); conn.commit()
    def fetch(self, saved=False):
        with self.get_connection() as conn: return pd.read_sql(f"SELECT * FROM videos {'WHERE is_saved=1' if saved else ''} ORDER BY velocity_value DESC", conn)

db = DatabaseEngine()

# TÍNH TOÁN TỐC ĐỘ
def calc_vel(item):
    prev = db.get_last_metric(item['id'])
    curr_views = item.get('playCount', 0); now = datetime.now()
    if prev:
        hours = (now - pd.to_datetime(prev[1])).total_seconds() / 3600.0
        if hours > 0.05: return round(max(0, curr_views - prev[0]) / hours, 1), "⚡ Thực"
    age = max(1.0, (now - datetime.fromtimestamp(item.get('createTime', 0))).total_seconds() / 3600.0)
    return round(curr_views / age, 1), "🆕 Dự báo"

# QUÉT DỮ LIỆU
def run_scan(token, tags, limit, country, proxy):
    if not token: return False, "Thiếu Token Apify!"
    client = ApifyClient(token)
    p_config = {"useApifyProxy": True}
    if proxy: p_config = {"useApifyProxy": False, "proxyUrls": [proxy]}
    elif country != "ALL": p_config = {"useApifyProxy": True, "apifyProxyCountry": country}
    
    try:
        run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tags.split(','), "resultsPerPage": limit, "proxyConfiguration": p_config, "searchSection": ""})
        if not run: return False, "Lỗi Apify."
        items = client.dataset(run['defaultDatasetId']).list_items().items
        if not items: return False, "Không có video."
        
        count = 0
        for item in items:
            v, t = calc_vel(item); db.upsert_video(item, v, t); count += 1
        return True, f"Đã quét {count} video."
    except Exception as e: return False, str(e)

# HÀM TẢI VIDEO (MỚI)
@st.cache_data(show_spinner=False)
def get_video_bytes(url):
    try:
        # Giả lập trình duyệt để TikTok không chặn
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, stream=True, timeout=10)
        if response.status_code == 200:
            return response.content
    except: pass
    return None

# GIAO DIỆN
with st.sidebar:
    st.title("🦅 TikTok OS v10.0"); st.caption("Video Downloader Edition")
    
    # Không còn ô nhập API Key AI nữa -> Rất gọn
    api_tk = st.text_input("Apify Token", type="password")
    
    with st.expander("⚙️ Cài đặt Quét", expanded=True):
        tags = st.text_area("Hashtags", "shilajit, amazonfinds")
        country_map = {"🌐 Global": "ALL", "Mỹ": "US", "Việt Nam": "VN", "Anh": "GB", "Pháp": "FR"}
        country = country_map[st.selectbox("Quốc gia", list(country_map.keys()))]
        limit = st.slider("Số lượng", 10, 50, 20) # Giảm limit để load nhanh hơn

    if st.button("🚀 QUÉT VIDEO", type="primary"): 
        with st.status("Đang quét dữ liệu..."):
            s, m = run_scan(api_tk, tags, limit, country, "")
            if s: st.success(m); time.sleep(1); st.rerun()
            else: st.error(m)
            
    if st.button("📥 Xuất File Excel"):
        st.download_button("Download CSV", db.fetch().to_csv(index=False).encode('utf-8'), "data.csv", "text/csv")

# DASHBOARD
df_all = db.fetch()
df_saved = db.fetch(saved=True)

if not df_all.empty:
    # Xử lý dữ liệu an toàn
    df_all['followers'] = pd.to_numeric(df_all['author_followers'], errors='coerce').fillna(0)
    df_all['views'] = pd.to_numeric(df_all['current_views'], errors='coerce').fillna(0)
    df_all['safe_followers'] = df_all['followers'].apply(lambda x: x if x > 0 else 1)
    df_all['ratio'] = df_all['views'] / df_all['safe_followers']

    c1, c2, c3 = st.columns(3)
    c1.metric("Tổng Video", len(df_all))
    c2.metric("Đã Lưu", len(df_saved))
    c3.metric("Tốc độ cao nhất", f"{df_all['velocity_value'].max():.0f}/h")

    t1, t2, t3 = st.tabs(["🔥 Danh sách & Tải", "❤️ Kho Lưu Trữ", "📊 Biểu Đồ"])
    
    def render(d, saved=False):
        if d.empty: st.info("Trống."); return
        for _, r in d.head(50).iterrows():
            emoji = "💎" if r.get('ratio', 0) > 1.0 else "😐"
            with st.expander(f"{emoji} {r['velocity_value']:.0f}/h | {r['author_name']}"):
                c1, c2 = st.columns([1.5, 1])
                
                # Cột thông tin
                c1.caption(f"👀 {r['current_views']:,} views | 👤 {r['author_followers']:,} subs")
                c1.caption(f"🎵 {r['music_title']}")
                
                # Cột chức năng
                if c2.button("❤️ Lưu / Bỏ", key=f"s_{r['video_id']}_{saved}"): db.toggle_save(r['video_id'], r['is_saved']); st.rerun()
                
                # TÍNH NĂNG DOWNLOAD VIDEO
                video_bytes = None
                # Chỉ tải khi người dùng mở expander để tiết kiệm băng thông
                if r['video_url']:
                    video_bytes = get_video_bytes(r['video_url'])
                    
                if video_bytes:
                    c2.download_button(
                        label="📥 Tải Video MP4",
                        data=video_bytes,
                        file_name=f"{r['author_name']}_{r['video_id']}.mp4",
                        mime="video/mp4",
                        key=f"dl_{r['video_id']}_{saved}"
                    )
                else:
                    c2.markdown(f"[🔗 Mở Link Gốc]({r['video_url']})")

                # Video Player
                st.components.v1.iframe(f"https://www.tiktok.com/embed/v2/{r['video_id']}", height=400)
                
                # Affiliate Sourcing Links
                q = urllib.parse.quote(r['description'][:50])
                st.markdown(f"**🛒 Tìm nguồn hàng:** [🔎 Amazon](https://www.amazon.com/s?k={q}) | [🛍️ AliExpress](https://www.aliexpress.com/wholesale?SearchText={q})")

    with t1: render(df_all)
    with t2: render(df_saved, True)
    with t3:
        try:
            st.plotly_chart(px.scatter(df_all, x='safe_followers', y='velocity_value', size='views', color='ratio', log_x=True, title="Rađa Viral"), use_container_width=True)
        except: st.warning("Không đủ dữ liệu.")
else:
    st.info("👋 Chào bạn! Hãy nhập Token Apify và bấm QUÉT để bắt đầu.")

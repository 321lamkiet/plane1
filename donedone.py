import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from apify_client import ApifyClient
from datetime import datetime
import time
import contextlib
import pytz
from google import genai # [NEW] Thư viện mới của Google
import streamlit.components.v1 as components
import urllib.parse

# ==========================================
# CẤU HÌNH (v8.0 Modern Core)
# ==========================================
st.set_page_config(page_title="TikTok OS v8.0", page_icon="⚡", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
</style>
""", unsafe_allow_html=True)

# DATABASE
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v80_modern.db"): 
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
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY, author_name TEXT, author_followers INTEGER, description TEXT, video_url TEXT, thumbnail_url TEXT, music_title TEXT, music_author TEXT, is_saved INTEGER DEFAULT 0, created_at INTEGER, last_scraped_at TIMESTAMP, current_views INTEGER, current_shares INTEGER, velocity_value REAL, velocity_type TEXT, ai_analysis TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, video_id TEXT, play_count INTEGER, share_count INTEGER, scraped_at TIMESTAMP)""")
            conn.commit()
    def get_last_metric(self, video_id):
        with self.get_connection() as conn: return conn.execute("SELECT play_count, scraped_at FROM metrics WHERE video_id =? ORDER BY scraped_at DESC LIMIT 1", (video_id,)).fetchone()
    def upsert_video(self, data, vel, v_type):
        vid_id = data.get('id'); author = data.get('authorMeta', {}).get('name', 'Unknown')
        if not vid_id: return
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT ai_analysis FROM videos WHERE video_id=?), NULL))", 
                         (vid_id, author, data.get('authorMeta', {}).get('fans', 0), data.get('text', ''), data.get('webVideoUrl', ''), data.get('videoMeta', {}).get('coverUrl', ''), data.get('musicMeta', {}).get('musicName', ''), data.get('musicMeta', {}).get('musicAuthor', ''), 0, int(time.time()), datetime.now(), data.get('playCount', 0), data.get('shareCount', 0), vel, v_type, vid_id))
            conn.execute("INSERT INTO metrics (video_id, play_count, share_count, scraped_at) VALUES (?,?,?,?)", (vid_id, data.get('playCount', 0), data.get('shareCount', 0), datetime.now()))
            conn.commit()
    def update_ai(self, vid, txt):
        with self.get_connection() as conn: conn.execute("UPDATE videos SET ai_analysis=? WHERE video_id=?", (txt, vid)); conn.commit()
    def toggle_save(self, vid, status):
        with self.get_connection() as conn: conn.execute("UPDATE videos SET is_saved=? WHERE video_id=?", (0 if status==1 else 1, vid)); conn.commit()
    def fetch(self, saved=False):
        with self.get_connection() as conn: return pd.read_sql(f"SELECT * FROM videos {'WHERE is_saved=1' if saved else ''} ORDER BY velocity_value DESC", conn)

db = DatabaseEngine()

# LOGIC QUÉT
def calc_vel(item):
    prev = db.get_last_metric(item['id'])
    curr_views = item.get('playCount', 0); now = datetime.now()
    if prev:
        hours = (now - pd.to_datetime(prev[1])).total_seconds() / 3600.0
        if hours > 0.05: return round(max(0, curr_views - prev[0]) / hours, 1), "⚡ Thực"
    age = max(1.0, (now - datetime.fromtimestamp(item.get('createTime', 0))).total_seconds() / 3600.0)
    return round(curr_views / age, 1), "🆕 Dự báo"

def run_scan(token, tags, limit, country, filter_mode, ai_keys, proxy):
    if not token: return False, "Thiếu Token!"
    client = ApifyClient(token)
    p_config = {"useApifyProxy": True}
    if proxy: p_config = {"useApifyProxy": False, "proxyUrls": [proxy]}
    elif country != "ALL": p_config = {"useApifyProxy": True, "apifyProxyCountry": country}
    
    try:
        run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tags.split(','), "resultsPerPage": limit, "proxyConfiguration": p_config, "searchSection": ""})
        items = client.dataset(run['defaultDatasetId']).list_items().items
        if not items: return False, "Không có video."
        count = 0
        for item in items:
            is_ai = any(k in item.get('text', '').lower() for k in ai_keys.split(','))
            if (filter_mode == "🎯 Chỉ lấy Video AI" and not is_ai) or (filter_mode == "🚫 Chặn Video AI" and is_ai): continue
            v, t = calc_vel(item); db.upsert_video(item, v, t); count += 1
        return True, f"Lưu {count} video."
    except Exception as e: return False, str(e)

# [NEW] CẤU TRÚC GENAI MỚI (V1.0)
def run_gemini(key, desc, auth):
    if not key: return "⚠️ Thiếu API Key"
    try:
        # Cú pháp mới của thư viện google-genai
        client = genai.Client(api_key=key)
        response = client.models.generate_content(
            model="gemini-2.0-flash", # Dùng model mới nhất
            contents=f"Phân tích Marketing TikTok: Tác giả {auth}, Caption: {desc}. 3 ý: Hook, Nỗi đau, Remake."
        )
        return response.text
    except Exception as e:
        return f"❌ Lỗi AI: {str(e)}"

# UI
with st.sidebar:
    st.title("🦅 TikTok OS v8.0"); st.caption("New AI Core")
    api_tk = st.text_input("Apify Token", type="password")
    gemini_tk = st.text_input("Gemini API Key", type="password")
    tags = st.text_area("Hashtags", "shilajit")
    country = st.selectbox("Quốc gia", ["ALL", "US", "VN"], index=1)
    if st.button("🚀 QUÉT"): 
        s, m = run_scan(api_tk, tags, 30, country, "🌐 Hiển thị tất cả", "", "")
        if s: st.success(m); time.sleep(1); st.rerun()
        else: st.error(m)

df = db.fetch()
if not df.empty:
    # [FIX] DỮ LIỆU BIỂU ĐỒ AN TOÀN
    try:
        # Ép kiểu số, nếu lỗi thành 0
        df['followers'] = pd.to_numeric(df['author_followers'], errors='coerce').fillna(0)
        df['views'] = pd.to_numeric(df['current_views'], errors='coerce').fillna(0)
        df['velocity'] = pd.to_numeric(df['velocity_value'], errors='coerce').fillna(0)
        
        # Đảm bảo followers >= 1 để vẽ Logarit không lỗi
        df['safe_followers'] = df['followers'].apply(lambda x: x if x > 0 else 1)
        df['ratio'] = df['views'] / df['safe_followers']
    except Exception as e:
        st.error(f"Lỗi xử lý dữ liệu: {e}")

    tab1, tab2 = st.tabs(["List Video", "Biểu Đồ"])
    with tab1:
        for _, r in df.head(50).iterrows():
            with st.expander(f"💎 {r['velocity_value']:.0f}/h | {r['author_name']}"):
                st.components.v1.iframe(f"https://www.tiktok.com/embed/v2/{r['video_id']}", height=400)
                if st.button("🧠 Phân tích (New AI)", key=r['video_id']):
                    anl = run_gemini(gemini_tk, r['description'], r['author_name'])
                    db.update_ai(r['video_id'], anl); st.rerun()
                if r['ai_analysis']: st.info(r['ai_analysis'])
    with tab2:
        try:
            # Vẽ biểu đồ với dữ liệu sạch 'safe_followers'
            st.plotly_chart(px.scatter(df, x='safe_followers', y='velocity', size='views', color='ratio', log_x=True, title="Rađa Viral"), use_container_width=True)
        except Exception as e:
            st.error(f"Lỗi vẽ biểu đồ: {e}")
else:
    st.info("Chưa có dữ liệu.")

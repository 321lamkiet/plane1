import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from apify_client import ApifyClient
from datetime import datetime
import time
import contextlib
import pytz
from google import genai
from google.genai import types
import os
import tempfile
import urllib.parse

# ==========================================
# CẤU HÌNH (v15.1 Final Ultimate)
# ==========================================
st.set_page_config(page_title="TikTok OS v15.1", page_icon="🛡️", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .analysis-box { background-color: #1F2937; padding: 20px; border-radius: 10px; border: 1px solid #374151; margin-top: 10px; }
</style>
""", unsafe_allow_html=True)

# DATABASE ENGINE
class DatabaseEngine:
    def __init__(self, db_name="tiktok_final_safe.db"): 
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
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY, author_name TEXT, author_followers INTEGER, description TEXT, video_url TEXT, thumbnail_url TEXT, music_title TEXT, music_author TEXT, is_saved INTEGER DEFAULT 0, created_at INTEGER, last_scraped_at TIMESTAMP, current_views INTEGER, current_shares INTEGER, velocity_value REAL, velocity_type TEXT)""")
            conn.commit()
    def upsert_video(self, data, vel, v_type):
        vid_id = data.get('id'); author = data.get('authorMeta', {}).get('name', 'Unknown')
        if not vid_id: return
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", 
                         (vid_id, author, data.get('authorMeta', {}).get('fans', 0), data.get('text', ''), data.get('webVideoUrl', ''), data.get('videoMeta', {}).get('coverUrl', ''), data.get('musicMeta', {}).get('musicName', ''), data.get('musicMeta', {}).get('musicAuthor', ''), 0, int(time.time()), datetime.now(), data.get('playCount', 0), data.get('shareCount', 0), vel, v_type))
            conn.commit()
    def fetch(self, saved=False):
        with self.get_connection() as conn: return pd.read_sql(f"SELECT * FROM videos {'WHERE is_saved=1' if saved else ''} ORDER BY velocity_value DESC", conn)

db = DatabaseEngine()

# LOGIC QUÉT DỮ LIỆU
def run_scan(token, tags, limit, country, separate_search):
    if not token: return False, "Thiếu Token!"
    client = ApifyClient(token)
    tag_list = [t.strip() for t in tags.split(',') if t.strip()]
    
    try:
        if separate_search:
            for tag in tag_list:
                run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": [tag], "resultsPerPage": limit, "proxyConfiguration": {"useApifyProxy": True, "apifyProxyCountry": country if country != "ALL" else "US"}})
                items = client.dataset(run['defaultDatasetId']).list_items().items
                for item in items: db.upsert_video(item, item.get('playCount', 0)/1000, "⚡")
        else:
            run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tag_list, "resultsPerPage": limit, "proxyConfiguration": {"useApifyProxy": True, "apifyProxyCountry": country if country != "ALL" else "US"}})
            items = client.dataset(run['defaultDatasetId']).list_items().items
            for item in items: db.upsert_video(item, item.get('playCount', 0)/1000, "⚡")
        return True, "Cập nhật dữ liệu thành công."
    except Exception as e: return False, str(e)

# AI CENTER (PHÂN TÍCH VIDEO UPLOAD TRỰC TIẾP)
def analyze_video_file(api_key, video_path):
    client = genai.Client(api_key=api_key)
    try:
        with st.spinner("⏳ Đang tải video lên AI (Vui lòng chờ)..."):
            uploaded_file = client.files.upload(file=video_path)
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(2)
            uploaded_file = client.files.get(name=uploaded_file.name)
        
        prompt = "Phân tích Marketing video TikTok này: 1. Hook (3s đầu), 2. Nỗi đau, 3. Kịch bản, 4. Gợi ý Remake để bán hàng. Trả lời bằng tiếng Việt."
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"), types.Part.from_text(text=prompt)])]
        )
        return response.text
    except Exception as e: return f"Lỗi AI: {str(e)}"

# GIAO DIỆN SIDEBAR
with st.sidebar:
    st.title("🦅 TikTok OS v15.1")
    api_tk = st.text_input("Apify Token", type="password")
    gemini_tk = st.text_input("Gemini API Key", type="password")
    st.divider()
    tags = st.text_area("Hashtags", "shilajit, wellness")
    sep_search = st.checkbox("Quét riêng từng từ khóa", value=True)
    limit = st.slider("Số lượng/từ khóa", 10, 50, 10)
    if st.button("🚀 QUÉT XU HƯỚNG", type="primary"):
        s, m = run_scan(api_tk, tags, limit, "US", sep_search)
        if s: st.success(m); time.sleep(1); st.rerun()
        else: st.error(m)

# TABS
tab1, tab2, tab3 = st.tabs(["🔥 Săn Xu Hướng", "🧠 Trung Tâm AI (Upload)", "📊 Biểu Đồ"])

with tab1:
    df = db.fetch()
    if not df.empty:
        df['followers'] = pd.to_numeric(df['author_followers'], errors='coerce').fillna(0)
        df['views'] = pd.to_numeric(df['current_views'], errors='coerce').fillna(0)
        st.dataframe(df[['author_name', 'current_views', 'description', 'video_url']], width=1200)
        st.info("💡 Copy link Video URL -> Tải về máy -> Qua Tab AI để phân tích sâu.")
    else: st.info("Chưa có dữ liệu.")

with tab2:
    st.header("📂 Phân tích File Video Gốc")
    st.markdown("Tải video về máy rồi upload vào đây. AI sẽ **xem trực tiếp** để bóc tách kịch bản.")
    uploaded_video = st.file_uploader("Chọn video MP4", type=["mp4", "mov"])
    if uploaded_video:
        c1, c2 = st.columns([1, 1.5])
        with c1: st.video(uploaded_video)
        with c2:
            if st.button("🚀 BẮT ĐẦU PHÂN TÍCH", type="primary"):
                if not gemini_tk: st.error("Thiếu Gemini Key!")
                else:
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                        tmp.write(uploaded_video.read()); tmp_path = tmp.name
                    result = analyze_video_file(gemini_tk, tmp_path)
                    st.markdown("### 📝 Kết quả:")
                    st.markdown(f'<div class="analysis-box">{result}</div>', unsafe_allow_html=True)
                    os.remove(tmp_path)

with tab3:
    if not df.empty:
        df['safe_followers'] = df['followers'].apply(lambda x: x if x > 0 else 1)
        df['ratio'] = df['views'] / df['safe_followers']
        st.plotly_chart(px.scatter(df, x='safe_followers', y='velocity_value', size='views', color='ratio', log_x=True, title="Rađa Viral"), use_container_width=True)

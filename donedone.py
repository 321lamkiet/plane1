import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from apify_client import ApifyClient
from datetime import datetime
import time
import contextlib
import pytz
import os
import tempfile
from google import genai
from google.genai import types
import streamlit.components.v1 as components # Thư viện để nhúng trình duyệt

# ==========================================
# CẤU HÌNH (v22.0 Browser Player)
# ==========================================
st.set_page_config(page_title="TikTok OS v22.0", page_icon="🌐", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 10px; }
   div[data-testid="stMetricValue"] { font-size: 20px; color: #34D399; }
   .analysis-box { background-color: #1F2937; padding: 20px; border-radius: 10px; border: 1px solid #374151; margin-top: 10px; }
</style>
""", unsafe_allow_html=True)

# DATABASE
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v22_embed.db"): 
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
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY, 
                author_name TEXT, 
                author_avatar TEXT,
                description TEXT, 
                video_url TEXT, 
                play_count INTEGER,
                digg_count INTEGER,
                share_count INTEGER,
                comment_count INTEGER,
                collect_count INTEGER,
                duration INTEGER,
                music_title TEXT,
                created_at TIMESTAMP
            )""")
            conn.commit()

    def upsert_video(self, item):
        vid = item.get('id')
        if not vid: return
        a_meta = item.get('authorMeta', {})
        v_meta = item.get('videoMeta', {})
        m_meta = item.get('musicMeta', {})
        
        with self.get_connection() as conn:
            conn.execute("""INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", 
                         (
                             vid,
                             a_meta.get('name', 'Unknown'),
                             a_meta.get('avatar', ''),
                             item.get('text', ''),
                             item.get('webVideoUrl', ''),
                             item.get('playCount', 0),
                             item.get('diggCount', 0),
                             item.get('shareCount', 0),
                             item.get('commentCount', 0),
                             item.get('collectCount', 0),
                             v_meta.get('duration', 0),
                             m_meta.get('musicName', 'Original Sound'),
                             datetime.now()
                         ))
            conn.commit()
    def fetch(self):
        with self.get_connection() as conn: return pd.read_sql("SELECT * FROM videos ORDER BY play_count DESC", conn)

db = DatabaseEngine()

# LOGIC QUÉT APIFY
def run_scan(token, tags, limit):
    if not token: return False, "Thiếu Token Apify!"
    client = ApifyClient(token)
    tag_list = [t.strip() for t in tags.split(',') if t.strip()]
    
    try:
        run_input = {
            "hashtags": tag_list,
            "resultsPerPage": limit,
            "searchSection": "",
            "proxyConfiguration": {"useApifyProxy": True}
        }
        run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
        if not run: return False, "Lỗi Actor."
        items = client.dataset(run['defaultDatasetId']).list_items().items
        if not items: return False, "Không có video."
        count = 0
        for item in items:
            db.upsert_video(item); count += 1
        return True, f"Đã lấy {count} video!"
    except Exception as e: return False, f"Lỗi: {str(e)}"

# LOGIC AI
def analyze_video_file(api_key, video_path):
    if not api_key: return "⚠️ Thiếu Gemini API Key!"
    time.sleep(2)
    try:
        client = genai.Client(api_key=api_key)
        with st.spinner("⏳ Đang tải video lên AI..."):
            uploaded_file = client.files.upload(file=video_path)
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(1); uploaded_file = client.files.get(name=uploaded_file.name)
        prompt = "Phân tích video TikTok này: Hook, Pain Point, Script, Remake Idea. Tiếng Việt."
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"), types.Part.from_text(text=prompt)])]
        )
        return response.text
    except Exception as e: return f"Lỗi AI: {str(e)}"

# HÀM NHÚNG TRÌNH DUYỆT (EMBED BROWSER STYLE)
def render_tiktok_embed(video_url, video_id):
    # Đây là mã nhúng CHÍNH HÃNG của TikTok
    # Nó sẽ hiển thị player y hệt như trên web, không cần gọi API
    embed_code = f"""
    <blockquote class="tiktok-embed" cite="{video_url}" data-video-id="{video_id}" style="max-width: 100%;min-width: 325px;" >
        <section> <a target="_blank" title="{video_id}" href="{video_url}">Checking TikTok...</a> </section>
    </blockquote>
    <script async src="https://www.tiktok.com/embed.js"></script>
    """
    # Chiều cao 750px để hiển thị trọn vẹn video dọc
    components.html(embed_code, height=750, scrolling=True)

# GIAO DIỆN
with st.sidebar:
    st.header("🔑 Cấu hình")
    apify_tk = st.text_input("Apify Token", type="password")
    gemini_tk = st.text_input("Gemini API Key", type="password")
    st.divider()
    tags = st.text_input("Hashtags", "shilajit, amazonfinds")
    limit = st.slider("Số lượng", 5, 50, 10)
    if st.button("🚀 QUÉT NGAY", type="primary"):
        with st.status("Đang quét..."):
            s, m = run_scan(apify_tk, tags, limit)
            if s: st.success(m); time.sleep(1); st.rerun()
            else: st.error(m)

# TABS
tab1, tab2 = st.tabs(["🔥 Danh sách Video (Giao diện Web)", "🧠 AI Upload"])

with tab1:
    df = db.fetch()
    if not df.empty:
        st.success(f"Đang hiển thị {len(df)} video. Bấm mở từng video để xem trực tiếp như trên trình duyệt.")
        
        for index, row in df.iterrows():
            engagement = 0
            if row['play_count'] > 0:
                engagement = ((row['digg_count'] + row['collect_count']) / row['play_count']) * 100
            
            label = f"🎥 {row['author_name']} | 👀 {row['play_count']:,} | ❤️ {row['digg_count']:,} | ⭐ {row['collect_count']:,}"
            
            with st.expander(label):
                c1, c2 = st.columns([1.5, 2])
                
                with c1:
                    # [QUAN TRỌNG] GỌI HÀM NHÚNG TRÌNH DUYỆT
                    # Không dùng st.video() nữa, dùng render_tiktok_embed
                    render_tiktok_embed(row['video_url'], row['video_id'])
                    
                with c2:
                    st.markdown("#### 📊 Chỉ số chi tiết")
                    m1, m2 = st.columns(2)
                    m1.metric("Lượt Lưu", f"{row['collect_count']:,}")
                    m2.metric("Chia sẻ", f"{row['share_count']:,}")
                    m3, m4 = st.columns(2)
                    m3.metric("Bình luận", f"{row['comment_count']:,}")
                    m4.metric("Tương tác", f"{engagement:.2f}%")
                    
                    st.divider()
                    if row['author_avatar']: st.image(row['author_avatar'], width=50)
                    st.markdown(f"**{row['author_name']}**")
                    st.info(f"🎵 {row['music_title']}")
                    st.write(f"📝 {row['description']}")
    else:
        st.info("Chưa có dữ liệu. Nhập Token và bấm Quét.")

with tab2:
    st.markdown("### 📂 Upload Video để AI phân tích")
    up_file = st.file_uploader("Chọn file MP4", type=["mp4"])
    if up_file:
        if st.button("🔍 Phân tích ngay"):
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                tmp.write(up_file.read()); tmp_path = tmp.name
            res = analyze_video_file(gemini_tk, tmp_path)
            st.markdown(f'<div class="analysis-box">{res}</div>', unsafe_allow_html=True)
            os.remove(tmp_path)

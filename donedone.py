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
import streamlit.components.v1 as components

# ==========================================
# CẤU HÌNH (v23.0 Ultimate Restore)
# ==========================================
st.set_page_config(page_title="TikTok OS v23.0", page_icon="💎", layout="wide")

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
    def __init__(self, db_name="tiktok_v23_restore.db"): 
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

# LOGIC AI FILE UPLOAD
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

# HÀM NHÚNG TRÌNH DUYỆT
def render_tiktok_embed(video_url, video_id):
    embed_code = f"""
    <blockquote class="tiktok-embed" cite="{video_url}" data-video-id="{video_id}" style="max-width: 100%;min-width: 325px;" >
        <section> <a target="_blank" title="{video_id}" href="{video_url}">Loading TikTok...</a> </section>
    </blockquote>
    <script async src="https://www.tiktok.com/embed.js"></script>
    """
    components.html(embed_code, height=750, scrolling=True)

# --- SIDEBAR (KHÔI PHỤC CÁC CÔNG CỤ CỦA BẠN) ---
with st.sidebar:
    st.header("🔑 Cấu hình")
    apify_tk = st.text_input("Apify Token", type="password")
    gemini_tk = st.text_input("Gemini API Key", type="password")
    
    st.divider()
    
    # KHÔI PHỤC BỘ LỌC RÁC
    st.header("🧹 Bộ Lọc Rác")
    min_views = st.number_input("Tối thiểu View:", value=1000, step=1000)
    min_diggs = st.number_input("Tối thiểu Tim:", value=50, step=50)
    
    st.divider()
    
    # KHÔI PHỤC RAĐA AI
    st.header("🤖 Rađa Video AI")
    ai_mode = st.radio("Chế độ lọc:", ["🌐 Tất cả", "🎯 Chỉ lấy Video AI", "🚫 Chặn Video AI"])
    
    st.divider()
    tags = st.text_input("Hashtags", "shilajit, amazonfinds")
    limit = st.slider("Số lượng quét", 5, 100, 10)
    
    if st.button("🚀 QUÉT NGAY", type="primary"):
        with st.status("Đang quét..."):
            s, m = run_scan(apify_tk, tags, limit)
            if s: st.success(m); time.sleep(1); st.rerun()
            else: st.error(m)

# MAIN TABS
tab1, tab2, tab3 = st.tabs(["🔥 Danh sách Video (Web Player)", "🧠 AI Upload", "📊 Biểu đồ"])

AI_KEYWORDS = ['#ai', 'ai art', 'generated', 'midjourney', 'chatgpt', 'openai', 'artificial']

with tab1:
    df = db.fetch()
    if not df.empty:
        # --- ÁP DỤNG BỘ LỌC ---
        df_filtered = df[(df['play_count'] >= min_views) & (df['digg_count'] >= min_diggs)]
        
        if ai_mode == "🎯 Chỉ lấy Video AI":
            df_filtered = df_filtered[df_filtered['description'].str.lower().str.contains('|'.join(AI_KEYWORDS), na=False)]
        elif ai_mode == "🚫 Chặn Video AI":
            df_filtered = df_filtered[~df_filtered['description'].str.lower().str.contains('|'.join(AI_KEYWORDS), na=False)]
            
        st.success(f"Hiển thị {len(df_filtered)} / {len(df)} video phù hợp.")

        for index, row in df_filtered.iterrows():
            engagement = 0
            if row['play_count'] > 0:
                engagement = ((row['digg_count'] + row['collect_count']) / row['play_count']) * 100
            
            # Label có icon AI nếu phát hiện
            is_ai = "🤖 AI" if any(k in str(row['description']).lower() for k in AI_KEYWORDS) else ""
            label = f"{is_ai} 🎥 {row['author_name']} | 👀 {row['play_count']:,} | ❤️ {row['digg_count']:,} | ⭐ {row['collect_count']:,}"
            
            with st.expander(label):
                c1, c2 = st.columns([1.5, 2])
                with c1:
                    # TRÌNH PHÁT WEB
                    render_tiktok_embed(row['video_url'], row['video_id'])
                with c2:
                    # DỮ LIỆU CHI TIẾT (FULL)
                    st.markdown("#### 📊 Chỉ số chi tiết")
                    m1, m2 = st.columns(2)
                    m1.metric("Lượt Lưu (Collect)", f"{row['collect_count']:,}")
                    m2.metric("Chia sẻ (Share)", f"{row['share_count']:,}")
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
    st.markdown("### 📂 Upload Video AI")
    up_file = st.file_uploader("Chọn file MP4", type=["mp4"])
    if up_file:
        if st.button("🔍 Phân tích ngay"):
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                tmp.write(up_file.read()); tmp_path = tmp.name
            res = analyze_video_file(gemini_tk, tmp_path)
            st.markdown(f'<div class="analysis-box">{res}</div>', unsafe_allow_html=True)
            os.remove(tmp_path)

with tab3:
    if not df.empty:
        fig = px.scatter(df, x='play_count', y='digg_count', size='collect_count', hover_name='author_name', log_x=True, title="Biểu đồ Viral")
        st.plotly_chart(fig, use_container_width=True)

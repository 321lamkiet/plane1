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

# ==========================================
# CẤU HÌNH (v18.0 Restore & Fix)
# ==========================================
st.set_page_config(page_title="TikTok OS v18.0", page_icon="💎", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .analysis-box { background-color: #1F2937; padding: 20px; border-radius: 10px; border: 1px solid #374151; margin-top: 10px; }
</style>
""", unsafe_allow_html=True)

# DATABASE ENGINE (KHÔI PHỤC ĐẦY ĐỦ CỘT)
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v18_fixed.db"): 
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
        # Map đúng trường dữ liệu từ ảnh Apify bạn gửi
        vid = item.get('id')
        if not vid: return

        # Trích xuất dữ liệu an toàn
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

# LOGIC QUÉT APIFY (KHÔI PHỤC)
def run_scan(token, tags, limit):
    if not token: return False, "Thiếu Token Apify!"
    client = ApifyClient(token)
    tag_list = [t.strip() for t in tags.split(',') if t.strip()]
    
    try:
        # Gọi đúng Actor TikTok Scraper
        run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tag_list, "resultsPerPage": limit})
        
        if not run: return False, "Lỗi khi gọi Actor Apify."
        
        items = client.dataset(run['defaultDatasetId']).list_items().items
        if not items: return False, "Không tìm thấy video nào."
        
        count = 0
        for item in items:
            db.upsert_video(item)
            count += 1
        return True, f"Đã cập nhật {count} video thành công!"
    except Exception as e: return False, f"Lỗi Apify: {str(e)}"

# LOGIC AI (PHÂN TÍCH FILE UPLOAD)
def analyze_video_file(api_key, video_path):
    if not api_key: return "⚠️ Thiếu Gemini API Key!"
    time.sleep(2) # Chống spam
    try:
        client = genai.Client(api_key=api_key)
        with st.spinner("⏳ Đang tải video lên AI..."):
            uploaded_file = client.files.upload(file=video_path)
        
        # Chờ xử lý
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(1)
            uploaded_file = client.files.get(name=uploaded_file.name)
            
        prompt = """Phân tích video TikTok này để làm Affiliate:
        1. 🎣 Hook: 3 giây đầu giữ chân người xem bằng cách nào?
        2. 😫 Pain Point: Video đánh vào nỗi đau gì của khách hàng?
        3. 🗣️ Script: Trích dẫn câu nói hay nhất.
        4. 💡 Remake: Gợi ý kịch bản quay lại tương tự.
        Trả lời tiếng Việt."""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[
                types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"),
                types.Part.from_text(text=prompt)
            ])]
        )
        return response.text
    except Exception as e: return f"Lỗi AI: {str(e)}"

# GIAO DIỆN
with st.sidebar:
    st.header("🔑 Cấu hình")
    apify_tk = st.text_input("Apify Token", type="password")
    gemini_tk = st.text_input("Gemini API Key", type="password")
    st.divider()
    tags = st.text_input("Hashtags", "shilajit, amazonfinds")
    limit = st.slider("Số lượng", 5, 50, 10)
    
    if st.button("🚀 QUÉT DỮ LIỆU", type="primary"):
        with st.status("Đang kết nối Apify..."):
            s, m = run_scan(apify_tk, tags, limit)
            if s: st.success(m); time.sleep(1); st.rerun()
            else: st.error(m)

# MAIN TABS
tab1, tab2, tab3 = st.tabs(["🔥 Bảng Dữ Liệu (Full)", "🧠 AI Phân Tích (Upload)", "📊 Biểu Đồ"])

with tab1:
    df = db.fetch()
    if not df.empty:
        # Hiển thị bảng dữ liệu với đầy đủ cột bạn yêu cầu
        st.dataframe(
            df,
            column_order=("author_avatar", "author_name", "play_count", "digg_count", "comment_count", "collect_count", "share_count", "duration", "music_title", "video_url"),
            column_config={
                "author_avatar": st.column_config.ImageColumn("Avatar"),
                "author_name": "Kênh",
                "play_count": st.column_config.NumberColumn("Views", format="%d"),
                "digg_count": st.column_config.NumberColumn("❤️ Tim", format="%d"),
                "comment_count": st.column_config.NumberColumn("💬 Chat", format="%d"),
                "collect_count": st.column_config.NumberColumn("⭐ Lưu", format="%d"),
                "share_count": st.column_config.NumberColumn("↗️ Share", format="%d"),
                "duration": st.column_config.NumberColumn("Giây", format="%d s"),
                "music_title": "🎵 Nhạc",
                "video_url": st.column_config.LinkColumn("Link Gốc")
            },
            height=600,
            use_container_width=True
        )
    else: st.info("Chưa có dữ liệu. Vui lòng nhập Token và bấm Quét.")

with tab2:
    st.markdown("### 📂 Upload Video để AI phân tích")
    up_file = st.file_uploader("Chọn file MP4", type=["mp4"])
    if up_file:
        c1, c2 = st.columns([1, 1.5])
        with c1: st.video(up_file)
        with c2:
            if st.button("🔍 Phân tích ngay"):
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                    tmp.write(up_file.read()); tmp_path = tmp.name
                res = analyze_video_file(gemini_tk, tmp_path)
                st.markdown(f'<div class="analysis-box">{res}</div>', unsafe_allow_html=True)
                os.remove(tmp_path)

with tab3:
    if not df.empty:
        fig = px.scatter(df, x='play_count', y='digg_count', size='collect_count', hover_name='author_name', log_x=True, title="Tương quan View vs Tim (Size = Lượt Lưu)")
        st.plotly_chart(fig, use_container_width=True)

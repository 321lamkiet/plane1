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

# ==========================================
# CẤU HÌNH HỆ THỐNG (v17.0 Data Pro)
# ==========================================
st.set_page_config(page_title="TikTok OS v17.0", page_icon="📊", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .analysis-box { background-color: #1F2937; padding: 20px; border-radius: 10px; border: 1px solid #374151; line-height: 1.6; }
</style>
""", unsafe_allow_html=True)

# --- DATABASE ENGINE (NÂNG CẤP CỘT) ---
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v17_pro.db"): 
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
            # Tạo bảng với đầy đủ cột từ Apify
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY, 
                author_name TEXT, 
                author_avatar TEXT,
                play_count INTEGER,
                digg_count INTEGER,
                comment_count INTEGER,
                share_count INTEGER,
                collect_count INTEGER,
                duration INTEGER,
                music_title TEXT,
                description TEXT, 
                video_url TEXT, 
                velocity REAL, 
                created_at TIMESTAMP
            )""")
            conn.commit()
            
    def upsert_video(self, item, vel):
        # Map dữ liệu từ JSON Apify vào Database
        v_id = item.get('id')
        if not v_id: return

        # Lấy thông tin an toàn (tránh lỗi nếu thiếu field)
        a_meta = item.get('authorMeta', {})
        v_meta = item.get('videoMeta', {})
        m_meta = item.get('musicMeta', {})
        
        with self.get_connection() as conn:
            conn.execute("""INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", 
                         (
                             v_id,
                             a_meta.get('name', 'Unknown'),
                             a_meta.get('avatar', ''), # Avatar
                             item.get('playCount', 0),
                             item.get('diggCount', 0),    # Tim
                             item.get('commentCount', 0), # Comment
                             item.get('shareCount', 0),   # Share
                             item.get('collectCount', 0), # Lưu (Bookmark)
                             v_meta.get('duration', 0),   # Thời lượng
                             f"{m_meta.get('musicName','')} - {m_meta.get('musicAuthor','')}", # Nhạc
                             item.get('text', ''),
                             item.get('webVideoUrl', ''),
                             vel,
                             datetime.now()
                         ))
            conn.commit()
            
    def fetch(self):
        with self.get_connection() as conn: return pd.read_sql("SELECT * FROM videos ORDER BY velocity DESC", conn)

db = DatabaseEngine()

# --- AI LOGIC (ANTI-429) ---
def analyze_video_ai(api_key, video_path):
    if not api_key: return "⚠️ Vui lòng nhập Gemini API Key!"
    time.sleep(2) 
    try:
        client = genai.Client(api_key=api_key)
        with st.spinner("🤖 AI đang xem video..."):
            uploaded_file = client.files.upload(file=video_path)
        
        # Wait for processing
        for i in range(10):
            time.sleep(1)
            if client.files.get(name=uploaded_file.name).state.name == "ACTIVE": break

        prompt = """Bạn là chuyên gia TikTok. Hãy phân tích video này:
        1. 🎣 Hook: 3 giây đầu họ làm gì?
        2. 📊 Tại sao video này nhiều tương tác (Tim/Lưu)?
        3. 🗣️ Script: Trích dẫn câu nói hay nhất.
        4. 💡 Remake: Gợi ý quay lại để bán hàng.
        Trả lời tiếng Việt."""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[
                types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"),
                types.Part.from_text(text=prompt)
            ])]
        )
        return response.text
    except Exception as e:
        if "429" in str(e): return "❌ Lỗi: Quá hạn mức gói Free. Đợi 1 phút nhé."
        return f"❌ Lỗi AI: {str(e)}"

# --- SIDEBAR ---
with st.sidebar:
    st.header("🔑 Cấu hình")
    apify_token = st.text_input("Apify Token", type="password")
    gemini_key = st.text_input("Gemini API Key", type="password")
    st.divider()
    tags = st.text_input("Hashtags", "shilajit, amazonfinds")
    limit = st.slider("Số lượng", 5, 50, 10)
    
    if st.button("🚀 QUÉT CHI TIẾT", type="primary"):
        if not apify_token: st.error("Thiếu Token Apify!")
        else:
            client = ApifyClient(apify_token)
            with st.status("Đang lấy dữ liệu chi tiết..."):
                tag_list = [t.strip() for t in tags.split(',')]
                run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tag_list, "resultsPerPage": limit})
                items = client.dataset(run['defaultDatasetId']).list_items().items
                for item in items:
                    # Tính điểm Viral đơn giản: (Tim + Lưu) / 1000
                    viral_score = (item.get('diggCount', 0) + item.get('collectCount', 0)) / 1000
                    db.upsert_video(item, viral_score)
            st.success(f"Đã cập nhật {len(items)} video full chỉ số!")
            st.rerun()

# --- MAIN UI ---
st.title("🦅 TikTok Intelligence OS v17.0 (Pro Data)")

tab1, tab2, tab3 = st.tabs(["🔥 Bảng Dữ Liệu", "🧠 AI Phân Tích", "📊 Biểu Đồ"])

with tab1:
    df = db.fetch()
    if not df.empty:
        # Tính tỷ lệ tương tác
        df['Engagement'] = ((df['digg_count'] + df['comment_count'] + df['collect_count']) / df['play_count'] * 100).round(2)
        
        # Hiển thị bảng dữ liệu đẹp mắt
        st.dataframe(
            df,
            column_order=("author_avatar", "author_name", "play_count", "digg_count", "collect_count", "Engagement", "duration", "music_title", "video_url"),
            column_config={
                "author_avatar": st.column_config.ImageColumn("Avatar"),
                "author_name": "Kênh",
                "play_count": st.column_config.NumberColumn("Views", format="%d"),
                "digg_count": st.column_config.NumberColumn("❤️ Tim", format="%d"),
                "collect_count": st.column_config.NumberColumn("⭐ Lưu", format="%d"),
                "Engagement": st.column_config.ProgressColumn("Tương tác %", min_value=0, max_value=10, format="%.2f%%"),
                "duration": st.column_config.NumberColumn("Giây", format="%d s"),
                "music_title": "🎵 Nhạc nền",
                "video_url": st.column_config.LinkColumn("Link Video")
            },
            height=600,
            use_container_width=True
        )
    else:
        st.info("Chưa có dữ liệu. Hãy nhập Token và bấm Quét.")

with tab2:
    st.markdown("### 📂 Upload Video để AI 'soi' chi tiết")
    up_file = st.file_uploader("Chọn file video (.mp4)", type=["mp4"])
    if up_file:
        c1, c2 = st.columns([1, 1.5])
        with c1: st.video(up_file)
        with c2: 
            if st.button("🔍 Phân tích ngay"):
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                    tmp.write(up_file.read()); tmp_path = tmp.name
                res = analyze_video_ai(gemini_key, tmp_path)
                st.markdown(f'<div class="analysis-box">{res}</div>', unsafe_allow_html=True)
                os.remove(tmp_path)

with tab3:
    if not df.empty:
        fig = px.scatter(df, x='play_count', y='collect_count', size='digg_count', color='Engagement', hover_name='author_name', log_x=True, title="Biểu đồ: View vs Lượt Lưu (Bubble size = Lượt Tim)")
        st.plotly_chart(fig, use_container_width=True)

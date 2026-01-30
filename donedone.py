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
# CẤU HÌNH (v20.0 Clean & AI Hunter)
# ==========================================
st.set_page_config(page_title="TikTok OS v20.0", page_icon="🤖", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .analysis-box { background-color: #1F2937; padding: 20px; border-radius: 10px; border: 1px solid #374151; margin-top: 10px; }
   img { border-radius: 50%; }
</style>
""", unsafe_allow_html=True)

# DATABASE ENGINE
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v20_hunter.db"): 
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
        run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tag_list, "resultsPerPage": limit})
        if not run: return False, "Lỗi gọi Actor."
        items = client.dataset(run['defaultDatasetId']).list_items().items
        if not items: return False, "Không có video."
        
        count = 0
        for item in items:
            db.upsert_video(item); count += 1
        return True, f"Đã lấy về {count} video!"
    except Exception as e: return False, f"Lỗi: {str(e)}"

# LOGIC AI PHÂN TÍCH
def analyze_video_file(api_key, video_path):
    if not api_key: return "⚠️ Thiếu Gemini API Key!"
    time.sleep(2)
    try:
        client = genai.Client(api_key=api_key)
        with st.spinner("⏳ Đang tải video lên AI..."):
            uploaded_file = client.files.upload(file=video_path)
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(1); uploaded_file = client.files.get(name=uploaded_file.name)
            
        prompt = "Phân tích video này: 1. Hook (3s đầu), 2. Nỗi đau, 3. Script, 4. Gợi ý Remake. Trả lời tiếng Việt."
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"), types.Part.from_text(text=prompt)])]
        )
        return response.text
    except Exception as e: return f"Lỗi AI: {str(e)}"

# GIAO DIỆN
with st.sidebar:
    st.header("🔑 Cấu hình")
    apify_tk = st.text_input("Apify Token", type="password")
    gemini_tk = st.text_input("Gemini API Key", type="password")
    
    st.divider()
    
    st.header("🧹 Bộ Lọc Rác")
    min_views = st.number_input("Tối thiểu View:", value=1000, step=1000, help="Ẩn video dưới mức này")
    min_diggs = st.number_input("Tối thiểu Tim:", value=50, step=50, help="Ẩn video ít like")
    
    st.divider()
    
    st.header("🤖 Rađa Video AI")
    ai_mode = st.radio("Chế độ lọc AI:", 
                       ["🌐 Tất cả (Mặc định)", "🎯 Chỉ lấy Video AI", "🚫 Chặn Video AI"],
                       help="Tìm video có chứa hashtag #ai, #midjourney, #chatgpt...")
    
    st.divider()
    
    tags = st.text_input("Hashtags", "shilajit, amazonfinds")
    limit = st.slider("Số lượng quét", 10, 100, 20)
    
    if st.button("🚀 QUÉT MỚI", type="primary"):
        with st.status("Đang quét dữ liệu..."):
            s, m = run_scan(apify_tk, tags, limit)
            if s: st.success(m); time.sleep(1); st.rerun()
            else: st.error(m)

# MAIN TABS
tab1, tab2, tab3 = st.tabs(["🔥 Danh sách Video (Đã Lọc)", "🧠 AI Upload", "📊 Biểu Đồ"])

# DANH SÁCH TỪ KHÓA AI
AI_KEYWORDS = ['#ai', 'ai art', 'generated', 'midjourney', 'chatgpt', 'openai', 'artificial', 'nhân tạo', 'robot', 'virtual', 'sora', 'heygen']

with tab1:
    df = db.fetch()
    if not df.empty:
        # --- BỘ LỌC RÁC ---
        # 1. Lọc theo chỉ số
        df_filtered = df[
            (df['play_count'] >= min_views) & 
            (df['digg_count'] >= min_diggs)
        ]
        
        # 2. Lọc theo AI
        if ai_mode == "🎯 Chỉ lấy Video AI":
            # Lọc những video có chứa từ khóa AI trong description
            df_filtered = df_filtered[df_filtered['description'].str.lower().str.contains('|'.join(AI_KEYWORDS), na=False)]
        elif ai_mode == "🚫 Chặn Video AI":
            # Lọc bỏ những video chứa từ khóa AI
            df_filtered = df_filtered[~df_filtered['description'].str.lower().str.contains('|'.join(AI_KEYWORDS), na=False)]

        # HIỂN THỊ KẾT QUẢ
        st.success(f"🔍 Tìm thấy {len(df_filtered)} / {len(df)} video phù hợp tiêu chí.")
        
        if len(df_filtered) == 0:
            st.warning("Không có video nào thỏa mãn bộ lọc. Hãy giảm tiêu chí 'Tối thiểu View' hoặc đổi Hashtag.")
        
        for index, row in df_filtered.iterrows():
            engagement = 0
            if row['play_count'] > 0:
                engagement = ((row['digg_count'] + row['collect_count']) / row['play_count']) * 100
            
            # Kiểm tra xem có phải AI không để gắn nhãn
            is_ai_label = "🤖 AI" if any(x in str(row['description']).lower() for x in AI_KEYWORDS) else ""
            
            label = f"{is_ai_label} 🎥 {row['author_name']} | 👀 {row['play_count']:,} | ❤️ {row['digg_count']:,} | ⭐ {row['collect_count']:,}"
            
            with st.expander(label):
                c1, c2 = st.columns([1.2, 2])
                with c1:
                    try: st.video(row['video_url'])
                    except: st.warning("Video chặn nhúng.")
                    st.markdown(f"[🔗 Link TikTok Gốc]({row['video_url']})")
                with c2:
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Chia sẻ", f"{row['share_count']:,}")
                    m2.metric("Bình luận", f"{row['comment_count']:,}")
                    m3.metric("Tương tác", f"{engagement:.2f}%")
                    st.markdown(f"**📝 Caption:** {row['description']}")
                    st.caption(f"Nhạc: {row['music_title']} | {row['duration']}s")
    else:
        st.info("Chưa có dữ liệu. Hãy nhập Token Apify và bấm QUÉT.")

with tab2:
    st.markdown("### 📂 Upload Video (Phân tích sâu hơn)")
    up_file = st.file_uploader("Chọn file MP4", type=["mp4"])
    if up_file:
        c1, c2 = st.columns([1, 1.5])
        with c1: st.video(up_file)
        with c2: 
            if st.button("🔍 Phân tích AI"):
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                    tmp.write(up_file.read()); tmp_path = tmp.name
                res = analyze_video_file(gemini_tk, tmp_path)
                st.markdown(f'<div class="analysis-box">{res}</div>', unsafe_allow_html=True)
                os.remove(tmp_path)

with tab3:
    if not df.empty:
        # Vẽ biểu đồ trên tập dữ liệu gốc để có cái nhìn tổng quan
        fig = px.scatter(df, x='play_count', y='digg_count', size='collect_count', hover_name='author_name', log_x=True, title="Biểu đồ Viral (Bubble = Lượt Lưu)")
        st.plotly_chart(fig, use_container_width=True)

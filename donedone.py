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
import requests
from google import genai
from google.genai import types
import streamlit.components.v1 as components

# ==========================================
# CẤU HÌNH (v27.0 Auto-Refresh Fix)
# ==========================================
st.set_page_config(page_title="TikTok OS v27.0", page_icon="⚡", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 10px; }
   div[data-testid="stMetricValue"] { font-size: 20px; color: #34D399; }
   .analysis-box { background-color: #1F2937; padding: 20px; border-radius: 10px; border: 1px solid #374151; margin-top: 10px; }
   .download-btn { text-decoration: none; display: inline-block; color: white; background-color: #2563EB; padding: 6px 12px; border-radius: 4px; font-size: 14px; }
</style>
""", unsafe_allow_html=True)

# DATABASE
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v27_fix.db"): 
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
                video_id TEXT PRIMARY KEY, author_name TEXT, author_avatar TEXT, description TEXT, 
                video_url TEXT, download_url TEXT, play_count INTEGER, digg_count INTEGER, 
                share_count INTEGER, comment_count INTEGER, collect_count INTEGER, duration INTEGER, 
                music_title TEXT, posted_at DATETIME, velocity REAL, created_at TIMESTAMP
            )""")
            conn.commit()

    def upsert_video(self, item):
        vid = item.get('id')
        if not vid: return
        a_meta = item.get('authorMeta', {})
        v_meta = item.get('videoMeta', {})
        m_meta = item.get('musicMeta', {})
        
        try:
            create_time_str = item.get('createTimeISO', '')
            if create_time_str: posted_dt = datetime.fromisoformat(create_time_str.replace('Z', '+00:00'))
            else: posted_dt = datetime.now(pytz.utc)
        except: posted_dt = datetime.now(pytz.utc)

        hours_since = (datetime.now(pytz.utc) - posted_dt).total_seconds() / 3600
        velocity = item.get('playCount', 0) / max(1, hours_since)
        dl_url = v_meta.get('downloadAddr', '')
        
        with self.get_connection() as conn:
            conn.execute("""INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", 
                         (vid, a_meta.get('name', 'Unknown'), a_meta.get('avatar', ''), item.get('text', ''), 
                          item.get('webVideoUrl', ''), dl_url, item.get('playCount', 0), item.get('diggCount', 0), 
                          item.get('shareCount', 0), item.get('commentCount', 0), item.get('collectCount', 0), 
                          v_meta.get('duration', 0), m_meta.get('musicName', 'Original Sound'), posted_dt, velocity, datetime.now()))
            conn.commit()
            
    def fetch(self):
        with self.get_connection() as conn: 
            df = pd.read_sql("SELECT * FROM videos", conn)
            if not df.empty: df['posted_at'] = pd.to_datetime(df['posted_at'])
            return df

db = DatabaseEngine()

# LOGIC QUÉT (Đã tối ưu cho Callback)
def run_scan_logic(token, tags, limit):
    if not token: return False, "⚠️ Thiếu Token Apify!"
    client = ApifyClient(token)
    tag_list = [t.strip() for t in tags.split(',') if t.strip()]
    try:
        # Gửi thông báo đang chạy (nhưng ko dùng st.status để tránh lỗi)
        run_input = {"hashtags": tag_list, "resultsPerPage": limit, "searchSection": "", "proxyConfiguration": {"useApifyProxy": True}}
        run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
        if not run: return False, "❌ Lỗi Actor Apify."
        items = client.dataset(run['defaultDatasetId']).list_items().items
        if not items: return False, "⚠️ Không tìm thấy video nào."
        
        count = 0
        for item in items:
            db.upsert_video(item); count += 1
        return True, f"✅ Đã cập nhật thành công {count} video!"
    except Exception as e: return False, f"❌ Lỗi: {str(e)}"

# HÀM CALLBACK (CHÌA KHÓA FIX LỖI CỦA BẠN)
def on_scan_click():
    # Lấy giá trị từ session state (do input chưa kịp submit)
    token = st.session_state.get("api_input", "")
    tags = st.session_state.get("tag_input", "")
    limit = st.session_state.get("limit_input", 10)
    
    with st.spinner("⏳ Đang quét dữ liệu... Vui lòng đợi..."):
        success, msg = run_scan_logic(token, tags, limit)
        # Lưu thông báo vào Session State để hiển thị sau khi reload
        st.session_state['scan_status'] = {'success': success, 'message': msg}

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

# NHÚNG TRÌNH DUYỆT
def render_tiktok_embed(video_url, video_id):
    embed_code = f"""
    <blockquote class="tiktok-embed" cite="{video_url}" data-video-id="{video_id}" style="max-width: 100%;min-width: 325px;" >
        <section> <a target="_blank" title="{video_id}" href="{video_url}">...</a> </section>
    </blockquote>
    <script async src="https://www.tiktok.com/embed.js"></script>
    """
    components.html(embed_code, height=750, scrolling=True)

# SIDEBAR
with st.sidebar:
    st.header("🔑 Cấu hình")
    # Gắn key để dùng trong Callback
    apify_tk = st.text_input("Apify Token", type="password", key="api_input")
    gemini_tk = st.text_input("Gemini API Key", type="password")
    
    st.divider()
    st.subheader("🎯 Bộ Lọc")
    
    time_filter = st.selectbox("📅 Thời gian đăng:", ["Tất cả", "24 Giờ qua", "3 Ngày qua", "7 Ngày qua", "30 Ngày qua"], index=0)
    min_views = st.number_input("Tối thiểu View:", value=1000, step=1000)
    min_engagement = st.slider("Tương tác tối thiểu (%):", 0.0, 20.0, 1.0)
    ai_mode = st.radio("Chế độ AI:", ["🌐 Tất cả", "🎯 Chỉ lấy Video AI", "🚫 Chặn Video AI"])
    
    st.divider()
    tags = st.text_input("Hashtags", "shilajit, amazonfinds", key="tag_input")
    limit = st.slider("Số lượng quét", 10, 100, 20, key="limit_input")
    
    # NÚT BẤM DÙNG CALLBACK (FIX LỖI)
    st.button("🚀 QUÉT MỚI", type="primary", on_click=on_scan_click)
    
    # Hiển thị thông báo sau khi reload
    if 'scan_status' in st.session_state:
        status = st.session_state['scan_status']
        if status['success']: st.success(status['message'])
        else: st.error(status['message'])
        # Xóa thông báo để không hiện mãi
        del st.session_state['scan_status']

# MAIN TABS
tab1, tab2, tab3 = st.tabs(["🔥 Danh sách Video (Trend Hunter)", "🧠 AI Upload", "📊 Biểu đồ"])
AI_KEYWORDS = ['#ai', 'ai art', 'generated', 'midjourney', 'chatgpt', 'openai', 'artificial']

with tab1:
    df = db.fetch()
    if not df.empty:
        df['engagement_rate'] = ((df['digg_count'] + df['collect_count']) / df['play_count'] * 100).fillna(0)
        
        now = datetime.now(pytz.utc)
        if time_filter == "24 Giờ qua": df = df[df['posted_at'] >= (now - pd.Timedelta(hours=24))]
        elif time_filter == "3 Ngày qua": df = df[df['posted_at'] >= (now - pd.Timedelta(days=3))]
        elif time_filter == "7 Ngày qua": df = df[df['posted_at'] >= (now - pd.Timedelta(days=7))]
        elif time_filter == "30 Ngày qua": df = df[df['posted_at'] >= (now - pd.Timedelta(days=30))]

        df = df[(df['play_count'] >= min_views) & (df['engagement_rate'] >= min_engagement)]
        
        if ai_mode == "🎯 Chỉ lấy Video AI":
            df = df[df['description'].str.lower().str.contains('|'.join(AI_KEYWORDS), na=False)]
        elif ai_mode == "🚫 Chặn Video AI":
            df = df[~df['description'].str.lower().str.contains('|'.join(AI_KEYWORDS), na=False)]
            
        sort_opt = st.selectbox("Sắp xếp theo:", ["🔥 Tốc độ Viral (View/Giờ)", "👀 Nhiều View Nhất", "❤️ Nhiều Tim Nhất", "📅 Mới Nhất"])
        if sort_opt == "🔥 Tốc độ Viral (View/Giờ)": df = df.sort_values(by='velocity', ascending=False)
        elif sort_opt == "👀 Nhiều View Nhất": df = df.sort_values(by='play_count', ascending=False)
        elif sort_opt == "❤️ Nhiều Tim Nhất": df = df.sort_values(by='digg_count', ascending=False)
        elif sort_opt == "📅 Mới Nhất": df = df.sort_values(by='posted_at', ascending=False)

        st.success(f"🔍 Tìm thấy {len(df)} video phù hợp.")

        for index, row in df.iterrows():
            is_ai = "🤖 AI" if any(k in str(row['description']).lower() for k in AI_KEYWORDS) else ""
            posted_str = row['posted_at'].strftime("%Y-%m-%d %H:%M")
            velocity_str = f"{row['velocity']:.0f} view/h"
            label = f"{is_ai} 🎥 {row['author_name']} | 🔥 {velocity_str} | 👀 {row['play_count']:,} | 📅 {posted_str}"
            
            with st.expander(label):
                c1, c2 = st.columns([1.5, 2])
                with c1:
                    render_tiktok_embed(row['video_url'], row['video_id'])
                    st.link_button("👉 Mở trên TikTok", row['video_url'], type="primary", use_container_width=True)
                    if row['download_url']:
                         st.markdown(f'<a href="{row["download_url"]}" target="_blank" class="download-btn">📥 Tải Video (Gốc)</a>', unsafe_allow_html=True)
                with c2:
                    st.markdown("#### 📊 Chỉ số Affiliate")
                    m1, m2 = st.columns(2)
                    m1.metric("Lượt Lưu", f"{row['collect_count']:,}")
                    m2.metric("Chia sẻ", f"{row['share_count']:,}")
                    m3, m4 = st.columns(2)
                    m3.metric("Bình luận", f"{row['comment_count']:,}")
                    m4.metric("Tương tác", f"{row['engagement_rate']:.2f}%")
                    st.divider()
                    st.info(f"📅 **Ngày đăng:** {posted_str}")
                    st.info(f"🚀 **Tốc độ:** {velocity_str}")
                    st.write(f"📝 **Caption:** {row['description']}")
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
        fig = px.scatter(df, x='posted_at', y='velocity', size='play_count', color='engagement_rate', hover_name='author_name', title="Biểu đồ: Ngày đăng vs Tốc độ Viral")
        st.plotly_chart(fig, use_container_width=True)

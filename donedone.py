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
# CẤU HÌNH HỆ THỐNG (v16.0 Final Masterpiece)
# ==========================================
st.set_page_config(page_title="TikTok OS v16.0", page_icon="🚀", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .analysis-box { background-color: #1F2937; padding: 20px; border-radius: 10px; border: 1px solid #374151; line-height: 1.6; }
</style>
""", unsafe_allow_html=True)

# --- DATABASE ENGINE ---
class DatabaseEngine:
    def __init__(self, db_name="tiktok_final_v16.db"): 
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
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY, author_name TEXT, author_followers INTEGER, description TEXT, video_url TEXT, views INTEGER, velocity REAL, last_update TIMESTAMP)""")
            conn.commit()
    def upsert_video(self, data, vel):
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?)", 
                         (data.get('id'), data.get('authorMeta', {}).get('name'), data.get('authorMeta', {}).get('fans', 0), data.get('text', ''), data.get('webVideoUrl', ''), data.get('playCount', 0), vel, datetime.now()))
            conn.commit()
    def fetch(self):
        with self.get_connection() as conn: return pd.read_sql("SELECT * FROM videos ORDER BY velocity DESC", conn)

db = DatabaseEngine()

# --- AI LOGIC (ANTI-429 & NEW CORE) ---
def analyze_video_ai(api_key, video_path):
    if not api_key: return "⚠️ Vui lòng nhập Gemini API Key!"
    
    # Ép hệ thống nghỉ 2 giây để tránh lỗi 429 nếu người dùng bấm quá nhanh
    time.sleep(2) 
    
    try:
        client = genai.Client(api_key=api_key)
        
        with st.spinner("🤖 AI đang xem video (Bước 1: Tải lên)..."):
            uploaded_file = client.files.upload(file=video_path)
        
        # Chờ xử lý video
        progress_bar = st.progress(0)
        for i in range(100):
            time.sleep(0.1)
            progress_bar.progress(i + 1)
            file_state = client.files.get(name=uploaded_file.name)
            if file_state.state.name == "ACTIVE": break
        progress_bar.empty()

        prompt = """Bạn là một chuyên gia TikTok Marketing. Hãy phân tích video này:
        1. 🎣 Hook: Phân tích 3 giây đầu giữ chân người xem như thế nào?
        2. 😫 Pain Point: Video đánh vào nỗi đau hay vấn đề gì?
        3. 🗣️ Script: Trích dẫn 3 câu quan trọng nhất trong video.
        4. 💡 Remake: Gợi ý cách quay lại video này để bán hàng hiệu quả nhất.
        Trả lời bằng tiếng Việt, ngắn gọn, súc tích."""

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[types.Content(role="user", parts=[
                types.Part.from_uri(file_uri=uploaded_file.uri, mime_type="video/mp4"),
                types.Part.from_text(text=prompt)
            ])]
        )
        return response.text
    except Exception as e:
        if "429" in str(e):
            return "❌ Lỗi: Bạn đã dùng quá giới hạn gói Miễn phí. Vui lòng đợi 1 phút rồi thử lại."
        return f"❌ Lỗi AI: {str(e)}"

# --- SIDEBAR CONTROL ---
with st.sidebar:
    st.header("🔑 Cấu hình hệ thống")
    apify_token = st.text_input("Apify Token", type="password")
    gemini_key = st.text_input("Gemini API Key", type="password")
    st.divider()
    st.header("🎯 Quét xu hướng")
    tags = st.text_input("Hashtags", "shilajit, wellness")
    limit = st.slider("Số lượng video", 5, 50, 10)
    
    if st.button("🚀 BẮT ĐẦU QUÉT", type="primary"):
        if not apify_token: st.error("Thiếu Token Apify!")
        else:
            client = ApifyClient(apify_token)
            with st.status("Đang quét TikTok..."):
                tag_list = [t.strip() for t in tags.split(',')]
                run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tag_list, "resultsPerPage": limit})
                items = client.dataset(run['defaultDatasetId']).list_items().items
                for item in items:
                    # Tính tốc độ ảo đơn giản: View/1000
                    db.upsert_video(item, item.get('playCount', 0)/1000)
            st.success(f"Đã lưu {len(items)} video!")
            st.rerun()

# --- MAIN INTERFACE ---
st.title("🦅 TikTok Intelligence OS v16.0")

tab1, tab2, tab3 = st.tabs(["🔥 Săn Xu Hướng", "🧠 Phân Tích Video AI", "📊 Chỉ số"])

with tab1:
    df = db.fetch()
    if not df.empty:
        st.dataframe(df[['author_name', 'views', 'velocity', 'video_url']], height=400, width=1200)
        st.info("💡 Mẹo: Tải video về máy, sau đó qua Tab 'Phân Tích AI' để bóc tách kịch bản.")
    else:
        st.info("Chưa có dữ liệu. Hãy quét ở thanh bên trái.")

with tab2:
    st.subheader("📁 Tải video lên để AI phân tích")
    st.markdown("AI sẽ xem file MP4 trực tiếp. Điều này giúp bạn tránh bị TikTok quét địa chỉ IP.")
    
    up_file = st.file_uploader("Chọn file video (.mp4, .mov)", type=["mp4", "mov"])
    
    if up_file:
        col_v, col_a = st.columns([1, 1.5])
        with col_v:
            st.video(up_file)
        with col_a:
            if st.button("🔍 Bắt đầu phân tích kịch bản", type="primary"):
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as tmp:
                    tmp.write(up_file.read())
                    tmp_path = tmp.name
                
                result = analyze_video_ai(gemini_key, tmp_path)
                st.markdown("### ✨ Kết quả phân tích:")
                st.markdown(f'<div class="analysis-box">{result}</div>', unsafe_allow_html=True)
                os.remove(tmp_path)

with tab3:
    if not df.empty:
        df['safe_followers'] = df['author_followers'].apply(lambda x: x if x > 0 else 1)
        fig = px.scatter(df, x='safe_followers', y='velocity', size='views', hover_name='author_name', log_x=True, title="Rađa Viral")
        st.plotly_chart(fig, use_container_width=True)

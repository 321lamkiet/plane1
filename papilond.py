import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from apify_client import ApifyClient
from datetime import datetime
import time
import contextlib
import pytz
from openai import OpenAI # [NEW] Thư viện OpenAI
import streamlit.components.v1 as components
import urllib.parse

# ==========================================
# 1. CẤU HÌNH HỆ THỐNG
# ==========================================
st.set_page_config(
    page_title="TikTok OS v7.1 (OpenAI)",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] {
        background-color: #1F2937; border: 1px solid #374151;
        border-radius: 8px; padding: 15px;
    }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .status-badge { padding: 4px 8px; border-radius: 4px; font-weight: bold; }
   .badge-live { background-color: #065F46; color: #34D399; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. DATABASE ENGINE
# ==========================================
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v71_openai.db"): # Đổi tên DB
        self.db_name = db_name
        self.init_db()

    @contextlib.contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_name, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        try:
            yield conn
        finally:
            conn.close()

    def init_db(self):
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    author_name TEXT,
                    author_followers INTEGER, 
                    description TEXT,
                    video_url TEXT,
                    thumbnail_url TEXT,
                    music_title TEXT,
                    music_author TEXT,
                    is_saved INTEGER DEFAULT 0,
                    created_at INTEGER,
                    last_scraped_at TIMESTAMP,
                    current_views INTEGER,
                    current_shares INTEGER,
                    velocity_value REAL,
                    velocity_type TEXT,
                    ai_analysis TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT,
                    play_count INTEGER,
                    share_count INTEGER,
                    scraped_at TIMESTAMP,
                    FOREIGN KEY(video_id) REFERENCES videos(video_id)
                )
            """)
            conn.commit()

    def get_last_metric(self, video_id):
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT play_count, scraped_at FROM metrics WHERE video_id =? ORDER BY scraped_at DESC LIMIT 1", (video_id,))
            return cursor.fetchone()

    def upsert_video(self, data, velocity, v_type):
        now = datetime.now()
        vid_id = data.get('id')
        if not vid_id: return 
        
        author = data.get('authorMeta', {}).get('name', 'Unknown')
        followers = data.get('authorMeta', {}).get('fans', 0)
        desc = data.get('text', '')
        url = data.get('webVideoUrl', '')
        thumb = data.get('videoMeta', {}).get('coverUrl', '')
        music_title = data.get('musicMeta', {}).get('musicName', 'Unknown')
        music_author = data.get('musicMeta', {}).get('musicAuthor', 'Unknown')
        created_at = data.get('createTime', int(time.time()))
        views = data.get('playCount', 0)
        shares = data.get('shareCount', 0)

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO videos (
                    video_id, author_name, author_followers, description, video_url, thumbnail_url,
                    music_title, music_author,
                    created_at, last_scraped_at, current_views, current_shares,
                    velocity_value, velocity_type
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(video_id) DO UPDATE SET
                    last_scraped_at=excluded.last_scraped_at,
                    current_views=excluded.current_views,
                    current_shares=excluded.current_shares,
                    author_followers=excluded.author_followers,
                    velocity_value=excluded.velocity_value,
                    velocity_type=excluded.velocity_type,
                    music_title=excluded.music_title,
                    music_author=excluded.music_author
            """, (vid_id, author, followers, desc, url, thumb, music_title, music_author, 
                  created_at, now, views, shares, velocity, v_type))
            conn.execute("INSERT INTO metrics (video_id, play_count, share_count, scraped_at) VALUES (?,?,?,?)",
                         (vid_id, views, shares, now))
            conn.commit()

    def update_ai_analysis(self, video_id, analysis_text):
        with self.get_connection() as conn:
            conn.execute("UPDATE videos SET ai_analysis =? WHERE video_id =?", (analysis_text, video_id))
            conn.commit()
            
    def toggle_save_video(self, video_id, current_status):
        new_status = 0 if current_status == 1 else 1
        with self.get_connection() as conn:
            conn.execute("UPDATE videos SET is_saved =? WHERE video_id =?", (new_status, video_id))
            conn.commit()

    def fetch_data(self, only_saved=False):
        with self.get_connection() as conn:
            if only_saved:
                return pd.read_sql("SELECT * FROM videos WHERE is_saved = 1 ORDER BY last_scraped_at DESC", conn)
            else:
                return pd.read_sql("SELECT * FROM videos ORDER BY velocity_value DESC", conn)

db = DatabaseEngine()

# ==========================================
# 3. CORE LOGIC
# ==========================================
def calculate_velocity(video_item):
    video_id = video_item['id']
    current_views = video_item.get('playCount', 0)
    current_time = datetime.now()
    prev = db.get_last_metric(video_id)
    
    if prev:
        prev_views, prev_time_str = prev
        prev_time = pd.to_datetime(prev_time_str)
        hours_diff = (current_time - prev_time).total_seconds() / 3600.0
        if hours_diff > 0.05:
            view_diff = max(0, current_views - prev_views)
            velocity = view_diff / hours_diff
            return round(velocity, 1), "⚡ Thực"
    else:
        created_ts = video_item.get('createTime', 0)
        if created_ts:
            age_hours = max(1.0, (current_time - datetime.fromtimestamp(created_ts)).total_seconds() / 3600.0)
            velocity = current_views / age_hours
            return round(velocity, 1), "🆕 Dự báo"
    return 0.0, "⏳ Chờ"

def run_apify_scan(token, hashtags_str, limit, country_code, filter_mode, ai_keywords, custom_proxy=None):
    if not token: return False, "Thiếu Apify Token!"
    client = ApifyClient(token)
    
    proxy_config = {"useApifyProxy": True}
    if custom_proxy and custom_proxy.strip():
        proxy_config = {"useApifyProxy": False, "proxyUrls": [custom_proxy.strip()]}
    elif country_code != "ALL":
        proxy_config = {"useApifyProxy": True, "apifyProxyCountry": country_code}

    hashtags_list = [h.strip() for h in hashtags_str.split(",") if h.strip()]
    if not hashtags_list: return False, "Nhập ít nhất 1 hashtag!"

    run_input = {
        "hashtags": hashtags_list,
        "resultsPerPage": limit,
        "proxyConfiguration": proxy_config,
        "searchSection": "", 
        "shouldDownloadCovers": False
    }
    
    try:
        run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
        if not run or 'defaultDatasetId' not in run:
             return False, "Lỗi Apify: Không trả về dữ liệu."

        dataset = client.dataset(run['defaultDatasetId']).list_items().items
        if not dataset: return False, "Không tìm thấy video nào."
        
        keywords = [k.strip().lower() for k in ai_keywords.split(",")] if ai_keywords else []
        valid_count = 0
        
        for item in dataset:
            desc = item.get('text', '').lower()
            is_ai_content = any(k in desc for k in keywords)
            
            should_save = False
            if filter_mode == "🌐 Hiển thị tất cả": should_save = True
            elif filter_mode == "🎯 Chỉ lấy Video AI" and is_ai_content: should_save = True
            elif filter_mode == "🚫 Chặn Video AI" and not is_ai_content: should_save = True
                
            if should_save:
                vel, v_type = calculate_velocity(item)
                db.upsert_video(item, vel, v_type)
                valid_count += 1
            
        return True, f"Quét xong! Lưu {valid_count} video."
    except Exception as e:
        return False, str(e)

# [NEW] HÀM XỬ LÝ OPENAI (GPT-4o-mini)
def analyze_with_gpt(api_key, video_desc, author):
    if not api_key: return "⚠️ Thiếu OpenAI API Key"
    
    try:
        client = OpenAI(api_key=api_key)
        
        # Prompt tối ưu cho Affiliate
        system_prompt = "Bạn là chuyên gia phân tích Viral Video trên TikTok. Hãy trả lời ngắn gọn, đi thẳng vào vấn đề."
        user_prompt = f"""
        Phân tích video này để làm Affiliate:
        - Tác giả: {author}
        - Caption: "{video_desc}"
        
        Hãy trả lời 3 gạch đầu dòng ngắn gọn bằng tiếng Việt:
        1. 🎣 Hook (Câu dẫn khách):
        2. 😫 Pain Point (Nỗi đau/Vấn đề):
        3. 💡 Ý tưởng Remake (Làm lại video này thế nào):
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini", # Model Ngon-Bổ-Rẻ nhất hiện nay
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Lỗi OpenAI: {str(e)}"

# ==========================================
# 5. GIAO DIỆN (FRONTEND)
# ==========================================
with st.sidebar:
    st.title("🦅 TikTok OS v7.1")
    st.caption("OpenAI Edition")
    
    with st.expander("🔑 Cấu hình", expanded=True):
        apify_token = st.text_input("Apify Token", type="password")
        # [NEW] Đổi label thành OpenAI
        openai_key = st.text_input("OpenAI API Key", type="password", help="Lấy tại platform.openai.com")
    
    with st.expander("⚙️ Quét", expanded=True):
        hashtags_str = st.text_area("Hashtags", "shilajit, amazonfinds")
        country_map = {"🌐 Global": "ALL", "Mỹ": "US", "Việt Nam": "VN", "Anh": "GB", "Pháp": "FR"}
        country_code = country_map[st.selectbox("Quốc Gia", list(country_map.keys()))]
        limit = st.slider("Số lượng", 10, 100, 30)

    st.subheader("🛡️ Lọc")
    filter_mode = st.radio("Chế độ:", ["🌐 Hiển thị tất cả", "🎯 Chỉ lấy Video AI", "🚫 Chặn Video AI"])
    ai_keywords = st.text_area("Từ khóa AI", "ai generated, #ai, midjourney", height=60)
    use_viral_filter = st.checkbox("Ẩn kênh lớn view thấp", value=True)

    if st.button("🚀 QUÉT NGAY", type="primary"):
        with st.status("Đang quét..."):
            success, msg = run_apify_scan(apify_token, hashtags_str, limit, country_code, filter_mode, ai_keywords)
            if success:
                st.success("Xong!"); time.sleep(1); st.rerun()
            else:
                st.error(msg)
    
    if st.button("📥 Tải CSV"):
        csv = db.fetch_data().to_csv(index=False).encode('utf-8')
        st.download_button("Download", csv, "tiktok_data.csv", "text/csv")

# --- DASHBOARD ---
st.markdown(f"### 🏳️ Thị trường: {country_code}")

df_all = db.fetch_data()
df_saved = db.fetch_data(only_saved=True)

# Xử lý dữ liệu an toàn
if not df_all.empty:
    df_all['author_followers'] = pd.to_numeric(df_all['author_followers'], errors='coerce').fillna(0)
    df_all['current_views'] = pd.to_numeric(df_all['current_views'], errors='coerce').fillna(0)
    df_all['velocity_value'] = pd.to_numeric(df_all['velocity_value'], errors='coerce').fillna(0)
    
    df_all['safe_followers'] = df_all['author_followers'].apply(lambda x: x if x > 0 else 1)
    df_all['viral_ratio'] = df_all['current_views'] / df_all['safe_followers']

    if use_viral_filter:
        df_all = df_all[ (df_all['viral_ratio'] > 0.5) | (df_all['velocity_value'] > 500) ]

col1, col2, col3 = st.columns(3)
col1.metric("Videos", len(df_all))
col2.metric("Đã Lưu", len(df_saved))
if not df_all.empty:
    col3.metric("Top Speed", f"{df_all['velocity_value'].max():.0f}/h")

st.divider()

tab1, tab2, tab3 = st.tabs(["🔥 Xu Hướng", "❤️ Đã Lưu", "📊 Biểu Đồ"])

def render_list(df, is_saved=False):
    if df.empty:
        st.info("Chưa có dữ liệu.")
        return
    for _, row in df.head(50).iterrows():
        emoji = "💎" if row.get('viral_ratio', 0) > 1.0 else "😐"
        with st.expander(f"{emoji} {row['velocity_value']:.0f}/h | {row['author_name']}"):
            c1, c2 = st.columns([2, 1])
            c1.caption(f"View: {row['current_views']:,} | Sub: {row['author_followers']:,}")
            if c2.button("Lưu/Bỏ", key=f"btn_{row['video_id']}_{is_saved}"):
                db.toggle_save_video(row['video_id'], row['is_saved']); st.rerun()
            
            components.iframe(f"https://www.tiktok.com/embed/v2/{row['video_id']}", height=450, scrolling=True)
            
            q = urllib.parse.quote(row['description'][:50])
            st.markdown(f"[🔎 Amazon](https://www.amazon.com/s?k={q}) | [🛍️ AliExpress](https://www.aliexpress.com/wholesale?SearchText={q})")
            
            # [NEW] Nút Phân tích gọi hàm GPT
            if st.button("🧠 OpenAI Phân tích", key=f"ai_{row['video_id']}_{is_saved}"):
                with st.spinner("GPT-4o-mini đang suy nghĩ..."):
                    anl = analyze_with_gpt(openai_key, row['description'], row['author_name'])
                    db.update_ai_analysis(row['video_id'], anl); st.rerun()
            if row['ai_analysis']: st.info(row['ai_analysis'])

with tab1: render_list(df_all)
with tab2: render_list(df_saved, True)

with tab3:
    if not df_all.empty:
        try:
            fig = px.scatter(
                df_all, x="author_followers", y="velocity_value", 
                size="current_views", color="viral_ratio",
                hover_name="author_name", color_continuous_scale="Turbo",
                title="Rađa Viral"
            )
            st.plotly_chart(fig, use_container_width=True)
        except Exception as e:
            st.error(f"Lỗi hiển thị biểu đồ: {e}")
    else:
        st.info("Chưa có dữ liệu để vẽ.")

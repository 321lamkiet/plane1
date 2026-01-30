import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from apify_client import ApifyClient
from datetime import datetime, time as dt_time
import time
import contextlib
import pytz
import google.generativeai as genai
import streamlit.components.v1 as components

# ==========================================
# 1. CẤU HÌNH HỆ THỐNG
# ==========================================
st.set_page_config(
    page_title="TikTok Intelligence OS v4.6",
    page_icon="🦅",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
    div[data-testid="stMetric"] {
        background-color: #1F2937;
        border: 1px solid #374151;
        border-radius: 8px;
        padding: 15px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
   .status-badge {
        padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 0.8em;
    }
   .badge-live { background-color: #065F46; color: #34D399; }
   .badge-sleep { background-color: #7F1D1D; color: #FCA5A5; }
    iframe { border-radius: 12px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 2. DATABASE ENGINE
# ==========================================
class DatabaseEngine:
    def __init__(self, db_name="tiktok_os_v4_final.db"):
        self.db_name = db_name
        self.init_db()

    @contextlib.contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_name, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
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
                    description TEXT,
                    video_url TEXT,
                    thumbnail_url TEXT,
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
            cursor = conn.execute("""
                SELECT play_count, scraped_at FROM metrics 
                WHERE video_id =? ORDER BY scraped_at DESC LIMIT 1
            """, (video_id,))
            return cursor.fetchone()

    def upsert_video(self, data, velocity, v_type):
        now = datetime.now()
        vid_id = data.get('id')
        author = data.get('authorMeta', {}).get('name', 'Unknown')
        desc = data.get('text', '')
        url = data.get('webVideoUrl', '')
        thumb = data.get('videoMeta', {}).get('coverUrl', '')
        created_at = data.get('createTime', int(time.time()))
        views = data.get('playCount', 0)
        shares = data.get('shareCount', 0)

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO videos (
                    video_id, author_name, description, video_url, thumbnail_url,
                    created_at, last_scraped_at, current_views, current_shares,
                    velocity_value, velocity_type
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(video_id) DO UPDATE SET
                    last_scraped_at=excluded.last_scraped_at,
                    current_views=excluded.current_views,
                    current_shares=excluded.current_shares,
                    velocity_value=excluded.velocity_value,
                    velocity_type=excluded.velocity_type
            """, (vid_id, author, desc, url, thumb, created_at, now, views, shares, velocity, v_type))
            conn.execute("INSERT INTO metrics (video_id, play_count, share_count, scraped_at) VALUES (?,?,?,?)",
                         (vid_id, views, shares, now))
            conn.commit()
            
    def update_ai_analysis(self, video_id, analysis_text):
        with self.get_connection() as conn:
            conn.execute("UPDATE videos SET ai_analysis =? WHERE video_id =?", (analysis_text, video_id))
            conn.commit()

    def fetch_data(self):
        with self.get_connection() as conn:
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
            return round(velocity, 1), "⚡ Real"
    else:
        created_ts = video_item.get('createTime', 0)
        if created_ts:
            age_hours = max(1.0, (current_time - datetime.fromtimestamp(created_ts)).total_seconds() / 3600.0)
            velocity = current_views / age_hours
            return round(velocity, 1), "🆕 Est"
    return 0.0, "⏳ Wait"

def check_us_market_hours():
    try:
        us_tz = pytz.timezone('US/Eastern')
        now_us = datetime.now(us_tz)
        is_active = 8 <= now_us.hour < 23
        return is_active, now_us.strftime("%I:%M %p")
    except:
        return True, "Unknown Time"

def run_apify_scan(token, hashtag, limit, custom_proxy=None):
    if not token: return False, "Chưa nhập API Token!"
    
    client = ApifyClient(token)
    
    if custom_proxy and custom_proxy.strip():
        proxy_config = {"useApifyProxy": False, "proxyUrls": [custom_proxy.strip()]}
    else:
        proxy_config = {"useApifyProxy": True, "apifyProxyCountry": "US"}

    run_input = {
        "hashtags": [hashtag],
        "resultsPerPage": limit,
        "proxyConfiguration": proxy_config,
        "searchSection": "", 
        "shouldDownloadCovers": False
    }
    
    try:
        run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
        if not run or 'defaultDatasetId' not in run:
             return False, "Lỗi Apify: Không trả về Dataset."

        dataset = client.dataset(run['defaultDatasetId']).list_items().items
        if not dataset: return False, "Không tìm thấy video."
        
        for item in dataset:
            vel, v_type = calculate_velocity(item)
            db.upsert_video(item, vel, v_type)
            
        return True, f"Đã quét {len(dataset)} video!"
    except Exception as e:
        return False, str(e)

def analyze_with_gemini(api_key, video_desc, author):
    if not api_key: return "⚠️ Thiếu Gemini API Key"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"Phân tích Marketing Video TikTok: Author: {author}, Caption: {video_desc}. Trả lời 3 ý tiếng Việt: 1.Hook, 2.Pain Point, 3.Ý tưởng làm lại."
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Lỗi AI: {str(e)}"

# ==========================================
# 5. GIAO DIỆN (FRONTEND)
# ==========================================
with st.sidebar:
    st.title("🦅 TikTok Intelligence OS")
    st.caption("v4.6 | Countdown & Export")
    
    with st.expander("🔑 Token", expanded=True):
        apify_token = st.text_input("Apify Token", type="password")
        gemini_key = st.text_input("Gemini API Key", type="password")
    
    with st.expander("⚙️ Cấu hình", expanded=True):
        hashtag = st.text_input("Hashtag", "amazonfinds")
        custom_proxy = st.text_input("Custom Proxy (Optional)", placeholder="http://user:pass@ip:port", help="Để trống nếu muốn dùng Proxy Apify US")
        limit = st.slider("Số lượng quét/lần", 10, 100, 20)
        
    st.subheader("🤖 Auto-Pilot (Tự động)")
    auto_mode = st.checkbox("Bật chế độ chạy tự động (Loop)")
    
    if auto_mode:
        scan_interval = st.number_input("Nghỉ giữa các lần (Phút)", min_value=15, max_value=120, value=30, step=5)
        max_scans = st.number_input("Giới hạn số lần/ngày", 1, 50, 10)
    else:
        st.info("Chế độ thủ công: Quét 1 lần.")
        scan_interval = 30
        max_scans = 1
    
    if 'scan_count' not in st.session_state: st.session_state['scan_count'] = 0
    
    btn_label = "🚀 BẮT ĐẦU CHẠY AUTO" if auto_mode else "🚀 QUÉT NGAY (1 Lần)"
    
    if st.button(btn_label, type="primary", use_container_width=True):
        if st.session_state['scan_count'] >= max_scans and auto_mode:
            st.error("🛑 Đạt giới hạn quét trong ngày!")
        else:
            with st.status("Đang kết nối...") as status:
                success, msg = run_apify_scan(apify_token, hashtag, limit, custom_proxy)
                if success:
                    st.session_state['scan_count'] += 1
                    status.update(label="✅ Xong!", state="complete")
                    time.sleep(1)
                    st.rerun()
                else:
                    status.update(label="❌ Lỗi", state="error")
                    st.error(msg)
    
    st.divider()
    # [NEW FEATURE] Nút Download CSV
    if st.button("📥 Tải Dữ liệu (Excel/CSV)"):
        csv = db.fetch_data().to_csv(index=False).encode('utf-8')
        st.download_button("Bấm để tải về", data=csv, file_name="tiktok_data.csv", mime="text/csv")

# --- DASHBOARD ---
market_active, us_time = check_us_market_hours()
status_html = f'<span class="status-badge badge-live">LIVE</span>' if market_active else f'<span class="status-badge badge-sleep">SLEEP</span>'

st.markdown(f"### 🇺🇸 US Market: {us_time} | {status_html}", unsafe_allow_html=True)

df = db.fetch_data()

if not df.empty:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Videos", len(df))
    k2.metric("Avg Vel", f"{df['velocity_value'].mean():.0f}/h")
    k3.metric("Top Vel", f"{df['velocity_value'].max():.0f}/h")
    scan_status = f"{st.session_state['scan_count']}/{max_scans}" if auto_mode else "Manual"
    k4.metric("Scans Today", scan_status)
    
    st.divider()
    
    c_left, c_right = st.columns([1.5, 2])
    with c_left:
        st.subheader("🔥 Top Trending")
        for _, row in df.head(5).iterrows():
            with st.expander(f"📈 {row['velocity_value']:.0f}/h | {row['author_name']}", expanded=False):
                components.iframe(f"https://www.tiktok.com/embed/v2/{row['video_id']}", height=550, scrolling=True)
                if st.button(f"🧠 Phân tích AI", key=f"btn_{row['video_id']}"):
                    with st.spinner("Gemini loading..."):
                        analysis = analyze_with_gemini(gemini_key, row['description'], row['author_name'])
                        db.update_ai_analysis(row['video_id'], analysis)
                        st.rerun()
                if row['ai_analysis']: st.info(row['ai_analysis'])

    with c_right:
        st.subheader("📊 Market Chart")
        fig = px.bar(df.head(10), y='author_name', x='velocity_value', orientation='h', color='velocity_value', color_continuous_scale='Viridis')
        fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df[['description', 'velocity_value', 'current_shares']], height=600)

    # --- LOGIC TỰ ĐỘNG [CẢI TIẾN: COUNTDOWN] ---
    if auto_mode:
        if st.session_state['scan_count'] >= max_scans: 
            st.toast("🛑 Đã đạt giới hạn quét.", icon="🛑")
        elif not market_active:
            st.warning(f"💤 Thị trường Mỹ đang ngủ ({us_time}). Tạm dừng 30p...")
            time.sleep(1800); st.rerun()
        else:
            # [NEW] Đồng hồ đếm ngược trực quan
            placeholder = st.empty()
            total_seconds = scan_interval * 60
            
            # Hiển thị thanh đếm ngược thay vì treo máy
            for i in range(total_seconds, 0, -1):
                # Cập nhật mỗi 10 giây để đỡ lag, hoặc mỗi giây nếu muốn
                if i % 10 == 0 or i < 10: 
                    mins, secs = divmod(i, 60)
                    placeholder.info(f"⏳ Auto-Pilot đang chờ: **{mins} phút {secs} giây** nữa sẽ quét lại...")
                time.sleep(1)
            
            placeholder.empty()
            st.cache_data.clear()
            run_apify_scan(apify_token, hashtag, limit, custom_proxy)
            st.session_state['scan_count'] += 1
            st.rerun()
else:
    st.info("👋 Nhập Token để bắt đầu.")

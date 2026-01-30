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
import streamlit.components.v1 as components # Import quan trọng cho trình phát video

# ==========================================
# 1. SYSTEM CONFIG & STYLING
# ==========================================
st.set_page_config(
    page_title="TikTok Affiliate OS v4.2 (Stable)",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
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
# 2. DATABASE ENGINE (WAL MODE - FIXED)
# ==========================================
class DatabaseEngine:
    def __init__(self, db_name="tiktok_os_v4_stable.db"):
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
            # Video Metadata
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
            # Historical Metrics
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
        # Xử lý dữ liệu an toàn để tránh lỗi KeyError
        vid_id = data.get('id')
        author = data.get('authorMeta', {}).get('name', 'Unknown')
        desc = data.get('text', '')
        url = data.get('webVideoUrl', '')
        thumb = data.get('videoMeta', {}).get('coverUrl', '')
        created_at = data.get('createTime', int(time.time())) # [FIXED] Lấy timestamp thay vì dict
        views = data.get('playCount', 0)
        shares = data.get('shareCount', 0)

        with self.get_connection() as conn:
            # Upsert Video Metadata
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
            """, (
                vid_id, author, desc, url, thumb,
                created_at, now, views, shares,
                velocity, v_type
            ))
            # Insert History
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
# 3. CORE LOGIC: HYBRID VELOCITY
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
            created_time = datetime.fromtimestamp(created_ts)
            age_hours = max(1.0, (current_time - created_time).total_seconds() / 3600.0)
            velocity = current_views / age_hours
            return round(velocity, 1), "🆕 Est"
            
    return 0.0, "⏳ Wait"

# ==========================================
# 4. INTELLIGENT SCANNERS
# ==========================================
def check_us_market_hours():
    try:
        us_tz = pytz.timezone('US/Eastern')
        now_us = datetime.now(us_tz)
        is_active = 8 <= now_us.hour < 23
        return is_active, now_us.strftime("%I:%M %p")
    except:
        return True, "Unknown Time" # Fallback nếu lỗi timezone

def run_apify_scan(token, hashtag, limit):
    if not token: return False, "Missing API Token"
    
    client = ApifyClient(token)
    run_input = {
        "hashtags": [hashtag],
        "resultsPerPage": limit,
        "proxyConfiguration": {"useApifyProxy": True, "apifyProxyCountry": "US"},
        "searchSection": "/video/search",
        "shouldDownloadCovers": False
    }
    
    try:
        run = client.actor("clockworks/tiktok-scraper").call(run_input=run_input)
        
        # Kiểm tra nếu dataset rỗng
        if not run or 'defaultDatasetId' not in run:
             return False, "Scan failed (No dataset ID)"

        dataset = client.dataset(run['defaultDatasetId']).list_items().items
        
        if not dataset: return False, "No videos found."
        
        for item in dataset:
            vel, v_type = calculate_velocity(item)
            db.upsert_video(item, vel, v_type)
            
        return True, f"Scanned {len(dataset)} videos."
    except Exception as e:
        return False, str(e)

def analyze_with_gemini(api_key, video_desc, author):
    if not api_key: return "⚠️ Missing Gemini API Key"
    
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        
        prompt = f"""
        Act as a Viral Content Strategist. Analyze this TikTok video:
        - Creator: {author}
        - Caption: "{video_desc}"
        
        Output in Vietnamese (Tiếng Việt):
        1. 🪝 Hook (Câu dẫn khách):
        2. 😫 Pain Point (Nỗi đau):
        3. 💡 Hướng làm video tương tự:
        """
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"AI Error: {str(e)}"

# ==========================================
# 5. FRONTEND UI
# ==========================================
# --- SIDEBAR ---
with st.sidebar:
    st.title("🦅 TikTok Intelligence OS")
    st.caption("v4.2 Stable | No-Code Player")
    
    with st.expander("🔑 API Credentials", expanded=True):
        apify_token = st.text_input("Apify Token", type="password")
        gemini_key = st.text_input("Gemini API Key", type="password")
    
    with st.expander("⚙️ Scan Settings", expanded=True):
        hashtag = st.text_input("Hashtag", "amazonfinds")
        limit = st.slider("Limit", 10, 100, 20)
        
    st.subheader("🤖 Auto-Pilot")
    auto_mode = st.checkbox("Enable Smart Schedule")
    max_scans = st.number_input("Safety Limit", 1, 50, 10)
    
    # [FIXED] Standardized Session State Access
    if 'scan_count' not in st.session_state:
        st.session_state['scan_count'] = 0
    
    if st.button("🚀 SCAN NOW", type="primary", use_container_width=True):
        if st.session_state['scan_count'] >= max_scans:
            st.error("🛑 Limit Reached!")
        else:
            with st.status("Scanning TikTok US...") as status:
                success, msg = run_apify_scan(apify_token, hashtag, limit)
                if success:
                    st.session_state['scan_count'] += 1
                    status.update(label="✅ Complete!", state="complete")
                    time.sleep(1)
                    st.rerun()
                else:
                    status.update(label="❌ Failed", state="error")
                    st.error(msg)

# --- MAIN DASHBOARD ---
market_active, us_time = check_us_market_hours()
status_html = f'<span class="status-badge badge-live">ACTIVE</span>' if market_active else f'<span class="status-badge badge-sleep">SLEEPING</span>'

st.markdown(f"### 🇺🇸 US Market: {us_time} | Status: {status_html}", unsafe_allow_html=True)

df = db.fetch_data()

if not df.empty:
    # Top KPI
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Tracked Videos", len(df))
    k2.metric("Avg Velocity", f"{df['velocity_value'].mean():.0f}/h")
    k3.metric("Top Breakout", f"{df['velocity_value'].max():.0f}/h")
    k4.metric("Scans Today", f"{st.session_state['scan_count']}/{max_scans}")
    
    st.divider()
    
    # Layout 2 Cột
    c_left, c_right = st.columns([1.5, 2])
    
    with c_left:
        st.subheader("🔥 Breakout Feed")
        # List view top 5 videos
        for _, row in df.head(5).iterrows():
            # [FIXED] Hiển thị rõ ràng
            with st.expander(f"📈 {row['velocity_value']:.0f}/h | {row['author_name']}", expanded=False):
                # Nhúng Player
                components.iframe(
                    f"https://www.tiktok.com/embed/v2/{row['video_id']}", 
                    height=550, 
                    scrolling=True
                )
                
                # Nút AI
                if st.button(f"🧠 Analyze Logic", key=f"btn_{row['video_id']}"):
                    if not row['ai_analysis']:
                        with st.spinner("Asking Gemini..."):
                            analysis = analyze_with_gemini(gemini_key, row['description'], row['author_name'])
                            db.update_ai_analysis(row['video_id'], analysis)
                            st.rerun()
                    
                if row['ai_analysis']:
                    st.info(row['ai_analysis'])

    with c_right:
        st.subheader("📊 Market Trends")
        
        # Chart
        fig = px.bar(
            df.head(10), y='author_name', x='velocity_value', 
            orientation='h', color='velocity_value', 
            color_continuous_scale='Viridis', title="Top Velocity Leaders"
        )
        fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)
        
        # Table
        st.dataframe(
            df[['description', 'velocity_value', 'velocity_type', 'current_shares']],
            column_config={
                "velocity_value": st.column_config.ProgressColumn("Vel (v/h)", min_value=0, max_value=int(df['velocity_value'].max())),
                "current_shares": st.column_config.NumberColumn("Shares"),
                "description": st.column_config.TextColumn("Caption", width="medium")
            },
            height=600
        )

    # --- AUTO SCHEDULE LOGIC ---
    if auto_mode:
        if st.session_state['scan_count'] >= max_scans:
            st.toast("🛑 Safety Limit Reached. Stopping Auto-Scan.", icon="🛑")
        elif not market_active:
            st.toast(f"💤 Market Sleeping ({us_time}). Pausing 30m...", icon="💤")
            time.sleep(1800)
            st.rerun()
        else:
            st.toast("🔄 Auto-Scan Active. Refreshing in 30m...", icon="🔄")
            time.sleep(1800)
            st.cache_data.clear()
            run_apify_scan(apify_token, hashtag, limit)
            st.session_state['scan_count'] += 1
            st.rerun()
            
else:
    st.info("👋 Enter API Token & Start Scan to see data.")

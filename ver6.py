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
    page_title="TikTok Intelligence OS v4.8 (VN)",
    page_icon="🇻🇳",
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
    def __init__(self, db_name="tiktok_v48_vn.db"): # Đổi tên DB mới
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
                    author_followers INTEGER, 
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
        followers = data.get('authorMeta', {}).get('fans', 0)
        desc = data.get('text', '')
        url = data.get('webVideoUrl', '')
        thumb = data.get('videoMeta', {}).get('coverUrl', '')
        created_at = data.get('createTime', int(time.time()))
        views = data.get('playCount', 0)
        shares = data.get('shareCount', 0)

        with self.get_connection() as conn:
            conn.execute("""
                INSERT INTO videos (
                    video_id, author_name, author_followers, description, video_url, thumbnail_url,
                    created_at, last_scraped_at, current_views, current_shares,
                    velocity_value, velocity_type
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(video_id) DO UPDATE SET
                    last_scraped_at=excluded.last_scraped_at,
                    current_views=excluded.current_views,
                    current_shares=excluded.current_shares,
                    author_followers=excluded.author_followers,
                    velocity_value=excluded.velocity_value,
                    velocity_type=excluded.velocity_type
            """, (vid_id, author, followers, desc, url, thumb, created_at, now, views, shares, velocity, v_type))
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
            return round(velocity, 1), "⚡ Thực"
    else:
        created_ts = video_item.get('createTime', 0)
        if created_ts:
            age_hours = max(1.0, (current_time - datetime.fromtimestamp(created_ts)).total_seconds() / 3600.0)
            velocity = current_views / age_hours
            return round(velocity, 1), "🆕 Dự báo"
    return 0.0, "⏳ Chờ"

def check_market_hours(country_code):
    # Logic giờ giấc đơn giản hóa
    try:
        if country_code == "VN":
            tz = pytz.timezone('Asia/Ho_Chi_Minh')
        else:
            tz = pytz.timezone('US/Eastern') # Mặc định theo giờ Mỹ
            
        now = datetime.now(tz)
        is_active = 8 <= now.hour < 23
        return is_active, now.strftime("%H:%M")
    except:
        return True, "Unknown"

def run_apify_scan(token, hashtag, limit, country_code, custom_proxy=None):
    if not token: return False, "Thiếu API Token!"
    client = ApifyClient(token)
    
    # Logic Proxy & Quốc gia
    if custom_proxy and custom_proxy.strip():
        proxy_config = {"useApifyProxy": False, "proxyUrls": [custom_proxy.strip()]}
    else:
        # Nếu dùng Proxy Apify thì chọn quốc gia
        proxy_config = {"useApifyProxy": True, "apifyProxyCountry": country_code}

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
             return False, "Lỗi Apify: Không trả về dữ liệu."

        dataset = client.dataset(run['defaultDatasetId']).list_items().items
        if not dataset: return False, "Không tìm thấy video nào."
        
        for item in dataset:
            vel, v_type = calculate_velocity(item)
            db.upsert_video(item, vel, v_type)
            
        return True, f"Đã quét thành công {len(dataset)} video!"
    except Exception as e:
        return False, str(e)

def analyze_with_gemini(api_key, video_desc, author):
    if not api_key: return "⚠️ Thiếu Gemini API Key"
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"Bạn là chuyên gia Marketing TikTok. Phân tích video này: Tác giả: {author}, Caption: {video_desc}. Trả lời 3 ý ngắn gọn: 1.Hook (Câu dẫn), 2.Nỗi đau khách hàng, 3.Kịch bản đề xuất."
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Lỗi AI: {str(e)}"

# ==========================================
# 5. GIAO DIỆN (FRONTEND)
# ==========================================
with st.sidebar:
    st.title("🦅 TikTok Intelligence OS")
    st.caption("v4.8 VN | Đa Quốc Gia")
    
    with st.expander("🔑 Cấu hình API", expanded=True):
        apify_token = st.text_input("Apify Token", type="password")
        gemini_key = st.text_input("Gemini API Key", type="password")
    
    with st.expander("⚙️ Cài đặt Quét", expanded=True):
        hashtag = st.text_input("Hashtag (Từ khóa)", "amazonfinds")
        
        # [NEW] Chọn Quốc Gia
        country_map = {"Mỹ (US)": "US", "Việt Nam (VN)": "VN", "Anh (UK)": "GB", "Pháp (FR)": "FR", "Nhật (JP)": "JP"}
        selected_country = st.selectbox("Chọn Quốc Gia Quét", list(country_map.keys()))
        country_code = country_map[selected_country]
        
        custom_proxy = st.text_input("Proxy Riêng (Tùy chọn)", placeholder="http://...", help="Để trống nếu dùng Proxy của Apify")
        limit = st.slider("Số lượng video/lần", 10, 100, 20)

    st.subheader("🛡️ Bộ Lọc")
    use_viral_filter = st.checkbox("Bật chế độ Lọc Rác (Viral Mode)", help="Chỉ hiện video có View cao hơn Follower")
    
    st.subheader("🤖 Tự Động Hóa")
    auto_mode = st.checkbox("Bật chạy tự động (Loop)")
    
    if auto_mode:
        scan_interval = st.number_input("Nghỉ giữa các lần (Phút)", 15, 120, 30, step=5)
        max_scans = st.number_input("Giới hạn số lần/ngày", 1, 50, 10)
    else:
        scan_interval = 30
        max_scans = 1
    
    if 'scan_count' not in st.session_state: st.session_state['scan_count'] = 0
    
    btn_label = "🚀 BẮT ĐẦU CHẠY AUTO" if auto_mode else "🚀 QUÉT NGAY (1 Lần)"
    
    if st.button(btn_label, type="primary", use_container_width=True):
        if st.session_state['scan_count'] >= max_scans and auto_mode:
            st.error("🛑 Đã đạt giới hạn quét trong ngày!")
        else:
            with st.status("Đang kết nối tới TikTok...") as status:
                # Truyền country_code vào hàm quét
                success, msg = run_apify_scan(apify_token, hashtag, limit, country_code, custom_proxy)
                if success:
                    st.session_state['scan_count'] += 1
                    status.update(label="✅ Hoàn tất!", state="complete")
                    time.sleep(1)
                    st.rerun()
                else:
                    status.update(label="❌ Thất bại", state="error")
                    st.error(msg)
    
    if st.button("📥 Tải Dữ liệu (Excel/CSV)"):
        csv = db.fetch_data().to_csv(index=False).encode('utf-8')
        st.download_button("Bấm để tải về", data=csv, file_name="tiktok_data.csv", mime="text/csv")

# --- DASHBOARD ---
market_active, local_time = check_market_hours(country_code)
status_text = "ĐANG HOẠT ĐỘNG" if market_active else "ĐANG NGỦ"
status_color = "badge-live" if market_active else "badge-sleep"
status_html = f'<span class="status-badge {status_color}">{status_text}</span>'

st.markdown(f"### 🏳️ {selected_country}: {local_time} | {status_html}", unsafe_allow_html=True)

df = db.fetch_data()

# --- [LOGIC LỌC RÁC] ---
if use_viral_filter and not df.empty:
    df['safe_followers'] = df['author_followers'].replace(0, 1)
    df['viral_ratio'] = df['current_views'] / df['safe_followers']
    df_filtered = df[ (df['viral_ratio'] > 0.5) | (df['velocity_value'] > 500) ]
    
    removed_count = len(df) - len(df_filtered)
    if removed_count > 0:
        st.caption(f"🛡️ Đã ẩn {removed_count} video rác (Kênh lớn nhưng tương tác thấp).")
    df = df_filtered
# -----------------------

if not df.empty:
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Video Đã Lọc", len(df))
    k2.metric("Tốc độ TB", f"{df['velocity_value'].mean():.0f}/h")
    k3.metric("Tốc độ Đỉnh", f"{df['velocity_value'].max():.0f}/h")
    scan_status = f"{st.session_state['scan_count']}/{max_scans}" if auto_mode else "Thủ công"
    k4.metric("Lần quét", scan_status)
    
    st.divider()
    
    c_left, c_right = st.columns([1.5, 2])
    with c_left:
        st.subheader("🔥 Xu Hướng Nổi Bật")
        for _, row in df.head(5).iterrows():
            ratio = row['current_views'] / max(1, row['author_followers'])
            emoji = "💎" if ratio > 1.0 else "😐"
            
            with st.expander(f"{emoji} Tốc độ: {row['velocity_value']:.0f}/h | {row['author_name']}", expanded=False):
                st.caption(f"Follow: {row['author_followers']:,} | View: {row['current_views']:,} (Tỉ lệ: {ratio:.1f}x)")
                components.iframe(f"https://www.tiktok.com/embed/v2/{row['video_id']}", height=550, scrolling=True)
                if st.button(f"🧠 Phân tích AI", key=f"btn_{row['video_id']}"):
                    with st.spinner("Gemini đang suy nghĩ..."):
                        analysis = analyze_with_gemini(gemini_key, row['description'], row['author_name'])
                        db.update_ai_analysis(row['video_id'], analysis)
                        st.rerun()
                if row['ai_analysis']: st.info(row['ai_analysis'])

    with c_right:
        st.subheader("📊 Biểu Đồ Tăng Trưởng")
        fig = px.bar(df.head(10), y='author_name', x='velocity_value', orientation='h', color='velocity_value', color_continuous_scale='Viridis', labels={'velocity_value': 'Tốc độ (view/h)', 'author_name': 'Kênh'})
        fig.update_layout(plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)')
        st.plotly_chart(fig, use_container_width=True)
        st.dataframe(df[['description', 'velocity_value', 'author_followers', 'current_shares']], height=600)

    # --- AUTO LOGIC ---
    if auto_mode:
        if st.session_state['scan_count'] >= max_scans: 
            st.toast("🛑 Đã đạt giới hạn quét.", icon="🛑")
        elif not market_active:
            st.warning(f"💤 Thị trường {selected_country} đang ngủ ({local_time}). Tạm dừng 30p...")
            time.sleep(1800); st.rerun()
        else:
            placeholder = st.empty()
            total_seconds = scan_interval * 60
            for i in range(total_seconds, 0, -1):
                if i % 10 == 0 or i < 10: 
                    mins, secs = divmod(i, 60)
                    placeholder.info(f"⏳ Tự động chạy lại sau: **{mins} phút {secs} giây**...")
                time.sleep(1)
            placeholder.empty()
            st.cache_data.clear()
            run_apify_scan(apify_token, hashtag, limit, country_code, custom_proxy)
            st.session_state['scan_count'] += 1
            st.rerun()
else:
    st.info("👋 Chào bạn! Hãy nhập Token ở bên trái và bấm nút Quét để bắt đầu.")

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
import streamlit.components.v1 as components
import urllib.parse
import requests

# ==========================================
# CẤU HÌNH (v11.1 Multi-Search)
# ==========================================
st.set_page_config(page_title="TikTok OS v11.1", page_icon="🎯", layout="wide")

st.markdown("""
<style>
   .stApp { background-color: #0E1117; }
   div[data-testid="stMetric"] { background-color: #1F2937; border: 1px solid #374151; border-radius: 8px; padding: 15px; }
   div[data-testid="stMetricValue"] { font-size: 24px; color: #34D399; }
</style>
""", unsafe_allow_html=True)

# DATABASE
class DatabaseEngine:
    def __init__(self, db_name="tiktok_v111_multi.db"): 
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
            conn.execute("""CREATE TABLE IF NOT EXISTS videos (video_id TEXT PRIMARY KEY, author_name TEXT, author_followers INTEGER, description TEXT, video_url TEXT, thumbnail_url TEXT, music_title TEXT, music_author TEXT, is_saved INTEGER DEFAULT 0, created_at INTEGER, last_scraped_at TIMESTAMP, current_views INTEGER, current_shares INTEGER, velocity_value REAL, velocity_type TEXT, ai_analysis TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS metrics (id INTEGER PRIMARY KEY, video_id TEXT, play_count INTEGER, share_count INTEGER, scraped_at TIMESTAMP)""")
            conn.commit()
    def get_last_metric(self, video_id):
        with self.get_connection() as conn: return conn.execute("SELECT play_count, scraped_at FROM metrics WHERE video_id =? ORDER BY scraped_at DESC LIMIT 1", (video_id,)).fetchone()
    
    def upsert_video(self, data, vel, v_type):
        vid_id = data.get('id'); author = data.get('authorMeta', {}).get('name', 'Unknown')
        if not vid_id: return
        
        download_url = data.get('videoMeta', {}).get('downloadAddr', '')
        web_url = data.get('webVideoUrl', '')
        final_url = download_url if download_url else web_url

        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,COALESCE((SELECT ai_analysis FROM videos WHERE video_id=?), NULL))", 
                         (vid_id, author, data.get('authorMeta', {}).get('fans', 0), data.get('text', ''), final_url, data.get('videoMeta', {}).get('coverUrl', ''), data.get('musicMeta', {}).get('musicName', ''), data.get('musicMeta', {}).get('musicAuthor', ''), 0, int(time.time()), datetime.now(), data.get('playCount', 0), data.get('shareCount', 0), vel, v_type, vid_id))
            conn.execute("INSERT INTO metrics (video_id, play_count, share_count, scraped_at) VALUES (?,?,?,?)", (vid_id, data.get('playCount', 0), data.get('shareCount', 0), datetime.now()))
            conn.commit()

    def update_ai(self, vid, txt):
        with self.get_connection() as conn: conn.execute("UPDATE videos SET ai_analysis=? WHERE video_id=?", (txt, vid)); conn.commit()
    def toggle_save(self, vid, status):
        with self.get_connection() as conn: conn.execute("UPDATE videos SET is_saved=? WHERE video_id=?", (0 if status==1 else 1, vid)); conn.commit()
    def fetch(self, saved=False):
        with self.get_connection() as conn: return pd.read_sql(f"SELECT * FROM videos {'WHERE is_saved=1' if saved else ''} ORDER BY velocity_value DESC", conn)

db = DatabaseEngine()

# LOGIC TÍNH TOÁN
def calc_vel(item):
    prev = db.get_last_metric(item['id'])
    curr_views = item.get('playCount', 0); now = datetime.now()
    if prev:
        hours = (now - pd.to_datetime(prev[1])).total_seconds() / 3600.0
        if hours > 0.05: return round(max(0, curr_views - prev[0]) / hours, 1), "⚡ Thực"
    age = max(1.0, (now - datetime.fromtimestamp(item.get('createTime', 0))).total_seconds() / 3600.0)
    return round(curr_views / age, 1), "🆕 Dự báo"

# LOGIC QUÉT MỚI (HỖ TRỢ TÁCH TỪ KHÓA)
def run_scan(token, tags, limit, country, filter_mode, ai_keys, proxy, separate_search):
    if not token: return False, "Thiếu Token!"
    client = ApifyClient(token)
    p_config = {"useApifyProxy": True}
    if proxy: p_config = {"useApifyProxy": False, "proxyUrls": [proxy]}
    elif country != "ALL": p_config = {"useApifyProxy": True, "apifyProxyCountry": country}
    
    tag_list = [t.strip() for t in tags.split(',') if t.strip()]
    if not tag_list: return False, "Chưa nhập Hashtag!"

    total_videos = 0
    
    # --- LOGIC QUÉT RIÊNG LẺ ---
    if separate_search:
        status_box = st.empty()
        for i, tag in enumerate(tag_list):
            status_box.info(f"⏳ Đang quét từ khóa [{i+1}/{len(tag_list)}]: '{tag}' ({limit} video)...")
            try:
                run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": [tag], "resultsPerPage": limit, "proxyConfiguration": p_config, "searchSection": ""})
                if not run: continue
                items = client.dataset(run['defaultDatasetId']).list_items().items
                
                count = 0
                for item in items:
                    is_ai = any(k in item.get('text', '').lower() for k in ai_keys.split(',')) if ai_keys else False
                    if (filter_mode == "🎯 Chỉ lấy Video AI" and not is_ai) or (filter_mode == "🚫 Chặn Video AI" and is_ai): continue
                    
                    v, t = calc_vel(item); db.upsert_video(item, v, t); count += 1
                total_videos += count
            except Exception as e:
                st.error(f"Lỗi khi quét '{tag}': {str(e)}")
        status_box.empty()
        return True, f"✅ Đã quét xong {len(tag_list)} từ khóa. Tổng cộng: {total_videos} video."

    # --- LOGIC QUÉT GỘP (NHƯ CŨ) ---
    else:
        try:
            run = client.actor("clockworks/tiktok-scraper").call(run_input={"hashtags": tag_list, "resultsPerPage": limit, "proxyConfiguration": p_config, "searchSection": ""})
            items = client.dataset(run['defaultDatasetId']).list_items().items
            if not items: return False, "Không có video."
            count = 0
            for item in items:
                is_ai = any(k in item.get('text', '').lower() for k in ai_keys.split(',')) if ai_keys else False
                if (filter_mode == "🎯 Chỉ lấy Video AI" and not is_ai) or (filter_mode == "🚫 Chặn Video AI" and is_ai): continue
                
                v, t = calc_vel(item); db.upsert_video(item, v, t); count += 1
            return True, f"✅ Đã lưu {count} video."
        except Exception as e: return False, str(e)

# AI GOOGLE GENAI
def run_gemini(key, desc, auth):
    if not key: return "⚠️ Thiếu API Key"
    try:
        client = genai.Client(api_key=key)
        try:
            return client.models.generate_content(model="gemini-2.0-flash", contents=f"Phân tích ngắn: {auth}, {desc}. Hook, Pain, Remake.").text
        except:
            time.sleep(2)
            return client.models.generate_content(model="gemini-1.5-flash", contents=f"Phân tích ngắn: {auth}, {desc}. Hook, Pain, Remake.").text
    except Exception as e: return f"Lỗi AI: {str(e)}"

# GIAO DIỆN
with st.sidebar:
    st.title("🦅 TikTok OS v11.1"); st.caption("Multi-Keyword Mode")
    
    with st.expander("🔑 Cấu hình API", expanded=True):
        api_tk = st.text_input("Apify Token", type="password")
        gemini_tk = st.text_input("Gemini API Key", type="password")
    
    with st.expander("⚙️ Quét & Lọc", expanded=True):
        tags = st.text_area("Hashtags (phân cách bằng dấu phẩy)", "shilajit, sea moss, amazonfinds")
        
        # [NEW] Checkbox chế độ quét riêng
        separate_search = st.checkbox("✅ Quét riêng từng từ khóa", value=False, help="Nếu chọn: Quét 10 video cho từ A, rồi quét 10 video cho từ B. Nếu không chọn: Quét trộn lẫn.")
        
        country_map = {"🌐 Global": "ALL", "Mỹ": "US", "Việt Nam": "VN", "Anh": "GB", "Pháp": "FR"}
        country = country_map[st.selectbox("Quốc gia", list(country_map.keys()))]
        limit = st.slider("Số lượng (cho mỗi lần quét)", 10, 100, 10) # Mặc định 10 cho đúng ý bạn
        
        st.markdown("---")
        st.markdown("**🤖 AI Hunter**")
        filter_mode = st.radio("Chế độ:", ["🌐 Hiển thị tất cả", "🎯 Chỉ lấy Video AI", "🚫 Chặn Video AI"])
        ai_keys = st.text_area("Từ khóa AI", "ai generated, #ai, midjourney", height=60)
        use_filter = st.checkbox("Ẩn kênh lớn view thấp", value=True)

    if st.button("🚀 QUÉT NGAY", type="primary"): 
        with st.status("Đang khởi động..."):
            s, m = run_scan(api_tk, tags, limit, country, filter_mode, ai_keys, "", separate_search)
            if s: st.success(m); time.sleep(1); st.rerun()
            else: st.error(m)
            
    if st.button("📥 Tải Excel"):
        st.download_button("Download CSV", db.fetch().to_csv(index=False).encode('utf-8'), "data.csv", "text/csv")

# DASHBOARD
df_all = db.fetch()
df_saved = db.fetch(saved=True)

if not df_all.empty:
    df_all['followers'] = pd.to_numeric(df_all['author_followers'], errors='coerce').fillna(0)
    df_all['views'] = pd.to_numeric(df_all['current_views'], errors='coerce').fillna(0)
    df_all['safe_followers'] = df_all['followers'].apply(lambda x: x if x > 0 else 1)
    df_all['ratio'] = df_all['views'] / df_all['safe_followers']
    
    if use_filter: df_all = df_all[(df_all['ratio'] > 0.5) | (df_all['velocity_value'] > 500)]

    c1, c2, c3 = st.columns(3)
    c1.metric("Videos", len(df_all))
    c2.metric("Đã Lưu", len(df_saved))
    if not df_all.empty: c3.metric("Top Speed", f"{df_all['velocity_value'].max():.0f}/h")

    t1, t2, t3 = st.tabs(["🔥 Xu Hướng", "❤️ Kho Lưu Trữ", "📊 Biểu Đồ"])
    
    def render(d, saved=False):
        if d.empty: st.info("Trống."); return
        for _, r in d.head(50).iterrows():
            emoji = "💎" if r.get('ratio', 0) > 1.0 else "😐"
            with st.expander(f"{emoji} {r['velocity_value']:.0f}/h | {r['author_name']}"):
                c1, c2 = st.columns([1.5, 1])
                c1.caption(f"👀 {r['current_views']:,} views | 👤 {r['author_followers']:,} subs")
                c1.caption(f"🎵 {r['music_title']}")
                if c2.button("❤️ Lưu/Bỏ", key=f"s_{r['video_id']}_{saved}"): db.toggle_save(r['video_id'], r['is_saved']); st.rerun()
                
                if c2.button("🧠 Phân tích AI", key=f"a_{r['video_id']}_{saved}"):
                    with st.spinner("AI đang đọc..."):
                        anl = run_gemini(gemini_tk, r['description'], r['author_name'])
                        db.update_ai(r['video_id'], anl); st.rerun()
                
                try: st.video(r['video_url'])
                except: st.warning("Không thể phát video.")
                
                q = urllib.parse.quote(r['description'][:50])
                st.markdown(f"**🛒 Nguồn:** [🔎 Amazon](https://www.amazon.com/s?k={q}) | [🛍️ AliExpress](https://www.aliexpress.com/wholesale?SearchText={q})")
                if r['ai_analysis']: st.info(r['ai_analysis'])

    with t1: render(df_all)
    with t2: render(df_saved, True)
    with t3:
        if not df_all.empty:
            try: st.plotly_chart(px.scatter(df_all, x='safe_followers', y='velocity_value', size='views', color='ratio', log_x=True, title="Rađa Viral"), use_container_width=True)
            except: st.warning("Dữ liệu chưa đủ vẽ biểu đồ.")
        else: st.info("Chưa có dữ liệu.")
else:
    st.info("👋 Chào bạn! Hãy nhập Token và bấm QUÉT để bắt đầu.")

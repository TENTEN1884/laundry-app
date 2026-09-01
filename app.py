import streamlit as st
import requests
import time
from urllib.parse import urlparse
from email.header import Header
from datetime import datetime, timedelta, timezone

# ── 1. [본인 정보 입력] Supabase 설정 ──────────────────────────────
RAW_SUPABASE_URL = "https://drgiuzphovgqmelckjey.supabase.co"
RAW_SUPABASE_KEY = "sb_publishable_DiWIz_z7TkqWELKFdwEuqQ_H_1ME8pP"

_parsed = urlparse(RAW_SUPABASE_URL.strip())
if _parsed.scheme and _parsed.netloc:
    SUPABASE_URL = f"{_parsed.scheme}://{_parsed.netloc}"
else:
    SUPABASE_URL = RAW_SUPABASE_URL.strip().rstrip("/")

SUPABASE_KEY = RAW_SUPABASE_KEY.strip()

CHANNEL = "laundry-myhome-alarm-101"
NTFY_URL = f"https://ntfy.sh/{CHANNEL}"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# ── 2. Supabase 상태 관리 함수 ────────────────────────────────────
def load_state():
    try:
        url = f"{SUPABASE_URL}/rest/v1/laundry_state"
        params = {"id": "eq.1", "select": "*"}
        res = requests.get(url, headers=SUPABASE_HEADERS, params=params, timeout=5)
        
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                row = data[0]
                end_time = datetime.fromisoformat(row["end_time"]) if row.get("end_time") else None
                return {
                    "is_running": bool(row.get("is_running", False)),
                    "end_time": end_time,
                    "notified": bool(row.get("notified", False))
                }
            elif len(data) == 0:
                requests.post(url, headers=SUPABASE_HEADERS, json={"id": 1, "is_running": False}, timeout=5)
        else:
            st.error(f"Supabase 오류: {res.text}")
    except Exception as e:
        st.error(f"연결 오류: {e}")
    return {"is_running": False, "end_time": None, "notified": False}

def save_state(state):
    try:
        url = f"{SUPABASE_URL}/rest/v1/laundry_state"
        params = {"id": "eq.1"}
        end_time_str = state["end_time"].isoformat() if isinstance(state.get("end_time"), datetime) else None
        payload = {
            "is_running": state["is_running"],
            "end_time": end_time_str,
            "notified": state["notified"],
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        requests.patch(url, headers=SUPABASE_HEADERS, params=params, json=payload, timeout=5)
    except Exception as e:
        st.error(f"저장 오류: {e}")

def send_ntfy_notification(ntfy_url, title, message):
    try:
        encoded_title = Header(title, "utf-8").encode()
        requests.post(ntfy_url, data=message.encode("utf-8"), headers={"Title": encoded_title, "Priority": "high", "Tags": "washing_machine"}, timeout=5)
    except:
        pass

# ── 3. UI 화면 렌더링 ─────────────────────────────────────────────
st.set_page_config(page_title="세탁실 현황", layout="centered")

state = load_state()

st.title("🧺 세탁기 사용 현황")

now = datetime.now(timezone.utc) if state["end_time"] and state["end_time"].tzinfo else datetime.now()

if state["is_running"] and state["end_time"]:
    remaining_seconds = (state["end_time"] - now).total_seconds()
    if remaining_seconds > 0:
        remaining_minutes = int(remaining_seconds // 60)
        remaining_secs = int(remaining_seconds % 60)

        st.error("🔴 현재 세탁기가 작동 중입니다!")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("남은 시간", f"{remaining_minutes}분 {remaining_secs}초")
        with col2:
            st.metric("완료 예정", state["end_time"].strftime("%H:%M"))
    else:
        st.warning("🟡 세탁이 완료되었습니다! 빨래를 수거해주세요.")
        if not state["notified"]:
            send_ntfy_notification(NTFY_URL, "🧺 세탁 완료!", "빨래가 끝났습니다. 세탁물을 수거해 주세요!")
            state["notified"] = True
            save_state(state)
else:
    st.success("🟢 현재 사용 가능합니다. 비어있어요!")

st.divider()

if not state["is_running"]:
    st.subheader("새 세탁 시작")
    duration = st.number_input("소요 시간(분)을 입력하세요", min_value=5, max_value=180, value=45, step=5)
    if st.button("세탁 시작하기 🚀", type="primary", use_container_width=True):
        new_state = {
            "is_running": True,
            "end_time": datetime.now(timezone.utc) + timedelta(minutes=int(duration)),
            "notified": False,
        }
        save_state(new_state)
        st.rerun()
else:
    if st.button("✅ 빨래 수거 완료 (세탁기 비우기)", use_container_width=True):
        save_state({"is_running": False, "end_time": None, "notified": False})
        st.rerun()

# ── 4. 안정적인 네이티브 자동 새로고침 ─────────────────────────────
# 외부 부품 대신 파이썬 기본 기능을 사용해 에러를 원천 차단합니다.
if state["is_running"]:
    time.sleep(5)
    st.rerun()
else:
    time.sleep(15)
    st.rerun()

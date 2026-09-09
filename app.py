import streamlit as st
import requests
import time
from urllib.parse import urlparse
from email.header import Header
from datetime import datetime, timedelta, timezone

# ── 1. Streamlit 비밀 금고(Secrets)에서 정보 가져오기 ──────────────
RAW_SUPABASE_URL = st.secrets["SUPABASE_URL"]
RAW_SUPABASE_KEY = st.secrets["SUPABASE_KEY"]

_parsed = urlparse(RAW_SUPABASE_URL.strip())
if _parsed.scheme and _parsed.netloc:
    SUPABASE_URL = f"{_parsed.scheme}://{_parsed.netloc}"
else:
    SUPABASE_URL = RAW_SUPABASE_URL.strip().rstrip("/")

SUPABASE_KEY = RAW_SUPABASE_KEY.strip()

CHANNEL = "laundry-myhome-alarm-101"
NTFY_URL = f"https://ntfy.sh/{CHANNEL}"

# 한국/일본 표준시 (UTC+9) - 완료 예정 시각을 로컬 시간으로 보여주기 위함
KST = timezone(timedelta(hours=9))

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# 배포 환경에서는 첫 요청이 느릴 때가 있어(콜드 스타트 등), 타임아웃을 넉넉히 주고
# 실패 시 한 번 더 재시도해서 일시적인 "연결 오류"를 줄인다.
def request_with_retry(method, url, retries=2, **kwargs):
    kwargs.setdefault("timeout", 10)
    last_error = None
    for attempt in range(retries):
        try:
            return requests.request(method, url, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                time.sleep(1)
    raise last_error

# ── 2. Supabase 상태 관리 함수 ────────────────────────────────────
def load_state():
    try:
        url = f"{SUPABASE_URL}/rest/v1/laundry_state"
        params = {"id": "eq.1", "select": "*"}
        res = request_with_retry("GET", url, headers=SUPABASE_HEADERS, params=params)

        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                row = data[0]
                end_time = datetime.fromisoformat(row["end_time"]) if row.get("end_time") else None
                return {
                    "is_running": bool(row.get("is_running", False)),
                    "end_time": end_time,
                    "notified": bool(row.get("notified", False)),
                    "pin": row.get("pin"),
                    "room_number": row.get("room_number"),
                }
            elif len(data) == 0:
                request_with_retry("POST", url, headers=SUPABASE_HEADERS, json={"id": 1, "is_running": False})
        else:
            st.error(f"Supabase 오류: {res.text}")
    except Exception as e:
        st.error(f"연결 오류: {e}")
    return {"is_running": False, "end_time": None, "notified": False, "pin": None, "room_number": None}

def save_state(state):
    try:
        url = f"{SUPABASE_URL}/rest/v1/laundry_state"
        params = {"id": "eq.1"}
        end_time_str = state["end_time"].isoformat() if isinstance(state.get("end_time"), datetime) else None
        payload = {
            "is_running": state["is_running"],
            "end_time": end_time_str,
            "notified": state["notified"],
            "pin": state.get("pin"),
            "room_number": state.get("room_number"),
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        request_with_retry("PATCH", url, headers=SUPABASE_HEADERS, params=params, json=payload)
    except Exception as e:
        st.error(f"저장 오류: {e}")

def send_ntfy_notification(ntfy_url, title, message):
    try:
        encoded_title = Header(title, "utf-8").encode()
        requests.post(ntfy_url, data=message.encode("utf-8"), headers={"Title": encoded_title, "Priority": "high", "Tags": "washing_machine"}, timeout=5)
    except:
        pass

# ── 방문/클릭 통계 (포트폴리오용 사용 데이터) ──────────────────────
def log_event(event_type):
    try:
        url = f"{SUPABASE_URL}/rest/v1/analytics_events"
        requests.post(url, headers=SUPABASE_HEADERS, json={"event_type": event_type}, timeout=5)
    except Exception:
        pass

# ── 3. UI 화면 렌더링 ─────────────────────────────────────────────
st.set_page_config(page_title="세탁실 현황", layout="centered")

# 화면이 자동으로 자주 새로고침되면서 이전 화면 요소가 옅게 남았다가 사라지는
# 전환 애니메이션(잔상 현상)이 보일 수 있어, 관련 트랜지션/애니메이션을 꺼서
# 화면이 바로바로 전환되도록 한다.
st.markdown(
    """
    <style>
    [data-testid="stAppViewContainer"] * {
        transition: none !important;
        animation: none !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if "visit_logged" not in st.session_state:
    log_event("page_view")
    st.session_state["visit_logged"] = True

state = load_state()

st.title("🧺 세탁기 사용 현황")

now = datetime.now(timezone.utc) if state["end_time"] and state["end_time"].tzinfo else datetime.now()

if state["is_running"] and state["end_time"]:
    remaining_seconds = (state["end_time"] - now).total_seconds()
    if remaining_seconds > 0:
        remaining_minutes = int(remaining_seconds // 60)
        remaining_secs = int(remaining_seconds % 60)

        end_time_local = state["end_time"].astimezone(KST) if state["end_time"].tzinfo else state["end_time"]
        room_number = state.get("room_number")

        if room_number:
            st.error(f"🔴 {room_number}호 세탁물이 작동 중입니다!")
        else:
            st.error("🔴 현재 세탁기가 작동 중입니다!")
        col1, col2 = st.columns(2)
        with col1:
            st.metric("남은 시간", f"{remaining_minutes}분 {remaining_secs}초")
        with col2:
            st.metric("완료 예정 시각 (24시간제)", end_time_local.strftime("%H:%M"))
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
    with st.form("start_form"):
        room_number = st.text_input("방 번호를 입력하세요 (예: 2008)")
        duration = st.number_input("소요 시간(분)을 입력하세요", min_value=5, max_value=180, value=45, step=5)
        pin = st.text_input("본인확인용 비밀번호 4자리를 입력하세요", max_chars=4, type="password")
        submitted = st.form_submit_button("세탁 시작하기 🚀", type="primary", use_container_width=True)

        if submitted:
            if not room_number.strip():
                st.error("방 번호를 입력해주세요.")
            elif not (pin.isdigit() and len(pin) == 4):
                st.error("비밀번호는 숫자 4자리로 입력해주세요.")
            else:
                new_state = {
                    "is_running": True,
                    "end_time": datetime.now(timezone.utc) + timedelta(minutes=int(duration)),
                    "notified": False,
                    "pin": pin,
                    "room_number": room_number.strip(),
                }
                save_state(new_state)
                log_event("start_wash")
                st.rerun()
else:
    st.subheader("빨래 수거 완료 처리")
    if state.get("room_number"):
        st.caption(f"현재 세탁 중: {state['room_number']}호")
    with st.form("complete_form"):
        input_pin = st.text_input("본인확인용 비밀번호 4자리를 입력하세요", max_chars=4, type="password")
        confirmed = st.form_submit_button("✅ 빨래 수거 완료 (세탁기 비우기)", use_container_width=True)

        if confirmed:
            if input_pin == state.get("pin"):
                save_state({"is_running": False, "end_time": None, "notified": False, "pin": None, "room_number": None})
                log_event("complete_wash")
                st.rerun()
            else:
                st.error("비밀번호가 일치하지 않습니다.")

st.divider()
st.caption("🛠️ 오류가 발생하거나 앱이 작동하지 않을 때: [문의 채팅방](https://open.kakao.com/o/sDMzKNMi)")

# ── 4. 안정적인 네이티브 자동 새로고침 ─────────────────────────────
if state["is_running"]:
    time.sleep(5)
    st.rerun()
else:
    time.sleep(15)
    st.rerun()

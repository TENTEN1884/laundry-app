import streamlit as st
from streamlit_autorefresh import st_autorefresh
import requests
from urllib.parse import urlparse
from email.header import Header
from datetime import datetime, timedelta, timezone

# ── 1. 일반 Python 실행 시 Streamlit 자동 구동 ────────────────────
if __name__ == "__main__":
    if not os.environ.get("STREAMLIT_RUN_ACTIVE"):
        os.environ["STREAMLIT_RUN_ACTIVE"] = "1"
        from streamlit.web import cli as stcli
        sys.argv = [
            "streamlit", "run", os.path.abspath(__file__),
            "--server.headless", "false",
            "--server.address", "127.0.0.1"
        ]
        sys.exit(stcli.main())

# ── 2. Streamlit 앱 모듈 로드 ────────────────────────────────────
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import requests

# ── 3. [본인 정보 입력] Supabase 설정 ──────────────────────────────
# 본인의 Supabase Project URL과 anon key를 아래 따옴표 안에 넣어주세요.
RAW_SUPABASE_URL = "https://drgiuzphovgqmelckjey.supabase.co"
RAW_SUPABASE_KEY = "sb_publishable_DiWIz_z7TkqWELKFdwEuqQ_H_1ME8pP"

_parsed = urlparse(RAW_SUPABASE_URL.strip())
if _parsed.scheme and _parsed.netloc:
    SUPABASE_URL = f"{_parsed.scheme}://{_parsed.netloc}"
else:
    SUPABASE_URL = RAW_SUPABASE_URL.strip().rstrip("/")

SUPABASE_KEY = RAW_SUPABASE_KEY.strip()

# 고유 푸시 알림 채널명 (원하는 이름으로 수정 가능)
CHANNEL = "laundry-myhome-alarm-101"
NTFY_URL = f"https://ntfy.sh/{CHANNEL}"

SUPABASE_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

# ── 5. Supabase 상태 관리 함수 ────────────────────────────────────
def load_state():
    try:
        url = f"{SUPABASE_URL}/rest/v1/laundry_state"
        params = {"id": "eq.1", "select": "*"}
        res = requests.get(url, headers=SUPABASE_HEADERS, params=params, timeout=5)
        
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                row = data[0]
                end_time = None
                if row.get("end_time"):
                    end_time = datetime.fromisoformat(row["end_time"])
                return {
                    "is_running": bool(row.get("is_running", False)),
                    "end_time": end_time,
                    "notified": bool(row.get("notified", False))
                }
            elif len(data) == 0:
                requests.post(url, headers=SUPABASE_HEADERS, json={"id": 1, "is_running": False}, timeout=5)
        else:
            st.error(f"Supabase 연동 오류 ({res.status_code}): {res.text}")
    except Exception as e:
        st.error(f"Supabase 연결 예외: {e}")
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
        res = requests.patch(url, headers=SUPABASE_HEADERS, params=params, json=payload, timeout=5)
        if res.status_code not in (200, 204):
            st.error(f"Supabase 저장 오류 ({res.status_code}): {res.text}")
    except Exception as e:
        st.error(f"Supabase 저장 예외: {e}")

# ── 6. ntfy 알림 전송 함수 ────────────────────────────────────────
def send_ntfy_notification(ntfy_url, title, message):
    try:
        encoded_title = Header(title, "utf-8").encode()
        requests.post(
            ntfy_url,
            data=message.encode("utf-8"),
            headers={
                "Title": encoded_title,
                "Priority": "high",
                "Tags": "washing_machine",
            },
            timeout=5,
        )
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════
#  UI 화면 렌더링
# ══════════════════════════════════════════════════════════════════
st.set_page_config(page_title="세탁실 현황", layout="centered", initial_sidebar_state="collapsed")

state = load_state()

# 실시간 동기화
if state["is_running"]:
    st_autorefresh(interval=5000, key="autorefresh_running")
else:
    st_autorefresh(interval=12000, key="autorefresh_idle")

st.title("🧺 세탁기 사용 현황")

now = datetime.now(timezone.utc) if state["end_time"] and state["end_time"].tzinfo else datetime.now()
# ── 상태 표시 섹션 ────────────────────────────────────────────────
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
            st.metric("완료 예정 시각", state["end_time"].strftime("%H:%M"))

    else:
        st.warning("🟡 세탁이 완료되었습니다! 빨래를 수거해주세요.")

        if not state["notified"]:
            send_ntfy_notification(NTFY_URL, "🧺 세탁 완료!", "빨래가 끝났습니다. 세탁물을 수거해 주세요!")
            state["notified"] = True
            save_state(state)

else:
    st.success("🟢 현재 사용 가능합니다. 비어있어요!")

st.divider()

# ── 조작 버튼 섹션 ────────────────────────────────────────────────
if not state["is_running"]:
    st.subheader("새 세탁 시작")
    duration = st.number_input(
        "소요 시간(분)을 입력하세요",
        min_value=5, max_value=180, value=45, step=5
    )

    if st.button("세탁 시작하기 🚀", type="primary", use_container_width=True):
        new_state = {
            "is_running": True,
            "end_time": datetime.now(timezone.utc) + timedelta(minutes=int(duration)),
            "notified": False,
        }
        save_state(new_state)
        send_ntfy_notification(
            NTFY_URL,
            "🧺 세탁 시작",
            f"세탁이 시작되었습니다. {int(duration)}분 뒤 완료 알림을 보내드립니다."
        )
        st.rerun()

else:
    if st.button("✅ 빨래 수거 완료 (세탁기 비우기)", use_container_width=True):
        save_state({"is_running": False, "end_time": None, "notified": False})
        st.rerun()

st.divider()
st.caption(f"🔔 알림 수신: 스마트폰 'ntfy' 앱에서 `{CHANNEL}` 채널 구독")
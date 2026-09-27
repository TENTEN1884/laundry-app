import streamlit as st
import requests
import time
import json
import hashlib
import smtplib
import secrets as pysecrets
from urllib.parse import urlparse
from email.header import Header
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

# ── 1. Streamlit 비밀 금고(Secrets)에서 정보 가져오기 ──────────────
RAW_SUPABASE_URL = st.secrets["SUPABASE_URL"]
RAW_SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")
KAKAO_REST_API_KEY = st.secrets.get("KAKAO_REST_API_KEY", "")
KAKAO_REDIRECT_URI = st.secrets.get("KAKAO_REDIRECT_URI", "")
KAKAO_CLIENT_SECRET = st.secrets.get("KAKAO_CLIENT_SECRET", "")
LAUNDRY_STATE_ID = int(st.secrets.get("LAUNDRY_STATE_ID", 1))
EMAIL_SMTP_ADDRESS = st.secrets.get("EMAIL_SMTP_ADDRESS", "")
EMAIL_SMTP_APP_PASSWORD = st.secrets.get("EMAIL_SMTP_APP_PASSWORD", "")
EMAIL_SMTP_HOST = st.secrets.get("EMAIL_SMTP_HOST", "smtp.gmail.com")
EMAIL_SMTP_PORT = int(st.secrets.get("EMAIL_SMTP_PORT", 587))
EMAIL_SMTP_LOGIN = st.secrets.get("EMAIL_SMTP_LOGIN", "") or EMAIL_SMTP_ADDRESS

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
        params = {"id": f"eq.{LAUNDRY_STATE_ID}", "select": "*"}
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
                    "auto_reset_at": row.get("auto_reset_at"),
                    "auto_reset_room": row.get("auto_reset_room"),
                }
            elif len(data) == 0:
                request_with_retry("POST", url, headers=SUPABASE_HEADERS, json={"id": LAUNDRY_STATE_ID, "is_running": False})
        else:
            st.error(f"Supabase 오류: {res.text}")
    except Exception as e:
        st.error(f"연결 오류: {e}")
    return {"is_running": False, "end_time": None, "notified": False, "pin": None, "room_number": None, "auto_reset_at": None, "auto_reset_room": None}

def save_state(state):
    try:
        url = f"{SUPABASE_URL}/rest/v1/laundry_state"
        params = {"id": f"eq.{LAUNDRY_STATE_ID}"}
        end_time_str = state["end_time"].isoformat() if isinstance(state.get("end_time"), datetime) else None
        payload = {
            "is_running": state["is_running"],
            "end_time": end_time_str,
            "notified": state["notified"],
            "pin": state.get("pin"),
            "room_number": state.get("room_number"),
            "auto_reset_at": state.get("auto_reset_at"),
            "auto_reset_room": state.get("auto_reset_room"),
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

def send_email(to_email, subject, body):
    if not (EMAIL_SMTP_ADDRESS and EMAIL_SMTP_APP_PASSWORD):
        return
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = EMAIL_SMTP_ADDRESS
        msg["To"] = to_email
        with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(EMAIL_SMTP_LOGIN, EMAIL_SMTP_APP_PASSWORD)
            server.sendmail(EMAIL_SMTP_ADDRESS, [to_email], msg.as_string())
    except Exception:
        pass

# ── 비밀번호 해시 (이메일 회원가입용) ────────────────────────────────
def hash_password(password, salt=None):
    if salt is None:
        salt = pysecrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
    return digest, salt

def verify_password(password, salt, expected_hash):
    digest, _ = hash_password(password, salt)
    return digest == expected_hash

# ── 방문/클릭 통계 (포트폴리오용 사용 데이터) ──────────────────────
def log_event(event_type):
    try:
        url = f"{SUPABASE_URL}/rest/v1/analytics_events"
        requests.post(url, headers=SUPABASE_HEADERS, json={"event_type": event_type}, timeout=5)
    except Exception:
        pass

# ── 카카오 로그인 & 카카오톡 알림 ──────────────────────────────────
def kakao_login_url():
    return (
        "https://kauth.kakao.com/oauth/authorize"
        f"?client_id={KAKAO_REST_API_KEY}"
        f"&redirect_uri={KAKAO_REDIRECT_URI}"
        "&response_type=code"
        "&scope=talk_message"
    )

def kakao_exchange_code(code):
    try:
        payload = {
            "grant_type": "authorization_code",
            "client_id": KAKAO_REST_API_KEY,
            "redirect_uri": KAKAO_REDIRECT_URI,
            "code": code,
        }
        if KAKAO_CLIENT_SECRET:
            payload["client_secret"] = KAKAO_CLIENT_SECRET
        res = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

def kakao_refresh_access_token(refresh_token):
    try:
        payload = {
            "grant_type": "refresh_token",
            "client_id": KAKAO_REST_API_KEY,
            "refresh_token": refresh_token,
        }
        if KAKAO_CLIENT_SECRET:
            payload["client_secret"] = KAKAO_CLIENT_SECRET
        res = requests.post(
            "https://kauth.kakao.com/oauth/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return None

def kakao_get_user_info(access_token):
    try:
        res = requests.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if res.status_code == 200:
            data = res.json()
            kakao_id = data.get("id")
            nickname = (data.get("properties") or {}).get("nickname")
            return kakao_id, nickname
    except Exception:
        pass
    return None, None

def save_kakao_user(kakao_id, access_token, refresh_token, expires_in, nickname=None):
    expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
    try:
        url = f"{SUPABASE_URL}/rest/v1/kakao_users"
        payload = {
            "kakao_id": kakao_id,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_expires_at": expires_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if nickname:
            payload["nickname"] = nickname
        request_with_retry(
            "POST", url,
            headers={**SUPABASE_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "kakao_id"},
            json=payload,
        )
    except Exception:
        pass

def create_app_session(user_type, user_id):
    token = pysecrets.token_urlsafe(32)
    try:
        url = f"{SUPABASE_URL}/rest/v1/app_sessions"
        request_with_retry("POST", url, headers=SUPABASE_HEADERS, json={"session_token": token, "user_type": user_type, "user_id": str(user_id)})
        return token
    except Exception:
        return None

def get_session_user(session_token):
    try:
        url = f"{SUPABASE_URL}/rest/v1/app_sessions"
        params = {"session_token": f"eq.{session_token}", "select": "user_type,user_id"}
        res = request_with_retry("GET", url, headers=SUPABASE_HEADERS, params=params)
        if res.status_code == 200:
            data = res.json()
            if data:
                return data[0]["user_type"], data[0]["user_id"]
    except Exception:
        pass
    return None, None

def delete_app_session(session_token):
    try:
        url = f"{SUPABASE_URL}/rest/v1/app_sessions"
        request_with_retry("DELETE", url, headers=SUPABASE_HEADERS, params={"session_token": f"eq.{session_token}"})
    except Exception:
        pass

def get_nickname(user_type, user_id):
    try:
        if user_type == "kakao":
            url = f"{SUPABASE_URL}/rest/v1/kakao_users"
            params = {"kakao_id": f"eq.{user_id}", "select": "nickname"}
        else:
            url = f"{SUPABASE_URL}/rest/v1/email_users"
            params = {"email": f"eq.{user_id}", "select": "nickname"}
        res = request_with_retry("GET", url, headers=SUPABASE_HEADERS, params=params)
        if res.status_code == 200:
            data = res.json()
            if data and data[0].get("nickname"):
                return data[0]["nickname"]
    except Exception:
        pass
    return "회원"

# ── 이메일 회원가입 / 로그인 ──────────────────────────────────────
def create_email_user(email, password, nickname):
    email = email.strip().lower()
    password_hash, salt = hash_password(password)
    try:
        url = f"{SUPABASE_URL}/rest/v1/email_users"
        res = request_with_retry(
            "POST", url,
            headers={**SUPABASE_HEADERS, "Prefer": "return=representation"},
            json={
                "email": email,
                "password_hash": password_hash,
                "password_salt": salt,
                "nickname": nickname.strip(),
            },
        )
        if res.status_code in (200, 201):
            return True, None
        if res.status_code == 409:
            return False, "이미 가입된 이메일이에요."
        return False, "가입 중 오류가 발생했어요."
    except Exception:
        return False, "가입 중 오류가 발생했어요."

def get_email_user(email):
    email = email.strip().lower()
    try:
        url = f"{SUPABASE_URL}/rest/v1/email_users"
        params = {"email": f"eq.{email}", "select": "*"}
        res = request_with_retry("GET", url, headers=SUPABASE_HEADERS, params=params)
        if res.status_code == 200:
            data = res.json()
            if data:
                return data[0]
    except Exception:
        pass
    return None

def get_all_email_users():
    try:
        url = f"{SUPABASE_URL}/rest/v1/email_users"
        res = request_with_retry("GET", url, headers=SUPABASE_HEADERS, params={"select": "*"})
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return []

def notify_all_email_users(subject, body):
    for user in get_all_email_users():
        send_email(user["email"], subject, body)

def get_all_kakao_users():
    try:
        url = f"{SUPABASE_URL}/rest/v1/kakao_users"
        res = request_with_retry("GET", url, headers=SUPABASE_HEADERS, params={"select": "*"})
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return []

def send_kakao_talk_message(kakao_user, text):
    access_token = kakao_user["access_token"]
    expires_at = datetime.fromisoformat(kakao_user["token_expires_at"])
    if datetime.now(timezone.utc) >= expires_at:
        refreshed = kakao_refresh_access_token(kakao_user["refresh_token"])
        if not refreshed:
            return
        access_token = refreshed["access_token"]
        save_kakao_user(
            kakao_user["kakao_id"],
            access_token,
            refreshed.get("refresh_token", kakao_user["refresh_token"]),
            refreshed.get("expires_in", 21599),
        )
    try:
        template = json.dumps({
            "object_type": "text",
            "text": text,
            "link": {"web_url": KAKAO_REDIRECT_URI, "mobile_web_url": KAKAO_REDIRECT_URI},
        })
        requests.post(
            "https://kapi.kakao.com/v2/api/talk/memo/default/send",
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": template},
            timeout=10,
        )
    except Exception:
        pass

def notify_all_kakao_users(text):
    for user in get_all_kakao_users():
        send_kakao_talk_message(user, text)

# ── 3. UI 화면 렌더링 ─────────────────────────────────────────────
st.set_page_config(page_title="세탁실 현황 2", layout="centered")

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

# ── 로그인 처리 (카카오 / 이메일 통합, 자동 로그인 유지 포함) ──────────
LOCAL_STORAGE_KEY = "laundry_session"

if "user_type" not in st.session_state:
    st.session_state["user_type"] = None
    st.session_state["user_id"] = None
    st.session_state["nickname"] = None

query_params = st.query_params

pending_local_storage_token = st.session_state.pop("pending_ls_token", None)

if "code" in query_params and KAKAO_REST_API_KEY:
    token_data = kakao_exchange_code(query_params["code"])
    if token_data and token_data.get("access_token"):
        kakao_id, kakao_nickname = kakao_get_user_info(token_data["access_token"])
        if kakao_id:
            kakao_nickname = kakao_nickname or "카카오 사용자"
            save_kakao_user(
                kakao_id,
                token_data["access_token"],
                token_data.get("refresh_token", ""),
                token_data.get("expires_in", 21599),
                kakao_nickname,
            )
            session_token = create_app_session("kakao", kakao_id)
            st.session_state["user_type"] = "kakao"
            st.session_state["user_id"] = kakao_id
            st.session_state["nickname"] = kakao_nickname
            st.session_state["session_token"] = session_token
            pending_local_storage_token = session_token
            log_event("kakao_login")
    st.query_params.clear()
elif "session" in query_params:
    user_type, user_id = get_session_user(query_params["session"])
    if user_type:
        st.session_state["user_type"] = user_type
        st.session_state["user_id"] = user_id
        st.session_state["nickname"] = get_nickname(user_type, user_id)
        st.session_state["session_token"] = query_params["session"]
elif st.session_state.get("user_type") is None and not st.session_state.get("session_checked"):
    st.session_state["session_checked"] = True
    st.components.v1.html(
        f"""
        <script>
        try {{
            const t = localStorage.getItem("{LOCAL_STORAGE_KEY}");
            if (t) {{
                const url = new URL(window.top.location.href);
                url.searchParams.set("session", t);
                window.top.location.href = url.toString();
            }}
        }} catch (e) {{}}
        </script>
        """,
        height=0,
    )

if pending_local_storage_token:
    st.components.v1.html(
        f"""
        <script>
        try {{ localStorage.setItem("{LOCAL_STORAGE_KEY}", "{pending_local_storage_token}"); }} catch (e) {{}}
        </script>
        """,
        height=0,
    )

state = load_state()

title_col, auth_col = st.columns([4, 2])
with title_col:
    st.title("🧺 세탁기 사용 현황 2")

is_logged_in = bool(st.session_state.get("user_type"))

with auth_col:
    st.write("")
    if is_logged_in:
        st.markdown(f"**{st.session_state.get('nickname') or '회원'}**님")
        if st.button("로그아웃", key="logout_btn", use_container_width=True):
            token = st.session_state.get("session_token")
            if token:
                delete_app_session(token)
            st.session_state["user_type"] = None
            st.session_state["user_id"] = None
            st.session_state["nickname"] = None
            st.session_state["session_checked"] = True
            st.components.v1.html(
                f"""<script>try {{ localStorage.removeItem("{LOCAL_STORAGE_KEY}"); }} catch (e) {{}}</script>""",
                height=0,
            )
            st.rerun()
    else:
        if st.button("회원가입 / 로그인", key="open_auth_btn", use_container_width=True):
            st.session_state["show_auth_panel"] = not st.session_state.get("show_auth_panel", False)

if not is_logged_in and st.session_state.get("show_auth_panel"):
    with st.container(border=True):
        tab_kakao, tab_email = st.tabs(["💬 카카오로 계속하기", "📧 이메일로 계속하기"])

        with tab_kakao:
            if KAKAO_REST_API_KEY:
                st.markdown(f"[💬 카카오로 회원가입 / 로그인]({kakao_login_url()})")
                st.caption("카카오로 회원가입하면 완료 시 카카오톡 '나에게 보내기'로 알려드려요. (푸시 알림은 울리지 않을 수 있어요)")
            else:
                st.caption("카카오 로그인이 아직 설정되지 않았어요.")

        with tab_email:
            st.caption("메일로 회원가입하면 완료 시 이메일로 알려드려요.")
            email_mode = st.radio("이메일 모드", ["로그인", "회원가입"], horizontal=True, key="email_mode", label_visibility="collapsed")

            if email_mode == "회원가입":
                with st.form("email_signup_form"):
                    su_nickname = st.text_input("닉네임")
                    su_email = st.text_input("이메일")
                    su_password = st.text_input("비밀번호 (4자 이상)", type="password")
                    su_submit = st.form_submit_button("회원가입", use_container_width=True)

                    if su_submit:
                        if not su_nickname.strip():
                            st.error("닉네임을 입력해주세요.")
                        elif "@" not in su_email:
                            st.error("올바른 이메일을 입력해주세요.")
                        elif len(su_password) < 4:
                            st.error("비밀번호는 4자 이상으로 입력해주세요.")
                        else:
                            ok, err = create_email_user(su_email, su_password, su_nickname)
                            if ok:
                                email_norm = su_email.strip().lower()
                                session_token = create_app_session("email", email_norm)
                                st.session_state["user_type"] = "email"
                                st.session_state["user_id"] = email_norm
                                st.session_state["nickname"] = su_nickname.strip()
                                st.session_state["session_token"] = session_token
                                st.session_state["pending_ls_token"] = session_token
                                st.session_state["show_auth_panel"] = False
                                log_event("email_signup")
                                st.rerun()
                            else:
                                st.error(err)
            else:
                with st.form("email_login_form"):
                    li_email = st.text_input("이메일")
                    li_password = st.text_input("비밀번호", type="password")
                    li_submit = st.form_submit_button("로그인", use_container_width=True)

                    if li_submit:
                        user = get_email_user(li_email)
                        if user and verify_password(li_password, user["password_salt"], user["password_hash"]):
                            session_token = create_app_session("email", user["email"])
                            st.session_state["user_type"] = "email"
                            st.session_state["user_id"] = user["email"]
                            st.session_state["nickname"] = user.get("nickname") or "회원"
                            st.session_state["session_token"] = session_token
                            st.session_state["pending_ls_token"] = session_token
                            st.session_state["show_auth_panel"] = False
                            log_event("email_login")
                            st.rerun()
                        else:
                            st.error("이메일 또는 비밀번호가 일치하지 않습니다.")

now = datetime.now(timezone.utc) if state["end_time"] and state["end_time"].tzinfo else datetime.now()

# 시간 종료 후 이 시간(초) 동안 아무도 수거 완료 처리를 하지 않으면 자동으로 초기화한다.
GRACE_SECONDS = 30 * 60

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
    elif -remaining_seconds >= GRACE_SECONDS:
        save_state({
            "is_running": False,
            "end_time": None,
            "notified": False,
            "pin": None,
            "room_number": None,
            "auto_reset_at": datetime.now(timezone.utc).isoformat(),
            "auto_reset_room": state.get("room_number"),
        })
        log_event("auto_reset")
        st.session_state.pop("my_pin", None)
        st.rerun()
    else:
        st.warning("🟡 세탁이 완료되었습니다! 빨래를 수거해주세요.")
        if not state["notified"]:
            send_ntfy_notification(NTFY_URL, "🧺 세탁 완료!", "빨래가 끝났습니다. 세탁물을 수거해 주세요!")
            room_number = state.get("room_number")
            notify_text = f"{room_number}호 빨래가 끝났습니다! 세탁물을 수거해 주세요." if room_number else "빨래가 끝났습니다! 세탁물을 수거해 주세요."
            notify_all_kakao_users(notify_text)
            notify_all_email_users("🧺 세탁 완료 알림", notify_text)
            state["notified"] = True
            save_state(state)
else:
    if state.get("auto_reset_room"):
        st.warning(
            f"⚠️ {state['auto_reset_room']}호 세탁물이 초기화되었습니다. "
            "(시간 초과 후 30분간 미수거 또는 다른 사용자의 조기 종료 처리) "
            "사용 전 실제로 비어있는지 확인해주세요."
        )
    st.success("🟢 현재 사용 가능합니다. 비어있어요!")

st.divider()

if not state["is_running"]:
    st.subheader("새 세탁 시작")
    with st.form("start_form"):
        room_number = st.text_input("방 번호를 입력하세요 (예: 2008)")
        duration = st.number_input("소요 시간(분)을 입력하세요", min_value=5, max_value=180, value=45, step=5)
        pin = None
        if not is_logged_in:
            pin = st.text_input("본인확인용 비밀번호 4자리를 입력하세요", max_chars=4, type="password")
        submitted = st.form_submit_button("세탁 시작하기 🚀", type="primary", use_container_width=True)

        if submitted:
            if not room_number.strip():
                st.error("방 번호를 입력해주세요.")
            elif not is_logged_in and not (pin and pin.isdigit() and len(pin) == 4):
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
                if pin:
                    st.session_state["my_pin"] = pin
                log_event("start_wash")
                st.rerun()
else:
    st.subheader("빨래 수거 완료 처리")
    if state.get("room_number"):
        st.caption(f"현재 세탁 중: {state['room_number']}호")

    if is_logged_in:
        st.caption("✓ 로그인되어 있어서 비밀번호 없이 완료 처리할 수 있어요.")
        if st.button("✅ 빨래 수거 완료 (세탁기 비우기)", use_container_width=True, key="member_complete"):
            save_state({"is_running": False, "end_time": None, "notified": False, "pin": None, "room_number": None})
            st.session_state.pop("my_pin", None)
            log_event("complete_wash")
            st.rerun()
    elif state.get("pin") and st.session_state.get("my_pin") == state.get("pin"):
        st.caption("✓ 이 브라우저에서 시작한 세탁물이라 비밀번호 없이 완료 처리할 수 있어요.")
        if st.button("✅ 빨래 수거 완료 (세탁기 비우기)", use_container_width=True, key="quick_complete"):
            save_state({"is_running": False, "end_time": None, "notified": False, "pin": None, "room_number": None})
            st.session_state.pop("my_pin", None)
            log_event("complete_wash")
            st.rerun()
    elif not state.get("pin"):
        st.caption("이 세탁물은 로그인한 회원이 시작해서 비밀번호가 없어요. 로그인 후 완료 처리해주세요.")
    else:
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

    with st.expander("🤔 세탁기가 이미 비어있는 것 같나요? (비밀번호 없이 조기 종료)"):
        st.caption("예상 시간이 남아있어도, 세탁기가 실제로 멈춰있고 빨래가 없는 걸 직접 확인하셨다면 아래 버튼을 눌러주세요.")
        if st.button("🔄 조기 종료 처리하고 내가 새로 시작할게요", use_container_width=True, key="early_release_button"):
            save_state({
                "is_running": False,
                "end_time": None,
                "notified": False,
                "pin": None,
                "room_number": None,
                "auto_reset_at": datetime.now(timezone.utc).isoformat(),
                "auto_reset_room": state.get("room_number"),
            })
            log_event("early_release")
            st.session_state.pop("my_pin", None)
            st.rerun()

    with st.expander("⚙️ 관리자"):
        with st.form("admin_reset_form"):
            admin_pw = st.text_input("관리자 비밀번호", type="password")
            admin_confirmed = st.form_submit_button("🚨 강제 종료 (세탁기 비우기)", use_container_width=True)

            if admin_confirmed:
                if ADMIN_PASSWORD and admin_pw == ADMIN_PASSWORD:
                    save_state({"is_running": False, "end_time": None, "notified": False, "pin": None, "room_number": None})
                    log_event("admin_reset")
                    st.rerun()
                else:
                    st.error("관리자 비밀번호가 일치하지 않습니다.")

st.divider()
st.caption("🛠️ 오류가 발생하거나 앱이 작동하지 않을 때: [문의 채팅방](https://open.kakao.com/o/sDMzKNMi)")

# ── 4. 안정적인 네이티브 자동 새로고침 ─────────────────────────────
if state["is_running"]:
    time.sleep(5)
    st.rerun()
else:
    time.sleep(15)
    st.rerun()

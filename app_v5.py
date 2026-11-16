
import math
import requests
import streamlit as st
import folium
import osmnx as ox
from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="חסימות זירה", page_icon="🚓", layout="wide")

st.markdown("""
<style>
html, body, [class*="css"] { direction: rtl; text-align: right; }
.block-container { max-width: 1000px; padding-top: 0.8rem; }
.stButton button { min-height: 48px; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

st.title("🚓 מערכת סיוע לחסימות ובידוד זירה")
st.caption("גרסת ניסוי · כל חסימה מחייבת אימות ואישור מפקד בשטח")

session = requests.Session()
session.headers.update({"User-Agent": "HasimotMVP/1.5"})

ARC_BASE = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer"

def suggest_addresses(searchterm: str):
    term = (searchterm or "").strip()
    if len(term) < 3:
        return []

    try:
        r = session.get(
            f"{ARC_BASE}/suggest",
            params={
                "text": term,
                "f": "json",
                "countryCode": "ISR",
                "maxSuggestions": 8,
            },
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()

        results = []
        for s in data.get("suggestions", []):
            results.append((
                s["text"],
                {
                    "text": s["text"],
                    "magicKey": s.get("magicKey", "")
                }
            ))
        return results
    except Exception:
        return []

st.markdown("### כתובת האירוע")

selected_address = st_searchbox(
    suggest_addresses,
    placeholder="התחל להקליד רחוב, מספר ועיר...",
    label="חיפוש כתובת",
    key="address_search",
    clear_on_submit=False,
    edit_after_submit="option",
    debounce=350,
)

city_fallback = st.text_input(
    "עיר (רק אם לא בחרת הצעה מהרשימה)",
    value="עכו",
    placeholder="לדוגמה: עכו"
)

st.text_input("מדינה", value="ישראל", disabled=True)

incident_type = st.selectbox(
    "סוג אירוע",
    ["נפילת טיל / סכנה", "תאונת דרכים", "שריפה", "אירוע פלילי", "חפץ חשוד", "אחר"]
)

def hav(a,b,c,d):
    R = 6371000
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c-a)
    dl = math.radians(d-b)
    q = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(q))

@st.cache_data(ttl=3600, show_spinner=False)
def geocode_selected(text, magic_key):
    params = {
        "SingleLine": text,
        "magicKey": magic_key,
        "f": "json",
        "countryCode": "ISR",
        "maxLocations": 1,
        "outFields": "Match_addr"
    }
    r = session.get(f"{ARC_BASE}/findAddressCandidates", params=params, timeout=8)
    r.raise_for_status()
    j = r.json()
    cand = j.get("candidates", [])
    if not cand:
        raise RuntimeError("הכתובת שנבחרה לא נמצאה")
    c = cand[0]
    return float(c["location"]["y"]), float(c["location"]["x"]), c.get("address", text)

@st.cache_data(ttl=3600, show_spinner=False)
def geocode_free_text(text, city):
    full = f"{text}, {city}, ישראל"
    params = {
        "SingleLine": full,
        "f": "json",
        "countryCode": "ISR",
        "maxLocations": 1,
        "outFields": "Match_addr"
    }
    r = session.get(f"{ARC_BASE}/findAddressCandidates", params=params, timeout=8)
    r.raise_for_status()
    j = r.json()
    cand = j.get("candidates", [])
    if not cand:
        raise RuntimeError("לא הצלחתי לאתר את הכתובת")
    c = cand[0]
    return float(c["location"]["y"]), float(c["location"]["x"]), c.get("address", full)

@st.cache_resource(show_spinner=False)
def load_graph(lat, lon):
    ox.settings.use_cache = True
    ox.settings.log_console = False
    return ox.graph.graph_from_point(
        (lat, lon),
        dist=1200,
        network_type="drive",
        simplify=True,
        retain_all=False,
        truncate_by_edge=True,
    )

def street_names(G, node):
    names = []
    for _, _, data in G.edges(node, data=True):
        name = data.get("name")
        if isinstance(name, list):
            names.extend(name)
        elif isinstance(name, str):
            names.append(name)

    out = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out[:2]

def make_candidates(G, lat, lon, minr, maxr):
    UG = G.to_undirected()
    out = []

    for n in G.nodes:
        y = G.nodes[n]["y"]
        x = G.nodes[n]["x"]
        d = hav(lat, lon, y, x)
        deg = UG.degree(n)

        if minr <= d <= maxr and deg >= 3:
            out.append((n, d, deg))

    out.sort(key=lambda x: (-x[2], abs(x[1] - (minr + maxr) / 2)))
    return out

def pick_spread(G, cand, max_points, min_sep):
    out = []

    for n, d, deg in cand:
        y = G.nodes[n]["y"]
        x = G.nodes[n]["x"]

        if all(
            hav(y, x, G.nodes[k]["y"], G.nodes[k]["x"]) >= min_sep
            for k, _, __ in out
        ):
            out.append((n, d, deg))

        if len(out) >= max_points:
            break

    return out

def clean(G, items):
    out = []

    for i, (n, d, deg) in enumerate(items, 1):
        names = street_names(G, n)

        out.append({
            "label": " / ".join(names) if names else f"נקודת חסימה {i}",
            "lat": G.nodes[n]["y"],
            "lon": G.nodes[n]["x"],
            "dist": d,
        })

    return out

if "analysis" not in st.session_state:
    st.session_state.analysis = None

if st.button("🔎 נתח זירה והצע חסימות", type="primary", use_container_width=True):
    st.session_state.analysis = None

    try:
        with st.status("מבצע ניתוח...", expanded=True) as status:
            status.write("📍 מאתר כתובת...")

            if isinstance(selected_address, dict):
                lat, lon, display = geocode_selected(
                    selected_address["text"],
                    selected_address.get("magicKey", "")
                )
            else:
                raw = (selected_address or "").strip()
                if not raw:
                    raise RuntimeError("התחל להקליד כתובת ובחר אחת מההצעות")
                lat, lon, display = geocode_free_text(raw, city_fallback)

            status.write("🛣️ טוען רשת כבישים...")
            G = load_graph(round(lat, 6), round(lon, 6))

            status.write("🚓 מחשב נקודות הצבת ניידות...")

            inner = pick_spread(G, make_candidates(G, lat, lon, 100, 220), 4, 70)
            outer = pick_spread(G, make_candidates(G, lat, lon, 280, 550), 4, 115)
            far = pick_spread(G, make_candidates(G, lat, lon, 650, 950), 4, 160)

            rings = {
                1: clean(G, inner),
                2: clean(G, outer),
                3: clean(G, far),
            }

            st.session_state.analysis = {
                "lat": lat,
                "lon": lon,
                "display": display,
                "rings": rings,
                "type": incident_type,
            }

            status.update(label="הניתוח הושלם", state="complete", expanded=False)

    except Exception as e:
        st.error(f"לא הצלחתי להשלים את הניתוח: {e}")
        st.info("אם שירות הכבישים לא מגיב, נסה שוב לאחר כמה שניות.")

a = st.session_state.analysis

RING_NAMES = {
    1: "מעגל פנימי",
    2: "מעגל חיצוני",
    3: "מעגל רחוק",
}

RING_COLORS = {
    1: "#ef4444",
    2: "#f59e0b",
    3: "#eab308",
}

def police_icon(color):
    return folium.DivIcon(
        icon_size=(46, 46),
        icon_anchor=(23, 23),
        html=f"""
        <style>
        @keyframes policeBlue {{
            0%,49% {{opacity:1}}
            50%,100% {{opacity:.15}}
        }}
        @keyframes policeRed {{
            0%,49% {{opacity:.15}}
            50%,100% {{opacity:1}}
        }}
        </style>
        <div style="
            width:46px;height:46px;border-radius:50%;
            background:{color};border:3px solid white;
            box-shadow:0 2px 8px rgba(0,0,0,.45);
            position:relative;display:flex;align-items:center;justify-content:center;
            font-size:26px;">
            🚓
            <span style="
                position:absolute;top:3px;left:13px;width:8px;height:6px;
                background:#1e90ff;border-radius:3px;
                animation:policeBlue .65s infinite;">
            </span>
            <span style="
                position:absolute;top:3px;right:13px;width:8px;height:6px;
                background:#ff2b2b;border-radius:3px;
                animation:policeRed .65s infinite;">
            </span>
        </div>
        """
    )

if a:
    st.success(f"📍 {a['display']}")
    st.write(f"**סוג אירוע:** {a['type']}")

    tabs = st.tabs([
        "🔴 מעגל פנימי",
        "🟠 מעגל חיצוני",
        "🟡 מעגל רחוק",
        "🗺️ כל המעגלים",
    ])

    def render_map(selected):
        m = folium.Map(
            location=[a["lat"], a["lon"]],
            zoom_start=15,
            control_scale=True
        )

        folium.Marker(
            [a["lat"], a["lon"]],
            tooltip="מוקד האירוע",
            icon=folium.Icon(color="red", icon="info-sign"),
        ).add_to(m)

        for rad, color in [(220, "red"), (550, "orange"), (950, "gold")]:
            folium.Circle(
                [a["lat"], a["lon"]],
                radius=rad,
                color=color,
                fill=False,
                dash_array="5 7",
                weight=2,
            ).add_to(m)

        rings_to_show = [1, 2, 3] if selected == "all" else [selected]

        for r in rings_to_show:
            for i, n in enumerate(a["rings"][r], 1):
                folium.Marker(
                    [n["lat"], n["lon"]],
                    tooltip=f"{RING_NAMES[r]}: {n['label']}",
                    popup=(
                        f"🚓 {RING_NAMES[r]}<br>"
                        f"{n['label']}<br>"
                        f"{int(n['dist'])} מ׳"
                    ),
                    icon=police_icon(RING_COLORS[r]),
                ).add_to(m)

        st_folium(m, height=520, use_container_width=True)

    def render_list(r):
        if not a["rings"][r]:
            st.warning(f"לא נמצאו נקודות מתאימות ב{RING_NAMES[r]}.")
            return

        for i, n in enumerate(a["rings"][r], 1):
            with st.container(border=True):
                left, right = st.columns([4, 1])

                with left:
                    st.markdown(
                        f"**🚓 {i}. {n['label']}**  \n"
                        f"מרחק משוער: **{int(n['dist'])} מ׳**"
                    )

                    g = (
                        "https://www.google.com/maps/dir/?api=1"
                        f"&destination={n['lat']},{n['lon']}"
                        "&travelmode=driving"
                    )
                    w = (
                        "https://waze.com/ul?"
                        f"ll={n['lat']},{n['lon']}&navigate=yes"
                    )

                    b1, b2 = st.columns(2)
                    b1.link_button("Google Maps", g, use_container_width=True)
                    b2.link_button("Waze", w, use_container_width=True)

                with right:
                    st.checkbox(
                        "אישור",
                        key=f"approved_{r}_{i}_{round(n['lat'], 5)}",
                    )

    with tabs[0]:
        render_map(1)
        render_list(1)

    with tabs[1]:
        render_map(2)
        render_list(2)

    with tabs[2]:
        render_map(3)
        render_list(3)

    with tabs[3]:
        render_map("all")

    st.caption(
        "MVP ניסויי: אין עדיין עומסי תנועה חיים, חסימות קיימות, "
        "עבודות כביש או שכבות GIS ארגוניות."
    )

else:
    st.info("התחל להקליד כתובת, בחר את ההצעה המדויקת ולחץ על 'נתח זירה והצע חסימות'.")

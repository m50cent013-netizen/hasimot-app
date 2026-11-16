
import math
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="חסימות זירה", page_icon="🚓", layout="wide")

st.markdown("""
<style>
html, body, [class*="css"] { direction: rtl; text-align: right; }
.block-container { max-width: 1000px; padding-top: 0.8rem; }
div[data-testid="stTextInput"] input { font-size: 1.05rem; }
.stButton button { min-height: 48px; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

st.title("🚓 מערכת סיוע לחסימות ובידוד זירה")
st.caption("גרסת ניסוי · כל חסימה מחייבת אימות ואישור מפקד בשטח")

c1, c2, c3 = st.columns([2, 1.25, 1])
with c1:
    street = st.text_input("רחוב ומספר", value="בן עמי 50", placeholder="לדוגמה: בן עמי 50")
with c2:
    city = st.text_input("עיר", value="עכו", placeholder="לדוגמה: עכו")
with c3:
    st.text_input("מדינה", value="ישראל", disabled=True)

incident_type = st.selectbox(
    "סוג אירוע",
    ["נפילת טיל / סכנה", "תאונת דרכים", "שריפה", "אירוע פלילי", "חפץ חשוד", "אחר"]
)

session = requests.Session()
session.headers.update({"User-Agent": "HasimotMVP/1.3"})

def hav(a,b,c,d):
    R = 6371000
    p1, p2 = math.radians(a), math.radians(c)
    dp = math.radians(c-a)
    dl = math.radians(d-b)
    q = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(q))

@st.cache_data(ttl=3600, show_spinner=False)
def geocode_arcgis(street, city):
    url = "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates"
    params = {
        "Address": street,
        "City": city,
        "CountryCode": "ISR",
        "f": "json",
        "maxLocations": 1,
        "outFields": "Match_addr"
    }
    r = session.get(url, params=params, timeout=8)
    r.raise_for_status()
    j = r.json()
    cand = j.get("candidates", [])
    if not cand:
        raise RuntimeError("ArcGIS לא מצא כתובת")
    c = cand[0]
    return float(c["location"]["y"]), float(c["location"]["x"]), c.get("address", f"{street}, {city}")

@st.cache_data(ttl=3600, show_spinner=False)
def geocode_nominatim(street, city):
    q = f"{street}, {city}, ישראל"
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "il", "accept-language": "he"}
    r = session.get(url, params=params, timeout=8)
    r.raise_for_status()
    j = r.json()
    if not j:
        raise RuntimeError("Nominatim לא מצא כתובת")
    return float(j[0]["lat"]), float(j[0]["lon"]), j[0].get("display_name", q)

def geocode(street, city):
    errors = []
    for fn in (geocode_arcgis, geocode_nominatim):
        try:
            return fn(street.strip(), city.strip())
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError("לא הצלחתי לאתר את הכתובת. בדוק רחוב, מספר ועיר.")

@st.cache_data(ttl=900, show_spinner=False)
def overpass(lat, lon, radius=1000):
    q = (
        f'[out:json][timeout:8];'
        f'way(around:{radius},{lat},{lon})["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|service"];'
        f'(._;>;);out body;'
    )

    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]

    for ep in endpoints:
        try:
            r = session.post(ep, data={"data": q}, timeout=12)
            r.raise_for_status()
            j = r.json()
            if j.get("elements"):
                return j
        except Exception:
            continue

    raise RuntimeError("שירות נתוני הכבישים לא הגיב. נסה שוב בעוד כמה שניות.")

def parse_nodes(j):
    nodes = {}

    for e in j["elements"]:
        if e["type"] == "node":
            nodes[e["id"]] = {
                "id": e["id"],
                "lat": e["lat"],
                "lon": e["lon"],
                "ways": 0,
                "names": set()
            }

    for e in j["elements"]:
        if e["type"] == "way" and "nodes" in e:
            name = (e.get("tags") or {}).get("name", "")
            for nid in e["nodes"]:
                if nid in nodes:
                    nodes[nid]["ways"] += 1
                    if name:
                        nodes[nid]["names"].add(name)

    return list(nodes.values())

def pick(nodes, lat, lon, minr, maxr, count, minsep):
    cand = []

    for n in nodes:
        n = n.copy()
        n["dist"] = hav(lat, lon, n["lat"], n["lon"])
        if minr <= n["dist"] <= maxr and n["ways"] >= 2:
            cand.append(n)

    cand.sort(key=lambda x: (-x["ways"], abs(x["dist"] - (minr + maxr) / 2)))

    out = []
    for n in cand:
        if all(hav(n["lat"], n["lon"], x["lat"], x["lon"]) >= minsep for x in out):
            out.append(n)
            if len(out) >= count:
                break

    return out

def clean(items):
    out = []
    for i, n in enumerate(items, 1):
        names = list(n["names"])[:2]
        out.append({
            "label": " / ".join(names) if names else f"נקודת חסימה {i}",
            "lat": n["lat"],
            "lon": n["lon"],
            "dist": n["dist"]
        })
    return out

if "analysis" not in st.session_state:
    st.session_state.analysis = None

if st.button("🔎 נתח זירה והצע חסימות", type="primary", use_container_width=True):
    st.session_state.analysis = None

    if not street.strip() or not city.strip():
        st.warning("יש למלא רחוב ומספר + עיר.")
    else:
        try:
            with st.status("מבצע ניתוח...", expanded=True) as status:
                status.write("📍 מאתר כתובת...")
                lat, lon, display = geocode(street, city)

                status.write("🛣️ טוען רשת כבישים...")
                raw = overpass(round(lat, 6), round(lon, 6))
                nodes = parse_nodes(raw)

                status.write("🚧 מחשב נקודות חסימה...")
                rings = {
                    1: clean(pick(nodes, lat, lon, 100, 220, 4, 70)),
                    2: clean(pick(nodes, lat, lon, 280, 550, 4, 115)),
                    3: clean(pick(nodes, lat, lon, 650, 950, 4, 160)),
                }

                st.session_state.analysis = {
                    "lat": lat,
                    "lon": lon,
                    "display": display,
                    "rings": rings,
                    "type": incident_type
                }

                status.update(label="הניתוח הושלם", state="complete", expanded=False)

        except Exception as e:
            st.error(f"לא הצלחתי להשלים את הניתוח: {e}")

a = st.session_state.analysis

if a:
    st.success(f"📍 {a['display']}")
    st.write(f"**סוג אירוע:** {a['type']}")

    tabs = st.tabs(["🔴 מעגל 1", "🟠 מעגל 2", "🟡 מעגל 3", "🗺️ כל המעגלים"])

    def render_map(selected):
        m = folium.Map(location=[a["lat"], a["lon"]], zoom_start=15, control_scale=True)

        folium.Marker(
            [a["lat"], a["lon"]],
            tooltip="מוקד האירוע",
            icon=folium.Icon(color="red", icon="info-sign")
        ).add_to(m)

        for rad, color in [(220, "red"), (550, "orange"), (950, "gold")]:
            folium.Circle(
                [a["lat"], a["lon"]],
                radius=rad,
                color=color,
                fill=False,
                dash_array="5 7",
                weight=2
            ).add_to(m)

        rings_to_show = [1, 2, 3] if selected == "all" else [selected]

        for r in rings_to_show:
            for i, n in enumerate(a["rings"][r], 1):
                bg = "#ef4444" if r == 1 else "#f59e0b" if r == 2 else "#eab308"

                folium.Marker(
                    [n["lat"], n["lon"]],
                    tooltip=f"מעגל {r}: {n['label']}",
                    popup=f"🚧 חסימה מוצעת<br>{n['label']}<br>{int(n['dist'])} מ׳",
                    icon=folium.DivIcon(html=f"""
                    <div style="
                        width:36px;height:36px;border-radius:50%;
                        background:{bg};border:3px solid white;
                        display:flex;align-items:center;justify-content:center;
                        font-size:20px;box-shadow:0 2px 6px rgba(0,0,0,.5)
                    ">🚧</div>
                    """)
                ).add_to(m)

        st_folium(m, height=520, use_container_width=True)

    def render_list(r):
        if not a["rings"][r]:
            st.warning("לא נמצאו נקודות מתאימות במעגל הזה.")
            return

        for i, n in enumerate(a["rings"][r], 1):
            with st.container(border=True):
                left, right = st.columns([4, 1])

                with left:
                    st.markdown(
                        f"**🚧 {i}. {n['label']}**  \n"
                        f"מרחק משוער: **{int(n['dist'])} מ׳**"
                    )

                    g = f"https://www.google.com/maps/dir/?api=1&destination={n['lat']},{n['lon']}&travelmode=driving"
                    w = f"https://waze.com/ul?ll={n['lat']},{n['lon']}&navigate=yes"

                    b1, b2 = st.columns(2)
                    b1.link_button("Google Maps", g, use_container_width=True)
                    b2.link_button("Waze", w, use_container_width=True)

                with right:
                    st.checkbox(
                        "אישור",
                        key=f"approved_{r}_{i}_{round(n['lat'], 5)}"
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
    st.info("הזן רחוב ומספר + עיר ולחץ על 'נתח זירה והצע חסימות'.")

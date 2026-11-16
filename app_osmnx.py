
import math
import streamlit as st
import folium
import requests
import osmnx as ox
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
session.headers.update({"User-Agent": "HasimotMVP/1.4"})

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
        raise RuntimeError("הכתובת לא נמצאה")
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
        raise RuntimeError("הכתובת לא נמצאה")
    return float(j[0]["lat"]), float(j[0]["lon"]), j[0].get("display_name", q)

def geocode(street, city):
    for fn in (geocode_arcgis, geocode_nominatim):
        try:
            return fn(street.strip(), city.strip())
        except Exception:
            pass
    raise RuntimeError("לא הצלחתי לאתר את הכתובת. בדוק רחוב, מספר ועיר.")

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
        truncate_by_edge=True
    )

def street_names(G, node):
    names = []
    for _, _, data in G.edges(node, data=True):
        name = data.get("name")
        if isinstance(name, list):
            names.extend(name)
        elif isinstance(name, str):
            names.append(name)
    seen = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    return seen[:2]

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
    out.sort(key=lambda x: (-x[2], abs(x[1] - (minr+maxr)/2)))
    return out

def pick_spread(G, cand, max_points, min_sep):
    out = []
    for n, d, deg in cand:
        y = G.nodes[n]["y"]
        x = G.nodes[n]["x"]
        if all(hav(y, x, G.nodes[k]["y"], G.nodes[k]["x"]) >= min_sep for k,_,__ in out):
            out.append((n,d,deg))
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
            "dist": d
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
                G = load_graph(round(lat, 6), round(lon, 6))

                status.write("🚧 מחשב נקודות חסימה...")
                r1 = pick_spread(G, make_candidates(G, lat, lon, 100, 220), 4, 70)
                r2 = pick_spread(G, make_candidates(G, lat, lon, 280, 550), 4, 115)
                r3 = pick_spread(G, make_candidates(G, lat, lon, 650, 950), 4, 160)

                rings = {
                    1: clean(G, r1),
                    2: clean(G, r2),
                    3: clean(G, r3),
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
            st.info("אם השגיאה חוזרת, נסה שוב פעם אחת. ייתכן ששירות נתוני הרחובות זמנית לא זמין.")

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
                    st.checkbox("אישור", key=f"approved_{r}_{i}_{round(n['lat'], 5)}")

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

    st.caption("MVP ניסויי: אין עדיין עומסי תנועה חיים, חסימות קיימות, עבודות כביש או שכבות GIS ארגוניות.")

else:
    st.info("הזן רחוב ומספר + עיר ולחץ על 'נתח זירה והצע חסימות'.")

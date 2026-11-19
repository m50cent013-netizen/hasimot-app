
import math
import time
import urllib.parse
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium

st.set_page_config(page_title="חסימות זירה", page_icon="🚓", layout="wide")

st.markdown("""
<style>
html, body, [class*="css"] { direction: rtl; text-align: right; }
.block-container { max-width: 1050px; padding-top: .8rem; }
.stButton button { min-height: 48px; font-weight: 700; }
div[data-testid="stTextInput"] input { font-size: 1.05rem; }
</style>
""", unsafe_allow_html=True)

st.title("🚓 מערכת סיוע לחסימות ובידוד זירה")
st.caption("גרסת ניסוי · כל חסימה וכל ציר חירום מחייבים אימות ואישור מפקד בשטח")

address = st.text_input("כתובת האירוע", value="בן עמי 50, עכו, ישראל")
incident_type = st.selectbox(
    "סוג אירוע",
    ["נפילת טיל / סכנה", "תאונת דרכים", "שריפה", "אירוע פלילי", "חפץ חשוד", "אחר"]
)

HTTP = requests.Session()
HTTP.headers.update({"User-Agent":"HasimotMVP/2.1","Accept":"application/json"})

def hav(a,b,c,d):
    R=6371000
    p1,p2=math.radians(a),math.radians(c)
    dp=math.radians(c-a)
    dl=math.radians(d-b)
    q=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(q))

@st.cache_data(ttl=3600, show_spinner=False)
def geocode(addr):
    url="https://nominatim.openstreetmap.org/search"
    attempts=[
        {"q":addr,"format":"jsonv2","limit":1,"countrycodes":"il","accept-language":"he"},
        {"q":addr,"format":"jsonv2","limit":1,"accept-language":"he"},
    ]
    last=None
    for params in attempts:
        try:
            r=HTTP.get(url,params=params,timeout=12)
            r.raise_for_status()
            data=r.json()
            if data:
                return float(data[0]["lat"]),float(data[0]["lon"]),data[0].get("display_name",addr)
        except Exception as e:
            last=e
            time.sleep(1.05)
    raise RuntimeError(f"לא הצלחתי לאתר את הכתובת: {last}")

@st.cache_data(ttl=600, show_spinner=False)
def overpass(lat,lon):
    endpoints=[
        "https://overpass.private.coffee/api/interpreter",
        "https://overpass-api.de/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    ]
    errors=[]
    for radius in (1250,950):
        q=(
            f'[out:json][timeout:16];'
            f'way(around:{radius},{lat},{lon})["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|construction"];'
            f'(._;>;);out body;'
        )
        for ep in endpoints:
            try:
                r=HTTP.post(ep,data={"data":q},timeout=(8,22),headers={"User-Agent":"HasimotMVP/2.1"})
                r.raise_for_status()
                data=r.json()
                if data.get("elements"):
                    return data,ep
                errors.append(f"{ep}: empty")
            except Exception as e:
                errors.append(f"{ep}: {type(e).__name__}")
                time.sleep(.35)
    raise RuntimeError("לא התקבלו נתוני כבישים מאף שרת. "+" | ".join(errors[-6:]))

def parse_nodes(data):
    nodes={}
    for e in data.get("elements",[]):
        if e.get("type")=="node":
            nodes[e["id"]]={"id":e["id"],"lat":e["lat"],"lon":e["lon"],"ways":0,"names":set()}
    for e in data.get("elements",[]):
        if e.get("type")=="way" and "nodes" in e:
            tags=e.get("tags") or {}
            name=tags.get("name") or tags.get("ref") or ""
            for nid in e["nodes"]:
                if nid in nodes:
                    nodes[nid]["ways"]+=1
                    if name:
                        nodes[nid]["names"].add(str(name))
    return list(nodes.values())

def pick(nodes,lat,lon,minr,maxr,count,minsep):
    midpoint=(minr+maxr)/2

    def ranked(min_ways):
        arr=[]
        for n0 in nodes:
            n=n0.copy()
            n["dist"]=hav(lat,lon,n["lat"],n["lon"])
            if minr<=n["dist"]<=maxr and n["ways"]>=min_ways:
                arr.append(n)
        arr.sort(key=lambda x:(-x["ways"],abs(x["dist"]-midpoint)))
        return arr

    out=[]
    for min_ways in (2,1):
        for n in ranked(min_ways):
            if all(hav(n["lat"],n["lon"],x["lat"],x["lon"])>=minsep for x in out):
                out.append(n)
                if len(out)>=count:
                    return out
        if out:
            return out
    return out

def clean(items):
    out=[]
    for i,n in enumerate(items,1):
        names=list(n["names"])[:2]
        out.append({
            "label":" / ".join(names) if names else f"נקודת חסימה {i}",
            "lat":n["lat"],"lon":n["lon"],"dist":n["dist"],"approved":False
        })
    return out

def google_route(origin_lat,origin_lon,destination):
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={origin_lat},{origin_lon}"
        f"&destination={urllib.parse.quote(destination)}"
        "&travelmode=driving"
    )

def waze_destination(destination):
    return "https://www.waze.com/ul?q="+urllib.parse.quote(destination)+"&navigate=yes"

if "analysis" not in st.session_state:
    st.session_state.analysis=None

if st.button("🔎 נתח זירה והצע חסימות",type="primary",use_container_width=True):
    with st.spinner("מאתר כתובת ומנתח רשת כבישים..."):
        try:
            lat,lon,display=geocode(address)
            raw,server=overpass(lat,lon)
            nodes=parse_nodes(raw)
            rings={
                1:clean(pick(nodes,lat,lon,100,220,4,70)),
                2:clean(pick(nodes,lat,lon,280,550,4,115)),
                3:clean(pick(nodes,lat,lon,650,1100,4,170)),
            }
            st.session_state.analysis={
                "lat":lat,"lon":lon,"display":display,
                "rings":rings,"type":incident_type,"server":server
            }
        except Exception as e:
            st.session_state.analysis={"error":str(e)}

a=st.session_state.analysis

if a and "error" in a:
    st.error("הכתובת/סריקה לא הושלמה.")
    st.code(a["error"])
    st.info("לחץ שוב על ניתוח. המערכת תנסה שרת חלופי אוטומטית.")

elif a:
    st.success(f"📍 {a['display']}")
    st.write(f"**סוג אירוע:** {a['type']}")

    total=sum(len(a["rings"][r]) for r in (1,2,3))
    st.info(f"נמצאו {total} נקודות חסימה מוצעות." if total else "הכתובת אותרה, אך לא נמצאו מספיק נקודות חסימה.")

    st.subheader("🏥 צירי חירום לבתי חולים")
    rambam="Rambam Health Care Campus, Haifa, Israel"
    nahariya="Galilee Medical Center, Nahariya, Israel"

    c1,c2=st.columns(2)
    with c1:
        st.markdown("#### רמב״ם – חיפה")
        st.link_button("🚑 מסלול מהזירה לרמב״ם",google_route(a["lat"],a["lon"],rambam),use_container_width=True)
        st.link_button("Waze לרמב״ם",waze_destination(rambam),use_container_width=True)
    with c2:
        st.markdown("#### המרכז הרפואי לגליל – נהריה")
        st.link_button("🚑 מסלול מהזירה לנהריה",google_route(a["lat"],a["lon"],nahariya),use_container_width=True)
        st.link_button("Waze לנהריה",waze_destination(nahariya),use_container_width=True)

    tabs=st.tabs(["🔴 מעגל 1","🟠 מעגל 2","🟡 מעגל 3","🗺️ כל המעגלים"])

    def render_map(selected):
        m=folium.Map(location=[a["lat"],a["lon"]],zoom_start=15,control_scale=True)
        folium.Marker(
            [a["lat"],a["lon"]],
            tooltip="מוקד האירוע",
            popup=a["display"],
            icon=folium.Icon(color="red",icon="info-sign")
        ).add_to(m)

        for rad,color in [(220,"red"),(550,"orange"),(1100,"gold")]:
            folium.Circle(
                [a["lat"],a["lon"]],
                radius=rad,color=color,fill=False,dash_array="5 7"
            ).add_to(m)

        rings=[1,2,3] if selected=="all" else [selected]
        for r in rings:
            color={1:"red",2:"orange",3:"beige"}[r]
            for n in a["rings"][r]:
                folium.Marker(
                    [n["lat"],n["lon"]],
                    tooltip=f"מעגל {r}: {n['label']}",
                    popup=f"🚧 {n['label']}<br>{int(n['dist'])} מ׳",
                    icon=folium.Icon(color=color,icon="road")
                ).add_to(m)

        st_folium(m,height=520,use_container_width=True)

    def render_list(r):
        if not a["rings"][r]:
            st.warning("לא נמצאו נקודות מתאימות במעגל הזה.")
            return

        for i,n in enumerate(a["rings"][r],1):
            with st.container(border=True):
                st.markdown(f"**🚧 {i}. {n['label']}**")
                st.write(f"מרחק משוער מהמוקד: **{int(n['dist'])} מ׳**")
                st.checkbox(
                    "✅ מאשר את נקודת החסימה",
                    key=f"approved_{r}_{i}_{round(n['lat'],5)}"
                )

                g=f"https://www.google.com/maps/dir/?api=1&destination={n['lat']},{n['lon']}&travelmode=driving"
                w=f"https://waze.com/ul?ll={n['lat']},{n['lon']}&navigate=yes"

                x,y=st.columns(2)
                x.link_button("Google Maps",g,use_container_width=True)
                y.link_button("Waze",w,use_container_width=True)

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

    st.caption("MVP ניסויי בלבד. אין עדיין תנועה חיה, חסימות קיימות, עבודות כביש או שכבות GIS ארגוניות.")
else:
    st.info("הקלד כתובת ולחץ על 'נתח זירה והצע חסימות'.")

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

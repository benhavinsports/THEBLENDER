import datetime as dt
import streamlit as st

from engine.core import build_core3, run_blender
from services.slate import get_mlb_slate

st.set_page_config(page_title="MLB HR Blender V5", layout="wide")
st.title("⚾ MLB HR BLENDER V5")
st.caption("True Event Engine — Gate 0 through Gate 18")

selected_date = st.date_input("Slate date", value=dt.date.today())
debug = st.toggle("Show gate audit", value=False)

with st.spinner("Loading MLB slate..."):
    games = get_mlb_slate(selected_date.isoformat())

if not games:
    st.error("NO GAMES LOADED")
    st.stop()

st.success(f"Loaded {len(games)} game(s)")
for game in games:
    st.write(f"{game['away']} vs {game['home']}")

if st.button("RUN BLENDER ENGINE", type="primary", use_container_width=True):
    with st.spinner("Running all Blender gates..."):
        results = run_blender(games, season=selected_date.year)

    st.subheader("🏆 FINAL HR SURVIVORS")
    for result in results:
        st.markdown("---")
        st.write("GAME:", result.get("game", "UNKNOWN"))
        st.write("FINAL SURVIVOR:", result.get("survivor", "NO SURVIVOR"))
        st.write("WHY:", result.get("why", "NO DATA"))
        st.write("STATUS:", result.get("status", "FAILED"))
        if debug:
            st.write("PIPELINE HEALTH:")
            st.json(result.get("pipeline_health", {}))
            st.write("TARGET:", result.get("target_side"))
            st.json(result.get("audit", []))
            with st.expander("Gate 1 input profiles"):
                st.json(result.get("gate1_profiles", []))

    st.subheader("🔥 CORE 3")
    core3 = build_core3(results)
    if not core3:
        st.error("NO CORE 3")
    else:
        for index, result in enumerate(core3, start=1):
            st.write(f"{index}. {result['survivor']} — {result['game']}")

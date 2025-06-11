

import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import time
import os
import io
from cryptography.fernet import Fernet

# Static shared encryption key
ENCRYPTION_KEY = b'27VNJatctBfMT4CEYRmiB5F_IzRa0akQ0cDHSBIRtz4='
fernet = Fernet(ENCRYPTION_KEY)

# 🔍 Locate OneDrive > Benchmarks folder
def find_benchmarks_folder():
    base_path = os.path.join(os.environ.get("USERPROFILE", "C:\\Users\\Default"))
    for i in os.listdir(base_path):
        if i.startswith("OneDrive "):
            base_path = os.path.join(base_path, i)
            break
    for root, dirs, _ in os.walk(base_path):
        for d in dirs:
            if d.lower().endswith("benchmarks"):
                return os.path.join(root, d)
    return None

# 🔐 Load and decrypt CSV
def load_encrypted_csv(path):
    try:
        with open(path, 'rb') as f:
            encrypted_data = f.read()
        decrypted = fernet.decrypt(encrypted_data).decode()
        return pd.read_csv(io.StringIO(decrypted))
    except Exception as e:
        st.error(f"❌ Failed to decrypt/load CSV: {e}")
        st.stop()

# 🌐 Streamlit setup
st.set_page_config(page_title="🏆 Benchmark Dashboard", layout="wide")
st.title("🏆 Team Benchmark Dashboard")

# 📁 OneDrive root
ONEDRIVE_FOLDER = find_benchmarks_folder()
if not ONEDRIVE_FOLDER:
    st.error("❌ OneDrive Benchmarks folder not found.")
    st.stop()

# 📊 Load Benchmarks
CSV_PATH = os.path.join(ONEDRIVE_FOLDER, "master_results.csv")
df = load_encrypted_csv(CSV_PATH)
df.columns = [col.strip() for col in df.columns]
df["Last Run"] = pd.to_datetime(df["Last Run"])

# 🎛️ Filters
teams = df["Team"].unique().tolist()
selected_teams = st.multiselect("🔍 Filter Teams", teams, default=teams)
df_filtered = df[df["Team"].isin(selected_teams)]
min_score, max_score = st.slider(
    "🎚 Filter by Current Score Range",
    float(df["Current Score"].min()), float(df["Current Score"].max()),
    (float(df["Current Score"].min()), float(df["Current Score"].max()))
)
df_filtered = df_filtered[df_filtered["Current Score"].between(min_score, max_score)]

# 📈 KPI
col1, col2, col3 = st.columns(3)
col1.metric("🔢 Teams", df_filtered["Team"].nunique())
col2.metric("📊 Avg. Score", f"{df_filtered['Current Score'].mean():.2f}")
col3.metric("🥇 Best Score (Lowest)", f"{df_filtered['Best Score'].min():.2f}")

# 📥 Download Benchmarks
st.download_button(
    label="📥 Download Filtered CSV",
    data=df_filtered.to_csv(index=False).encode("utf-8"),
    file_name="filtered_benchmark_results.csv",
    mime="text/csv",
)

# 📊 Tabs
tab1, tab2, tab3, tab4 = st.tabs([
    "📈 Scores Table", "📊 Charts", "🕒 Last Runs", "🚦 Violations"
])

df_sorted = df_filtered.sort_values("Current Score")
df_time = df_filtered.sort_values("Last Run", ascending=False)
df_best = df_filtered.loc[df_filtered.groupby("Team")["Best Score"].idxmin()]
df_best_sorted = df_best.sort_values("Best Score")  # ✅ Sorted by Best Score

with tab1:
    st.subheader("📈 Current Scores (Sorted)")
    st.dataframe(df_sorted.style.background_gradient(cmap="Greens", subset=["Current Score"]))

with tab2:
    st.subheader("📊 Team Score Chart")
    fig = px.bar(df_sorted, x="Team", y="Current Score", color="Current Score",
                 color_continuous_scale="Blues_r", text="Current Score")
    st.plotly_chart(fig)
    
    st.subheader("🥇 Best Scores by Team (Sorted)")
    st.dataframe(df_best_sorted[["Team", "Best Score"]])

with tab3:
    st.subheader("🕒 Last Run Times")
    st.dataframe(df_time[["Team", "Last Run"]])



with tab4:
    st.subheader("🚦 Violations Summary")

    violation_dir = os.path.join(ONEDRIVE_FOLDER, "violation_detection")
    master_violation_path = os.path.join(violation_dir, "master_violation.csv")
    violation_df = load_encrypted_csv(master_violation_path)
    violation_df.columns = [col.strip().lower() for col in violation_df.columns]

    # 🔢 Convert columns to numeric (safe conversion)
    cols_to_convert = [
        "current_lane", "lowest_lane",
        "current_collision", "lowest_collision",
        "current_redlight", "lowest_redlight"
    ]
    for col in cols_to_convert:
        if col in violation_df.columns:
            violation_df[col] = pd.to_numeric(violation_df[col], errors="coerce")

    # ✅ Sort by total lowest violations (optional helper column)
    if all(col in violation_df.columns for col in ["lowest_lane", "lowest_collision", "lowest_redlight"]):
        violation_df["total_lowest_violations"] = (
            violation_df["lowest_lane"] +
            violation_df["lowest_collision"] +
            violation_df["lowest_redlight"]
        )
        violation_df_sorted = violation_df.sort_values(by="total_lowest_violations", ascending=True)
        st.dataframe(violation_df_sorted)
    else:
        st.warning("⚠️ Missing one or more of: 'lowest_lane', 'lowest_collision', 'lowest_redlight'.")
        st.dataframe(violation_df)

    # 📥 Download updated violation data
    st.download_button(
        label="📥 Download Violations CSV",
        data=violation_df.to_csv(index=False).encode("utf-8"),
        file_name="violations_summary.csv",
        mime="text/csv",
    )


# 🔁 Auto-refresh
if st.checkbox("🔄 Auto-refresh every 10 sec"):
    time.sleep(10)
    st.experimental_rerun()

# Footer
st.markdown("---")
st.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

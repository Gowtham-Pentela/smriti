import streamlit as st
import requests

st.set_page_config(layout="wide", page_title="KGF-E Assistant", page_icon="🧠")
st.title("KGF-E Organizational Intelligence Assistant 🧠")
st.caption("Sandbox Environment: Redwood Inference Production Corp")

# ── Initialize data at module scope so col2 can always reference it ──────────
data = None

# Establish the wide two-panel interface setup
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Conversation Workspace")
    user_query = st.text_input("Ask an onboarding or operational question:")

    if user_query:
        with st.spinner("Executing hybrid HNSW lookup & model verification..."):
            try:
                res = requests.post("http://127.0.0.1:8000/query", json={"query": user_query})

                if res.status_code == 200:
                    data = res.json()

                    # ── Latency metric ─────────────────────────────────────────
                    latency_seconds = data.get("latency_seconds", None)
                    if latency_seconds is not None:
                        latency_ms = latency_seconds * 1000
                        st.metric(
                            label="⏱️ End-to-End Query Latency",
                            value=f"{latency_ms:.2f} ms",
                        )
                    else:
                        st.caption("⏱️ Latency data not available (restart backend)")

                    # ── Response ───────────────────────────────────────────────
                    st.markdown("### Assistant Response")
                    st.write(data.get("response"))

                else:
                    st.error(f"Backend returned an execution error code: {res.status_code}")

            except Exception as e:
                st.error(f"Failed to communicate with local API server: {e}")

with col2:
    st.subheader("Verified Source Citations")
    if data and "retrieved_context" in data:
        if not data["retrieved_context"]:
            st.info("No context fragments matched search thresholds.")
        for idx, chunk in enumerate(data["retrieved_context"]):
            # Safe .get() lookups — handles both Slack and PDF/document chunks
            location = chunk.get("location", "unknown")
            score = chunk.get("score", 0.0)
            author_id = chunk.get("author_id", "N/A")
            source = chunk.get("source", "Unknown Source")
            doc_type = chunk.get("type", "document")
            content = chunk.get("content", "")

            if doc_type == "slack":
                title = f"✉️ Slack | #{location} | Match: {round(score * 100, 1)}%"
            else:
                title = f"📄 {doc_type.upper()} | {location} | Match: {round(score * 100, 1)}%"

            with st.expander(title):
                st.markdown(f"**Author:** `{author_id}`")
                st.markdown(f"**Source Document ID:** `{source}`")
                st.divider()
                st.text(content)

# ── Data Source Connectors ────────────────────────────────────────────────────
st.divider()
st.subheader("📡 Connect a Data Source")
st.caption(
    "Ingestion runs in the background. Poll /ingest-status for progress. "
    "Each source is deduplicated automatically — re-running is safe."
)

connector_tabs = st.tabs(["Slack"])

# ── Slack connector tab ───────────────────────────────────────────────────────
with connector_tabs[0]:
    st.markdown("#### Slack Live Connector")
    st.info(
        "Paste your Slack **Bot Token** (`xoxb-...`) and the channel IDs you want to index. "
        "The token is used only for this ingestion run unless you check 'Save token'.",
        icon="ℹ️",
    )

    with st.form("slack_ingest_form"):
        slack_token = st.text_input(
            "Bot Token (starts with xoxb-)",
            type="password",
            placeholder="paste-your-slack-bot-token-here",
        )
        slack_channels_raw = st.text_input(
            "Channel IDs (comma-separated)",
            placeholder="C01234ABCDE, C09876ZYXWV",
        )
        slack_days_back = st.slider(
            "History lookback (days)", min_value=7, max_value=365, value=90, step=7
        )
        save_token = st.checkbox(
            "Save token encrypted to DB for future syncs",
            value=False,
        )
        submitted = st.form_submit_button("🚀 Start Ingestion")

    if submitted:
        if not slack_token or not slack_channels_raw:
            st.warning("Provide both a Bot Token and at least one Channel ID.")
        else:
            channel_ids = [c.strip() for c in slack_channels_raw.split(",") if c.strip()]
            try:
                resp = requests.post(
                    "http://127.0.0.1:8000/ingest-slack",
                    json={
                        "bot_token": slack_token,
                        "channel_ids": channel_ids,
                        "days_back": slack_days_back,
                        "save_token": save_token,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    st.success(
                        f"✅ Ingestion started for {len(channel_ids)} channel(s). "
                        "Check /ingest-status for live progress."
                    )
                else:
                    st.error(f"Backend error {resp.status_code}: {resp.text}")
            except Exception as e:
                st.error(f"Could not reach backend: {e}")

    # ── Ingestion status card ─────────────────────────────────────────────────
    if st.button("🔄 Refresh ingestion status"):
        try:
            status_resp = requests.get("http://127.0.0.1:8000/ingest-status", timeout=5)
            if status_resp.status_code == 200:
                s = status_resp.json()
                state_icon = "🟢" if s.get("is_running") else "⚫"
                st.markdown(
                    f"{state_icon} **Status:** `{s.get('message', 'unknown')}` | "
                    f"**Ingested:** {s.get('ingested', 0)} | "
                    f"**Skipped (dedup):** {s.get('skipped', 0)}"
                )
                if s.get("errors"):
                    st.error("Errors:\n" + "\n".join(s["errors"]))
        except Exception as e:
            st.error(f"Status check failed: {e}")
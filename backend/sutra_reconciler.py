import os
import json
import httpx
import asyncio
import datetime
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
MODEL_NAME = "phi4-mini:latest"
EMBED_MODEL = "nomic-embed-text"

def clean_json_response(raw_text: str) -> str:
    """Strip markdown code block wrappers from LLM json response."""
    text = raw_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()

async def get_embedding(text: str) -> list[float]:
    """Fetch 768-dim embedding from local Ollama using nomic-embed-text."""
    async with httpx.AsyncClient() as client:
        payload = {
            "model": EMBED_MODEL,
            "prompt": "search_document: " + text
        }
        try:
            resp = await client.post(OLLAMA_EMBED_URL, json=payload, timeout=20.0)
            if resp.status_code == 200:
                return resp.json().get("embedding", [])
            else:
                print(f"[Reconciler] Embedding API error: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"[Reconciler] Failed to fetch embedding: {e}")
    return [0.0] * 768

async def extract_decisions(transcript_text: str, meeting_id: str, tenant_id: str, db_pool) -> list[dict]:
    """Prompts phi4-mini to extract structured decisions from the transcript."""
    if not transcript_text.strip():
        print(f"[Reconciler] Transcript is empty for meeting {meeting_id}. Skipping extraction.")
        return []

    prompt = f"""
    You are an advanced AI assistant designed to extract key decisions, policies, architectural rules, and concrete facts from a meeting transcript.
    Analyze the transcript below and extract all structured decisions.
    For each decision, identify:
    - entity_name: The component, API path, database table, feature, or module affected (e.g. "/v1/auth", "billing_migration", "Postgres index").
    - action_type: The type of change (e.g. "deprecate", "integrate", "refactor", "create", "modify", "delete").
    - summary: A precise sentence summarizing the decision, rule, or action item.
    - owner_email: The email address of the person responsible for this decision/action (or null if not mentioned).
    - target_date: The target date for implementation in YYYY-MM-DD format (or null if not mentioned).

    Response MUST be a valid JSON array of objects. Do not include any explanation or markdown formatting other than the JSON itself.
    
    Transcript:
    {transcript_text}
    """

    async with httpx.AsyncClient() as client:
        payload = {
            "model": MODEL_NAME,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": 4096
            }
        }
        
        try:
            resp = await client.post(OLLAMA_CHAT_URL, json=payload, timeout=120.0)
            if resp.status_code != 200:
                print(f"[Reconciler] LLM extraction failed: {resp.status_code}")
                return []
            
            raw_content = resp.json().get("message", {}).get("content", "")
            json_str = clean_json_response(raw_content)
            decisions = json.loads(json_str)
            if not isinstance(decisions, list):
                print("[Reconciler] Extracted decisions is not a list.")
                return []
            
            print(f"[Reconciler] Extracted {len(decisions)} decisions from meeting {meeting_id}.")
            
            # Embed and insert decisions in DB
            inserted_decisions = []
            async with db_pool.acquire() as conn:
                for dec in decisions:
                    entity_name = dec.get("entity_name") or "general"
                    action_type = dec.get("action_type") or "modify"
                    summary = dec.get("summary") or ""
                    owner_email = dec.get("owner_email")
                    target_date_str = dec.get("target_date")
                    
                    if not summary:
                        continue
                    
                    target_date = None
                    if target_date_str:
                        try:
                            target_date = datetime.datetime.strptime(target_date_str, "%Y-%m-%d").date()
                        except ValueError:
                            pass
                    
                    # Generate embedding
                    embedding = await get_embedding(summary)
                    emb_str = f"[{','.join(map(str, embedding))}]"
                    
                    row = await conn.fetchrow(
                        """
                        INSERT INTO public.decision_nodes (
                            tenant_id, meeting_id, entity_name, action_type, summary, owner_email, target_date, embedding
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8::text::vector)
                        RETURNING id, entity_name, action_type, summary, owner_email, target_date
                        """,
                        tenant_id, meeting_id, entity_name, action_type, summary, owner_email, target_date, emb_str
                    )
                    
                    inserted_decisions.append({
                        "id": str(row["id"]),
                        "entity_name": row["entity_name"],
                        "action_type": row["action_type"],
                        "summary": row["summary"],
                        "owner_email": row["owner_email"],
                        "target_date": str(row["target_date"]) if row["target_date"] else None,
                        "embedding": embedding
                    })
            
            return inserted_decisions
            
        except Exception as e:
            print(f"[Reconciler] Error in extract_decisions: {e}")
            return []

async def check_conflicts(new_node: dict, tenant_id: str, db_pool) -> list[dict]:
    """Performs pgvector search to find similar decisions and check semantic relationships via LLM."""
    relations_found = []
    embedding = new_node.get("embedding")
    if not embedding:
        return []
    
    emb_str = f"[{','.join(map(str, embedding))}]"
    node_id = new_node.get("id")
    
    async with db_pool.acquire() as conn:
        # Retrieve top 5 most similar decision nodes from historical meetings
        rows = await conn.fetch(
            """
            SELECT dn.id, dn.entity_name, dn.action_type, dn.summary, dn.created_at, m.title as meeting_title
            FROM public.decision_nodes dn
            LEFT JOIN public.meetings m ON dn.meeting_id = m.id
            WHERE dn.tenant_id = $1::uuid AND dn.id != $2::uuid
            ORDER BY dn.embedding <=> $3::text::vector
            LIMIT 5
            """,
            tenant_id, node_id, emb_str
        )
        
        async with httpx.AsyncClient() as client:
            for row in rows:
                hist_id = str(row["id"])
                hist_summary = row["summary"]
                hist_entity = row["entity_name"]
                hist_meeting = row["meeting_title"] or "Previous Knowledge Base"
                
                conflict_prompt = f"""
                Analyze the relationship between these two software architectural/project decisions.
                Decision A (New Meeting Decision): "{new_node['summary']}" affecting component "{new_node['entity_name']}"
                Decision B (Historical Database Decision): "{hist_summary}" affecting component "{hist_entity}"

                Is there a contradiction, a dependency, or does Decision A supersede/replace Decision B?
                Your response must be exactly one word from this list:
                - 'contradicts' (if Decision A contradicts, opposes, or is mutually exclusive with Decision B)
                - 'depends_on' (if Decision A requires Decision B to be done first or relies on it)
                - 'supersedes' (if Decision A replaces, updates, or overrides Decision B)
                - 'none' (if they are related but there is no contradiction, dependency, or superseding relationship)

                Only respond with one of the four words: contradicts, depends_on, supersedes, none.
                Do not include any explanation or formatting.
                """
                
                try:
                    resp = await client.post(OLLAMA_CHAT_URL, json={
                        "model": MODEL_NAME,
                        "messages": [{"role": "user", "content": conflict_prompt}],
                        "stream": False,
                        "options": {"temperature": 0.0}
                    }, timeout=20.0)
                    
                    if resp.status_code == 200:
                        relation = resp.json().get("message", {}).get("content", "").strip().lower().replace("'", "").replace('"', '')
                        
                        if relation in ['contradicts', 'depends_on', 'supersedes']:
                            print(f"[Reconciler] Relationship found: New decision {node_id} {relation} historical decision {hist_id}")
                            
                            # Insert into relations table
                            await conn.execute(
                                """
                                INSERT INTO public.decision_relations (tenant_id, node_id_a, node_id_b, relation_type)
                                VALUES ($1, $2, $3, $4)
                                ON CONFLICT (node_id_a, node_id_b, relation_type) DO NOTHING
                                """,
                                tenant_id, node_id, hist_id, relation
                            )
                            
                            relations_found.append({
                                "hist_id": hist_id,
                                "hist_summary": hist_summary,
                                "relation": relation,
                                "entity_name": hist_entity,
                                "meeting_title": hist_meeting,
                                "created_at": row["created_at"].strftime("%Y-%m-%d")
                            })
                except Exception as e:
                    print(f"[Reconciler] Error checking relation for historical node {hist_id}: {e}")
                    
    return relations_found

async def compile_action_plan(meeting_id: str, tenant_id: str, db_pool) -> dict:
    """Compiles the final markdown action plan and conflict alert checklist."""
    async with db_pool.acquire() as conn:
        # Get meeting details
        meeting = await conn.fetchrow(
            "SELECT title, scheduled_start, attendees, meeting_url FROM public.meetings WHERE id = $1",
            meeting_id
        )
        if not meeting:
            return {}
        
        # Get decisions
        decisions = await conn.fetch(
            """
            SELECT id, entity_name, action_type, summary, owner_email, target_date
            FROM public.decision_nodes
            WHERE meeting_id = $1
            """,
            meeting_id
        )
        
        decisions_summary_list = []
        relations_list = []
        
        for idx, dec in enumerate(decisions):
            dec_id = dec["id"]
            dec_info = f"- [{dec['entity_name'].upper()}] ({dec['action_type']}): {dec['summary']} (Owner: {dec['owner_email'] or 'TBD'}, Target: {dec['target_date'] or 'TBD'})"
            decisions_summary_list.append(dec_info)
            
            # Find relations
            relations = await conn.fetch(
                """
                SELECT dr.relation_type, dn.summary, dn.entity_name, m.title as meeting_title, dn.created_at
                FROM public.decision_relations dr
                JOIN public.decision_nodes dn ON dr.node_id_b = dn.id
                LEFT JOIN public.meetings m ON dn.meeting_id = m.id
                WHERE dr.node_id_a = $1
                """,
                dec_id
            )
            
            for rel in relations:
                rel_info = (
                    f"  * ALERT: This decision {rel['relation_type'].upper()} a past decision: "
                    f"\"{rel['summary']}\" (from meeting \"{rel['meeting_title'] or 'Docs'}\" on {rel['created_at'].strftime('%Y-%m-%d')})"
                )
                relations_list.append(rel_info)
                
        decisions_str = "\n".join(decisions_summary_list) if decisions_summary_list else "No concrete decisions extracted."
        relations_str = "\n".join(relations_list) if relations_list else "No conflicts or dependencies detected."
        
        # Call LLM to synthesize report
        prompt = f"""
        Generate a professional Post-Meeting Action Plan and Summary report based on the details below:

        Meeting Title: {meeting['title']}
        Date: {meeting['scheduled_start'].strftime('%Y-%m-%d %H:%M %Z')}
        Attendees: {', '.join(meeting['attendees'])}

        Concrete Decisions / Action Items:
        {decisions_str}

        Knowledge Reconciler / Conflict Alerts:
        {relations_str}

        Please format the output in beautiful markdown including:
        1. An **Executive Summary** detailing the main achievements and context.
        2. A structured markdown table of **Decisions & Action Items** with columns: Component/Entity, Action, Summary, Owner, Deadline.
        3. A **Reconciliation & Semantic Alignment** section detailing any conflicts ('contradicts'), dependencies ('depends_on'), or replacements ('supersedes') identified against our knowledge base. Give clear action suggestions on how the team can align on conflicting items.
        """
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(OLLAMA_CHAT_URL, json={
                    "model": MODEL_NAME,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {
                        "temperature": 0.2,
                        "num_ctx": 4096
                    }
                }, timeout=120.0)
                
                markdown_plan = resp.json().get("message", {}).get("content", "") if resp.status_code == 200 else "Action Plan compilation failed."
                return {
                    "title": meeting["title"],
                    "scheduled_start": meeting["scheduled_start"],
                    "attendees": meeting["attendees"],
                    "markdown": markdown_plan,
                    "decisions": [dict(d) for d in decisions],
                    "relations": relations_list
                }
            except Exception as e:
                print(f"[Reconciler] Error compiling markdown plan: {e}")
                return {}

def generate_premium_html(plan_data: dict) -> str:
    """Wraps the markdown plan in a gorgeous, responsive, premium email template."""
    # Simple markdown parser for HTML formatting in email
    markdown = plan_data.get("markdown", "")
    
    # Simple replacement of markdown to HTML tags
    html_content = markdown
    # Convert bold
    html_content = html_content.replace("**", "<strong>").replace("**", "</strong>")
    # Convert lists
    lines = html_content.split("\n")
    formatted_lines = []
    in_list = False
    in_table = False
    
    for line in lines:
        stripped = line.strip()
        # Headings
        if stripped.startswith("### "):
            if in_list:
                formatted_lines.append("</ul>")
                in_list = False
            formatted_lines.append(f"<h3 style='color: #818cf8; margin-top: 24px; font-family: Inter, sans-serif;'>{stripped[4:]}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                formatted_lines.append("</ul>")
                in_list = False
            formatted_lines.append(f"<h2 style='color: #6366f1; border-bottom: 1px solid #4338ca; padding-bottom: 6px; margin-top: 32px; font-family: Inter, sans-serif;'>{stripped[3:]}</h2>")
        elif stripped.startswith("# "):
            if in_list:
                formatted_lines.append("</ul>")
                in_list = False
            formatted_lines.append(f"<h1 style='color: #4f46e5; margin-top: 32px; font-family: Inter, sans-serif;'>{stripped[2:]}</h1>")
        # Bullet list
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                formatted_lines.append("<ul style='padding-left: 20px; line-height: 1.6;'>")
                in_list = True
            # Check for alert text
            content = stripped[2:]
            if "ALERT" in content or "contradicts" in content.lower():
                formatted_lines.append(f"<li style='margin-bottom: 8px; color: #f87171; font-weight: bold;'>⚠️ {content}</li>")
            else:
                formatted_lines.append(f"<li style='margin-bottom: 8px;'>{content}</li>")
        # Table rows
        elif stripped.startswith("|"):
            if in_list:
                formatted_lines.append("</ul>")
                in_list = False
            if not in_table:
                formatted_lines.append("<table style='width: 100%; border-collapse: collapse; margin: 16px 0; font-size: 14px;'>")
                in_table = True
            
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if "---" in cells[0]:
                continue # Skip alignment rows
            
            row_style = "style='border-bottom: 1px solid #312e81; padding: 12px; text-align: left;'"
            cell_tag = "th" if len(formatted_lines) > 0 and formatted_lines[-1].endswith("collapse; margin: 16px 0; font-size: 14px;'>") else "td"
            
            cell_html = "".join([f"<{cell_tag} style='padding: 10px; border: 1px solid #312e81;'>{cell}</{cell_tag}>" for cell in cells])
            formatted_lines.append(f"<tr>{cell_html}</tr>")
        else:
            if in_list:
                formatted_lines.append("</ul>")
                in_list = False
            if in_table and not stripped.startswith("|"):
                formatted_lines.append("</table>")
                in_table = False
            
            if stripped:
                formatted_lines.append(f"<p style='line-height: 1.6; margin-bottom: 16px;'>{stripped}</p>")
                
    if in_list:
        formatted_lines.append("</ul>")
    if in_table:
        formatted_lines.append("</table>")
        
    body_html = "\n".join(formatted_lines)

    premium_template = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Sutra Post-Meeting Action Plan</title>
</head>
<body style="margin: 0; padding: 0; background-color: #030014; color: #f3f4f6; font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;">
  <table width="100%" border="0" cellspacing="0" cellpadding="0" style="background-color: #030014; width: 100%;">
    <tr>
      <td align="center" style="padding: 40px 10px;">
        <table width="650" border="0" cellspacing="0" cellpadding="0" style="background-color: #080710; border: 1px solid #1f1a3a; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 30px rgba(0,0,0,0.5);">
          
          <!-- Header Banner -->
          <tr>
            <td style="background: linear-gradient(135deg, #1e1b4b 0%, #31106a 100%); padding: 32px 40px; text-align: center; border-bottom: 1px solid #2e1065;">
              <h4 style="margin: 0; color: #a5b4fc; text-transform: uppercase; letter-spacing: 2px; font-size: 12px; font-weight: 700;">Smriti Knowledge Base • Sutra Bot</h4>
              <h1 style="margin: 8px 0 0 0; color: #ffffff; font-size: 24px; font-weight: 800; font-family: Inter, sans-serif;">{plan_data.get('title')}</h1>
              <p style="margin: 12px 0 0 0; color: #cbd5e1; font-size: 13px;">Date: {plan_data.get('scheduled_start').strftime('%Y-%m-%d %H:%M %Z')}</p>
            </td>
          </tr>
          
          <!-- Attendees Section -->
          <tr>
            <td style="padding: 20px 40px 0 40px;">
              <div style="background-color: #0f0b21; border: 1px dashed #4338ca; border-radius: 8px; padding: 12px 16px; font-size: 13px; color: #cbd5e1;">
                <strong>Invitees & Attendees:</strong> {', '.join(plan_data.get('attendees', []))}
              </div>
            </td>
          </tr>

          <!-- Main Report Content -->
          <tr>
            <td style="padding: 24px 40px 40px 40px; font-size: 15px; color: #e2e8f0;">
              {body_html}
            </td>
          </tr>
          
          <!-- Footer Branding -->
          <tr>
            <td style="background-color: #02000a; border-top: 1px solid #1e1b4b; padding: 24px 40px; text-align: center; font-size: 12px; color: #64748b;">
              <p style="margin: 0;">This report was compiled and semantic conflicts reconciled automatically by <strong>Sutra</strong>.</p>
              <p style="margin: 8px 0 0 0;">Smriti.one — Capture requirements, prevent regression.</p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return premium_template

async def distribute_action_plan(meeting_id: str, tenant_id: str, db_pool):
    """Compiles plan, saves HTML file locally, logs output, and attempts email SMTP dispatch."""
    plan_data = await compile_action_plan(meeting_id, tenant_id, db_pool)
    if not plan_data:
        print(f"[Reconciler] Failed to generate plan data for meeting {meeting_id}.")
        return False
        
    html_report = generate_premium_html(plan_data)
    
    # 1. Save HTML report locally for testability & local manual verification
    os.makedirs("/Users/gowtham/local-assistant/data/sutra_emails", exist_ok=True)
    file_path = f"/Users/gowtham/local-assistant/data/sutra_emails/meeting_{meeting_id}_action_plan.html"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_report)
    print(f"[Reconciler] Action plan saved locally at: [file://{file_path}]")
    
    # 2. Log compiled action plan
    print(f"\n==================== SUTRA ACTION PLAN ====================")
    print(plan_data.get("markdown"))
    print(f"===========================================================\n")
    
    # 3. SMTP Dispatch
    smtp_host = os.getenv("SMTP_HOST")
    smtp_port = os.getenv("SMTP_PORT")
    smtp_user = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    smtp_sender = os.getenv("SMTP_SENDER", "sutra@smriti.one")
    
    if smtp_host and smtp_port and smtp_user and smtp_password:
        print(f"[Reconciler] Sending Action Plan email to attendees: {plan_data['attendees']}")
        try:
            # Send asynchronously using threading loop or standard run in executor
            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Action Plan: {plan_data['title']}"
            msg['From'] = smtp_sender
            msg['To'] = ", ".join(plan_data['attendees'])
            
            # Plain text fallback
            text_part = MIMEText(plan_data.get("markdown", ""), 'plain')
            html_part = MIMEText(html_report, 'html')
            
            msg.attach(text_part)
            msg.attach(html_part)
            
            # Run SMTP call in a separate thread to avoid blocking loop
            def send_smtp():
                with smtplib.SMTP(smtp_host, int(smtp_port)) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_password)
                    server.sendmail(smtp_sender, plan_data['attendees'], msg.as_string())
            
            await asyncio.get_event_loop().run_in_executor(None, send_smtp)
            print("[Reconciler] Email sent successfully.")
            return True
        except Exception as e:
            print(f"[Reconciler] Failed to send email via SMTP: {e}")
    else:
        print("[Reconciler] SMTP credentials not set in .env. Skipping email dispatch (logged above).")
        
    return True

async def run_sutra_pipeline(transcript_text: str, meeting_id: str, tenant_id: str, db_pool):
    """Orchestrates the complete decision extraction, conflict verification, and Action Plan distribution."""
    print(f"[Sutra Pipeline] Running for meeting {meeting_id} and tenant {tenant_id}...")
    try:
        # 1. Extract decisions
        new_nodes = await extract_decisions(transcript_text, meeting_id, tenant_id, db_pool)
        
        # 2. Check conflicts for each extracted decision
        for node in new_nodes:
            await check_conflicts(node, tenant_id, db_pool)
            
        # 3. Compile and distribute action plan via email
        success = await distribute_action_plan(meeting_id, tenant_id, db_pool)
        print(f"[Sutra Pipeline] Completed. Distribution status: {success}")
        return success
    except Exception as e:
        print(f"[Sutra Pipeline] Error processing meeting {meeting_id}: {e}")
        return False


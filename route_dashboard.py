from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from database import get_db, VirtualNumber, ReceivedCode, CallLog, TelegramUser
from config import COMPANY_NAME

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(db: Session = Depends(get_db)):
    total_numbers = db.query(VirtualNumber).filter(VirtualNumber.is_active == True).count()
    total_codes = db.query(ReceivedCode).count()
    total_calls = db.query(CallLog).count()
    total_users = db.query(TelegramUser).count()

    recent_codes = db.query(ReceivedCode).order_by(ReceivedCode.received_at.desc()).limit(10).all()
    recent_numbers = db.query(VirtualNumber).filter(VirtualNumber.is_active == True).order_by(
        VirtualNumber.created_at.desc()).limit(10).all()

    codes_html = ""
    for c in recent_codes:
        codes_html += f"""
        <tr>
            <td><code>{c.phone_number}</code></td>
            <td>{c.from_number}</td>
            <td><strong>{c.code}</strong></td>
            <td>{c.service or 'Unknown'}</td>
            <td>{c.received_at.strftime('%Y-%m-%d %H:%M')}</td>
        </tr>"""

    numbers_html = ""
    for n in recent_numbers:
        numbers_html += f"""
        <tr>
            <td><code>{n.phone_number}</code></td>
            <td>{n.country_name}</td>
            <td>{n.country_code}</td>
            <td>{n.user_id}</td>
            <td>{'✅ Active' if n.is_active else '❌ Inactive'}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{COMPANY_NAME} Telecom — Dashboard</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0e1a; color: #e2e8f0; min-height: 100vh; }}
  header {{ background: linear-gradient(135deg, #1a1f35 0%, #0d1220 100%); border-bottom: 1px solid #2d3748; padding: 20px 40px; display: flex; align-items: center; gap: 16px; }}
  .logo {{ font-size: 28px; font-weight: 900; letter-spacing: -1px; background: linear-gradient(90deg, #6366f1, #8b5cf6, #06b6d4); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  .logo-sub {{ font-size: 13px; color: #64748b; letter-spacing: 3px; text-transform: uppercase; }}
  main {{ padding: 40px; max-width: 1400px; margin: 0 auto; }}
  h2 {{ font-size: 22px; font-weight: 700; margin-bottom: 24px; color: #f1f5f9; }}
  .stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 40px; }}
  .stat-card {{ background: #111827; border: 1px solid #1e293b; border-radius: 12px; padding: 24px; position: relative; overflow: hidden; }}
  .stat-card::before {{ content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px; background: linear-gradient(90deg, #6366f1, #06b6d4); }}
  .stat-icon {{ font-size: 28px; margin-bottom: 12px; }}
  .stat-value {{ font-size: 36px; font-weight: 800; background: linear-gradient(90deg, #6366f1, #06b6d4); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  .stat-label {{ font-size: 13px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; margin-top: 4px; }}
  .card {{ background: #111827; border: 1px solid #1e293b; border-radius: 12px; padding: 28px; margin-bottom: 32px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th {{ text-align: left; padding: 12px 16px; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: #64748b; border-bottom: 1px solid #1e293b; }}
  td {{ padding: 14px 16px; border-bottom: 1px solid #1a2234; font-size: 14px; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #1a2234; }}
  code {{ background: #1e293b; padding: 2px 8px; border-radius: 4px; font-family: monospace; font-size: 13px; color: #06b6d4; }}
  strong {{ color: #6366f1; font-size: 16px; }}
  .badge-active {{ background: #064e3b; color: #10b981; padding: 3px 10px; border-radius: 20px; font-size: 12px; }}
  .divider {{ width: 1px; height: 40px; background: #2d3748; margin: 0 16px; }}
  footer {{ text-align: center; padding: 24px; color: #334155; font-size: 13px; }}
</style>
</head>
<body>
<header>
  <div>
    <div class="logo">KOR</div>
    <div class="logo-sub">Telecom Platform</div>
  </div>
  <div class="divider"></div>
  <div style="color:#64748b;font-size:13px;">Admin Dashboard</div>
</header>

<main>
  <div class="stats">
    <div class="stat-card">
      <div class="stat-icon">📱</div>
      <div class="stat-value">{total_numbers}</div>
      <div class="stat-label">Active Numbers</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon">🔑</div>
      <div class="stat-value">{total_codes}</div>
      <div class="stat-label">Codes Received</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon">📞</div>
      <div class="stat-value">{total_calls}</div>
      <div class="stat-label">Calls Handled</div>
    </div>
    <div class="stat-card">
      <div class="stat-icon">👥</div>
      <div class="stat-value">{total_users}</div>
      <div class="stat-label">Telegram Users</div>
    </div>
  </div>

  <div class="card">
    <h2>📨 Recent Verification Codes</h2>
    <table>
      <thead>
        <tr>
          <th>Number</th>
          <th>From</th>
          <th>Code</th>
          <th>Service</th>
          <th>Received</th>
        </tr>
      </thead>
      <tbody>
        {codes_html if codes_html else '<tr><td colspan="5" style="text-align:center;color:#64748b;padding:32px">No codes received yet</td></tr>'}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h2>📋 Active Virtual Numbers</h2>
    <table>
      <thead>
        <tr>
          <th>Phone Number</th>
          <th>Country</th>
          <th>Code</th>
          <th>User ID</th>
          <th>Status</th>
        </tr>
      </thead>
      <tbody>
        {numbers_html if numbers_html else '<tr><td colspan="5" style="text-align:center;color:#64748b;padding:32px">No numbers created yet</td></tr>'}
      </tbody>
    </table>
  </div>
</main>

<footer>
  © 2024 KOR Telecom — Connecting the World 🌐 &nbsp;|&nbsp; 
  <a href="/docs" style="color:#6366f1;text-decoration:none;">API Docs</a> &nbsp;|&nbsp;
  <a href="/health" style="color:#6366f1;text-decoration:none;">Health Check</a>
</footer>
</body>
</html>"""
    return HTMLResponse(content=html)

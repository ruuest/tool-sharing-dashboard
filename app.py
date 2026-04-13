import os
import secrets
from pathlib import Path
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.status import HTTP_401_UNAUTHORIZED
from models import SessionLocal, init_db, Friend, Tool, ToolAccess, ActivityLog, gen_token, utcnow

# Ensure data dir exists
Path("data").mkdir(exist_ok=True)

app = FastAPI(title="Tool Sharing Dashboard")
templates = Jinja2Templates(directory="templates")
security = HTTPBasic()

AUTH_USER = os.environ.get("BASIC_AUTH_USER", "admin")
AUTH_PASS = os.environ.get("BASIC_AUTH_PASS", "admin")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not (secrets.compare_digest(credentials.username, AUTH_USER)
            and secrets.compare_digest(credentials.password, AUTH_PASS)):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def log_activity(db, message: str):
    db.add(ActivityLog(message=message))
    db.commit()


# --- Startup ---

@app.on_event("startup")
def startup():
    init_db()


# --- Health ---

@app.get("/api/status")
def api_status():
    return JSONResponse({"status": "ok", "service": "tool-sharing-dashboard"})


# --- Dashboard ---

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: str = Depends(verify_auth), db=Depends(get_db)):
    friends_count = db.query(Friend).count()
    active_shares = db.query(ToolAccess).filter(ToolAccess.enabled == True).count()
    tools = db.query(Tool).all()
    logs = db.query(ActivityLog).order_by(ActivityLog.created_at.desc()).limit(20).all()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "friends_count": friends_count,
        "active_shares": active_shares,
        "tools": tools,
        "logs": logs,
    })


# --- Friends ---

@app.get("/friends", response_class=HTMLResponse)
def friends_list(request: Request, user: str = Depends(verify_auth), db=Depends(get_db)):
    friends = db.query(Friend).order_by(Friend.created_at.desc()).all()
    return templates.TemplateResponse("friends.html", {"request": request, "friends": friends})


@app.post("/friends")
def add_friend(
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    telegram_id: str = Form(""),
    user: str = Depends(verify_auth),
    db=Depends(get_db),
):
    friend = Friend(name=name, email=email, telegram_id=telegram_id)
    db.add(friend)
    db.commit()
    db.refresh(friend)
    # Create ToolAccess rows for all tools
    for tool in db.query(Tool).all():
        db.add(ToolAccess(friend_id=friend.id, tool_id=tool.id, enabled=False))
    db.commit()
    log_activity(db, f"友人を追加: {friend.name}")
    return RedirectResponse(url=f"/friends/{friend.id}", status_code=303)


@app.get("/friends/{friend_id}", response_class=HTMLResponse)
def friend_detail(request: Request, friend_id: int, user: str = Depends(verify_auth), db=Depends(get_db)):
    friend = db.query(Friend).filter(Friend.id == friend_id).first()
    if not friend:
        raise HTTPException(status_code=404, detail="Friend not found")
    tools = db.query(Tool).all()
    access_map = {a.tool_id: a for a in friend.accesses}
    # Ensure access rows exist for all tools
    for tool in tools:
        if tool.id not in access_map:
            ta = ToolAccess(friend_id=friend.id, tool_id=tool.id, enabled=False)
            db.add(ta)
            db.commit()
            db.refresh(ta)
            access_map[tool.id] = ta
    base_url = str(request.base_url).rstrip("/")
    invite_url = f"{base_url}/invite/{friend.invite_token}"
    return templates.TemplateResponse("friend_detail.html", {
        "request": request,
        "friend": friend,
        "tools": tools,
        "access_map": access_map,
        "invite_url": invite_url,
    })


@app.post("/friends/{friend_id}/edit")
def edit_friend(
    friend_id: int,
    name: str = Form(...),
    email: str = Form(""),
    telegram_id: str = Form(""),
    user: str = Depends(verify_auth),
    db=Depends(get_db),
):
    friend = db.query(Friend).filter(Friend.id == friend_id).first()
    if not friend:
        raise HTTPException(status_code=404, detail="Friend not found")
    friend.name = name
    friend.email = email
    friend.telegram_id = telegram_id
    db.commit()
    log_activity(db, f"友人を編集: {friend.name}")
    return RedirectResponse(url=f"/friends/{friend_id}", status_code=303)


@app.post("/friends/{friend_id}/delete")
def delete_friend(friend_id: int, user: str = Depends(verify_auth), db=Depends(get_db)):
    friend = db.query(Friend).filter(Friend.id == friend_id).first()
    if not friend:
        raise HTTPException(status_code=404, detail="Friend not found")
    name = friend.name
    db.delete(friend)
    db.commit()
    log_activity(db, f"友人を削除: {name}")
    return RedirectResponse(url="/friends", status_code=303)


@app.post("/friends/{friend_id}/toggle/{tool_id}")
def toggle_access(friend_id: int, tool_id: int, user: str = Depends(verify_auth), db=Depends(get_db)):
    access = db.query(ToolAccess).filter(
        ToolAccess.friend_id == friend_id, ToolAccess.tool_id == tool_id
    ).first()
    if not access:
        access = ToolAccess(friend_id=friend_id, tool_id=tool_id, enabled=True)
        db.add(access)
    else:
        access.enabled = not access.enabled
        access.granted_at = utcnow()
    db.commit()
    friend = db.query(Friend).filter(Friend.id == friend_id).first()
    tool = db.query(Tool).filter(Tool.id == tool_id).first()
    status = "ON" if access.enabled else "OFF"
    log_activity(db, f"{friend.name} の {tool.name} を {status} に変更")
    return RedirectResponse(url=f"/friends/{friend_id}", status_code=303)


# --- Tools ---

@app.get("/tools", response_class=HTMLResponse)
def tools_list(request: Request, user: str = Depends(verify_auth), db=Depends(get_db)):
    tools = db.query(Tool).all()
    # Count active shares per tool
    share_counts = {}
    for tool in tools:
        share_counts[tool.id] = db.query(ToolAccess).filter(
            ToolAccess.tool_id == tool.id, ToolAccess.enabled == True
        ).count()
    return templates.TemplateResponse("tools.html", {
        "request": request,
        "tools": tools,
        "share_counts": share_counts,
    })


# --- Invite (Public, no auth) ---

@app.get("/invite/{token}", response_class=HTMLResponse)
def invite_page(request: Request, token: str, db=Depends(get_db)):
    friend = db.query(Friend).filter(Friend.invite_token == token).first()
    if not friend:
        raise HTTPException(status_code=404, detail="Invalid invite link")
    enabled_accesses = [a for a in friend.accesses if a.enabled]
    tools_with_config = []
    for access in enabled_accesses:
        tools_with_config.append({
            "name": access.tool.name,
            "description": access.tool.description,
            "status": access.tool.status,
            "config_template": access.tool.config_template,
        })
    return templates.TemplateResponse("invite.html", {
        "request": request,
        "friend": friend,
        "tools": tools_with_config,
    })

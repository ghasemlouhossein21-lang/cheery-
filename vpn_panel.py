"""Multi-instance PasarGuard adapter. No active-panel setting is used."""
import database as db
import pasargad
import pasargad_panel

PANEL_LABELS={"pasargad":"پاسارگارد (PasarGuard)"}

def available_panels(): return ["pasargad"] if db.list_vpn_panels("pasargad", True) else []
def get_default_panel_id():
    panels = db.list_vpn_panels("pasargad", True)
    return int(panels[0]["id"]) if panels else None

def active_panel():
    """سازگاری با مسیرهای قدیمی: پنل فعال فعلی را برمی‌گرداند."""
    return get_panel(get_default_panel_id())
def list_panels(enabled_only=False): return db.list_vpn_panels("pasargad", enabled_only)
def get_panel(panel_id=None):
    if panel_id is not None:
        p=db.get_vpn_panel(int(panel_id))
        if p: return p
    xs=list_panels(True)
    return xs[0] if xs else None
def panel_label(panel_or_id=None):
    p=get_panel(panel_or_id)
    return (p or {}).get("name") or PANEL_LABELS["pasargad"]
_MSG="هیچ پنل پاسارگاردی به ربات وصل نیست. از بخش «مدیریت پنل‌های پاسارگارد» یک پنل اضافه کنید."
async def test_connection(panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.test_connection(p) if p else (False,None,_MSG)
async def get_system_stats(panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.get_system_stats(p) if p else (False,None,_MSG)
async def get_templates(panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.get_templates(p) if p else (False,None,_MSG)
async def get_template(template_id,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.get_template(p,template_id) if p else (False,None,_MSG)
async def create_user_from_template(template_id,username,device_limit=None,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.create_user_from_template(p,template_id,username,device_limit=device_limit) if p else (False,None,_MSG)
async def create_user_custom(template_id,username,volume_gb,days,device_limit=None,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.create_user_custom(p,template_id,username,volume_gb,days,device_limit=device_limit) if p else (False,None,_MSG)
async def renew_user(username,template_id,device_limit=None,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.renew_user(p,username,template_id,device_limit=device_limit) if p else (False,None,_MSG)
async def renew_user_custom(username,volume_gb,days,device_limit=None,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.renew_user_custom(p,username,volume_gb,days,device_limit=device_limit) if p else (False,None,_MSG)
async def renew_user_additive(username, add_volume_gb, add_days, source=None, panel_id=None):
    # حجم/زمان جدید به مقدار واقعی فعلی پنل اضافه می‌شود؛ نامحدود قبلی محدود نمی‌شود.
    if panel_id is None and source and str(source).isdigit(): panel_id=int(source)
    p=get_panel(panel_id)
    if not p: return False,None,_MSG
    ok,current,msg=await pasargad_panel.get_user(p,username)
    if not ok or not isinstance(current,dict): return False,current,msg
    try: add_gb=float(add_volume_gb or 0)
    except: add_gb=0
    try: add_days=int(add_days or 0)
    except: add_days=0
    try: cur_lim=int(current.get('data_limit') or 0)
    except: cur_lim=0
    new_gb=0 if cur_lim<=0 else cur_lim/(1024**3)+max(0,add_gb)
    import time
    try: exp=int(current.get('expire') or 0)
    except: exp=0
    if exp<=0: new_days=0
    elif exp>int(time.time()): new_days=max(1,round((exp-int(time.time()))/86400))+max(0,add_days)
    else: new_days=max(0,add_days)
    return await pasargad_panel.renew_user_custom(p,username,new_gb,new_days)

async def get_user(username,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.get_user(p,username) if p else (False,None,_MSG)
async def disable_user(username,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.disable_user(p,username) if p else (False,None,_MSG)
async def enable_user(username,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.enable_user(p,username) if p else (False,None,_MSG)
async def revoke_sub(username,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.revoke_sub(p,username) if p else (False,None,_MSG)
async def delete_user(username,panel_id=None):
    p=get_panel(panel_id); return await pasargad_panel.delete_user(p,username) if p else (False,None,_MSG)
def extract_link_and_username(payload): return pasargad.extract_link_and_username(payload)
async def warmup_cache(panel_id=None):
    p=get_panel(panel_id)
    if p:
        try: await pasargad_panel.get_templates(p)
        except Exception: pass

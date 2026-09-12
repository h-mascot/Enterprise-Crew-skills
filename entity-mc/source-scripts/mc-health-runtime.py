#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, shlex, subprocess, time, urllib.request
from pathlib import Path

def load(path, default=None):
    try: return json.loads(Path(path).read_text())
    except (OSError, ValueError): return default
def atomic(path,value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(value,separators=(",",":"))+"\n"); tmp.replace(path)
def remote_snapshot(host,cron_log,state_dir,timeout):
    status_paths=[f"{state_dir}/auto-status.json",f"{state_dir}/review-status.json"]
    cron_command=f"if test -f {shlex.quote(cron_log)}; then printf '%s\\n' {shlex.quote(cron_log)}; stat -c %Y {shlex.quote(cron_log)} 2>/dev/null || stat -f %m {shlex.quote(cron_log)}; printf '\\n\\036'; fi"
    status_commands=[f"if test -f {shlex.quote(p)}; then printf '%s\\n' {shlex.quote(p)}; stat -c %Y {shlex.quote(p)} 2>/dev/null || stat -f %m {shlex.quote(p)}; cat {shlex.quote(p)}; printf '\\036'; fi" for p in status_paths]
    script="; ".join([cron_command,*status_commands])
    result=subprocess.run(["ssh","-o",f"ConnectTimeout={timeout}","-o","BatchMode=yes",host,script],capture_output=True,text=True,timeout=timeout+3)
    if result.returncode: raise RuntimeError("ssh_unreachable")
    found={}
    for block in result.stdout.split("\x1e"):
      lines=block.strip().splitlines()
      if len(lines)>=2:
        try: found[lines[0]]={"mtime":int(lines[1]),"content":"\n".join(lines[2:])}
        except ValueError: pass
    return found
def local_snapshot(cron_log,state_dir):
    found={}
    for path in [cron_log,f"{state_dir}/auto-status.json",f"{state_dir}/review-status.json"]:
      item=Path(path)
      if item.is_file(): found[path]={"mtime":int(item.stat().st_mtime),"content":"" if path==cron_log else item.read_text()}
    return found
def parsed(snapshot,path):
    try: value=json.loads(snapshot[path]["content"])
    except (KeyError,ValueError): return None
    return value if isinstance(value,dict) and value.get("schema_version")==1 else None
def classify(agent,snapshot,now,cron_fresh,status_fresh):
    cron=agent["cron_log"]; state=agent["state_dir"]
    if agent.get("held"): return "held",agent.get("held_reason","maintenance_hold"),{}
    if cron not in snapshot: return "stale","no_cron_log",{}
    cron_age=now-snapshot[cron]["mtime"]
    if cron_age>cron_fresh: return "stale","stale_cron",{"cron_age_secs":cron_age}
    expected=agent.get("enabled_belts",agent.get("belts",["auto","review"])); by_belt={belt:parsed(snapshot,f"{state}/{belt}-status.json") for belt in expected}
    fresh=[value for value in by_belt.values() if value and now-int(value.get("checked_at_epoch") or 0)<=status_fresh]
    missing=[belt for belt,value in by_belt.items() if not value or now-int(value.get("checked_at_epoch") or 0)>status_fresh]
    if missing: return "failure","missing_fresh_outcome_status",{"cron_age_secs":cron_age,"missing_belts":missing}
    bad=[s for s in fresh if s.get("status") in {"error","attention"} or int(s.get("failed_count") or 0)>0 or int(s.get("exhausted_count") or 0)>0]
    detail={"cron_age_secs":cron_age,"belts":{s.get("belt","unknown"):s for s in fresh}}
    if bad: return "failure","runtime_failure",detail
    if any(s.get("status")=="running" for s in fresh): return "running","execution_active",detail
    return "idle_healthy","scheduler_idle",detail
def main():
    p=argparse.ArgumentParser(); p.add_argument("--dry-run",action="store_true"); args=p.parse_args()
    inventory_path=os.getenv("ENTITY_MC_HEALTH_INVENTORY","")
    if not inventory_path: print(json.dumps({"error":"health_inventory_required"})); return 2
    inventory=load(inventory_path,{}) or {}; agents=inventory.get("agents",[])
    if not agents: print(json.dumps({"error":"health_inventory_empty"})); return 2
    now=int(os.getenv("ENTITY_MC_HEALTH_NOW_EPOCH",str(int(time.time())))); cron_fresh=int(os.getenv("ENTITY_MC_HEALTH_CRON_FRESH_SECS","900")); status_fresh=int(os.getenv("ENTITY_MC_HEALTH_STATUS_FRESH_SECS","1800")); timeout=int(os.getenv("ENTITY_MC_HEALTH_SSH_TIMEOUT","5"))
    state_path=Path(os.getenv("ENTITY_MC_HEALTH_STATE",str(Path(inventory_path).parent/"health-state.json"))); previous=load(state_path,{}) or {}; current={}; alerts=[]
    for agent in agents:
      name=agent["name"]
      try:
        if agent.get("held"): status,reason,detail="held",agent.get("held_reason","maintenance_hold"),{}
        else:
          snapshot=local_snapshot(agent["cron_log"],agent["state_dir"]) if agent.get("host") in {"local","localhost",None,""} else remote_snapshot(agent["host"],agent["cron_log"],agent["state_dir"],timeout)
          status,reason,detail=classify(agent,snapshot,now,cron_fresh,status_fresh)
      except Exception as exc: status,reason,detail="unreachable","transport_failure",{"error":str(exc)}
      record={"agent":name,"status":status,"reason":reason,"checked_at_epoch":now,**detail}; current[name]=record; print(json.dumps(record,separators=(",",":")))
      old=(previous.get(name) or {}).get("status") if isinstance(previous.get(name),dict) else previous.get(name)
      if status in {"failure","stale","unreachable"} and old!=status: alerts.append(f"⚠️ **{name}**: {status} ({reason})")
    webhook=os.getenv("MC_ESCALATOR_WEBHOOK","")
    if alerts and webhook and not args.dry_run and os.getenv("ENTITY_MC_HEALTH_NO_NOTIFY")!="1":
      payload=json.dumps({"content":"🏥 **Entity MC Health Check**\n\n"+"\n".join(alerts)}).encode(); req=urllib.request.Request(webhook,data=payload,method="POST",headers={"Content-Type":"application/json"})
      try:
        with urllib.request.urlopen(req,timeout=10) as response:
          if not 200<=response.status<300: raise RuntimeError(f"webhook_http_{response.status}")
        print(json.dumps({"health_alert_sent":True}))
      except Exception as exc: print(json.dumps({"health_alert_sent":False,"error":str(exc)})); return 1
    elif alerts: print(json.dumps({"alerts":alerts,"notification":"suppressed" if args.dry_run or os.getenv("ENTITY_MC_HEALTH_NO_NOTIFY")=="1" else "not_configured"}))
    if not args.dry_run: atomic(state_path,current)
    return 0
if __name__=="__main__": raise SystemExit(main())

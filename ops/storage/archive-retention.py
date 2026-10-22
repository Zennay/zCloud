#!/usr/bin/env python3
"""Archive cold files across projects. Never delete without verified remote copy."""
import argparse, hashlib, json, os, pathlib, subprocess, sys, time
from datetime import datetime, timezone

def digest(path):
    h=hashlib.sha256()
    with open(path,"rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",required=True)
    p.add_argument("--apply",action="store_true")
    a=p.parse_args()
    config=json.load(open(a.config))
    remote=config["remote"].rstrip("/")
    if not remote.startswith("vpsarchive:"): raise SystemExit("Unexpected remote")
    for project in config["projects"]:
        root=pathlib.Path(project["root"]).resolve()
        if not root.is_dir(): continue
        age=project.get("min_age_days",30)*86400
        for sub in project.get("cold_directories",[]):
            directory=(root/sub).resolve()
            if not directory.is_relative_to(root) or not directory.is_dir(): continue
            for source in directory.rglob("*"):
                if not source.is_file() or source.is_symlink(): continue
                if time.time()-source.stat().st_mtime<age: continue
                if any(part in {"live","champion","checkpoints","active","current"} for part in source.relative_to(root).parts): continue
                rel=source.relative_to(root)
                destination=f"{remote}/{project['name']}/{rel.as_posix()}"
                print(json.dumps({"project":project["name"],"file":str(source),"remote":destination,"apply":a.apply}),flush=True)
                if not a.apply: continue
                before=source.stat()
                expected=digest(source)
                cmd=["rclone","copyto",str(source),destination,"--checksum"]
                subprocess.run(cmd,check=True,timeout=3600)
                # Independently read back the remote bytes before deleting locally.
                verify=subprocess.Popen(["rclone","cat",destination],stdout=subprocess.PIPE)
                h=hashlib.sha256()
                for chunk in iter(lambda:verify.stdout.read(1024*1024),b""): h.update(chunk)
                if verify.wait()!=0 or h.hexdigest()!=expected: raise RuntimeError(f"Archive verification failed: {source}")
                after=source.stat()
                if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino): continue
                source.unlink()
if __name__=="__main__": main()

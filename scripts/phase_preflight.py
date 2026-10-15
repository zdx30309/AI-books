import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(*args, **kwargs):
    return subprocess.check_output(args, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--control-path", default="/tmp/video-plan-inspect-ssh")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    git = lambda *arguments: run("git", "-C", str(root), *arguments)
    git("fetch", "origin")
    head = git("rev-parse", "HEAD").decode().strip()
    remote = git("rev-parse", "origin/main").decode().strip()
    assert head == remote, "远端或HEAD已移动，先核对合并，禁止覆盖协作者提交"
    status = git("status", "--porcelain").decode()
    assert args.allow_dirty or not status.strip(), "发布前工作区必须干净"
    ssh = ["ssh", "-S", args.control_path, "-o", "BatchMode=yes", "root@101.200.48.238"]
    deployed = run(*ssh, "cat /opt/video-plan/.deploy-version").decode().strip()
    deployed_commit = git("rev-parse", deployed + "^{commit}").decode().strip()
    git("merge-base", "--is-ancestor", deployed_commit, remote)
    names = git("ls-tree", "-r", "--name-only", deployed_commit, "backend/app").decode().splitlines()
    remote_script = (
        "import hashlib,json,sys;from pathlib import Path;"
        "names=json.load(sys.stdin);"
        "print(json.dumps({name:hashlib.sha256((Path('/opt/video-plan')/name).read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest() "
        "if (Path('/opt/video-plan')/name).is_file() else None for name in names}))"
    )
    import shlex
    observed = json.loads(run(*ssh, "python3 -c " + shlex.quote(remote_script), input=json.dumps(names).encode()))
    mismatches = [name for name in names if observed[name] != hashlib.sha256(
        git("show", deployed_commit + ":" + name).replace(b"\r\n", b"\n")).hexdigest()]
    assert not mismatches, "服务器源码与标记版本不一致：" + ", ".join(mismatches[:10])
    record = {"phase": args.phase, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "head": head, "origin_main": remote, "deployed": deployed_commit,
              "server_files_verified": len(names), "dirty_files": len(status.splitlines()),
              "pending_release_files": git("diff", "--name-only", deployed_commit, head).decode().splitlines()}
    journal = root / ".local/reliability_preflight.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()

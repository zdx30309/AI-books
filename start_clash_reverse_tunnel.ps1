$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:VIDEO_PLAN_SSH_HOST = "101.200.48.238"
$env:VIDEO_PLAN_SSH_USER = "root"
$env:VIDEO_PLAN_SSH_KEY = Join-Path $env:USERPROFILE ".ssh\video_plan_tunnel_rsa"
$env:VIDEO_PLAN_REMOTE_BIND = "172.18.0.1"
$env:VIDEO_PLAN_REMOTE_PORT = "7897"
$env:VIDEO_PLAN_LOCAL_PROXY_HOST = "127.0.0.1"
$env:VIDEO_PLAN_LOCAL_PROXY_PORT = "7897"
$env:VIDEO_PLAN_TUNNEL_LOG = Join-Path $root "clash_reverse_tunnel.log"
$env:VIDEO_PLAN_SSH_KEEPALIVE_SECONDS = "30"

Set-Location $root
python .\clash_reverse_tunnel.py

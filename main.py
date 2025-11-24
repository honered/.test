import os
import sqlite3
import time
from datetime import datetime

import cartopy.crs as ccrs
import httpx
import matplotlib.pyplot as plt
from cartopy.feature import ShapelyFeature
from cartopy.io.shapereader import Reader
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
LOCAL = os.environ.get("LOCAL")
SESSION = httpx.Client(timeout=30)
MAX_RUN_TIME = 5 * 60  # 5 minutes

BASE_DATA_FOLDER = "natural_earth"
OUTPUT_FOLDER = "map"
DB_FOLDER = "db"
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(DB_FOLDER, exist_ok=True)

COASTLINE_SHP = f"{BASE_DATA_FOLDER}/ne_50m_coastline.shp"
LAND_SHP = f"{BASE_DATA_FOLDER}/ne_50m_land.shp"
COUNTRIES_SHP = f"{BASE_DATA_FOLDER}/ne_50m_admin_0_countries.shp"

FIG_SIZE = (12, 9)
DPI = 300
ZOOM_DEFAULT = 2.0
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
MARKER_SIZE = 14
MARKER_SYMBOL = "o"

TITLE_FONTSIZE = 20
TITLE_WEIGHT = "bold"
TITLE_Y = 0.975
INFO_FONTSIZE = 12
INFO_Y_START = 0.935
INFO_LINE_SPACING = 0.025
TEXT_COLOR = "#000000"

TOP_MARGIN = 0.85

COASTLINE = Reader(COASTLINE_SHP)
LAND = Reader(LAND_SHP)
COUNTRIES = Reader(COUNTRIES_SHP)

MAX_ENTRIES_PER_DB = 10000


# ---------------- Database management ----------------
def get_db_connection():
    existing_files = [
        f
        for f in os.listdir(DB_FOLDER)
        if f.startswith("database_") and f.endswith(".db")
    ]
    existing_files.sort()
    if existing_files:
        latest_file = existing_files[-1]
        latest_path = os.path.join(DB_FOLDER, latest_file)
        conn = sqlite3.connect(latest_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM quakes")
        count = cur.fetchone()[0]
        if count >= MAX_ENTRIES_PER_DB:
            new_index = int(latest_file.split("_")[1].split(".")[0]) + 1
            new_path = os.path.join(DB_FOLDER, f"database_{new_index}.db")
            conn = sqlite3.connect(new_path)
    else:
        conn = sqlite3.connect(os.path.join(DB_FOLDER, "database_1.db"))

    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS quakes (
        id TEXT PRIMARY KEY,
        mag REAL,
        place TEXT,
        time INTEGER,
        updated INTEGER,
        url TEXT,
        detail TEXT,
        status TEXT,
        tsunami INTEGER,
        sig INTEGER,
        net TEXT,
        code TEXT,
        latitude REAL,
        longitude REAL,
        depth REAL
    )
    """)
    conn.commit()
    return conn, cur


conn, cur = get_db_connection()


# ---------------- Utilities ----------------
def saveEarthquake(i):
    global conn, cur
    p = i["properties"]
    g = i["geometry"]["coordinates"]

    cur.execute(
        """
        INSERT OR REPLACE INTO quakes (
            id, mag, place, time, updated, url, detail, status,
            tsunami, sig, net, code, latitude, longitude, depth
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            i["id"],
            p.get("mag"),
            p.get("place"),
            p.get("time"),
            p.get("updated"),
            p.get("url"),
            p.get("detail"),
            p.get("status"),
            p.get("tsunami"),
            p.get("sig"),
            p.get("net"),
            p.get("code"),
            g[1],
            g[0],
            g[2],
        ),
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM quakes")
    if cur.fetchone()[0] >= MAX_ENTRIES_PER_DB:
        conn.close()
        conn, cur = get_db_connection()


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


# ---------------- Telegram ----------------
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

    caption = f"""
<b>{p.get("title", p.get("place", "No title found"))}</b>

ID: <code>{i["id"]}</code>
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
                print(f"Sent to Telegram: {i['id']}", flush=True)
                saveEarthquake(i)
                return
            else:
                print(f"Telegram API error: {res_json}", flush=True)
        except Exception as e:
            print(f"Error sending to Telegram (attempt {attempt + 1}): {e}", flush=True)
        attempt += 1
        time.sleep(2)

    print(f"Failed to send {i['id']} after {retries} attempts. Exiting.", flush=True)
    exit(1)


# ---------------- Map plotting ----------------
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
    print(f"Saved → {filename}", flush=True)


# ---------------- Processing ----------------
def process_earthquake(i):
    loc = i["geometry"]["coordinates"]
    lon, lat, _ = loc
    print(lat, lon, flush=True)
    plot_offline_map(lat, lon, i)
    sendToTelegram(i)


# ---------------- Main ----------------
if __name__ == "__main__":
    r = SESSION.get(
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson"
    ).json()["features"]
    r = sorted(r, key=lambda x: x["properties"]["time"])

    cur.execute("SELECT id FROM quakes")
    existing_ids = {row[0] for row in cur.fetchall()}

    startTime = time.time()

    new_quakes = [i for i in r if i["id"] not in existing_ids]
    for i in new_quakes:
        if i["properties"]["type"] != "earthquake":
            continue
        x = i["properties"]["place"]
        i["properties"]["place"] = x[0].upper() + x[1:]

        process_earthquake(i)
        print()

        if not LOCAL and time.time() - startTime >= MAX_RUN_TIME:
            break

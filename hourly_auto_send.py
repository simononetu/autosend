import requests
from datetime import datetime, timezone, timedelta
import json
import os
import sys
import hashlib
import logging
import asyncio
import telegram
import pytz

# 定義台灣時區常數
TAIWAN_TZ = pytz.timezone('Asia/Taipei')

def get_taiwan_time():
    """取得台灣當前時間"""
    return datetime.now(TAIWAN_TZ)

# 設定日誌
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === 從檔案讀取 API Keys 和 Chat ID ===
API_KEY_FILE = "API-KEY.txt"
TELEGRAM_TOKEN_FILE = "TELEGRAM-TOKEN.txt"
CHAT_ID_FILE = "CHAT-ID.txt"

def load_api_keys():
    if not os.path.exists(API_KEY_FILE):
        raise FileNotFoundError(f"找不到 '{API_KEY_FILE}'，請建立並輸入 CWA API Key")
    if not os.path.exists(TELEGRAM_TOKEN_FILE):
        raise FileNotFoundError(f"找不到 '{TELEGRAM_TOKEN_FILE}'，請建立並輸入 Telegram Bot Token")
    if not os.path.exists(CHAT_ID_FILE):
        raise FileNotFoundError(f"找不到 '{CHAT_ID_FILE}'，請建立並輸入 Telegram 聊天 ID")
    
    with open(API_KEY_FILE, "r", encoding="utf-8") as f:
        cwa_key = f.read().strip()
    with open(TELEGRAM_TOKEN_FILE, "r", encoding="utf-8") as f:
        telegram_token = f.read().strip()
    with open(CHAT_ID_FILE, "r", encoding="utf-8") as f:
        chat_id = f.read().strip()
    
    if not cwa_key or not telegram_token or not chat_id:
        raise ValueError("API Key、Token 或 Chat ID 為空")
    return cwa_key, telegram_token, chat_id

# === CWA 即時觀測資料 (O-A0001-001) ===
DATA_ID = "O-A0001-001"
TW_TIMEZONE = timezone(timedelta(hours=8))

def convert_to_local_time(utc_time_str):
    if not utc_time_str:
        return ""
    if 'T' not in utc_time_str:
        return utc_time_str
    try:
        dt = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
        return dt.astimezone(TW_TIMEZONE).strftime("%m/%d %H:%M")
    except Exception as e:
        logger.warning(f"時間轉換失敗: {utc_time_str} -> {e}")
        return utc_time_str.split('T')[1][:5] if 'T' in utc_time_str else ""

def safe_id(name):
    return "station-" + hashlib.md5(name.encode('utf-8')).hexdigest()[:8]

def fetch_weather_data(api_key):
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{DATA_ID}"
    params = {"Authorization": api_key, "format": "JSON", "limit": 1000}
    
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data.get("success") != "true":
            raise Exception(f"API 錯誤: {data.get('result', {}).get('message', '未知錯誤')}")
        return data
    except requests.exceptions.RequestException as e:
        raise Exception(f"請求失敗: {e}")

def parse_weather_data(data):
    if "records" not in data or "Station" not in data["records"]:
        raise ValueError("API 回傳格式異常,缺少 Station 資料")
    
    stations = data["records"]["Station"]
    rows = []
    
    # === 除錯：檢查前3個測站的資料結構 ===
    logger.info("=== 資料結構檢查 ===")
    for i, station in enumerate(stations[:3]):
        station_name = station.get("StationName", "")
        elements = station.get("WeatherElement", {})
        logger.info(f"\n測站 {i+1}: {station_name}")
        logger.info(f"WeatherElement keys: {list(elements.keys())}")
        
        # 檢查 GustInfo
        if "GustInfo" in elements:
            logger.info(f"  GustInfo 內容: {elements['GustInfo']}")
        else:
            logger.info(f"  GustInfo: 不存在")
        
        # 檢查 Now
        if "Now" in elements:
            logger.info(f"  Now 內容: {elements['Now']}")
        else:
            logger.info(f"  Now: 不存在")
        
        # 檢查所有可能的雨量和陣風欄位
        possible_fields = ['Gust', 'Rainfall', 'Precipitation', 'RAIN', 'GUST']
        for field in possible_fields:
            if field in elements:
                logger.info(f"  發現欄位 {field}: {elements[field]}")
    
    logger.info("\n=== 開始解析資料 ===\n")
    
    for station in stations:
        station_name = station.get("StationName", "")
        geo_info = station.get("GeoInfo", {})
        county_name = geo_info.get("CountyName", "未知縣市")
        
        # 時間
        obs_time = station.get("ObsTime", {})
        datetime_str = obs_time.get("DateTime", "") if isinstance(obs_time, dict) else ""
        local_time = convert_to_local_time(datetime_str)

        # 氣象要素
        elements = station.get("WeatherElement", {})
        
        # === 基本欄位處理 ===
        def get_value(key):
            """取得欄位值，過濾異常值"""
            val = elements.get(key, "")
            if isinstance(val, dict):
                val = val.get("value", "")
            if val in ("-99", "-99.0", "-999", "NA", "X", ""):
                return ""
            return str(val)
        
        # === 陣風：嘗試多種可能的路徑 ===
        gust_value = ""
        # 方法1: GustInfo.PeakGustSpeed
        if isinstance(elements.get("GustInfo"), dict):
            gust = elements["GustInfo"].get("PeakGustSpeed", "")
            if gust and gust not in ("-99", "-99.0", "-999", "NA", "X", ""):
                gust_value = str(gust)
        # 方法2: 直接的 Gust 欄位
        if not gust_value:
            gust_value = get_value("Gust")
        # 方法3: GUST 欄位
        if not gust_value:
            gust_value = get_value("GUST")
        
        # === 累積雨量：嘗試多種可能的路徑 ===
        rainfall_value = ""
        # 方法1: Now.Precipitation
        if isinstance(elements.get("Now"), dict):
            precip = elements["Now"].get("Precipitation", "")
            if precip and precip not in ("-99", "-99.0", "-999", "NA", "X", "", "-98"):
                if precip == "T":
                    rainfall_value = "雨跡"
                else:
                    rainfall_value = str(precip)
        # 方法2: 直接的 Precipitation 欄位
        if not rainfall_value:
            precip = get_value("Precipitation")
            if precip:
                if precip == "T":
                    rainfall_value = "雨跡"
                else:
                    rainfall_value = precip
        # 方法3: Rainfall 欄位
        if not rainfall_value:
            rainfall_value = get_value("Rainfall")
        # 方法4: RAIN 欄位
        if not rainfall_value:
            rainfall_value = get_value("RAIN")

        row = {
            "站名": station_name,
            "時間": local_time or "無資料",
            "DateTime": datetime_str,
            "CountyName": county_name,
            "AirPressure": get_value("AirPressure"),
            "AirTemperature": get_value("AirTemperature"),
            "WindDirection": get_value("WindDirection"),
            "WindSpeed": get_value("WindSpeed"),
            "Gust": gust_value,
            "Rainfall": rainfall_value,
            "RelativeHumidity": get_value("RelativeHumidity"),
            "Weather": get_value("Weather"),
        }
        rows.append(row)
    
    # 統計有資料的站數
    total_stations = len(rows)
    gust_count = sum(1 for r in rows if r["Gust"])
    rainfall_count = sum(1 for r in rows if r["Rainfall"])
    
    logger.info(f"總測站數: {total_stations}")
    logger.info(f"有陣風資料: {gust_count} 站 ({gust_count/total_stations*100:.1f}%)")
    logger.info(f"有雨量資料: {rainfall_count} 站 ({rainfall_count/total_stations*100:.1f}%)")
    
    grouped_data = {}
    id_map = {}
    
    for row in rows:
        key = row["CountyName"]
        grouped_data.setdefault(key, []).append(row)
        if key not in id_map:
            id_map[key] = safe_id(key)
    
    for items in grouped_data.values():
        items.sort(key=lambda x: x["時間"], reverse=True)
    
    return grouped_data, id_map

def generate_html(grouped_data, id_map):
    location_names = sorted(grouped_data.keys())
    js_data = json.dumps(grouped_data, ensure_ascii=False)
    js_id_map = json.dumps(id_map, ensure_ascii=False)
    
    # 手機優化：每4個換行、縮小間距
    radio_html = ""
    for i, name in enumerate(location_names):
        checked = 'checked' if i == 0 else ''
        radio_html += f'''
        <label style="margin-right: 12px; font-size: 0.92em; display: inline-block; margin-bottom: 8px;">
            <input type="radio" name="county" value="{name}" {checked} style="margin-right: 4px;"> {name}
        </label>'''
        if (i + 1) % 4 == 0:
            radio_html += "<br>"

    text_html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>臺灣氣象觀測站即時資料 (O-A0001-001)</title>
    <style>
        body {{ 
            font-family: 'Microsoft JhengHei', 'Segoe UI', sans-serif; 
            margin: 12px;
            background: #f9f9fb; 
            color: #333;
            line-height: 1.5;
        }}
        h1 {{ 
            color: #004a99; 
            border-bottom: 2px solid #004a99;
            padding-bottom: 8px;
            font-size: 1.4em;
            margin-bottom: 16px;
        }}
        #controls {{ 
            background: #fff; 
            padding: 16px;
            border-radius: 12px;
            box-shadow: 0 3px 10px rgba(0,0,0,0.08);
            margin-bottom: 18px;
            font-size: 0.95em;
        }}
        .radio-group {{ 
            line-height: 2.2; 
        }}
        .location-container {{ 
            margin-bottom: 32px;
            padding: 18px; 
            background: #fff; 
            border-radius: 10px; 
            box-shadow: 0 2px 8px rgba(0,0,0,0.06); 
            border-left: 4px solid #007bff;
        }}
        .table-wrapper {{
            max-height: 68vh;
            overflow: auto;
            -webkit-overflow-scrolling: touch;
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            margin-top: 12px;
        }}
        .weather-table {{ 
            width: 100%; 
            border-collapse: collapse; 
            font-size: 0.88em;
        }}
        .weather-table thead th {{
            position: sticky;
            top: 0;
            z-index: 10;
            background: #e3f2fd;
            border: 1px solid #ddd;
            padding: 10px 6px;
            text-align: center;
            white-space: nowrap;
            font-weight: bold;
            font-size: 0.88em;
            box-shadow: 0 2px 3px rgba(0,0,0,0.08);
        }}
        .weather-table td {{ 
            border: 1px solid #eee; 
            padding: 7px 5px; 
            text-align: center;
            white-space: nowrap;
            font-size: 0.84em;
        }}
        .weather-table tbody tr:nth-child(even) {{ 
            background: #fdfdff; 
        }}
        .weather-table tbody tr:hover {{
            background: #e8f5e9;
        }}
        .no-data {{ 
            color: #777; 
            text-align: center; 
            padding: 28px; 
            font-size: 0.9em;
        }}
        footer {{
            margin-top: 28px;
            padding: 16px;
            background: #fff;
            border-top: 1px solid #eee;
            font-size: 0.78em;
            color: #666;
            text-align: center;
        }}
        @media (max-width: 768px) {{
            body {{ margin: 8px; }}
            h1 {{ font-size: 1.25em; padding-bottom: 6px; margin-bottom: 12px; }}
            #controls {{ padding: 14px; border-radius: 10px; }}
            .radio-group label {{
                margin-right: 10px !important;
                margin-bottom: 7px;
                font-size: 0.9em;
            }}
            .location-container {{ padding: 14px; margin-bottom: 24px; border-radius: 8px; }}
            .table-wrapper {{ max-height: 58vh; border-radius: 5px; }}
            .weather-table {{ font-size: 0.78em; }}
            .weather-table thead th {{ padding: 8px 4px; font-size: 0.82em; }}
            .weather-table td {{ padding: 6px 3px; font-size: 0.78em; }}
            footer {{ font-size: 0.74em; padding: 12px; }}
        }}
        @media (max-width: 380px) {{
            .radio-group label {{ margin-right: 8px !important; font-size: 0.86em; }}
            .weather-table {{ font-size: 0.74em; }}
            .weather-table thead th, .weather-table td {{ padding: 5px 2px; }}
        }}
    </style>
</head>
<body>
    <h1>臺灣氣象觀測站即時資料</h1>
    <div id="controls">
        <div class="radio-group">
            <strong>請選擇縣市：</strong><br>
            {radio_html}
        </div>
    </div>
    <div id="weatherOutput">
        <p class="no-data">請選擇一個縣市以查看即時觀測資料</p>
    </div>
    <footer>
        <p>
            資料來源: 交通部中央氣象署 (CWA) | 資料集: {DATA_ID} | 
            生成時間: {get_taiwan_time().strftime("%Y-%m-%d %H:%M:%S")}
        </p>
    </footer>
    <script>
        const ALL_DATA = {js_data};
        const radios = document.querySelectorAll('input[name="county"]');
        const output = document.getElementById('weatherOutput');

        function renderCounty(county, data) {{
            let html = `<div class="location-container">`;
            html += `<h2 style="margin:0 0 12px 0; font-size:1.1em; color:#007bff;">${{county}}（共 ${{data.length}} 站）</h2>`;
            if (data && data.length > 0) {{
                html += `<div class="table-wrapper"><table class="weather-table"><thead><tr>
                    <th>站名</th><th>時間</th><th>氣壓</th><th>氣溫</th><th>風向</th>
                    <th>風速</th><th>陣風</th><th>累積雨量</th><th>相對溼度</th><th>天氣</th>
                </tr></thead><tbody>`;
                data.forEach(row => {{
                    html += `<tr>
                        <td>${{row['站名'] || '-'}}</td>
                        <td>${{row['時間'] || '-'}}</td>
                        <td>${{row['AirPressure'] || '-'}}</td>
                        <td>${{row['AirTemperature'] ? row['AirTemperature'] + '°C' : '-'}}</td>
                        <td>${{row['WindDirection'] || '-'}}</td>
                        <td>${{row['WindSpeed'] || '-'}}</td>
                        <td>${{row['Gust'] || '-'}}</td>
                        <td>${{row['Rainfall'] || '-'}}</td>
                        <td>${{row['RelativeHumidity'] ? row['RelativeHumidity'] + '%' : '-'}}</td>
                        <td>${{row['Weather'] || '-'}}</td>
                    </tr>`;
                }});
                html += `</tbody></table></div>`;
            }} else {{
                html += `<p class="no-data">查無資料。</p>`;
            }}
            html += `</div>`;
            return html;
        }}

        function updateDisplay() {{
            const selected = document.querySelector('input[name="county"]:checked');
            if (!selected) {{ output.innerHTML = '<p class="no-data">請選擇一個縣市</p>'; return; }}
            const county = selected.value;
            output.innerHTML = '<div style="padding:16px; text-align:center; color:#666;">載入中...</div>';
            setTimeout(() => {{
                try {{ output.innerHTML = renderCounty(county, ALL_DATA[county]); }}
                catch (e) {{ output.innerHTML = `<p style="color:red; padding:16px;">${{county}} 載入失敗</p>`; }}
            }}, 80);
        }}

        radios.forEach(radio => radio.addEventListener('change', updateDisplay));
        window.addEventListener('load', () => setTimeout(updateDisplay, 150));
    </script>
</body>
</html>"""
    return text_html

async def main():
    try:
        cwa_key, telegram_token, chat_id = load_api_keys()
        logger.info("正在從 CWA 抓取資料...")
        data = fetch_weather_data(cwa_key)
        
        # 儲存原始 JSON 供檢查
        debug_file = f"debug_raw_data_{get_taiwan_time().strftime('%Y%m%d_%H%M%S')}.json"
        with open(debug_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"原始資料已儲存至: {debug_file}")
        
        logger.info("正在解析資料...")
        grouped_data, id_map = parse_weather_data(data)
        
        logger.info("正在產生報告...")
        html_content = generate_html(grouped_data, id_map)
        
        timestamp = get_taiwan_time().strftime('%Y%m%d_%H%M%S')
        filename = f"realtime_weather_{timestamp}.html"
        
        with open(filename, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        logger.info("資料處理完成，正在傳送檔案到 Telegram...")
        
        bot = telegram.Bot(token=telegram_token)
        async with bot:
            with open(filename, "rb") as f:
                await bot.send_document(
                    chat_id=chat_id,
                    document=f,
                    filename="臺灣氣象觀測站即時資料_手機優化版.html",
                    caption=(
                        f"即時觀測資料已產生！\n"
                        f"縣市數：{len(grouped_data)}\n"
                        f"總站數：{sum(len(v) for v in grouped_data.values())}\n"
                        f"產生時間：{get_taiwan_time().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                        f"請下載後用瀏覽器開啟，選擇您想看的縣市"
                    )
                )
        
        os.remove(filename)
        logger.info("傳送完成！")
        logger.info(f"除錯檔案保留: {debug_file}")
        
    except Exception as e:
        logger.error(f"錯誤: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
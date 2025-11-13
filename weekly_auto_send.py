import requests
from datetime import datetime, timezone, timedelta
import json
import os
import hashlib
import logging

# 設定日誌
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === 檔案設定 ===
API_KEY_FILE = "API-KEY.txt"
TELEGRAM_TOKEN_FILE = "TELEGRAM-TOKEN.txt"
CHAT_ID_FILE = "CHAT-ID.txt"

# 固定輸出的 HTML 檔名
OUTPUT_HTML_FILENAME = "1週12小時天氣預報.html"

# === 讀取設定 ===
def load_config():
    def read_file(path, name):
        if not os.path.exists(path):
            raise FileNotFoundError(f"找不到 '{path}'，請建立並填入 {name}")
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
        if not value:
            raise ValueError(f"{name} 不能為空")
        return value

    cwa_key = read_file(API_KEY_FILE, "CWA API Key")
    bot_token = read_file(TELEGRAM_TOKEN_FILE, "Telegram Bot Token")
    chat_id = read_file(CHAT_ID_FILE, "目標 Chat ID (群組/頻道)")
    return cwa_key, bot_token, chat_id

# === CWA 設定 ===
DATA_ID = "F-D0047-091"
TW_TIMEZONE = timezone(timedelta(hours=8))

def convert_to_local_time(utc_time_str):
    """轉換 UTC 為台灣時間"""
    if not utc_time_str:
        return ""
    try:
        dt = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
        return dt.astimezone(TW_TIMEZONE).strftime("%m/%d %H:%M")
    except:
        return utc_time_str.split('T')[1][:5] if 'T' in utc_time_str else ""

def safe_id(name):
    return "chart-" + hashlib.md5(name.encode('utf-8')).hexdigest()[:8]

def fetch_weather_data(api_key):
    url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{DATA_ID}"
    params = {"Authorization": api_key, "format": "JSON"}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("success") != "true":
        raise Exception(f"API 錯誤: {data.get('result', {}).get('message')}")
    return data

def parse_weather_data(data):
    locations = data["records"]["Locations"][0]["Location"]
    csv_rows = []
    
    ELEMENT_MAP = {
        "最高溫度": "MaxTemperature",
        "最低溫度": "MinTemperature",
        "天氣現象": "Weather",
        "天氣代碼": "WeatherCode"
    }
    TIME_BASE_KEY = "天氣現象"
    
    for loc in locations:
        location_name = loc.get("LocationName", "未知地點")
        elements = {e["ElementName"]: e for e in loc.get("WeatherElement", [])}
        time_base = elements.get(TIME_BASE_KEY)
        if not time_base or not time_base.get("Time"): continue
        
        for t in time_base["Time"]:
            start_raw = t.get("StartTime") or t.get("startTime")
            start = convert_to_local_time(start_raw)
            
            row = {
                "縣市/鄉鎮": location_name,
                "時間": start,
                "天氣現象": "", "天氣代碼": "",
                "最高溫": "", "最低溫": "",
                "最高溫數值": 0, "最低溫數值": 0
            }
            
            for elem_name, key in ELEMENT_MAP.items():
                elem = elements.get(elem_name)
                if not elem: continue
                matched = next((x for x in elem.get("Time", []) if
                               (x.get("StartTime") or x.get("startTime")) == start_raw), None)
                if not matched: continue
                val = matched.get("ElementValue")
                if isinstance(val, list): val = val[0].get(key, "") if val else ""
                elif isinstance(val, dict): val = val.get(key, "")
                
                if elem_name == "最高溫度":
                    row["最高溫"] = f"{val} °C" if val else ""
                    row["最高溫數值"] = float(val) if val and val.replace('.','').replace('-','').isdigit() else 0
                elif elem_name == "最低溫度":
                    row["最低溫"] = f"{val} °C" if val else ""
                    row["最低溫數值"] = float(val) if val and val.replace('.','').replace('-','').isdigit() else 0
                elif elem_name == "天氣現象":
                    row["天氣現象"] = val
                elif elem_name == "天氣代碼":
                    row["天氣代碼"] = val
            
            csv_rows.append(row)
    
    grouped_data = {}
    id_map = {}
    for row in csv_rows:
        loc = row["縣市/鄉鎮"]
        grouped_data.setdefault(loc, []).append(row)
        if loc not in id_map:
            id_map[loc] = safe_id(loc)
    
    for rows in grouped_data.values():
        rows.sort(key=lambda x: x["時間"])
    
    return grouped_data, id_map

def generate_html(grouped_data, id_map):
    location_names = sorted(grouped_data.keys())
    js_data = json.dumps(grouped_data, ensure_ascii=False)
    js_id_map = json.dumps(id_map, ensure_ascii=False)
    
    checkboxes_html = ""
    for i, name in enumerate(location_names):
        checkboxes_html += f'<label style="margin-right: 16px; font-size: 0.95em;"><input type="checkbox" value="{name}" checked> {name}</label>'
        if (i + 1) % 7 == 0:
            checkboxes_html += "<br>"
    
    generate_time = datetime.now(TW_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>臺灣 1週逐12小時天氣預報 (文字版)</title>
    <style>
        body {{ font-family: 'Microsoft JhengHei', 'Segoe UI', sans-serif; margin: 20px; background: #f7f7f7; color: #333; }}
        h1 {{ color: #004a99; border-bottom: 3px solid #004a99; padding-bottom: 10px; }}
        #controls {{ background: #fff; padding: 20px; border-radius: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); margin-bottom: 20px; }}
        .checkbox-group {{ line-height: 2.2; }}
        .select-all {{ margin-bottom: 12px; font-weight: bold; }}
        .location-container {{ margin-bottom: 40px; padding: 20px; background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); border-left: 5px solid #007bff; display: none; }}
        .weather-table {{ width: 100%; border-collapse: collapse; }}
        .weather-table th, .weather-table td {{ border: 1px solid #eee; padding: 10px; text-align: center; }}
        .weather-table th {{ background: #e9f5ff; font-weight: bold; }}
        .weather-table tr:nth-child(even) {{ background: #fcfdff; }}
        .no-data {{ color: #888; text-align: center; padding: 30px; }}
        .code {{ font-size: 0.9em; color: #666; }}
        @media (max-width: 768px) {{ .checkbox-group label {{ display: inline-block; margin: 6px 10px 6px 0; }} }}
    </style>
</head>
<body>
    <h1>臺灣 1週逐12小時天氣預報 (文字版)</h1>
    <div id="controls">
        <div class="select-all"><label><input type="checkbox" id="selectAll" checked> 全選 / 取消全選</label></div>
        <div class="checkbox-group">{checkboxes_html}</div>
    </div>
    <div id="weatherOutput"><p class="no-data">正在載入資料...</p></div>

    <script>
        const ALL_DATA = {js_data};
        const ID_MAP = {js_id_map};
        const checkboxes = document.querySelectorAll('.checkbox-group input[type="checkbox"]');
        const selectAll = document.getElementById('selectAll');
        const output = document.getElementById('weatherOutput');

        function renderLocation(name, data) {{
            const chartId = ID_MAP[name];
            let html = `<div class="location-container" id="container-${{chartId}}"><h2 style="margin:0 0 15px 0;">${{name}} 的天氣預報</h2>`;
            if (data && data.length > 0) {{
                html += `<table class="weather-table"><thead><tr><th>時間</th><th>天氣</th><th>最高溫</th><th>最低溫</th></tr></thead><tbody>`;
                data.forEach(row => {{
                    const code = row['天氣代碼'] ? ` <span class="code">(${{row['天氣代碼']}})</span>` : '';
                    html += `<tr><td>${{row['時間']}}</td><td>${{row['天氣現象']}}${{code}}</td><td>${{row['最高溫']}}</td><td>${{row['最低溫']}}</td></tr>`;
                }});
                html += `</tbody></table>`;
            }} else {{
                html += `<p class="no-data">查無資料。</p>`;
            }}
            html += `</div>`;
            return {{ html, chartId }};
        }}

        function updateDisplay() {{
            const selected = Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);
            output.innerHTML = selected.length === 0 ? '<p class="no-data">請勾選上方縣市以查看天氣預報。</p>' : '<div style="padding:10px;">載入中...</div>';
            setTimeout(() => {{
                output.innerHTML = '';
                selected.forEach(name => {{
                    try {{
                        const {{ html, chartId }} = renderLocation(name, ALL_DATA[name]);
                        output.insertAdjacentHTML('beforeend', html);
                        document.getElementById(`container-${{chartId}}`).style.display = 'block';
                    }} catch (e) {{
                        output.insertAdjacentHTML('beforeend', `<p style="color:red;">${{name}} 載入失敗</p>`);
                    }}
                }});
            }}, 100);
        }}

        checkboxes.forEach(cb => cb.addEventListener('change', updateDisplay));
        selectAll.addEventListener('change', () => {{ checkboxes.forEach(cb => cb.checked = selectAll.checked); updateDisplay(); }});
        window.addEventListener('load', () => setTimeout(updateDisplay, 200));
    </script>

    <hr style="margin-top: 50px;">
    <footer>
        <p style="font-size: 0.8em; color: #666;">
            資料來源: 交通部中央氣象署 (CWA) | 資料集: {DATA_ID} | 
            生成時間: {generate_time} (台灣時間)
        </p>
    </footer>
</body>
</html>"""

# === 發送 Telegram ===
def send_to_telegram(bot_token, chat_id, html_content):
    # 使用固定檔名
    filename = OUTPUT_HTML_FILENAME
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    with open(filename, "rb") as f:
        files = {'document': f}
        try:
            data_start = html_content.find('const ALL_DATA =') + len('const ALL_DATA =')
            data_end = html_content.find(';', data_start)
            data_str = html_content[data_start:data_end]
            location_count = len(json.loads(data_str))
        except:
            location_count = "未知"
            
        data = {
            'chat_id': chat_id,
            'caption': f"天氣預報已更新！\n\n"
                       f"地點數：{location_count} 個\n"
                       f"更新時間：{datetime.now(TW_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} (台灣時間)\n\n"
                       f"請下載後用瀏覽器開啟"
        }
        response = requests.post(url, data=data, files=files, timeout=60)
    
    # 刪除暫存檔
    if os.path.exists(filename):
        os.remove(filename)
    
    if response.status_code != 200:
        raise Exception(f"Telegram 發送失敗: {response.text}")
    logger.info("已成功發送到 Telegram")

# === 主程式 ===
def main():
    try:
        logger.info("開始執行天氣報告產生與發送...")
        cwa_key, bot_token, chat_id = load_config()
        
        logger.info("正在抓取 CWA 資料...")
        data = fetch_weather_data(cwa_key)
        
        logger.info("正在解析資料...")
        grouped_data, id_map = parse_weather_data(data)
        
        logger.info("正在產生 HTML...")
        html = generate_html(grouped_data, id_map)
        
        logger.info("正在發送到 Telegram...")
        send_to_telegram(bot_token, chat_id, html)
        
        logger.info("完成！報告已發送。")
        
    except Exception as e:
        logger.error(f"執行失敗: {e}")
        print(f"錯誤: {e}")

if __name__ == "__main__":
    main()
import os
import openpyxl
from openpyxl import Workbook
import datetime

def get_excel_path(chat_id):
    """Returns the path for the excel file, prioritizing Google Drive if configured."""
    base_dir = os.getenv("GOOGLE_DRIVE_PATH")
    if not base_dir or not os.path.exists(base_dir):
        base_dir = 'data/excel'
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f'media_log_{chat_id}.xlsx')

def log_media_to_excel(chat_id, data):
    """Logs extracted media info to an Excel file per chat ID."""
    filename = get_excel_path(chat_id)
    
    headers = ['Timestamp', 'Filename', 'File Type', 'Extracted Summary', 'Key Entities']
    
    if not os.path.exists(filename):
        wb = Workbook()
        ws = wb.active
        ws.title = "Media Log"
        ws.append(headers)
    else:
        wb = openpyxl.load_workbook(filename)
        ws = wb.active
        
    row = [
        datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        data.get('filename', ''),
        data.get('file_type', ''),
        data.get('summary', ''),
        data.get('entities', '')
    ]
    ws.append(row)
    wb.save(filename)
    return filename

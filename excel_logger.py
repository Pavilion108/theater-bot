import os
import openpyxl
from openpyxl import Workbook
import datetime

def log_media_to_excel(chat_id, data):
    """Logs extracted media info to an Excel file per chat ID."""
    os.makedirs('data/excel', exist_ok=True)
    filename = f'data/excel/media_log_{chat_id}.xlsx'
    
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

import csv
import datetime
from io import BytesIO, StringIO
from zoneinfo import ZoneInfo
from django.http import HttpResponse
from django.utils import timezone
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

TAIPEI_TZ = ZoneInfo("Asia/Taipei")


def format_cell_value(val):
    if val is None:
        return ""
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, datetime.datetime):
        if timezone.is_aware(val):
            val = val.astimezone(TAIPEI_TZ)
        else:
            val = val.replace(tzinfo=TAIPEI_TZ)
        return val.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, datetime.date):
        return val.strftime("%Y-%m-%d")
    return str(val)


def _auto_fit_columns(ws, min_width=12, max_width=50):
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            val_str = str(cell.value or "")
            # 若包含換行，取最長的一行計算寬度
            lines = val_str.split("\n")
            for line in lines:
                length = sum(2 if ord(c) > 127 else 1 for c in line)
                if length > max_len:
                    max_len = length
        ws.column_dimensions[col_letter].width = min(max(max_len + 4, min_width), max_width)


def export_to_excel(filename_prefix: str, sheet_name: str, headers: list, rows: list) -> HttpResponse:
    """
    產生包含專業美化樣式與自適應欄寬的 Excel (.xlsx) 回應。
    所有時間日期統一為 Asia/Taipei 時區。
    """
    now = datetime.datetime.now(TAIPEI_TZ)
    filename = f"{filename_prefix}_{now.strftime('%Y%m%d_%H%M%S')}.xlsx"

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]  # Excel 活頁簿名稱長度上限 31 字元

    # 樣式定義
    header_font = Font(name="微軟正黑體", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")  # Slate-700
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=False)

    data_font = Font(name="微軟正黑體", size=10)
    data_align = Alignment(vertical="center")

    thin_border_side = Side(style="thin", color="E2E8F0")
    thin_border = Border(
        left=thin_border_side,
        right=thin_border_side,
        top=thin_border_side,
        bottom=thin_border_side,
    )

    # 寫入表頭
    ws.append(headers)
    ws.row_dimensions[1].height = 28

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # 寫入資料
    for row_idx, row_data in enumerate(rows, start=2):
        formatted_row = [format_cell_value(val) for val in row_data]
        ws.append(formatted_row)
        ws.row_dimensions[row_idx].height = 20

        for col_idx in range(1, len(formatted_row) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = data_font
            cell.alignment = data_align
            cell.border = thin_border

    _auto_fit_columns(ws)

    # 凍結首列
    ws.freeze_panes = "A2"

    # 輸出至記憶體串流
    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    # Content-Disposition 支援標準檔名
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def generate_import_template_excel(
    filename_prefix: str,
    main_sheet_name: str,
    headers: list[str],
    required_indices: set[int],
    sample_rows: list[list],
    guide_sections: list[dict],
    dropdown_configs: list[dict] = None,
) -> HttpResponse:
    """
    產生雙分頁智慧匯入範本 (.xlsx)：
    - Sheet 1: 資料填寫區（第 1 列為表頭，必填欄位以深藍/紅星標示或醒目底色，後續為範例資料列，並套用下拉選單驗證）
    - Sheet 2: 系統現有代碼對照與填寫說明（多個說明區塊表格）
    """
    now = datetime.datetime.now(TAIPEI_TZ)
    filename = f"{filename_prefix}_{now.strftime('%Y%m%d')}.xlsx"

    wb = openpyxl.Workbook()
    ws_main = wb.active
    ws_main.title = main_sheet_name[:31]

    # 樣式定義
    req_header_font = Font(name="微軟正黑體", size=11, bold=True, color="FFFFFF")
    req_header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")  # 深藍 (必填)
    opt_header_font = Font(name="微軟正黑體", size=11, bold=True, color="FFFFFF")
    opt_header_fill = PatternFill(start_color="475569", end_color="475569", fill_type="solid")  # 灰藍 (選填)
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=False)

    data_font = Font(name="微軟正黑體", size=10)
    data_align = Alignment(vertical="center")

    thin_border_side = Side(style="thin", color="CBD5E1")
    thin_border = Border(
        left=thin_border_side,
        right=thin_border_side,
        top=thin_border_side,
        bottom=thin_border_side,
    )

    # Sheet 1: 寫入表頭
    ws_main.append(headers)
    ws_main.row_dimensions[1].height = 28

    for col_idx in range(1, len(headers) + 1):
        cell = ws_main.cell(row=1, column=col_idx)
        is_req = (col_idx - 1) in required_indices
        cell.font = req_header_font if is_req else opt_header_font
        cell.fill = req_header_fill if is_req else opt_header_fill
        cell.alignment = header_align
        cell.border = thin_border

    # Sheet 1: 寫入範例資料
    for row_idx, row_data in enumerate(sample_rows, start=2):
        formatted_row = [format_cell_value(val) for val in row_data]
        ws_main.append(formatted_row)
        ws_main.row_dimensions[row_idx].height = 22
        for col_idx in range(1, len(formatted_row) + 1):
            cell = ws_main.cell(row=row_idx, column=col_idx)
            cell.font = data_font
            cell.alignment = data_align
            cell.border = thin_border
            # 將代號/序號/統編/電話欄位設為文字格式，避免 Excel 吃掉前導 0
            cell.number_format = "@"

    # 預先為第 2~200 列設定文字格式與框線
    for r_idx in range(len(sample_rows) + 2, 102):
        ws_main.row_dimensions[r_idx].height = 20
        for c_idx in range(1, len(headers) + 1):
            c = ws_main.cell(row=r_idx, column=c_idx)
            c.font = data_font
            c.border = thin_border
            c.number_format = "@"

    # 設定下拉選單驗證 (DataValidation)
    if dropdown_configs:
        for cfg in dropdown_configs:
            col_idx = cfg.get("col_idx")  # 1-based
            options = cfg.get("options", [])
            if not col_idx or not options:
                continue
            formula_str = ",".join(str(x).replace('"', "") for x in options)
            # Excel 直接字串清單上限為 255 字元
            if len(formula_str) <= 250:
                dv = DataValidation(
                    type="list",
                    formula1=f'"{formula_str}"',
                    allow_blank=True,
                )
                col_letter = get_column_letter(col_idx)
                dv.add(f"{col_letter}2:{col_letter}500")
                ws_main.add_data_validation(dv)

    _auto_fit_columns(ws_main, min_width=14, max_width=45)
    ws_main.freeze_panes = "A2"

    # Sheet 2: 系統現有代碼對照與填寫說明
    ws_guide = wb.create_sheet(title="系統代碼對照與填寫說明")
    title_font = Font(name="微軟正黑體", size=12, bold=True, color="1E293B")
    sub_header_font = Font(name="微軟正黑體", size=10, bold=True, color="FFFFFF")
    sub_header_fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")

    current_row = 1
    for sec in guide_sections:
        sec_title = sec.get("title", "")
        sec_headers = sec.get("headers", [])
        sec_rows = sec.get("rows", [])

        if sec_title:
            title_cell = ws_guide.cell(row=current_row, column=1, value=sec_title)
            title_cell.font = title_font
            ws_guide.row_dimensions[current_row].height = 26
            current_row += 1

        if sec_headers:
            for c_idx, h_text in enumerate(sec_headers, start=1):
                c = ws_guide.cell(row=current_row, column=c_idx, value=h_text)
                c.font = sub_header_font
                c.fill = sub_header_fill
                c.alignment = header_align
                c.border = thin_border
            ws_guide.row_dimensions[current_row].height = 24
            current_row += 1

        for r_data in sec_rows:
            for c_idx, val in enumerate(r_data, start=1):
                c = ws_guide.cell(row=current_row, column=c_idx, value=format_cell_value(val))
                c.font = data_font
                c.alignment = Alignment(vertical="center", wrap_text=True)
                c.border = thin_border
            ws_guide.row_dimensions[current_row].height = 22
            current_row += 1

        current_row += 1  # 空一列分隔

    _auto_fit_columns(ws_guide, min_width=16, max_width=65)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def parse_uploaded_tabular_file(uploaded_file) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """
    解析上傳的 .xlsx 或 .csv 檔案。
    回傳: (headers, rows_with_line_no)
      - headers: 第一列的欄位名稱清單 (已去除頭尾空白及尾端 '*' 號)
      - rows_with_line_no: [(row_num, [col1_str, col2_str, ...]), ...] (自動略過整列全空白的列與範例說明列)
    """
    filename = (uploaded_file.name or "").lower()
    raw_rows = []

    if filename.endswith(".xlsx"):
        wb = openpyxl.load_workbook(uploaded_file, data_only=True)
        # 預設讀取第一個工作表
        ws = wb.worksheets[0]
        for row in ws.iter_rows(values_only=True):
            cleaned_row = []
            for cell_val in row:
                if cell_val is None:
                    cleaned_row.append("")
                elif isinstance(cell_val, datetime.datetime):
                    cleaned_row.append(cell_val.strftime("%Y-%m-%d"))
                elif isinstance(cell_val, datetime.date):
                    cleaned_row.append(cell_val.strftime("%Y-%m-%d"))
                elif isinstance(cell_val, float) and cell_val.is_integer():
                    cleaned_row.append(str(int(cell_val)))
                else:
                    cleaned_row.append(str(cell_val).strip())
            raw_rows.append(cleaned_row)
    elif filename.endswith(".csv"):
        raw_bytes = uploaded_file.read()
        text = None
        for enc in ("utf-8-sig", "utf-8", "cp950", "big5"):
            try:
                text = raw_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("無法解析 CSV 檔案編碼，請儲存為 UTF-8 或 Excel (.xlsx) 格式。")
        reader = csv.reader(StringIO(text))
        for row in reader:
            raw_rows.append([str(c).strip() for c in row])
    else:
        raise ValueError("不支援的檔案格式，請上傳 Excel (.xlsx) 或 CSV (.csv) 檔案。")

    if not raw_rows:
        raise ValueError("上傳的檔案內容為空白。")

    # 清理表頭名稱 (移除星號 * 與前後空白)
    raw_headers = raw_rows[0]
    headers = [h.replace("*", "").strip() for h in raw_headers]

    data_rows = []
    for idx, r in enumerate(raw_rows[1:], start=2):
        # 補齊或裁切至與 headers 相同長度
        if len(r) < len(headers):
            r = r + [""] * (len(headers) - len(r))
        else:
            r = r[: len(headers)]

        if not any(cell != "" for cell in r):
            continue  # 略過全空列

        data_rows.append((idx, r))

    return headers, data_rows


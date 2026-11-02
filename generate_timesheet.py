#!/usr/bin/env python3
"""
Direct PDF Timesheet Generator
Генератор таймшитов напрямую в PDF - без Excel
"""

import argparse
import calendar
import sys
from datetime import datetime, date
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

from moldova_holidays import get_moldova_holidays


class PDFTimesheetGenerator:
    """Generate timesheet directly to PDF"""
    
    def __init__(self, year, month, employee_name, company_name="Urchin Systems SA", hours_per_day=8, 
                 vacation_dates=None, sick_dates=None, custom_absences=None, holiday_scope="national", daily_hours=None):
        self.year = year
        self.month = month
        self.employee_name = employee_name
        self.company_name = company_name
        self.hours_per_day = hours_per_day
        self.daily_hours = daily_hours
        self.holiday_scope = "national"
        self.vacation_dates = vacation_dates or []  # List of date ranges
        self.sick_dates = sick_dates or []
        self.custom_absences = custom_absences or {}  # Dict: date -> reason
    
    def is_date_in_range(self, check_date, date_ranges):
        """Check if date is in any of the date ranges"""
        for start, end in date_ranges:
            if start <= check_date <= end:
                return True
        return False
    
    def get_all_days(self):
        """Get all days in month with holiday info"""
        num_days = calendar.monthrange(self.year, self.month)[1]
        
        all_days = []
        working_days_count = 0
        holidays_dict = get_moldova_holidays(self.year, self.holiday_scope)
        
        for day in range(1, num_days + 1):
            current_date = datetime(self.year, self.month, day)
            current_date_obj = current_date.date()
            
            is_weekend = current_date.weekday() >= 5
            is_holiday = current_date_obj in holidays_dict
            holiday_name = holidays_dict.get(current_date_obj, "")
            
            # Check for absences
            is_vacation = self.is_date_in_range(current_date_obj, self.vacation_dates)
            is_sick = self.is_date_in_range(current_date_obj, self.sick_dates)
            custom_absence = self.custom_absences.get(current_date_obj, None)
            
            # Determine absence type and reason
            if is_vacation:
                absence_type = 'vacation'
                absence_reason = 'Vacation / Concediu'
            elif is_sick:
                absence_type = 'sick'
                absence_reason = 'Sick Leave / Concediu Medical'
            elif custom_absence:
                absence_type = 'custom'
                absence_reason = custom_absence
            else:
                absence_type = None
                absence_reason = None
            
            logged_hours = (self.daily_hours.get(current_date_obj, 0) if self.daily_hours is not None
                            else (self.hours_per_day if not is_weekend and not is_holiday else 0))
            if absence_type:
                logged_hours = 0
            if logged_hours > 0:
                working_days_count += 1
            
            all_days.append({
                'date': current_date,
                'is_weekend': is_weekend,
                'is_holiday': is_holiday,
                'holiday_name': holiday_name,
                'absence_type': absence_type,
                'absence_reason': absence_reason,
                'hours': logged_hours,
            })
        
        total_hours = round(sum(day['hours'] for day in all_days), 6)
        return working_days_count, total_hours, all_days
    
    def generate(self, output_path):
        """Generate PDF timesheet"""
        print(f"🔄 Generating PDF timesheet for {calendar.month_name[self.month]} {self.year}...")
        
        working_days_count, total_hours, all_days = self.get_all_days()
        
        # Create PDF
        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=landscape(A4),
            rightMargin=15*mm,
            leftMargin=15*mm,
            topMargin=15*mm,
            bottomMargin=15*mm
        )
        
        elements = []
        styles = getSampleStyleSheet()
        
        # Title
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontSize=18,
            textColor=colors.white,
            alignment=TA_CENTER,
            spaceAfter=8*mm,
            fontName='Helvetica-Bold',
            backColor=colors.HexColor('#1F4E78'),
            leading=24
        )
        
        title = Paragraph('MONTHLY TIMESHEET', title_style)
        elements.append(title)
        elements.append(Spacer(1, 3*mm))
        
        # Header info
        month_str = f"{calendar.month_name[self.month]} {self.year}"
        header_data = [
            ['COMPANY:', self.company_name, 'WORK DAYS:', str(working_days_count)],
            ['EMPLOYEE:', self.employee_name, 'TOTAL HOURS:', str(total_hours)],
            ['MONTH:', month_str, '', ''],
        ]
        
        header_table = Table(header_data, colWidths=[35*mm, 80*mm, 38*mm, 27*mm])
        header_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
            ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#44546A')),
            ('TEXTCOLOR', (2, 0), (2, -1), colors.HexColor('#44546A')),
            ('TEXTCOLOR', (3, 0), (3, -1), colors.HexColor('#1F4E78')),
            ('FONTNAME', (3, 0), (3, -1), 'Helvetica-Bold'),
            ('BACKGROUND', (1, 0), (1, -1), colors.HexColor('#F2F2F2')),
            ('BACKGROUND', (3, 0), (3, -1), colors.HexColor('#F2F2F2')),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
            ('ALIGN', (3, 0), (3, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D0D0D0')),
            ('LEFTPADDING', (0, 0), (-1, -1), 3*mm),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3*mm),
            ('TOPPADDING', (0, 0), (-1, -1), 2*mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2*mm),
        ]))
        
        elements.append(header_table)
        elements.append(Spacer(1, 5*mm))
        
        # Data table
        table_headers = [['DATE', 'REGULAR HRS', 'OVERTIME HRS', 'OTHER', 'TOTAL HOURS']]
        table_data = []
        
        for day_info in all_days:
            current_date = day_info['date']
            is_weekend = day_info['is_weekend']
            is_holiday = day_info['is_holiday']
            holiday_name = day_info['holiday_name']
            absence_type = day_info['absence_type']
            absence_reason = day_info['absence_reason']
            
            date_str = current_date.strftime('%d-%b-%Y, %a')
            
            # Determine what to show
            if absence_type:
                # Vacation, sick leave, or custom absence
                regular_str = absence_reason
                hours = 0
            elif self.daily_hours is not None:
                regular_str = str(round(day_info['hours'], 4))
                hours = day_info['hours']
            elif is_holiday:
                regular_str = holiday_name
                hours = 0
            elif is_weekend:
                regular_str = '0'
                hours = 0
            else:
                regular_str = str(self.hours_per_day)
                hours = self.hours_per_day
            
            table_data.append([
                date_str,
                regular_str,
                '',  # Overtime
                '',  # Other
                str(hours)
            ])
        
        full_table_data = table_headers + table_data
        
        data_table = Table(full_table_data, colWidths=[52*mm, 38*mm, 38*mm, 37*mm, 25*mm])
        
        # Base table style
        table_style = [
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            
            # Header
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            
            # Data
            ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            
            # Grid
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#D0D0D0')),
            ('LEFTPADDING', (0, 0), (-1, -1), 2*mm),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2*mm),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5*mm),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5*mm),
        ]
        
        # Style weekends, holidays, and absences
        for i, day_info in enumerate(all_days, start=1):
            absence_type = day_info.get('absence_type')
            
            if absence_type == 'vacation':
                # Vacation - light blue background
                table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#D9E9F7')))
                table_style.append(('TEXTCOLOR', (0, i), (-1, i), colors.HexColor('#4472C4')))
                table_style.append(('FONTNAME', (0, i), (1, i), 'Helvetica-Oblique'))
                table_style.append(('FONTSIZE', (1, i), (1, i), 8))
                table_style.append(('ALIGN', (1, i), (1, i), 'LEFT'))
            elif absence_type == 'sick':
                # Sick leave - light yellow background
                table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FFF2CC')))
                table_style.append(('TEXTCOLOR', (0, i), (-1, i), colors.HexColor('#C65911')))
                table_style.append(('FONTNAME', (0, i), (1, i), 'Helvetica-Oblique'))
                table_style.append(('FONTSIZE', (1, i), (1, i), 8))
                table_style.append(('ALIGN', (1, i), (1, i), 'LEFT'))
            elif absence_type == 'custom':
                # Custom absence - light pink background
                table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FFE6F0')))
                table_style.append(('TEXTCOLOR', (0, i), (-1, i), colors.HexColor('#C00000')))
                table_style.append(('FONTNAME', (0, i), (1, i), 'Helvetica-Oblique'))
                table_style.append(('FONTSIZE', (1, i), (1, i), 8))
                table_style.append(('ALIGN', (1, i), (1, i), 'LEFT'))
            elif day_info['is_weekend'] or day_info['is_holiday']:
                # Weekends and holidays - gray
                table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#E7E6E6')))
                table_style.append(('TEXTCOLOR', (0, i), (-1, i), colors.HexColor('#7F7F7F')))
                table_style.append(('FONTNAME', (0, i), (1, i), 'Helvetica-Oblique'))
                if day_info['is_holiday']:
                    table_style.append(('FONTSIZE', (1, i), (1, i), 8))
                    table_style.append(('ALIGN', (1, i), (1, i), 'LEFT'))
        
        data_table.setStyle(TableStyle(table_style))
        elements.append(data_table)
        
        # Build PDF
        doc.build(elements)
        
        print(f"✅ PDF generated successfully!")
        print(f"   📅 Month: {calendar.month_name[self.month]} {self.year}")
        print(f"   👤 Employee: {self.employee_name}")
        print(f"   🏢 Company: {self.company_name}")
        print(f"   📊 Working days: {working_days_count}")
        print(f"   ⏰ Total hours: {total_hours}")
        print(f"💾 Saved to: {output_path}")
        
        return {
            'working_days': working_days_count,
            'total_hours': total_hours,
            'file': output_path
        }


# Excel styling — mirrors PDF color scheme
_XLSX_COLORS = {
    'title_bg': '1F4E78',
    'label': '44546A',
    'accent': '1F4E78',
    'header_bg': '4472C4',
    'header_fg': 'FFFFFF',
    'value_bg': 'F2F2F2',
    'grid': 'D0D0D0',
    'weekend_bg': 'E7E6E6',
    'weekend_fg': '7F7F7F',
    'vacation_bg': 'D9E9F7',
    'vacation_fg': '4472C4',
    'sick_bg': 'FFF2CC',
    'sick_fg': 'C65911',
    'custom_bg': 'FFE6F0',
    'custom_fg': 'C00000',
}


class ExcelTimesheetGenerator(PDFTimesheetGenerator):
    """Generate timesheet as styled Excel workbook (.xlsx)."""

    def _thin_border(self):
        side = Side(style='thin', color=_XLSX_COLORS['grid'])
        return Border(left=side, right=side, top=side, bottom=side)

    def _fill(self, hex_color):
        return PatternFill('solid', fgColor=hex_color)

    def generate(self, output_path):
        """Generate Excel timesheet."""
        print(f"Generating Excel timesheet for {calendar.month_name[self.month]} {self.year}...")

        working_days_count, total_hours, all_days = self.get_all_days()
        month_str = f"{calendar.month_name[self.month]} {self.year}"

        wb = Workbook()
        ws = wb.active
        ws.title = 'Timesheet'

        thin = self._thin_border()
        center = Alignment(horizontal='center', vertical='center')
        left = Alignment(horizontal='left', vertical='center')
        right = Alignment(horizontal='right', vertical='center')

        # Title row
        ws.merge_cells('A1:E1')
        title_cell = ws['A1']
        title_cell.value = 'MONTHLY TIMESHEET'
        title_cell.font = Font(bold=True, size=14, color=_XLSX_COLORS['header_fg'])
        title_cell.fill = self._fill(_XLSX_COLORS['title_bg'])
        title_cell.alignment = center
        title_cell.border = thin
        ws.row_dimensions[1].height = 28

        # Header info (rows 3-5)
        header_rows = [
            ('COMPANY:', self.company_name, 'WORK DAYS:', working_days_count),
            ('EMPLOYEE:', self.employee_name, 'TOTAL HOURS:', total_hours),
            ('MONTH:', month_str, '', None),
        ]
        for r_idx, (l1, v1, l2, v2) in enumerate(header_rows, start=3):
            ws.cell(r_idx, 1, l1).font = Font(bold=True, color=_XLSX_COLORS['label'])
            ws.cell(r_idx, 1).alignment = right
            ws.cell(r_idx, 1).border = thin

            c_val1 = ws.cell(r_idx, 2, v1)
            c_val1.fill = self._fill(_XLSX_COLORS['value_bg'])
            c_val1.alignment = left
            c_val1.border = thin

            if l2:
                ws.cell(r_idx, 3, l2).font = Font(bold=True, color=_XLSX_COLORS['label'])
                ws.cell(r_idx, 3).alignment = right
                ws.cell(r_idx, 3).border = thin

            if l2:
                c_val2 = ws.cell(r_idx, 4, v2)
                c_val2.font = Font(bold=True, color=_XLSX_COLORS['accent'])
                c_val2.fill = self._fill(_XLSX_COLORS['value_bg'])
                c_val2.alignment = center
                c_val2.border = thin

            for col in range(1, 5):
                if ws.cell(r_idx, col).border is None or ws.cell(r_idx, col).border == Border():
                    ws.cell(r_idx, col).border = thin

        # Column headers (row 7)
        headers = ['DATE', 'REGULAR HRS', 'OVERTIME HRS', 'OTHER', 'TOTAL HOURS']
        header_row = 7
        for col, hdr in enumerate(headers, start=1):
            cell = ws.cell(header_row, col, hdr)
            cell.font = Font(bold=True, color=_XLSX_COLORS['header_fg'])
            cell.fill = self._fill(_XLSX_COLORS['header_bg'])
            cell.alignment = center
            cell.border = thin

        # Data rows
        data_start = header_row + 1
        for i, day_info in enumerate(all_days):
            row = data_start + i
            current_date = day_info['date']
            absence_type = day_info['absence_type']
            is_weekend = day_info['is_weekend']
            is_holiday = day_info['is_holiday']

            date_str = current_date.strftime('%d-%b-%Y, %a')

            if absence_type:
                regular_val = day_info['absence_reason']
                hours = 0
            elif self.daily_hours is not None:
                regular_val = round(day_info['hours'], 4)
                hours = day_info['hours']
            elif is_holiday:
                regular_val = day_info['holiday_name']
                hours = 0
            elif is_weekend:
                regular_val = 0
                hours = 0
            else:
                regular_val = self.hours_per_day
                hours = self.hours_per_day

            row_values = [date_str, regular_val, None, None, hours]
            for col, val in enumerate(row_values, start=1):
                cell = ws.cell(row, col, val)
                cell.border = thin
                cell.alignment = center if col > 1 else left

            # Row color-coding (same priority as PDF)
            if absence_type == 'vacation':
                bg, fg = _XLSX_COLORS['vacation_bg'], _XLSX_COLORS['vacation_fg']
                ws.cell(row, 2).font = Font(italic=True, color=fg, size=9)
                ws.cell(row, 2).alignment = left
            elif absence_type == 'sick':
                bg, fg = _XLSX_COLORS['sick_bg'], _XLSX_COLORS['sick_fg']
                ws.cell(row, 2).font = Font(italic=True, color=fg, size=9)
                ws.cell(row, 2).alignment = left
            elif absence_type == 'custom':
                bg, fg = _XLSX_COLORS['custom_bg'], _XLSX_COLORS['custom_fg']
                ws.cell(row, 2).font = Font(italic=True, color=fg, size=9)
                ws.cell(row, 2).alignment = left
            elif is_weekend or is_holiday:
                bg, fg = _XLSX_COLORS['weekend_bg'], _XLSX_COLORS['weekend_fg']
                ws.cell(row, 2).font = Font(italic=True, color=fg)
                if is_holiday:
                    ws.cell(row, 2).font = Font(italic=True, color=fg, size=9)
                    ws.cell(row, 2).alignment = left
            else:
                bg, fg = None, None

            if bg:
                fill = self._fill(bg)
                for col in range(1, 6):
                    c = ws.cell(row, col)
                    c.fill = fill
                    if col != 2 or absence_type is None:
                        c.font = Font(color=fg) if fg else Font()

        ws.column_dimensions['A'].width = 22
        ws.column_dimensions['B'].width = 28
        ws.column_dimensions['C'].width = 14
        ws.column_dimensions['D'].width = 12
        ws.column_dimensions['E'].width = 14

        wb.save(str(output_path))

        print(f"Excel generated successfully!")
        print(f"   Month: {calendar.month_name[self.month]} {self.year}")
        print(f"   Employee: {self.employee_name}")
        print(f"   Working days: {working_days_count}")
        print(f"   Total hours: {total_hours}")
        print(f"Saved to: {output_path}")

        return {
            'working_days': working_days_count,
            'total_hours': total_hours,
            'file': output_path,
        }


def build_timesheet_filename(employee_name, year, month, ext='pdf'):
    """Standard output filename for PDF or Excel."""
    clean_name = employee_name.replace(' ', '_')
    month_name = calendar.month_name[month]
    return f"Timesheet_{clean_name}_{year}_{month:02d}_{month_name}.{ext}"


def main():
    parser = argparse.ArgumentParser(description='Generate PDF timesheets directly')
    
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument('--current', action='store_true', help='Generate for current month')
    date_group.add_argument('--year', type=int, help='Year (e.g., 2025)')
    
    parser.add_argument('--month', type=int, help='Month (1-12)')
    parser.add_argument('--employee', required=True, help='Employee name')
    parser.add_argument('--company', default='Urchin Systems SA', help='Company name')
    parser.add_argument('--hours', type=int, default=8, help='Default hours per day')
    parser.add_argument('--output', '-o', help='Output directory')
    parser.add_argument('--format', choices=['pdf', 'xlsx'], default='pdf',
                        help='Output format: pdf (default) or xlsx')
    
    # Absence options
    parser.add_argument('--vacation', help='Vacation dates: "14-21" or "14-21,25-28"')
    parser.add_argument('--sick', help='Sick leave dates: "5-7" or "5-7,10"')
    parser.add_argument('--absence', action='append', nargs=2, metavar=('DATE', 'REASON'),
                       help='Custom absence: --absence 15 "Personal day"')
    
    args = parser.parse_args()
    
    # Determine year and month
    if args.current:
        now = datetime.now()
        year = now.year
        month = now.month
    else:
        year = args.year
        if not args.month:
            parser.error("--month is required when using --year")
        month = args.month
    
    if month < 1 or month > 12:
        print(f"❌ Error: Invalid month {month}")
        sys.exit(1)
    
    # Parse vacation dates
    vacation_dates = []
    if args.vacation:
        for date_range in args.vacation.split(','):
            date_range = date_range.strip()
            if '-' in date_range:
                start_day, end_day = map(int, date_range.split('-'))
                start = date(year, month, start_day)
                end = date(year, month, end_day)
                vacation_dates.append((start, end))
            else:
                single_day = int(date_range)
                single_date = date(year, month, single_day)
                vacation_dates.append((single_date, single_date))
    
    # Parse sick leave dates
    sick_dates = []
    if args.sick:
        for date_range in args.sick.split(','):
            date_range = date_range.strip()
            if '-' in date_range:
                start_day, end_day = map(int, date_range.split('-'))
                start = date(year, month, start_day)
                end = date(year, month, end_day)
                sick_dates.append((start, end))
            else:
                single_day = int(date_range)
                single_date = date(year, month, single_day)
                sick_dates.append((single_date, single_date))
    
    # Parse custom absences
    custom_absences = {}
    if args.absence:
        for day_str, reason in args.absence:
            day = int(day_str)
            absence_date = date(year, month, day)
            custom_absences[absence_date] = reason
    
    # Generate filename
    output_dir = Path(args.output) if args.output else Path.cwd()
    output_dir.mkdir(exist_ok=True)

    ext = args.format
    filename = build_timesheet_filename(args.employee, year, month, ext)
    output_path = output_dir / filename

    fmt_label = 'EXCEL' if ext == 'xlsx' else 'PDF'
    print("=" * 60)
    print(f"{fmt_label} TIMESHEET GENERATOR / ГЕНЕРАТОР {fmt_label} ТАЙМШИТОВ")
    print("=" * 60)
    print()

    try:
        gen_cls = ExcelTimesheetGenerator if ext == 'xlsx' else PDFTimesheetGenerator
        generator = gen_cls(
            year=year,
            month=month,
            employee_name=args.employee,
            company_name=args.company,
            hours_per_day=args.hours,
            vacation_dates=vacation_dates,
            sick_dates=sick_dates,
            custom_absences=custom_absences
        )
        
        # Show info about absences
        if vacation_dates:
            print(f"🏖️  Vacation days: {len(vacation_dates)} period(s)")
        if sick_dates:
            print(f"🤒 Sick leave: {len(sick_dates)} period(s)")
        if custom_absences:
            print(f"📝 Custom absences: {len(custom_absences)} day(s)")
        if vacation_dates or sick_dates or custom_absences:
            print()
        
        generator.generate(output_path)
        
        print()
        print("=" * 60)
        print("✅ GENERATION COMPLETE / ГЕНЕРАЦИЯ ЗАВЕРШЕНА")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()


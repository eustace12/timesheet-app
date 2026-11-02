"""PDF generator for leave / vacation requests."""

from datetime import date, datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build_leave_filename(employee_name: str, start: date, end: date) -> str:
    clean = "".join(
        character if character.isalnum() else "_"
        for character in employee_name.strip()
    ).strip("_")
    clean = clean or "employee"
    return f"Leave_{clean}_{start.isoformat()}_{end.isoformat()}.pdf"


def generate_leave_pdf(
    output_path: str | Path,
    *,
    employee_name: str,
    company_name: str,
    start_date: date,
    end_date: date,
    leave_type: str,
    comment: str = "",
    submitted_at: datetime | None = None,
) -> Path:
    output_path = Path(output_path)
    submitted_at = submitted_at or datetime.now(timezone.utc)
    calendar_days = (end_date - start_date).days + 1
    if calendar_days < 1:
        raise ValueError("End date must be on or after start date")

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "LeaveTitle",
        parent=styles["Heading1"],
        fontSize=16,
        textColor=colors.white,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
        leading=22,
    )
    body = ParagraphStyle(
        "LeaveBody",
        parent=styles["Normal"],
        fontSize=11,
        leading=16,
        alignment=TA_LEFT,
    )
    muted = ParagraphStyle(
        "LeaveMuted",
        parent=body,
        textColor=colors.HexColor("#44546A"),
        fontSize=10,
    )

    elements = []
    title_table = Table(
        [[Paragraph("LEAVE REQUEST", title_style)]],
        colWidths=[174 * mm],
    )
    title_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1F4E78")),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 8 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8 * mm),
        ])
    )
    elements.append(title_table)
    elements.append(Spacer(1, 8 * mm))

    rows = [
        ["COMPANY", company_name],
        ["EMPLOYEE", employee_name],
        ["TYPE", leave_type],
        ["FROM", start_date.strftime("%d %B %Y")],
        ["TO", end_date.strftime("%d %B %Y")],
        ["CALENDAR DAYS", str(calendar_days)],
        ["COMMENT", comment or "—"],
        ["SUBMITTED", submitted_at.strftime("%Y-%m-%d %H:%M UTC")],
    ]
    data = [[Paragraph(f"<b>{k}</b>", muted), Paragraph(v, body)] for k, v in rows]
    info = Table(data, colWidths=[40 * mm, 134 * mm])
    info.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F2F2")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D0D0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
            ("TOPPADDING", (0, 0), (-1, -1), 3 * mm),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
        ])
    )
    elements.append(info)
    elements.append(Spacer(1, 16 * mm))
    elements.append(Paragraph(
        "Employee signature: ______________________ &nbsp;&nbsp;&nbsp; "
        "Accountant / Manager: ______________________",
        body,
    ))
    doc.build(elements)
    return output_path

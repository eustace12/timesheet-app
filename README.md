# 📋 Timesheet Generator

Professional monthly timesheet generator with Moldovan holidays support.

## Features

- ✅ **Automatic PDF generation** - direct to PDF, no Excel needed
- ✅ **Moldovan holidays** - all 12 public holidays for 2025
- ✅ **Vacation tracking** - select date ranges visually
- ✅ **Sick leave** - track medical absences
- ✅ **Visual calendar** - pick dates with beautiful UI
- ✅ **Quick actions** - "1 Week", "2 Weeks" buttons
- ✅ **Live statistics** - see working days & hours in real-time
- ✅ **Professional design** - ready for HR submission

## Quick Start

### Run Locally

```bash
# Install dependencies
pip3 install -r requirements.txt

# Start server
python3 web_app.py

# Open browser
http://localhost:8080
```

### Generate via CLI

```bash
# Simple generation
python3 generate_timesheet.py --year 2025 --month 11 --employee "Your Name"

# With vacation
python3 generate_timesheet.py --year 2025 --month 11 \
  --employee "Your Name" \
  --vacation "14-21"

# With multiple absences
python3 generate_timesheet.py --year 2025 --month 10 \
  --employee "Your Name" \
  --vacation "7-11" \
  --sick "25-27" \
  --absence 30 "Birthday"
```

## Deploy to Vercel

See [DEPLOY.md](DEPLOY.md) for detailed instructions.

Quick deploy:
```bash
npm install -g vercel
vercel
```

## Project Structure

```
timesheet-app/
├── api/
│   └── index.py          # Vercel serverless entry
├── templates/
│   └── index.html        # Web UI
├── generated/            # Output PDFs (gitignored)
├── generate_timesheet.py # Core PDF generator
├── web_app.py           # Flask web interface
├── requirements.txt     # Python dependencies
├── vercel.json          # Vercel config
└── README.md
```

## Moldovan Holidays 2025

Automatically included:
- New Year (Jan 1)
- Orthodox Christmas (Jan 7-8)
- Women's Day (Mar 8)
- Orthodox Easter (Apr 20-21)
- Labour Day (May 1)
- Victory Day (May 9)
- Independence Day (Aug 27)
- Romanian Language Day (Aug 31)
- **Chisinau City Day (Oct 14)**
- Christmas (Dec 25)

## License

MIT














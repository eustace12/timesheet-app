# Deploy to Vercel

## Quick Start (without Git)

### Option 1: Vercel CLI (Recommended)

1. **Install Vercel CLI:**
```bash
npm install -g vercel
```

2. **Login to Vercel:**
```bash
vercel login
```

3. **Deploy from this directory:**
```bash
cd /Users/eustace/Work/Urchinsys/timesheet-app
vercel
```

4. **Follow prompts:**
- Set up and deploy? **Yes**
- Which scope? Select your account
- Link to existing project? **No**
- Project name? `timesheet-generator` (or your choice)
- Directory? `.` (current)
- Override settings? **No**

5. **Done!** You'll get a URL like:
```
https://timesheet-generator-xxx.vercel.app
```

### Option 2: Vercel Web UI

1. Go to: https://vercel.com
2. Sign up with email (free)
3. Click **"Add New Project"**
4. Click **"Browse"** and upload this folder as ZIP
5. Deploy!

---

## Files Structure

```
timesheet-tools/
├── api/
│   └── index.py          # Vercel entry point
├── templates/
│   └── index.html        # Web UI
├── generate_timesheet.py # PDF generator
├── web_app.py           # Flask app
├── requirements.txt     # Python dependencies
├── vercel.json         # Vercel config
└── .vercelignore       # Files to ignore
```

---

## Testing Locally

Before deploying, test locally:

```bash
vercel dev
```

Opens on: http://localhost:3000

---

## Custom Domain (Optional)

After deployment, you can add custom domain in Vercel dashboard:
- Settings → Domains → Add Domain
- Free SSL certificate included

---

## Environment Variables

If needed, add in Vercel dashboard:
- Settings → Environment Variables

---

## Troubleshooting

**Error: Module not found**
- Check `requirements.txt` has all dependencies

**PDF not generating**
- Vercel has write limits - files save to `/tmp`
- PDF downloads should work fine

**Timeout errors**
- Free tier has 10s limit
- Your app is fast enough

---

## Cost

- ✅ **100% FREE** for this app
- Generous free tier limits
- No credit card needed

---

## Update Deployment

Just run again:
```bash
vercel --prod
```

Your URL stays the same!


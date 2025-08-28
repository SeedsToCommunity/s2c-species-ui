# Google Drive CSV Setup Guide

Your plant species application can now automatically fetch data from a Google Drive CSV file! This means you can update your plant data without needing to redeploy the application.

## How to Set Up Google Drive Integration

### Step 1: Prepare Your CSV File in Google Drive

1. Upload your plant species CSV file to Google Drive
2. Right-click the file and select "Share"
3. Click "Get link" and change permissions to "Anyone with the link can view"
4. Copy the share URL (it looks like: `https://drive.google.com/file/d/FILE_ID_HERE/view?usp=sharing`)

### Step 2: Configure the Environment Variable

1. In your Replit project, go to the **Secrets** tab (lock icon in the left sidebar)
2. Add a new secret with:
   - **Key**: `GOOGLE_DRIVE_CSV_URL`
   - **Value**: Your Google Drive share URL from Step 1

### Step 3: Test the Integration

1. Once you've added the secret, restart your application
2. Check the logs to see: "Successfully loaded X rows from Google Drive"
3. Your app will now automatically use the latest data from Google Drive!

## How It Works

- **Primary source**: Google Drive CSV (if URL is configured)
- **Fallback**: Local files (`origdata.tabsv` or `plants.csv`)
- **Auto-conversion**: Handles both share URLs and direct download URLs
- **Error handling**: Falls back gracefully if Google Drive is unavailable

## Benefits

✅ **No redeployment needed** - Update your CSV in Google Drive and the app automatically uses new data
✅ **Team collaboration** - Multiple people can update the Google Drive file
✅ **Backup safety** - Local files serve as fallback if Google Drive is unavailable
✅ **Real-time updates** - Changes in Google Drive appear immediately in your app

## CSV Format Requirements

Your Google Drive CSV should have the same column structure as your current data:
- Column names will be automatically cleaned (spaces become underscores, lowercase)
- Empty cells are handled gracefully
- No special formatting required

## Troubleshooting

- **Check the logs**: Look for "Successfully loaded X rows from Google Drive" message
- **Verify sharing**: Ensure the Google Drive file is shared with "Anyone with the link can view"
- **Check the URL**: Make sure you copied the full share URL from Google Drive
- **Fallback behavior**: If Google Drive fails, the app will use local files and log the error
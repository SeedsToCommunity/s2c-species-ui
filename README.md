# Southeast Michigan Native Plant Species Directory

A Flask-based web application for browsing and managing native plant species information for the Seeds to Community project.

## Quick Start

This application displays a directory of plant species with detailed information across multiple specialized screens including identification, seed collection, processing, storage, and stratification requirements.

## Documentation

For detailed information about this project, please refer to:

- **[replit.md](./replit.md)** - Complete project overview, architecture, features, and technical documentation
- **[GOOGLE_DRIVE_SETUP.md](./GOOGLE_DRIVE_SETUP.md)** - Instructions for connecting the app to Google Drive for dynamic data updates

## Key Features

- Multi-screen species detail workflow (5 specialized screens per species)
- Advanced filtering system with real-time AJAX updates
- Google Drive CSV integration for easy data updates
- Ultra-compact layout optimized for browsing many species
- Development/production environment detection for security

## Data Management

The application can load plant data from:
1. **Primary source**: Google Drive CSV (configured in `config/app_settings.json`)
2. **Fallback sources**: Local files (`origdata.tabsv` and `plants.csv`)

To update plant data, simply edit the Google Drive spreadsheet - no redeployment needed!

## Running the Application

The application runs automatically on Replit. If running locally:

```bash
gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app
```

## Technology Stack

- **Backend**: Python Flask
- **Frontend**: Bootstrap 5, Feather Icons
- **Data**: Pandas for CSV processing
- **Deployment**: Replit with Gunicorn

## For More Information

See [replit.md](./replit.md) for complete technical details, user preferences, completed features, and system architecture.

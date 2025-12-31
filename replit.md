# Plant Species Directory

## Overview

This is a Flask-based web application that displays a directory of plant species. The application reads plant data from a CSV file and presents it in a user-friendly web interface with Bootstrap styling. It's designed to be a simple, read-only catalog of native plant species and their characteristics, with placeholder functionality for future filtering capabilities.

## User Preferences

Preferred communication style: Simple, everyday language.

## Completed Features (Multi-Screen Species Workflow)

### Multi-Screen Species Detail System ✅
When a user selects a species from the main list, they enter a dedicated workflow with 5 specialized screens:

1. **Species Identification** - Information needed to identify the plant in the field
2. **Seed Collection** - Data relevant to collecting seeds from the plant
3. **Seed Processing** - Instructions and data for processing collected seeds
4. **Seed Storage** - Requirements and methods for storing processed seeds
5. **Stratification** - Stratification requirements and procedures

### Technical Implementation ✅
- Each screen has its own configuration file defining which columns to display
- Users can navigate between all 5 screens while staying on the same species
- "Back to Filter" button that preserves previous filter state
- URL routing supports direct links to specific species/screens
- Responsive navigation between workflow steps
- Fixed "No Data Available" bug that was incorrectly showing when data was present

## Completed Features (User Experience & Filtering)

### Advanced Filtering System ✅
- **Real-time AJAX filtering** - Instant results without page reloads for all filter types
- **Text search filtering** - Name-based search with debounced input
- **Multi-criteria dropdown filtering** - Start Seed Watch, Germination Code, Light, and Moisture filters
- **Comprehensive empty state handling** - Proper "No Species Match Your Filters" messages for all filter combinations
- **Consistent filtering behavior** - Both text and dropdown filters use unified AJAX update logic
- **Special character support** - Fixed regex filtering issues by using exact text matching
- **Filter preservation** - Maintains filter state during navigation and species detail workflows
- **Robust form state management** - Handles complex filter combinations and state transitions properly

### Ultra-Compact Layout System ✅
- **Single-line metadata display** - All data fields show "Label: Value" format instead of two-line display
- **Minimized card padding** - Reduced internal spacing from py-2 px-3 to py-1 px-2 for tighter layout
- **Compressed row gutters** - Custom CSS reduces Bootstrap spacing by 50% between cards
- **Optimized visual hierarchy** - Streamlined borders and shadows for cleaner appearance
- **Consistent compact formatting** - Applied to both main page cards and species detail screens

## Completed Features (Data Management)

### Google Drive CSV Integration ✅
- **Dynamic data loading** - App can fetch plant data directly from Google Drive CSV files
- **No-deployment updates** - Update plant data in Google Drive without redeploying the app
- **Fallback system** - Gracefully falls back to local files if Google Drive is unavailable
- **Automatic URL conversion** - Handles both Google Drive share URLs and direct download URLs
- **Environment configuration** - Uses `GOOGLE_DRIVE_CSV_URL` environment variable for setup

### Supplemental Data Integration ✅ (December 2025)
- **Two-file data model** - Main species list (S2C_Species_Data_Main) controls which species are available, supplemental data (PlantData Google Sheet) provides additional columns
- **Google Sheets support** - Automatically exports Google Sheets as CSV for processing
- **Genus + Species matching** - Joins supplemental data to main species using normalized genus/species key
- **Automatic column discovery** - New columns from supplemental data automatically appear in admin UI
- **Existing column assignment** - Supplemental columns can be assigned to screens using the same admin interface as main columns
- **Graceful fallback** - App continues to work if supplemental data is unavailable
- **Cache management** - Both main and supplemental data caches are cleared together on refresh
- **Configuration** - Uses `supplemental_file_prefix` in app_settings.json (default: "PlantData")

### Development vs Production Environment Detection ✅
- **Proper environment detection** - Uses REPLIT_ENVIRONMENT variable to distinguish development from production
- **Development-only features** - Edit capabilities and admin tools only appear in development workspace
- **Production security** - All administrative functions properly hidden when deployed
- **Environment-specific behavior** - Different feature sets based on deployment context

### Crowdsourced Contributions System ✅ (December 2025)
- **Combined submission modal** - Single "Contribute" button opens modal for both photos and knowledge
- **Multi-file image upload** - Users can select multiple images at once with shared tags (seeds/seedling)
- **Knowledge text submissions** - Free-form text area for tips, methods, and community knowledge
- **Category tagging** - Separate checkboxes for images (seeds, seedling) and text (identification, collection, storage, processing, stratification)
- **Attribution and consent** - First/last name required, consent checkbox for free educational use
- **Local pending storage** - Text submissions stored as JSON in `pending_metadata/` folder
- **Cloudinary image storage** - Images uploaded to Cloudinary with "Pending" tag
- **Admin review page** - Tabbed interface showing pending images and knowledge submissions separately
- **Approve/reject workflow** - Admin can approve or delete submissions
- **Google Drive integration** - Approved JSON files automatically upload to "Tier 1 Sources" folder in Google Drive
- **LLM-ready format** - Approved metadata stored in standard JSON format for separate LLM application to consume

## New Feature Requirements (Enhancement Phase)

### Data Enhancement
- **Incorporate full metadata from ChatGPT** - Expand plant data with comprehensive information from AI-generated content
- **Data validation and enrichment** - Ensure all species have complete, accurate information

### Branding and Navigation
- **Update page titles, headers, and footers** - Improve branding and user experience
- **Rename app for deployment** - Choose more appropriate name for users (current replit.app domain is fine)
- **Add external links** - Include links to:
  - Facebook page
  - Organization webpage  
  - Shared document space
- **Add hero image** - Include compelling image at top of main page for visual appeal

### Development and Deployment
- **Add GitHub connectivity** - Set up version control and backup through GitHub integration

### User Experience Improvements
- **Enhanced visual design** - Improve overall aesthetics and usability
- **Mobile optimization** - Ensure excellent mobile experience
- **Performance optimization** - Optimize loading times and responsiveness

## System Architecture

### Frontend Architecture
- **Template Engine**: Jinja2 templates with Flask's built-in templating system
- **UI Framework**: Bootstrap 5 with dark theme for responsive design
- **Icons**: Feather icons for visual elements
- **Single Page Application**: Simple one-route application displaying plant data in a table/card format
- **Error Handling**: Flash messages for user feedback on data loading issues

### Backend Architecture
- **Web Framework**: Flask - chosen for its simplicity and minimal setup requirements
- **Data Processing**: Pandas for CSV data manipulation and conversion to template-friendly formats
- **Error Handling**: Comprehensive exception handling for file operations with detailed logging
- **Logging**: Python's built-in logging module for debugging and monitoring
- **Configuration**: Environment-based configuration for session secrets

### Data Storage
- **Primary Data Source**: Google Drive CSV file (S2C_Species_Data_Main prefix) controls species availability
- **Supplemental Data Source**: Google Sheets file (PlantData prefix) provides additional columns merged on genus+species
- **Fallback Sources**: Local tab-separated file (`origdata.tabsv`) or CSV file (`plants.csv`)
- **Dynamic Loading**: Application fetches fresh data from Google Drive on each startup
- **Auto-conversion**: Handles both Google Drive share URLs and direct download URLs
- **Data Format**: CSV data automatically cleaned and converted to Python dictionaries for template rendering
- **No Database**: Cloud-based file approach with local fallback for reliability
- **Configuration System**: JSON-based configuration files for customizable display and app settings

### Application Structure
- **Entry Points**: Both `app.py` and `main.py` serve as application entry points
- **Template Organization**: HTML templates stored in standard Flask `templates/` directory
- **Static Assets**: External CDN resources for Bootstrap and icons to minimize local dependencies
- **Configuration Files**: JSON-based configuration system in `config/` directory
  - `display_columns.json`: Controls which plant data fields are displayed and how they're formatted
  - `app_settings.json`: Application-wide settings for UI, data sources, and display preferences

## External Dependencies

### Python Packages
- **Flask**: Core web framework for routing and templating
- **Pandas**: Data manipulation and CSV processing
- **Flask-SQLAlchemy**: Database ORM (included but not currently used)
- **psycopg2-binary**: PostgreSQL adapter (included but not currently used)
- **email_validator**: Email validation utilities (included but not currently used)
- **Gunicorn**: WSGI HTTP server for production deployment

### Frontend Libraries
- **Bootstrap 5**: CSS framework loaded from Replit CDN
- **Feather Icons**: Icon library loaded from CDNJS

### Development & Deployment
- **Replit Integration**: Configured for Replit hosting environment
- **Environment Variables**: Uses `SESSION_SECRET` environment variable for Flask session management
- **Debug Mode**: Enabled for development with comprehensive error logging
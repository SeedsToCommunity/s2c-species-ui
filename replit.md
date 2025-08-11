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

## New Feature Requirements (Enhancement Phase)

### Data Enhancement
- **Incorporate full metadata from ChatGPT** - Expand plant data with comprehensive information from AI-generated content
- **Data validation and enrichment** - Ensure all species have complete, accurate information

### Branding and Navigation
- **Update page titles, headers, and footers** - Improve branding and user experience
- **Add external links** - Include links to:
  - Facebook page
  - Organization webpage  
  - Shared document space
- **Add hero image** - Include compelling image at top of main page for visual appeal

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
- **Primary Data Source**: Tab-separated file (`origdata.tabsv`) containing plant species information
- **Auto-conversion**: Application automatically converts tab-separated data to CSV format on startup
- **Data Format**: Structured CSV data converted to Python dictionaries for template rendering
- **No Database**: Simple file-based approach suitable for static or infrequently updated data
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
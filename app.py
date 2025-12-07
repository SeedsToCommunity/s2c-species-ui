import os
import pandas as pd
import logging
import json
import requests
from datetime import datetime
from io import StringIO
from flask import Flask, render_template, flash, request, url_for, abort, redirect, jsonify
from urllib.parse import quote, unquote

# Google Drive API imports
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Global cache for plant data to avoid reloading on every request
_cached_plant_data = None
_data_cache_timestamp = None
_last_known_modified_time = None  # Track Google Drive file modification time

def get_google_drive_service():
    """Get an authenticated Google Drive API service"""
    if not GOOGLE_API_AVAILABLE:
        app.logger.warning("Google API libraries not available")
        return None
    
    # Check for service account credentials JSON
    service_account_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not service_account_json:
        app.logger.info("GOOGLE_SERVICE_ACCOUNT_JSON not configured")
        return None
    
    try:
        # Parse the JSON credentials
        credentials_info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        service = build('drive', 'v3', credentials=credentials)
        return service
    except Exception as e:
        app.logger.error(f"Error creating Google Drive service: {str(e)}")
        return None

def get_file_id_from_url(url):
    """Extract file ID from a Google Drive URL"""
    if 'drive.google.com' in url and 'file/d/' in url:
        return url.split('file/d/')[1].split('/')[0]
    return None

def check_google_drive_file_modified(file_id):
    """Check if a Google Drive file has been modified since last check"""
    global _last_known_modified_time
    
    service = get_google_drive_service()
    if not service:
        return None, "Google Drive API not configured"
    
    try:
        # Get file metadata including modifiedTime
        file_metadata = service.files().get(
            fileId=file_id,
            fields='id, name, modifiedTime'
        ).execute()
        
        current_modified_time = file_metadata.get('modifiedTime')
        file_name = file_metadata.get('name', 'Unknown')
        
        if _last_known_modified_time is None:
            # First check - no previous time to compare
            _last_known_modified_time = current_modified_time
            return True, f"First check for '{file_name}'"
        
        if current_modified_time != _last_known_modified_time:
            # File has been modified
            old_time = _last_known_modified_time
            _last_known_modified_time = current_modified_time
            return True, f"'{file_name}' was updated (changed from {old_time} to {current_modified_time})"
        else:
            return False, f"'{file_name}' has not changed (last modified: {current_modified_time})"
            
    except Exception as e:
        app.logger.error(f"Error checking Google Drive file: {str(e)}")
        return None, f"Error: {str(e)}"

def load_display_config():
    """Load display configuration from JSON file"""
    try:
        with open('config/display_columns.json', 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        app.logger.error("Display configuration file not found")
        return {"main_page_columns": [], "filter_columns": []}
    except Exception as e:
        app.logger.error(f"Error loading display configuration: {str(e)}")
        return {"main_page_columns": [], "filter_columns": []}

def convert_google_drive_url(share_url):
    """Convert Google Drive share URL to direct CSV download URL"""
    if 'drive.google.com' in share_url and 'file/d/' in share_url:
        # Extract the file ID from the share URL
        file_id = share_url.split('file/d/')[1].split('/')[0]
        # Return the direct download URL
        return f"https://drive.google.com/uc?id={file_id}&export=download"
    return share_url

def load_app_settings():
    """Load application settings from JSON configuration file"""
    try:
        with open('config/app_settings.json', 'r') as f:
            settings = json.load(f)
        return settings
    except FileNotFoundError:
        app.logger.warning("App settings file not found, using defaults")
        return {
            "data_source": {
                "google_drive_csv_url": "",
                "fallback_files": ["origdata.tabsv", "plants.csv"]
            }
        }
    except Exception as e:
        app.logger.error(f"Error loading app settings: {str(e)}")
        return {"data_source": {"google_drive_csv_url": "", "fallback_files": ["origdata.tabsv", "plants.csv"]}}

def load_plant_data(force_reload=False):
    """Load and process plant data from Google Drive CSV or fallback to local files"""
    global _cached_plant_data, _data_cache_timestamp
    
    # Return cached data if available and not forcing reload
    if not force_reload and _cached_plant_data is not None:
        return _cached_plant_data
    
    # Load settings and get Google Drive URL
    settings = load_app_settings()
    google_drive_url = settings.get('data_source', {}).get('google_drive_csv_url', '')
    
    try:
        if google_drive_url:
            app.logger.info(f"Loading data from Google Drive: {google_drive_url}")
            
            # Convert share URL to direct download URL if needed
            download_url = convert_google_drive_url(google_drive_url)
            
            # Download the CSV data
            response = requests.get(download_url, timeout=30)
            response.raise_for_status()
            
            # Read CSV from the response text
            csv_data = StringIO(response.text)
            df = pd.read_csv(csv_data)
            
            # Check if first row contains the actual headers (Google Sheets issue)
            if len(df) > 0 and 'Unnamed: 0' in df.columns and df.iloc[0, 0] == 'Botanical Name':
                app.logger.info("Detected header row in data, fixing column names")
                # Use first row as column names
                new_columns = df.iloc[0].tolist()
                df.columns = new_columns
                # Remove the header row from data
                df = df.iloc[1:].reset_index(drop=True)
            
            # Clean up column names - replace spaces with underscores and make lowercase
            df.columns = df.columns.str.replace(' ', '_').str.lower()
            
            # Clean up the data - fill NaN values with empty strings
            df = df.fillna('')
            
            app.logger.info(f"Successfully loaded {len(df)} rows from Google Drive")
            
            # Cache the data
            _cached_plant_data = df
            _data_cache_timestamp = pd.Timestamp.now()
            return df
            
    except requests.RequestException as e:
        app.logger.error(f"Error downloading from Google Drive: {str(e)}")
    except Exception as e:
        app.logger.error(f"Error processing Google Drive data: {str(e)}")
    
    # Fallback to local files
    try:
        app.logger.info("Falling back to local data files")
        fallback_files = settings.get('data_source', {}).get('fallback_files', ['origdata.tabsv', 'plants.csv'])
        
        # Try each fallback file in order
        for filename in fallback_files:
            if os.path.exists(filename):
                if filename.endswith('.tabsv'):
                    df = pd.read_csv(filename, sep='\t')
                else:
                    df = pd.read_csv(filename)
                break
        else:
            app.logger.error("No data files found (local or Google Drive)")
            return pd.DataFrame()
        
        # Clean up column names - replace spaces with underscores and make lowercase
        df.columns = df.columns.str.replace(' ', '_').str.lower()
        
        # Clean up the data - fill NaN values with empty strings
        df = df.fillna('')
        
        app.logger.info(f"Successfully loaded {len(df)} rows from local files")
        
        # Cache the data
        _cached_plant_data = df
        _data_cache_timestamp = pd.Timestamp.now()
        return df
        
    except Exception as e:
        app.logger.error(f"Error reading local plant data: {str(e)}")
        return pd.DataFrame()

def get_unique_values(df, column):
    """Get unique non-empty values from a column, handling CSV values"""
    if column not in df.columns:
        return []
    
    values = set()
    for val in df[column].dropna():
        if pd.isna(val) or val == '':
            continue
        # Handle CSV values (comma-separated)
        if ',' in str(val):
            for item in str(val).split(','):
                clean_item = item.strip()
                if clean_item:
                    values.add(clean_item)
        else:
            clean_val = str(val).strip()
            if clean_val:
                values.add(clean_val)
    
    return sorted(list(values))

def get_unique_values_ordered(df, column, preferred_order):
    """Get unique non-empty values from a column with custom ordering"""
    if column not in df.columns:
        return []
    
    values = set()
    for val in df[column].dropna():
        if pd.isna(val) or val == '':
            continue
        # Handle CSV values (comma-separated)
        if ',' in str(val):
            for item in str(val).split(','):
                clean_item = item.strip()
                if clean_item:
                    values.add(clean_item)
        else:
            clean_val = str(val).strip()
            if clean_val:
                values.add(clean_val)
    
    # Order according to preferred_order, then alphabetically for any extras
    unique_values = list(values)
    ordered_values = []
    
    # Add values in preferred order first
    for preferred_val in preferred_order:
        if preferred_val in unique_values:
            ordered_values.append(preferred_val)
            unique_values.remove(preferred_val)
    
    # Add remaining values alphabetically
    ordered_values.extend(sorted(unique_values))
    
    return ordered_values

def load_screen_config(screen_type):
    """Load screen configuration for species detail view"""
    try:
        with open(f'config/screen_{screen_type}.json', 'r') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        app.logger.error(f"Screen configuration file for {screen_type} not found")
        return {"screen_name": screen_type.title(), "columns": []}
    except Exception as e:
        app.logger.error(f"Error loading screen configuration for {screen_type}: {str(e)}")
        return {"screen_name": screen_type.title(), "columns": []}

def get_species_by_name(df, botanical_name):
    """Get a single species by botanical name"""
    decoded_name = unquote(botanical_name)
    species = df[df['botanical_name'] == decoded_name]
    if species.empty:
        return None
    return species.iloc[0].to_dict()

def filter_plants(df, filters):
    """Apply filters to the plant dataframe"""
    filtered_df = df.copy()
    
    # Name filter (searches both botanical and common names)
    if filters.get('name'):
        name_query = filters['name'].lower()
        mask = (
            filtered_df['botanical_name'].str.lower().str.contains(name_query, na=False, regex=False) |
            filtered_df['common_name'].str.lower().str.contains(name_query, na=False, regex=False)
        )
        filtered_df = filtered_df[mask]
    
    # Start collecting month filter
    if filters.get('start_seed_watch'):
        month_filter = filters['start_seed_watch']
        mask = filtered_df['start_seed_watch'].str.contains(month_filter, na=False, regex=False)
        filtered_df = filtered_df[mask]
    
    # Germination code filter
    if filters.get('germination_code'):
        germ_filter = filters['germination_code']
        mask = filtered_df['germination_code'].str.contains(germ_filter, na=False, regex=False)
        filtered_df = filtered_df[mask]
    
    # Light filter
    if filters.get('light'):
        light_filter = filters['light']
        mask = filtered_df['light'].str.contains(light_filter, na=False, regex=False)
        filtered_df = filtered_df[mask]
    
    # Moisture filter
    if filters.get('moisture'):
        moisture_filter = filters['moisture']
        mask = filtered_df['moisture'].str.contains(moisture_filter, na=False, regex=False)
        filtered_df = filtered_df[mask]
    
    return filtered_df

@app.route('/')
def index():
    """Main route to display plant species list with filtering"""
    try:
        # Load plant data from tab-separated file
        df = load_plant_data()
        
        if df.empty:
            flash('Error: No plant data could be loaded. Please check data files.', 'error')
            return render_template('index.html', plants=[], config={}, filter_options={})
        
        # Load display configuration
        config = load_display_config()
        
        # Get filter parameters from request
        filters = {
            'name': request.args.get('name', '').strip(),
            'start_seed_watch': request.args.get('start_seed_watch', ''),
            'germination_code': request.args.get('germination_code', ''),
            'light': request.args.get('light', ''),
            'moisture': request.args.get('moisture', '')
        }
        
        # Apply filters
        filtered_df = filter_plants(df, filters)
        
        # Ensure filtered_df is a DataFrame
        if not hasattr(filtered_df, 'to_dict'):
            app.logger.error(f"Filter function returned invalid type: {type(filtered_df)}")
            filtered_df = df.iloc[0:0]  # Empty DataFrame with same structure
        
        # Get unique values for filter dropdowns
        filter_options = {
            'start_seed_watch': get_unique_values(df, 'start_seed_watch'),
            'germination_code': get_unique_values(df, 'germination_code'),
            'light': get_unique_values_ordered(df, 'light', ['Sn', 'P', 'Sh']),
            'moisture': get_unique_values(df, 'moisture'),
            'total_count': len(df)
        }
        
        # Convert to list of dictionaries for easier template rendering
        plants = filtered_df.to_dict('records')
        
        app.logger.info(f"Successfully loaded {len(plants)} plant species (filtered from {len(df)} total)")
        return render_template('index.html', plants=plants, config=config, 
                             filter_options=filter_options, current_filters=filters)
        
    except Exception as e:
        app.logger.error(f"Error processing plant data: {str(e)}")
        flash(f'Error loading plant data: {str(e)}', 'error')
        return render_template('index.html', plants=[], config={}, filter_options={})

@app.route('/species/<path:botanical_name>')
@app.route('/species/<path:botanical_name>/<screen_type>')
def species_detail(botanical_name, screen_type='identification'):
    """Species detail view with workflow screens"""
    try:
        # Load plant data
        df = load_plant_data()
        if df.empty:
            flash('Error: No plant data could be loaded.', 'error')
            return abort(404)
        
        # Get the specific species
        species = get_species_by_name(df, botanical_name)
        if not species:
            flash(f'Species "{unquote(botanical_name)}" not found.', 'error')
            return abort(404)
        
        # Valid screen types
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification', 'planting']
        if screen_type not in valid_screens:
            return abort(404)
        
        # Load screen configuration
        screen_config = load_screen_config(screen_type)
        
        # Get filter parameters to preserve state
        current_filters = {
            'name': request.args.get('name', ''),
            'start_seed_watch': request.args.get('start_seed_watch', ''),
            'germination_code': request.args.get('germination_code', ''),
            'light': request.args.get('light', ''),
            'moisture': request.args.get('moisture', '')
        }
        
        # Check if running in development (only show in actual development workspace, not deployed)
        is_development = (os.environ.get('REPLIT_ENVIRONMENT', 'development') != 'production')
        
        return render_template('species_detail.html', 
                             species=species, 
                             screen_config=screen_config,
                             current_screen=screen_type,
                             valid_screens=valid_screens,
                             current_filters=current_filters,
                             botanical_name=unquote(botanical_name),
                             is_development=is_development)
        
    except Exception as e:
        app.logger.error(f"Error loading species detail: {str(e)}")
        flash(f'Error loading species data: {str(e)}', 'error')
        return abort(404)

@app.route('/api/species/<path:botanical_name>/<screen_type>')
def api_species_screen(botanical_name, screen_type):
    """API endpoint for loading species screen data without page reload"""
    try:
        # Load plant data
        df = load_plant_data()
        if df.empty:
            return {"error": "No plant data available"}, 500
        
        # Get the specific species
        species = get_species_by_name(df, botanical_name)
        if not species:
            return {"error": "Species not found"}, 404
        
        # Valid screen types
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification', 'planting']
        if screen_type not in valid_screens:
            return {"error": "Invalid screen type"}, 404
        
        # Load screen configuration
        screen_config = load_screen_config(screen_type)
        
        # Return JSON data
        return {
            "species": species,
            "screen_config": screen_config,
            "current_screen": screen_type
        }
        
    except Exception as e:
        app.logger.error(f"Error in API endpoint: {str(e)}")
        return {"error": str(e)}, 500

@app.route('/admin')
def admin_dashboard():
    """Admin dashboard listing all admin pages"""
    admin_pages = [
        {
            'title': 'Issue Reports',
            'url': '/admin/issues',
            'description': 'View and manage all reported issues from community members',
            'icon': 'alert-triangle'
        },
        {
            'title': 'Data Columns',
            'url': '/admin/columns', 
            'description': 'Analyze data columns, usage patterns, and field assignments',
            'icon': 'columns'
        },
        {
            'title': 'Refresh Data',
            'url': '/admin/refresh',
            'description': 'Manually refresh cached plant data from Google Drive',
            'icon': 'refresh-cw'
        },
        {
            'title': 'API Columns',
            'url': '/api/columns',
            'description': 'JSON API endpoint for programmatic access to column data',
            'icon': 'code'
        },
        {
            'title': 'Column Usage Grid',
            'url': '/admin/column-usage',
            'description': 'Grid view showing which columns are used on which pages',
            'icon': 'grid'
        }
    ]
    
    return render_template('admin_dashboard.html', admin_pages=admin_pages)

@app.route('/admin/refresh')
def refresh_data():
    """Admin route to manually refresh cached data"""
    global _cached_plant_data
    try:
        _cached_plant_data = None  # Clear cache
        df = load_plant_data(force_reload=True)
        flash(f'Data refreshed successfully! Loaded {len(df)} plant species.', 'success')
        return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"Error refreshing data: {str(e)}")
        flash(f'Error refreshing data: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/api/check-updates')
def check_for_updates():
    """API endpoint to check if Google Drive data has been updated and reload if needed"""
    global _cached_plant_data
    
    # Get the Google Drive URL from settings
    settings = load_app_settings()
    google_drive_url = settings.get('data_source', {}).get('google_drive_csv_url', '')
    
    if not google_drive_url:
        return jsonify({
            'status': 'error',
            'message': 'No Google Drive URL configured',
            'reloaded': False
        })
    
    # Extract file ID from URL
    file_id = get_file_id_from_url(google_drive_url)
    if not file_id:
        return jsonify({
            'status': 'error', 
            'message': 'Could not extract file ID from Google Drive URL',
            'reloaded': False
        })
    
    # Check if file has been modified
    was_modified, message = check_google_drive_file_modified(file_id)
    
    if was_modified is None:
        # Error occurred or API not configured - fall back to simple reload
        _cached_plant_data = None
        df = load_plant_data(force_reload=True)
        return jsonify({
            'status': 'reloaded',
            'message': f'API not available - forced reload. Loaded {len(df)} species. ({message})',
            'reloaded': True,
            'species_count': len(df)
        })
    
    if was_modified:
        # File was modified - reload the data
        _cached_plant_data = None
        df = load_plant_data(force_reload=True)
        return jsonify({
            'status': 'updated',
            'message': f'Data updated! Loaded {len(df)} species. {message}',
            'reloaded': True,
            'species_count': len(df)
        })
    else:
        # File not modified - no reload needed
        df = _cached_plant_data if _cached_plant_data is not None else load_plant_data()
        return jsonify({
            'status': 'unchanged',
            'message': f'Data is up to date. {message}',
            'reloaded': False,
            'species_count': len(df) if df is not None else 0
        })

@app.route('/admin/columns')
def admin_data_columns():
    """Admin page to view all available columns and their current assignments"""
    try:
        # Load plant data to get all available columns
        df = load_plant_data()
        if df.empty:
            flash('No plant data available to analyze columns', 'error')
            return render_template('admin_columns.html', columns_info={})
        
        # Get all column names from the data
        all_columns = list(df.columns)
        
        # Load current display configuration
        display_config = load_display_config()
        
        # Load all screen configurations
        screen_configs = {}
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification', 'planting']
        for screen in valid_screens:
            screen_configs[screen] = load_screen_config(screen)
        
        # Analyze column usage
        columns_info = {}
        for col in all_columns:
            # Get sample data for this column
            non_empty_values = df[col].dropna()
            sample_values = non_empty_values.head(3).tolist() if len(non_empty_values) > 0 else []
            
            # Check where this column is used
            used_in = {
                'main_page': any(c.get('field') == col for c in display_config.get('main_page_columns', [])),
                'screens': []
            }
            
            for screen_name, config in screen_configs.items():
                if any(c.get('field') == col for c in config.get('columns', [])):
                    used_in['screens'].append(screen_name)
            
            # Determine data type and length
            data_type = 'text'
            max_length = 0
            if len(non_empty_values) > 0:
                max_length = max(len(str(val)) for val in non_empty_values)
                if max_length > 100:
                    data_type = 'paragraph'
                elif any(str(val).startswith('http') for val in non_empty_values[:10]):
                    data_type = 'link'
            
            columns_info[col] = {
                'total_records': len(df),
                'non_empty_count': len(non_empty_values),
                'sample_values': sample_values,
                'max_length': max_length,
                'data_type': data_type,
                'used_in': used_in,
                'is_unused': not used_in['main_page'] and not used_in['screens']
            }
        
        return render_template('admin_columns.html', 
                             columns_info=columns_info,
                             display_config=display_config,
                             screen_configs=screen_configs,
                             total_species=len(df))
        
    except Exception as e:
        app.logger.error(f"Error analyzing columns: {str(e)}")
        flash(f'Error analyzing data columns: {str(e)}', 'error')
        return render_template('admin_columns.html', columns_info={})

@app.route('/api/columns')
def api_data_columns():
    """API endpoint to get column information as JSON"""
    try:
        # Load plant data
        df = load_plant_data()
        if df.empty:
            return {"error": "No plant data available"}, 500
        
        # Get all column names and sample data
        columns_data = {}
        for col in df.columns:
            non_empty_values = df[col].dropna()
            sample_values = non_empty_values.head(5).tolist() if len(non_empty_values) > 0 else []
            
            # Determine data characteristics
            max_length = max(len(str(val)) for val in non_empty_values) if len(non_empty_values) > 0 else 0
            data_type = 'text'
            if max_length > 100:
                data_type = 'paragraph'
            elif any(str(val).startswith('http') for val in non_empty_values[:10]):
                data_type = 'link'
            
            columns_data[col] = {
                "column_name": col,
                "total_records": len(df),
                "non_empty_count": len(non_empty_values),
                "fill_percentage": round((len(non_empty_values) / len(df) * 100), 1) if len(df) > 0 else 0,
                "max_length": max_length,
                "suggested_type": data_type,
                "sample_values": sample_values
            }
        
        return {
            "total_columns": len(columns_data),
            "total_species": len(df),
            "columns": columns_data,
            "csv_guidelines": {
                "paragraph_text": "Wrap long text in double quotes, use line breaks within quotes",
                "special_characters": "Escape internal quotes by doubling them (\"\")",
                "google_sheets": "Google Sheets handles CSV formatting automatically"
            }
        }
        
    except Exception as e:
        app.logger.error(f"Error in API column data: {str(e)}")
        return {"error": str(e)}, 500

@app.route('/report-issue/<path:botanical_name>', methods=['GET', 'POST'])
def report_issue(botanical_name):
    """Report an issue with species data"""
    if request.method == 'POST':
        try:
            # Get form data
            issue_type = request.form.get('issue_type', '')
            description = request.form.get('description', '')
            user_email = request.form.get('user_email', 'anonymous')
            
            # Create issue record
            issue_data = {
                'timestamp': datetime.now().isoformat(),
                'botanical_name': botanical_name,
                'issue_type': issue_type,
                'description': description,
                'user_email': user_email,
                'status': 'open'
            }
            
            # Append to issues file
            import json
            import os
            issues_file = 'reported_issues.json'
            
            # Load existing issues or create new list
            if os.path.exists(issues_file):
                with open(issues_file, 'r') as f:
                    issues = json.load(f)
            else:
                issues = []
            
            # Add new issue
            issues.append(issue_data)
            
            # Save back to file
            with open(issues_file, 'w') as f:
                json.dump(issues, f, indent=2)
            
            flash('Thank you! Your issue report has been submitted.', 'success')
            return redirect(url_for('species_detail', botanical_name=botanical_name))
            
        except Exception as e:
            app.logger.error(f"Error submitting issue report: {str(e)}")
            flash('Error submitting report. Please try again.', 'error')
    
    # Load species data for display
    try:
        df = load_plant_data()
        species = get_species_by_name(df, botanical_name)
        if not species:
            flash('Species not found', 'error')
            return redirect(url_for('index'))
            
        return render_template('report_issue.html', 
                             species=species, 
                             botanical_name=botanical_name)
    except Exception as e:
        app.logger.error(f"Error loading species for issue report: {str(e)}")
        flash('Error loading species data', 'error')
        return redirect(url_for('index'))

@app.route('/admin/column-usage')
def admin_column_usage():
    """Admin page showing column usage across all pages in a grid format"""
    try:
        # Load plant data to get all available columns
        df = load_plant_data()
        if df.empty:
            flash('No plant data available to analyze columns', 'error')
            return render_template('admin_column_usage.html', column_usage={})
        
        # Get all column names from the data
        all_columns = list(df.columns)
        
        # Load current display configuration
        display_config = load_display_config()
        
        # Load all screen configurations
        screen_configs = {}
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification', 'planting']
        for screen in valid_screens:
            screen_configs[screen] = load_screen_config(screen)
        
        # Build usage grid
        column_usage = {}
        for col in all_columns:
            usage = {
                'column_name': col,
                'main_page': False,
                'identification': False,
                'collection': False,
                'processing': False,
                'storage': False,
                'stratification': False,
                'planting': False,
                'total_uses': 0
            }
            
            # Check main page usage
            if any(c.get('field') == col for c in display_config.get('main_page_columns', [])):
                usage['main_page'] = True
                usage['total_uses'] += 1
            
            # Check each screen usage
            for screen_name, config in screen_configs.items():
                if any(c.get('field') == col for c in config.get('columns', [])):
                    usage[screen_name] = True
                    usage['total_uses'] += 1
            
            # Get some sample data for context
            non_empty_values = df[col].dropna()
            usage['sample_data'] = non_empty_values.head(2).tolist() if len(non_empty_values) > 0 else []
            usage['fill_percentage'] = round((len(non_empty_values) / len(df) * 100), 1) if len(df) > 0 else 0
            
            column_usage[col] = usage
        
        # Keep original column order from CSV file (preserves data structure)
        ordered_columns = [(col, column_usage[col]) for col in all_columns]
        
        # Check if running in development (only show in actual development workspace, not deployed)
        is_development = (os.environ.get('REPLIT_ENVIRONMENT', 'development') != 'production')
        
        return render_template('admin_column_usage.html', 
                             column_usage=dict(ordered_columns),
                             total_columns=len(all_columns),
                             total_species=len(df),
                             is_development=is_development)
        
    except Exception as e:
        app.logger.error(f"Error analyzing column usage: {str(e)}")
        flash(f'Error analyzing column usage: {str(e)}', 'error')
        return render_template('admin_column_usage.html', 
                             column_usage={}, 
                             is_development=(os.environ.get('REPLIT_ENVIRONMENT', 'development') != 'production'))

@app.route('/admin/toggle-column', methods=['POST'])
def toggle_column():
    """Toggle column usage for a specific screen (development only)"""
    # Only allow in development environment (not deployed)
    if os.environ.get('REPLIT_ENVIRONMENT', 'development') == 'production':
        return {"error": "Not available in production"}, 403
    
    try:
        data = request.get_json()
        column_name = data.get('column_name')
        screen_name = data.get('screen_name')
        enabled = data.get('enabled', False)
        
        if not column_name or not screen_name:
            return {"error": "Missing column_name or screen_name"}, 400
        
        # Handle main page vs screen configurations
        if screen_name == 'main_page':
            # Load main page display config
            display_config = load_display_config()
            columns = display_config.get('main_page_columns', [])
            
            if enabled:
                # Add column if not present
                if not any(c.get('field') == column_name for c in columns):
                    columns.append({
                        "field": column_name,
                        "label": column_name.replace('_', ' ').title(),
                        "width": "auto"
                    })
            else:
                # Remove column if present
                columns = [c for c in columns if c.get('field') != column_name]
            
            # Update config
            display_config['main_page_columns'] = columns
            
            # Save back to file
            import json
            with open('config/display_columns.json', 'w') as f:
                json.dump(display_config, f, indent=2)
        
        else:
            # Handle species screen configurations
            valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification', 'planting']
            if screen_name not in valid_screens:
                return {"error": f"Invalid screen name: {screen_name}"}, 400
            
            # Load screen config
            screen_config = load_screen_config(screen_name)
            columns = screen_config.get('columns', [])
            
            if enabled:
                # Add column if not present
                if not any(c.get('field') == column_name for c in columns):
                    columns.append({
                        "field": column_name,
                        "label": column_name.replace('_', ' ').title(),
                        "width": "auto"
                    })
            else:
                # Remove column if present (but ensure at least one remains)
                if len([c for c in columns if c.get('field') != column_name]) > 0:
                    columns = [c for c in columns if c.get('field') != column_name]
                else:
                    return {"error": "Cannot remove last column from screen"}, 400
            
            # Update config
            screen_config['columns'] = columns
            
            # Save back to file
            import json
            with open(f'config/screen_{screen_name}.json', 'w') as f:
                json.dump(screen_config, f, indent=2)
        
        # Clear any cached configurations (if you have them)
        global _cached_display_config, _cached_screen_configs
        _cached_display_config = None
        if '_cached_screen_configs' in globals():
            _cached_screen_configs.clear()
        
        return {"success": True, "message": "Column configuration updated"}
        
    except Exception as e:
        app.logger.error(f"Error toggling column: {str(e)}")
        return {"error": str(e)}, 500

@app.route('/admin/issues')
def admin_issues():
    """View all reported issues"""
    try:
        import json
        import os
        issues_file = 'reported_issues.json'
        
        if os.path.exists(issues_file):
            with open(issues_file, 'r') as f:
                issues = json.load(f)
        else:
            issues = []
        
        # Sort by timestamp, newest first
        issues.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return render_template('admin_issues.html', issues=issues)
        
    except Exception as e:
        app.logger.error(f"Error loading issues: {str(e)}")
        flash('Error loading issues', 'error')
        return render_template('admin_issues.html', issues=[])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

import os
import pandas as pd
import logging
import json
import requests
import threading
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

# Image file extensions for detection
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp')

# HTML tags to detect formatted content
import re
HTML_TAG_PATTERN = re.compile(r'<(b|i|strong|em|p|br|ul|li|ol|a|span|div|h[1-6])[^>]*>', re.IGNORECASE)

def is_image_url(url):
    """Check if a URL points to an image file"""
    if not isinstance(url, str) or not url.startswith('http'):
        return False
    # Check extension (handle URLs with query params)
    url_path = url.split('?')[0].lower()
    return any(url_path.endswith(ext) for ext in IMAGE_EXTENSIONS)

def contains_html(text):
    """Check if text contains HTML formatting tags"""
    if not isinstance(text, str):
        return False
    return bool(HTML_TAG_PATTERN.search(text))

def is_chart_data(data):
    """Check if data is suitable for rendering as a chart (dict with string keys and numeric values)"""
    if not isinstance(data, dict) or len(data) < 2:
        return False
    # Check if all values are numeric
    numeric_count = sum(1 for v in data.values() if isinstance(v, (int, float)))
    return numeric_count >= len(data) * 0.8  # At least 80% numeric values

# Custom Jinja filter to parse JSON and detect URL dictionaries
@app.template_filter('parse_json_urls')
def parse_json_urls_filter(value):
    """
    Parse a JSON string and return structured data for rendering.
    Returns a dict with 'type' and 'data' keys:
    - type: 'url_dict' for {name: url} dicts, 'url_list' for [url] lists, 
            'image' for single image URLs, 'image_dict' for {name: image_url} dicts,
            'text' for plain text
    - data: the parsed data or original value
    """
    if not value or not isinstance(value, str):
        return {'type': 'text', 'data': value}
    
    value = value.strip()
    
    # Check for single image URL (not JSON)
    if value.startswith('http') and is_image_url(value):
        return {'type': 'image', 'data': value}
    
    # Check for single non-image URL (not JSON)
    if value.startswith('http') and not value.startswith('{') and not value.startswith('['):
        return {'type': 'url', 'data': value}
    
    # Check for HTML-formatted content
    if contains_html(value):
        return {'type': 'html', 'data': value}
    
    if not (value.startswith('{') or value.startswith('[')):
        return {'type': 'text', 'data': value}
    
    try:
        parsed = json.loads(value)
        
        # Check if it's a dict with URL values
        if isinstance(parsed, dict):
            # Check if values are image URLs
            image_count = sum(1 for v in parsed.values() if is_image_url(v))
            url_count = sum(1 for v in parsed.values() if isinstance(v, str) and v.startswith('http'))
            
            if image_count > 0 and image_count >= len(parsed) * 0.5:
                return {'type': 'image_dict', 'data': parsed}
            if url_count > 0 and url_count >= len(parsed) * 0.5:
                return {'type': 'url_dict', 'data': parsed}
            # Check if it's chart data (numeric values)
            if is_chart_data(parsed):
                return {'type': 'chart', 'data': parsed}
            return {'type': 'json_dict', 'data': parsed}
        
        # Check if it's a list of URLs
        if isinstance(parsed, list):
            image_count = sum(1 for v in parsed if is_image_url(v))
            url_count = sum(1 for v in parsed if isinstance(v, str) and v.startswith('http'))
            
            if image_count > 0 and image_count >= len(parsed) * 0.5:
                return {'type': 'image_list', 'data': parsed}
            if url_count > 0 and url_count >= len(parsed) * 0.5:
                return {'type': 'url_list', 'data': parsed}
            return {'type': 'json_list', 'data': parsed}
        
        return {'type': 'text', 'data': value}
    except (json.JSONDecodeError, TypeError):
        return {'type': 'text', 'data': value}

@app.template_filter('interpret_c_value')
def interpret_c_value_filter(value):
    """Template filter to interpret Coefficient of Conservatism value"""
    return interpret_conservatism(value)

@app.template_filter('interpret_w_value')
def interpret_w_value_filter(value):
    """Template filter to interpret Wetland Indicator value"""
    return interpret_wetland(value)

_data_cache_timestamp = None
_last_known_modified_time = None  # Track Google Drive file modification time
_data_load_lock = threading.Lock()  # Prevent concurrent data loads

# Disk cache paths for persistence across restarts
CACHE_DIR = '/tmp/plant_data_cache'
MAIN_CACHE_FILE = os.path.join(CACHE_DIR, 'main_data.pkl')
SUPPLEMENTAL_CACHE_FILE = os.path.join(CACHE_DIR, 'supplemental_data.pkl')
CACHE_META_FILE = os.path.join(CACHE_DIR, 'cache_meta.json')

# Global cache for supplemental data (PlantData Google Sheet)
_cached_supplemental_data = None
_supplemental_file_id = None
_supplemental_file_name = None

# Track columns removed during last cleanup (for admin notification)
_last_removed_columns = []

# Column labels mapping file - stores original headers for display
COLUMN_LABELS_FILE = 'config/column_labels.json'

def load_column_labels():
    """Load the column labels mapping from config file"""
    try:
        if os.path.exists(COLUMN_LABELS_FILE):
            with open(COLUMN_LABELS_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        app.logger.warning(f"Could not load column labels: {e}")
    return {}

def save_column_labels(labels_mapping):
    """Save the column labels mapping to config file"""
    try:
        # Ensure config directory exists
        os.makedirs(os.path.dirname(COLUMN_LABELS_FILE), exist_ok=True)
        with open(COLUMN_LABELS_FILE, 'w') as f:
            json.dump(labels_mapping, f, indent=2)
        app.logger.info(f"Saved {len(labels_mapping)} column labels to {COLUMN_LABELS_FILE}")
    except Exception as e:
        app.logger.warning(f"Could not save column labels: {e}")

def update_column_labels_from_headers(original_columns):
    """Update the column labels mapping with original headers before normalization"""
    # Load existing labels
    labels = load_column_labels()
    
    # Create normalized -> original mapping
    for original in original_columns:
        normalized = str(original).replace(' ', '_').lower()
        # Only update if we have a valid original header
        if original and str(original).strip() and not str(original).startswith('Unnamed'):
            labels[normalized] = str(original).strip()
    
    # Save updated labels
    save_column_labels(labels)
    return labels

def get_column_label(field_name):
    """Get the display label for a column field name"""
    labels = load_column_labels()
    if field_name in labels:
        return labels[field_name]
    # Fallback to generated label
    return field_name.replace('_', ' ').title()

def cleanup_missing_columns(valid_columns):
    """Remove columns from config files that no longer exist in the data.
    
    Args:
        valid_columns: Set of column names that exist in the current DataFrame
        
    Returns:
        List of removed column names for logging/notification
    """
    global _last_removed_columns
    removed_columns = []
    
    # 1. Clean up screen config files
    screen_files = [
        'config/screen_identification.json',
        'config/screen_collection.json',
        'config/screen_processing.json',
        'config/screen_storage.json',
        'config/screen_stratification.json',
        'config/screen_planting.json'
    ]
    
    for screen_file in screen_files:
        try:
            if os.path.exists(screen_file):
                with open(screen_file, 'r') as f:
                    config = json.load(f)
                
                original_columns = config.get('columns', [])
                cleaned_columns = [
                    col for col in original_columns 
                    if col.get('field') in valid_columns
                ]
                
                # Track removed columns
                for col in original_columns:
                    field = col.get('field')
                    if field not in valid_columns and field not in removed_columns:
                        removed_columns.append(field)
                
                if len(cleaned_columns) < len(original_columns):
                    config['columns'] = cleaned_columns
                    with open(screen_file, 'w') as f:
                        json.dump(config, f, indent=2)
                    app.logger.info(f"Cleaned {len(original_columns) - len(cleaned_columns)} missing columns from {screen_file}")
        except Exception as e:
            app.logger.warning(f"Error cleaning {screen_file}: {e}")
    
    # 2. Clean up display_columns.json (main page and filters)
    try:
        display_config_file = 'config/display_columns.json'
        if os.path.exists(display_config_file):
            with open(display_config_file, 'r') as f:
                display_config = json.load(f)
            
            modified = False
            
            # Clean main page columns
            main_cols = display_config.get('main_page_columns', [])
            cleaned_main = [col for col in main_cols if col.get('field') in valid_columns]
            for col in main_cols:
                field = col.get('field')
                if field not in valid_columns and field not in removed_columns:
                    removed_columns.append(field)
            if len(cleaned_main) < len(main_cols):
                display_config['main_page_columns'] = cleaned_main
                modified = True
            
            # Clean filter columns
            filter_cols = display_config.get('filter_columns', [])
            cleaned_filters = [col for col in filter_cols if col.get('field') in valid_columns]
            for col in filter_cols:
                field = col.get('field')
                if field not in valid_columns and field not in removed_columns:
                    removed_columns.append(field)
            if len(cleaned_filters) < len(filter_cols):
                display_config['filter_columns'] = cleaned_filters
                modified = True
            
            if modified:
                with open(display_config_file, 'w') as f:
                    json.dump(display_config, f, indent=2)
                app.logger.info(f"Cleaned missing columns from {display_config_file}")
    except Exception as e:
        app.logger.warning(f"Error cleaning display_columns.json: {e}")
    
    # 3. Clean up column_labels.json (remove stale labels)
    try:
        labels = load_column_labels()
        cleaned_labels = {k: v for k, v in labels.items() if k in valid_columns}
        stale_count = len(labels) - len(cleaned_labels)
        if stale_count > 0:
            save_column_labels(cleaned_labels)
            app.logger.info(f"Removed {stale_count} stale entries from column_labels.json")
    except Exception as e:
        app.logger.warning(f"Error cleaning column_labels.json: {e}")
    
    # Store for admin notification
    _last_removed_columns = removed_columns
    return removed_columns

def get_last_removed_columns():
    """Get the list of columns removed during the last data load cleanup"""
    global _last_removed_columns
    return _last_removed_columns

def interpret_conservatism(c_value):
    """Interpret Coefficient of Conservatism value for display"""
    try:
        c = float(c_value)
    except (ValueError, TypeError):
        return None
    
    if c >= 0 and c <= 1:
        return {
            "value": c,
            "short": "Found almost anywhere",
            "description": "This plant grows in all kinds of places, including roadsides and disturbed areas. It's a survivor that isn't picky about conditions."
        }
    elif c >= 2 and c <= 3:
        return {
            "value": c,
            "short": "Adaptable",
            "description": "This plant can grow in many different settings, including areas that have been somewhat changed by human activity."
        }
    elif c >= 4 and c <= 6:
        return {
            "value": c,
            "short": "Prefers natural areas",
            "description": "This plant does best in natural areas but can handle some changes to its environment. Finding it suggests the habitat is in decent shape."
        }
    elif c >= 7 and c <= 8:
        return {
            "value": c,
            "short": "Needs quality habitat",
            "description": "This plant is choosy about where it lives. It strongly prefers natural areas that haven't been heavily disturbed."
        }
    elif c >= 9 and c <= 10:
        return {
            "value": c,
            "short": "Rare habitat specialist",
            "description": "This plant only thrives in high-quality natural areas. Finding it is a sign you're in a special place worth protecting."
        }
    return None

def interpret_wetland(w_value):
    """Interpret Coefficient of Wetness (CW) value for display
    
    Uses the -5 to +5 scale from Floristic Quality Assessment (Ladd & Thomas 2015):
    -5 = Obligate wetland (almost always in wetlands)
    -3 to -4 = Facultative wetland
    0 = Facultative (equally likely in wetlands or uplands)
    +3 to +4 = Facultative upland
    +5 = Dry upland (almost never in wetlands)
    """
    try:
        w = int(float(w_value))
    except (ValueError, TypeError):
        return None
    
    if w == -5:
        return {
            "short": "Loves wet feet",
            "description": "This plant almost always grows in wetlands. It thrives standing in water or saturated soil."
        }
    elif w in [-4, -3]:
        return {
            "short": "Prefers wet conditions",
            "description": "This plant is usually found in wetlands or very moist areas, but can sometimes grow in drier spots."
        }
    elif w in [-2, -1]:
        return {
            "short": "Leans toward moist",
            "description": "This plant does well with consistent moisture but isn't strictly a wetland species."
        }
    elif w == 0:
        return {
            "short": "Flexible about moisture",
            "description": "This plant is equally happy in wet or dry spots. It's adaptable with no strong preference."
        }
    elif w in [1, 2]:
        return {
            "short": "Leans toward dry",
            "description": "This plant prefers drier conditions but can tolerate occasional moisture."
        }
    elif w in [3, 4]:
        return {
            "short": "Prefers dry conditions",
            "description": "This plant usually grows in well-drained, drier upland areas and may struggle in wet soils."
        }
    elif w == 5:
        return {
            "short": "Needs dry ground",
            "description": "This plant almost never grows in wetlands. It needs well-drained soil and can rot in soggy conditions."
        }
    else:
        return None

def _ensure_cache_dir():
    """Ensure cache directory exists"""
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)

def _save_cache_to_disk(df, supplemental_df, file_name, supplemental_file_name):
    """Save data to disk cache for persistence across restarts"""
    try:
        _ensure_cache_dir()
        # Save main data
        df.to_pickle(MAIN_CACHE_FILE)
        # Save supplemental data if exists
        if supplemental_df is not None:
            supplemental_df.to_pickle(SUPPLEMENTAL_CACHE_FILE)
        # Save metadata
        meta = {
            'timestamp': datetime.now().isoformat(),
            'main_file': file_name,
            'supplemental_file': supplemental_file_name,
            'row_count': len(df)
        }
        with open(CACHE_META_FILE, 'w') as f:
            json.dump(meta, f)
        app.logger.info(f"Saved cache to disk: {len(df)} rows")
    except Exception as e:
        app.logger.warning(f"Failed to save disk cache: {e}")

def _load_cache_from_disk():
    """Load data from disk cache if available"""
    global _cached_plant_data, _cached_supplemental_data, _supplemental_file_name
    try:
        if os.path.exists(MAIN_CACHE_FILE) and os.path.exists(CACHE_META_FILE):
            # Load metadata to check cache age
            with open(CACHE_META_FILE, 'r') as f:
                meta = json.load(f)
            cache_time = datetime.fromisoformat(meta['timestamp'])
            age_hours = (datetime.now() - cache_time).total_seconds() / 3600
            
            # Use disk cache if less than 24 hours old
            if age_hours < 24:
                df = pd.read_pickle(MAIN_CACHE_FILE)
                supp_df = None
                if os.path.exists(SUPPLEMENTAL_CACHE_FILE):
                    supp_df = pd.read_pickle(SUPPLEMENTAL_CACHE_FILE)
                app.logger.info(f"Loaded {len(df)} rows from disk cache (age: {age_hours:.1f}h)")
                return df, supp_df, meta.get('supplemental_file')
    except Exception as e:
        app.logger.warning(f"Failed to load disk cache: {e}")
    return None, None, None

def _clear_disk_cache():
    """Clear disk cache when data is refreshed"""
    try:
        for f in [MAIN_CACHE_FILE, SUPPLEMENTAL_CACHE_FILE, CACHE_META_FILE]:
            if os.path.exists(f):
                os.remove(f)
        app.logger.info("Cleared disk cache")
    except Exception as e:
        app.logger.warning(f"Failed to clear disk cache: {e}")

def _get_supplemental_file_info():
    """Get info about the current supplemental data file"""
    global _supplemental_file_name, _cached_supplemental_data
    if _supplemental_file_name:
        col_count = len(_cached_supplemental_data.columns) if _cached_supplemental_data is not None else 0
        row_count = len(_cached_supplemental_data) if _cached_supplemental_data is not None else 0
        return {
            'found': True,
            'file_name': _supplemental_file_name,
            'columns': col_count,
            'rows': row_count
        }
    return {
        'found': False,
        'file_name': None,
        'columns': 0,
        'rows': 0
    }

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

# Global to track the current file being used
_current_file_id = None
_current_file_name = None

def find_latest_file_in_folder(folder_id, file_prefix):
    """Find the most recently modified file in a Google Drive folder that matches the prefix"""
    global _current_file_id, _current_file_name
    
    service = get_google_drive_service()
    if not service:
        return None, None, "Google Drive API not configured"
    
    try:
        # Search for files in the folder that match the prefix
        query = f"'{folder_id}' in parents and name contains '{file_prefix}' and mimeType='text/csv' and trashed=false"
        
        results = service.files().list(
            q=query,
            fields='files(id, name, modifiedTime)',
            orderBy='modifiedTime desc',
            pageSize=10
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            # Try without mimeType restriction (Google Sheets exported as CSV)
            query = f"'{folder_id}' in parents and name contains '{file_prefix}' and trashed=false"
            results = service.files().list(
                q=query,
                fields='files(id, name, modifiedTime)',
                orderBy='modifiedTime desc',
                pageSize=10
            ).execute()
            files = results.get('files', [])
        
        if not files:
            app.logger.warning(f"No files found in folder {folder_id} with prefix '{file_prefix}'")
            return None, None, f"No files found with prefix '{file_prefix}'"
        
        # Get the most recently modified file
        latest_file = files[0]
        file_id = latest_file['id']
        file_name = latest_file['name']
        modified_time = latest_file.get('modifiedTime', 'Unknown')
        
        # Check if this is a different file than what we're currently using
        if _current_file_id != file_id:
            old_file = _current_file_name or "None"
            _current_file_id = file_id
            _current_file_name = file_name
            app.logger.info(f"Found new file: '{file_name}' (modified: {modified_time}), was using: {old_file}")
            return file_id, file_name, f"New file found: '{file_name}' (modified: {modified_time})"
        else:
            return file_id, file_name, f"Same file: '{file_name}' (modified: {modified_time})"
            
    except Exception as e:
        app.logger.error(f"Error searching Google Drive folder: {str(e)}")
        return None, None, f"Error: {str(e)}"

def check_for_new_data_file():
    """Check if there's a new data file in the Google Drive folder"""
    global _current_file_id, _current_file_name
    
    settings = load_app_settings()
    folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
    file_prefix = settings.get('data_source', {}).get('file_prefix', '')
    
    if not folder_id or not file_prefix:
        # Fall back to legacy single-file URL if folder not configured
        return None, "Folder-based checking not configured"
    
    file_id, file_name, message = find_latest_file_in_folder(folder_id, file_prefix)
    
    if file_id is None:
        return None, message
    
    # Check if this is a new file
    was_different_file = (_current_file_id != file_id) if _current_file_id else True
    
    return was_different_file, message

def find_google_sheet_in_folder(folder_id, file_prefix):
    """Find a Google Sheet in a folder that matches the prefix"""
    global _supplemental_file_id, _supplemental_file_name
    
    service = get_google_drive_service()
    if not service:
        return None, None, "Google Drive API not configured"
    
    try:
        # Search for Google Sheets in the folder that match the prefix
        query = f"'{folder_id}' in parents and name contains '{file_prefix}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        
        results = service.files().list(
            q=query,
            fields='files(id, name, modifiedTime)',
            orderBy='modifiedTime desc',
            pageSize=10
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            app.logger.info(f"No Google Sheets found in folder with prefix '{file_prefix}'")
            return None, None, f"No Google Sheets found with prefix '{file_prefix}'"
        
        # Get the most recently modified file
        latest_file = files[0]
        file_id = latest_file['id']
        file_name = latest_file['name']
        modified_time = latest_file.get('modifiedTime', 'Unknown')
        
        _supplemental_file_id = file_id
        _supplemental_file_name = file_name
        app.logger.info(f"Found supplemental Google Sheet: '{file_name}' (modified: {modified_time})")
        
        return file_id, file_name, f"Found Google Sheet: '{file_name}' (modified: {modified_time})"
            
    except Exception as e:
        app.logger.error(f"Error searching for Google Sheet: {str(e)}")
        return None, None, f"Error: {str(e)}"

def export_google_sheet_as_csv(file_id):
    """Export a Google Sheet as CSV data"""
    service = get_google_drive_service()
    if not service:
        return None, "Google Drive API not configured"
    
    try:
        # Export the Google Sheet as CSV
        request = service.files().export_media(
            fileId=file_id,
            mimeType='text/csv'
        )
        
        # Execute the request and get the content
        from io import BytesIO
        from googleapiclient.http import MediaIoBaseDownload
        
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        fh.seek(0)
        csv_content = fh.read().decode('utf-8')
        return csv_content, None
        
    except Exception as e:
        app.logger.error(f"Error exporting Google Sheet as CSV: {str(e)}")
        return None, f"Error: {str(e)}"

def load_supplemental_data(force_reload=False):
    """Load supplemental data from PlantData Google Sheet"""
    global _cached_supplemental_data, _supplemental_file_id, _supplemental_file_name
    
    # Return cached data if available and not forcing reload
    if not force_reload and _cached_supplemental_data is not None:
        return _cached_supplemental_data
    
    settings = load_app_settings()
    folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
    supplemental_prefix = settings.get('data_source', {}).get('supplemental_file_prefix', 'PlantData')
    
    if not folder_id or not supplemental_prefix:
        app.logger.info("Supplemental data not configured")
        return None
    
    try:
        # Find the Google Sheet
        file_id, file_name, message = find_google_sheet_in_folder(folder_id, supplemental_prefix)
        
        if not file_id:
            app.logger.info(f"No supplemental data file found: {message}")
            return None
        
        # Export as CSV
        csv_content, error = export_google_sheet_as_csv(file_id)
        if error:
            app.logger.error(f"Error exporting supplemental data: {error}")
            return None
        
        # Parse CSV
        csv_data = StringIO(csv_content)
        df = pd.read_csv(csv_data)
        
        # Capture original column headers before normalization
        update_column_labels_from_headers(df.columns.tolist())
        
        # Clean up column names - replace spaces with underscores and make lowercase
        df.columns = df.columns.str.replace(' ', '_').str.lower()
        df = df.fillna('')
        
        app.logger.info(f"Successfully loaded {len(df)} rows of supplemental data from '{file_name}'")
        app.logger.info(f"Supplemental columns: {list(df.columns)}")
        
        _cached_supplemental_data = df
        return df
        
    except Exception as e:
        app.logger.error(f"Error loading supplemental data: {str(e)}")
        return None

def create_species_key(row):
    """Create a normalized key from genus and species for matching"""
    genus = str(row.get('genus', '')).strip().lower()
    species = str(row.get('species', '')).strip().lower()
    return f"{genus}_{species}"

def create_species_key_from_botanical_name(botanical_name):
    """Extract genus and species from botanical_name to create a merge key"""
    name = str(botanical_name).strip().lower()
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[0]}_{parts[1]}"
    elif len(parts) == 1:
        return f"{parts[0]}_"
    return ""

def merge_supplemental_data(main_df, supplemental_df):
    """Merge supplemental data into main dataframe using genus + species as key"""
    if supplemental_df is None or supplemental_df.empty:
        return main_df
    
    try:
        # Check if supplemental data has genus and species columns
        supp_has_keys = 'genus' in supplemental_df.columns and 'species' in supplemental_df.columns
        
        if not supp_has_keys:
            app.logger.warning("Supplemental data missing genus/species columns - cannot merge")
            return main_df
        
        # Check if main data has genus/species or botanical_name
        main_has_keys = 'genus' in main_df.columns and 'species' in main_df.columns
        main_has_botanical = 'botanical_name' in main_df.columns
        
        if not main_has_keys and not main_has_botanical:
            app.logger.warning("Main data missing genus/species and botanical_name columns - cannot merge supplemental data")
            return main_df
        
        # Create merge keys
        main_df = main_df.copy()
        supplemental_df = supplemental_df.copy()
        
        # Create merge key for main data
        if main_has_keys:
            main_df['_merge_key'] = main_df.apply(create_species_key, axis=1)
        else:
            main_df['_merge_key'] = main_df['botanical_name'].apply(create_species_key_from_botanical_name)
        
        # Create merge key for supplemental data
        supplemental_df['_merge_key'] = supplemental_df.apply(create_species_key, axis=1)
        
        # Get columns that are only in supplemental data (excluding merge key and common columns)
        main_cols = set(main_df.columns)
        supp_cols = set(supplemental_df.columns)
        new_cols = supp_cols - main_cols - {'_merge_key', 'genus', 'species'}
        
        if not new_cols:
            app.logger.info("No new columns in supplemental data to merge")
            main_df = main_df.drop('_merge_key', axis=1)
            return main_df
        
        # Select only the merge key and new columns from supplemental data
        supp_subset = supplemental_df[['_merge_key'] + list(new_cols)].copy()
        
        # Remove duplicates in supplemental data (keep first occurrence)
        supp_subset = supp_subset.drop_duplicates(subset=['_merge_key'], keep='first')
        
        # Merge
        merged_df = main_df.merge(supp_subset, on='_merge_key', how='left')
        
        # Clean up merge key
        merged_df = merged_df.drop('_merge_key', axis=1)
        
        # Fill NaN in new columns with empty string
        for col in new_cols:
            if col in merged_df.columns:
                merged_df[col] = merged_df[col].fillna('')
        
        app.logger.info(f"Successfully merged {len(new_cols)} supplemental columns: {list(new_cols)}")
        
        return merged_df
        
    except Exception as e:
        app.logger.error(f"Error merging supplemental data: {str(e)}")
        return main_df

def load_display_config():
    """Load display configuration from JSON file"""
    try:
        with open('config/display_columns.json', 'r') as f:
            config = json.load(f)
        
        # Apply original column labels from column_labels.json
        for col in config.get('main_page_columns', []):
            field_name = col.get('field')
            if field_name:
                original_label = get_column_label(field_name)
                col['label'] = original_label
        
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

def load_plant_data(force_reload=False, file_id_override=None):
    """Load and process plant data from Google Drive CSV or fallback to local files"""
    global _cached_plant_data, _data_cache_timestamp, _current_file_id
    
    # Clear disk cache when forcing reload
    if force_reload:
        _clear_disk_cache()
    
    # Return cached data if available and not forcing reload (fast path, no lock needed)
    if not force_reload and _cached_plant_data is not None:
        return _cached_plant_data
    
    # Use lock to prevent concurrent loading from Google Drive
    with _data_load_lock:
        # Double-check cache after acquiring lock (another thread may have loaded it)
        if not force_reload and _cached_plant_data is not None:
            return _cached_plant_data
        
        # Try disk cache for fast startup after restarts
        if not force_reload:
            disk_df, disk_supp_df, supp_file_name = _load_cache_from_disk()
            if disk_df is not None:
                global _cached_supplemental_data, _supplemental_file_name
                _cached_plant_data = disk_df
                _cached_supplemental_data = disk_supp_df
                _supplemental_file_name = supp_file_name
                _data_cache_timestamp = pd.Timestamp.now()
                return disk_df
        
        # Load settings
        settings = load_app_settings()
        folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
        file_prefix = settings.get('data_source', {}).get('file_prefix', '')
        google_drive_url = settings.get('data_source', {}).get('google_drive_csv_url', '')
        
        # First try folder-based approach if configured
        try:
            if folder_id and file_prefix:
                # Use override file_id if provided, otherwise find the latest
                file_name = None  # Initialize to avoid UnboundLocalError
                if file_id_override:
                    file_id = file_id_override
                    file_name = f"{file_prefix}_override"  # Placeholder name for override
                    app.logger.info(f"Using provided file ID: {file_id}")
                else:
                    file_id, file_name, message = find_latest_file_in_folder(folder_id, file_prefix)
                    if file_id:
                        app.logger.info(f"Found latest file: {file_name} ({file_id})")
                    else:
                        app.logger.warning(f"Could not find file in folder: {message}")
                        file_id = None
                
                if file_id:
                    # Download using the file ID
                    download_url = f"https://drive.google.com/uc?id={file_id}&export=download"
                    app.logger.info(f"Loading data from Google Drive file ID: {file_id}")
                    
                    response = requests.get(download_url, timeout=30)
                    response.raise_for_status()
                    
                    # Read CSV from the response text
                    csv_data = StringIO(response.text)
                    df = pd.read_csv(csv_data)
                    
                    # Check if first row contains the actual headers (Google Sheets issue)
                    if len(df) > 0 and 'Unnamed: 0' in df.columns and df.iloc[0, 0] == 'Botanical Name':
                        app.logger.info("Detected header row in data, fixing column names")
                        new_columns = df.iloc[0].tolist()
                        df.columns = new_columns
                        df = df.iloc[1:].reset_index(drop=True)
                    
                    # Capture original column headers before normalization
                    update_column_labels_from_headers(df.columns.tolist())
                    
                    # Clean up column names
                    df.columns = df.columns.str.replace(' ', '_').str.lower()
                    df = df.fillna('')
                    
                    app.logger.info(f"Successfully loaded {len(df)} rows from Google Drive (folder-based)")
                    
                    # Load and merge supplemental data
                    supplemental_df = load_supplemental_data(force_reload=force_reload)
                    if supplemental_df is not None:
                        df = merge_supplemental_data(df, supplemental_df)
                    
                    # Clean up any configured columns that no longer exist
                    valid_columns = set(df.columns.tolist())
                    removed = cleanup_missing_columns(valid_columns)
                    if removed:
                        app.logger.warning(f"Removed {len(removed)} missing columns from configs: {removed}")
                    
                    _cached_plant_data = df
                    _data_cache_timestamp = pd.Timestamp.now()
                    # Save to disk for fast restarts
                    _save_cache_to_disk(df, _cached_supplemental_data, file_name, _supplemental_file_name)
                    return df
                    
        except Exception as e:
            app.logger.error(f"Error with folder-based loading: {str(e)}")
        
        # Fall back to legacy single-URL approach
        try:
            if google_drive_url:
                app.logger.info(f"Loading data from Google Drive URL: {google_drive_url}")
                
                download_url = convert_google_drive_url(google_drive_url)
                response = requests.get(download_url, timeout=30)
                response.raise_for_status()
                
                csv_data = StringIO(response.text)
                df = pd.read_csv(csv_data)
                
                if len(df) > 0 and 'Unnamed: 0' in df.columns and df.iloc[0, 0] == 'Botanical Name':
                    app.logger.info("Detected header row in data, fixing column names")
                    new_columns = df.iloc[0].tolist()
                    df.columns = new_columns
                    df = df.iloc[1:].reset_index(drop=True)
                
                # Capture original column headers before normalization
                update_column_labels_from_headers(df.columns.tolist())
                
                df.columns = df.columns.str.replace(' ', '_').str.lower()
                df = df.fillna('')
                
                app.logger.info(f"Successfully loaded {len(df)} rows from Google Drive (URL-based)")
                
                # Load and merge supplemental data
                supplemental_df = load_supplemental_data(force_reload=force_reload)
                if supplemental_df is not None:
                    df = merge_supplemental_data(df, supplemental_df)
                
                # Clean up any configured columns that no longer exist
                valid_columns = set(df.columns.tolist())
                removed = cleanup_missing_columns(valid_columns)
                if removed:
                    app.logger.warning(f"Removed {len(removed)} missing columns from configs: {removed}")
                
                _cached_plant_data = df
                _data_cache_timestamp = pd.Timestamp.now()
                # Save to disk for fast restarts
                _save_cache_to_disk(df, _cached_supplemental_data, 'url_based', _supplemental_file_name)
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
            
            # Capture original column headers before normalization
            update_column_labels_from_headers(df.columns.tolist())
            
            # Clean up column names - replace spaces with underscores and make lowercase
            df.columns = df.columns.str.replace(' ', '_').str.lower()
            
            # Clean up the data - fill NaN values with empty strings
            df = df.fillna('')
            
            app.logger.info(f"Successfully loaded {len(df)} rows from local files")
            
            # Load and merge supplemental data
            supplemental_df = load_supplemental_data(force_reload=force_reload)
            if supplemental_df is not None:
                df = merge_supplemental_data(df, supplemental_df)
            
            # Clean up any configured columns that no longer exist
            valid_columns = set(df.columns.tolist())
            removed = cleanup_missing_columns(valid_columns)
            if removed:
                app.logger.warning(f"Removed {len(removed)} missing columns from configs: {removed}")
            
            # Cache the data
            _cached_plant_data = df
            _data_cache_timestamp = pd.Timestamp.now()
            # Save to disk for fast restarts
            _save_cache_to_disk(df, _cached_supplemental_data, 'local_fallback', _supplemental_file_name)
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
        
        # Apply original column labels from column_labels.json
        # This ensures labels match original spreadsheet headers
        for col in config.get('columns', []):
            field_name = col.get('field')
            if field_name:
                original_label = get_column_label(field_name)
                col['label'] = original_label
        
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
        
        return render_template('species_detail.html', 
                             species=species, 
                             screen_config=screen_config,
                             current_screen=screen_type,
                             valid_screens=valid_screens,
                             current_filters=current_filters,
                             botanical_name=unquote(botanical_name),
                             is_development=True)
        
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
    global _cached_plant_data, _cached_supplemental_data
    try:
        _cached_plant_data = None  # Clear cache
        _cached_supplemental_data = None  # Clear supplemental cache
        df = load_plant_data(force_reload=True)
        flash(f'Data refreshed successfully! Loaded {len(df)} plant species.', 'success')
        
        # Notify about any columns that were cleaned up
        removed = get_last_removed_columns()
        if removed:
            readable_names = [col.replace('_', ' ').title() for col in removed]
            flash(f'Cleaned up {len(removed)} missing columns from configurations: {", ".join(readable_names[:5])}{"..." if len(removed) > 5 else ""}', 'warning')
        
        return redirect(url_for('index'))
    except Exception as e:
        app.logger.error(f"Error refreshing data: {str(e)}")
        flash(f'Error refreshing data: {str(e)}', 'error')
        return redirect(url_for('index'))

@app.route('/api/check-updates')
def check_for_updates_endpoint():
    """API endpoint to check if a new data file exists in Google Drive folder and reload if needed"""
    global _cached_plant_data, _cached_supplemental_data, _current_file_id, _current_file_name
    
    settings = load_app_settings()
    folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
    file_prefix = settings.get('data_source', {}).get('file_prefix', '')
    
    # Try folder-based approach first
    if folder_id and file_prefix:
        # Store the current file ID before checking
        previous_file_id = _current_file_id
        
        # Find the latest file in the folder
        file_id, file_name, message = find_latest_file_in_folder(folder_id, file_prefix)
        
        if file_id is None:
            return jsonify({
                'status': 'error',
                'message': f'Could not find files: {message}',
                'reloaded': False,
                'supplemental': _get_supplemental_file_info()
            })
        
        # Check if this is a different file than before
        is_new_file = (previous_file_id != file_id)
        
        if is_new_file:
            # New file found - reload the data
            _cached_plant_data = None
            _cached_supplemental_data = None
            df = load_plant_data(force_reload=True, file_id_override=file_id)
            supp_info = _get_supplemental_file_info()
            return jsonify({
                'status': 'updated',
                'message': f"New file loaded: '{file_name}'. Loaded {len(df)} species.",
                'reloaded': True,
                'species_count': len(df),
                'file_name': file_name,
                'supplemental': supp_info
            })
        else:
            # Same file - no reload needed
            df = _cached_plant_data if _cached_plant_data is not None else load_plant_data()
            supp_info = _get_supplemental_file_info()
            return jsonify({
                'status': 'unchanged',
                'message': f"Already using latest file: '{file_name}'. {len(df)} species loaded.",
                'reloaded': False,
                'species_count': len(df) if df is not None else 0,
                'file_name': file_name,
                'supplemental': supp_info
            })
    
    # Fall back to legacy single-file checking
    google_drive_url = settings.get('data_source', {}).get('google_drive_csv_url', '')
    
    if not google_drive_url:
        return jsonify({
            'status': 'error',
            'message': 'No Google Drive folder or URL configured',
            'reloaded': False,
            'supplemental': _get_supplemental_file_info()
        })
    
    file_id = get_file_id_from_url(google_drive_url)
    if not file_id:
        return jsonify({
            'status': 'error', 
            'message': 'Could not extract file ID from Google Drive URL',
            'reloaded': False,
            'supplemental': _get_supplemental_file_info()
        })
    
    was_modified, message = check_google_drive_file_modified(file_id)
    
    if was_modified is None:
        _cached_plant_data = None
        _cached_supplemental_data = None
        df = load_plant_data(force_reload=True)
        supp_info = _get_supplemental_file_info()
        return jsonify({
            'status': 'reloaded',
            'message': f'API not available - forced reload. Loaded {len(df)} species. ({message})',
            'reloaded': True,
            'species_count': len(df),
            'supplemental': supp_info
        })
    
    if was_modified:
        _cached_plant_data = None
        _cached_supplemental_data = None
        df = load_plant_data(force_reload=True)
        supp_info = _get_supplemental_file_info()
        return jsonify({
            'status': 'updated',
            'message': f'Data updated! Loaded {len(df)} species. {message}',
            'reloaded': True,
            'species_count': len(df),
            'supplemental': supp_info
        })
    else:
        df = _cached_plant_data if _cached_plant_data is not None else load_plant_data()
        supp_info = _get_supplemental_file_info()
        return jsonify({
            'status': 'unchanged',
            'message': f'Data is up to date. {message}',
            'reloaded': False,
            'species_count': len(df) if df is not None else 0,
            'supplemental': supp_info
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

@app.route('/explain/wetland-indicator')
def explain_wetland():
    """Explanation page for Wetland Indicator values"""
    return render_template('explain_wetland.html')

@app.route('/explain/coefficient-of-conservatism')
def explain_conservatism():
    """Explanation page for Coefficient of Conservatism values"""
    return render_template('explain_conservatism.html')

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
        
        return render_template('admin_column_usage.html', 
                             column_usage=dict(ordered_columns),
                             total_columns=len(all_columns),
                             total_species=len(df),
                             is_development=True)
        
    except Exception as e:
        app.logger.error(f"Error analyzing column usage: {str(e)}")
        flash(f'Error analyzing column usage: {str(e)}', 'error')
        return render_template('admin_column_usage.html', 
                             column_usage={}, 
                             is_development=True)

@app.route('/admin/toggle-column', methods=['POST'])
def toggle_column():
    """Toggle column usage for a specific screen"""
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
                        "label": get_column_label(column_name),
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
                        "label": get_column_label(column_name),
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
        # Note: Screen configs are loaded fresh on each request, no cache to clear
        
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

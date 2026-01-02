import os
import pandas as pd
import logging
import json
import requests
import threading
from datetime import datetime
from io import StringIO
from flask import Flask, render_template, flash, request, url_for, abort, redirect, jsonify, session
from urllib.parse import quote, unquote

# Google Drive API imports
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False

# Cloudinary service imports
try:
    import cloudinary_service
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Context processor to make is_development available in all templates
@app.context_processor
def inject_is_development():
    """Make is_development available to all templates for nav menu visibility"""
    return {'is_development': os.environ.get('REPLIT_ENVIRONMENT') == 'development'}

# Global cache for plant data to avoid reloading on every request
_cached_plant_data = None

# Image file extensions for detection
IMAGE_EXTENSIONS = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp')

# HTML tags to detect formatted content
import re
HTML_TAG_PATTERN = re.compile(r'<(b|i|strong|em|p|br|ul|li|ol|a|span|div|h[1-6])[^>]*>', re.IGNORECASE)

def fix_http_url(url):
    """Convert http:// to https:// for sites that require it (like bonap.net)"""
    if isinstance(url, str) and url.startswith('http://bonap.net'):
        return url.replace('http://', 'https://', 1)
    return url

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

def is_tiered_data(data):
    """Check if data is tiered content format (tier1/tier2/tier3 with value and attribution)"""
    if not isinstance(data, dict):
        return False
    # Must have at least one tier (tier1, tier2, or tier3)
    has_tiers = any(key in data for key in ['tier1', 'tier2', 'tier3'])
    if not has_tiers:
        return False
    # Check that all present tiers have proper structure (value and attribution)
    tier_count = 0
    for tier_key in ['tier1', 'tier2', 'tier3']:
        if tier_key in data:
            tier = data[tier_key]
            if not isinstance(tier, dict):
                return False
            if 'value' not in tier or 'attribution' not in tier:
                return False
            tier_count += 1
    # Need at least one properly structured tier
    return tier_count > 0

# Custom Jinja filter to parse JSON and detect URL dictionaries
@app.template_filter('parse_json_urls')
def parse_json_urls_filter(value):
    """
    Parse a JSON string and return structured data for rendering.
    Returns a dict with 'type' and 'data' keys:
    - type: 'url_dict' for {name: url} dicts, 'url_list' for [url] lists, 
            'image' for single image URLs, 'image_dict' for {name: image_url} dicts,
            'tiered_content' for tier1/tier2/tier3 format, 'text' for plain text
    - data: the parsed data or original value
    """
    # Log at start to confirm filter is called
    val_preview = str(value)[:80] if value else 'EMPTY'
    logging.info(f"FILTER_CALLED: value starts with: {val_preview}")
    
    if not value:
        return {'type': 'text', 'data': value}
    
    # Handle case where value is already a dict (from pandas parsing)
    if isinstance(value, dict):
        # Check for tiered content format first
        if is_tiered_data(value):
            logging.info(f"TIERED_DATA: Detected tiered content format (from dict)")
            return {'type': 'tiered_content', 'data': value}
        # Check for similar_species
        if value.get('topic') == 'similar_species' and 'similar_species' in value:
            return {'type': 'similar_species', 'data': value}
        # Check if values are image URLs
        image_count = sum(1 for v in value.values() if is_image_url(v))
        url_count = sum(1 for v in value.values() if isinstance(v, str) and str(v).startswith('http'))
        if image_count > 0 and image_count >= len(value) * 0.5:
            return {'type': 'image_dict', 'data': value}
        if url_count > 0 and url_count >= len(value) * 0.5:
            return {'type': 'url_dict', 'data': value}
        if is_chart_data(value):
            return {'type': 'chart', 'data': value}
        return {'type': 'json_dict', 'data': value}
    
    if not isinstance(value, str):
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
        if isinstance(parsed, dict):
            logging.info(f"PARSE_JSON: Parsed JSON with keys: {list(parsed.keys())[:6]}")
        
        # Check if it's a dict with URL values
        if isinstance(parsed, dict):
            # Check for tiered content format (tier1/tier2/tier3 with value and attribution)
            if is_tiered_data(parsed):
                logging.info(f"TIERED_DATA: Detected tiered content format from JSON string")
                return {'type': 'tiered_content', 'data': parsed}
            
            # Log when we have tier keys but they don't pass validation
            if any(key in parsed for key in ['tier1', 'tier2', 'tier3']):
                logging.warning(f"TIERED_DATA: Found tier keys but is_tiered_data returned False. Keys: {list(parsed.keys())}")
            
            # Check for topic-based structured content (e.g., similar_species)
            topic = parsed.get('topic')
            has_similar_species_key = 'similar_species' in parsed
            if topic == 'similar_species' and has_similar_species_key:
                logging.info(f"SIMILAR_SPECIES: Detected and returning similar_species type")
                return {'type': 'similar_species', 'data': parsed}
            logging.debug(f"JSON dict parsed, topic={topic}, has_similar_species_key={has_similar_species_key}, keys={list(parsed.keys())[:5]}")
            
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

@app.template_filter('format_list_content')
def format_list_content_filter(value):
    """Template filter to format list-like content with proper line breaks.
    
    Detects bullet points (•, -, *, numbered) and converts newlines to <br> tags.
    Returns HTML-safe markup.
    """
    from markupsafe import Markup
    import re
    
    if not value or not isinstance(value, str):
        return value
    
    # Check if content looks like a list (has bullet points or numbered items)
    list_patterns = [
        r'^\s*[•\-\*]\s',  # Bullet points: •, -, *
        r'^\s*\d+[\.\)]\s',  # Numbered: 1. or 1)
    ]
    
    lines = value.split('\n')
    is_list = False
    
    for line in lines:
        for pattern in list_patterns:
            if re.match(pattern, line.strip()):
                is_list = True
                break
        if is_list:
            break
    
    if is_list or '\n' in value:
        # Escape HTML entities first, then convert newlines to <br>
        import html
        escaped = html.escape(value)
        formatted = escaped.replace('\n', '<br>')
        return Markup(formatted)
    
    return value

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

# Global cache for column attribution data (Column Sources tab)
_cached_attribution_data = None
_attribution_load_failed_no_creds = False  # Track if loading failed due to missing credentials (for retry)
ATTRIBUTION_CACHE_FILE = os.path.join(CACHE_DIR, 'attribution_data.pkl')

# Track columns removed during last cleanup (for admin notification)
_last_removed_columns = []

# Cached column usage statistics (computed during data load for fast admin page)
_cached_column_usage = None

# Column labels mapping file - stores original headers for display
COLUMN_LABELS_FILE = 'config/column_labels.json'

# In-memory cache for column labels (avoid repeated disk reads)
_cached_column_labels = None

def load_column_labels():
    """Load the column labels mapping from config file (cached in memory)"""
    global _cached_column_labels
    if _cached_column_labels is not None:
        return _cached_column_labels
    try:
        if os.path.exists(COLUMN_LABELS_FILE):
            with open(COLUMN_LABELS_FILE, 'r') as f:
                _cached_column_labels = json.load(f)
                return _cached_column_labels
    except Exception as e:
        app.logger.warning(f"Could not load column labels: {e}")
    _cached_column_labels = {}
    return _cached_column_labels

def save_column_labels(labels_mapping):
    """Save the column labels mapping to config file and update cache"""
    global _cached_column_labels
    try:
        # Ensure config directory exists
        os.makedirs(os.path.dirname(COLUMN_LABELS_FILE), exist_ok=True)
        with open(COLUMN_LABELS_FILE, 'w') as f:
            json.dump(labels_mapping, f, indent=2)
        # Update in-memory cache
        _cached_column_labels = labels_mapping
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

def compute_column_usage(df):
    """Compute column usage statistics and cache them for fast admin page access.
    
    This is called during data load so the admin page doesn't have to recalculate.
    """
    global _cached_column_usage
    
    if df is None or df.empty:
        _cached_column_usage = None
        return
    
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
    
    # Store in cache with metadata
    _cached_column_usage = {
        'data': column_usage,
        'columns_order': all_columns,
        'total_columns': len(all_columns),
        'total_species': len(df)
    }
    app.logger.info(f"Cached column usage stats for {len(all_columns)} columns")

def get_cached_column_usage():
    """Get the cached column usage data, returns None if not cached"""
    global _cached_column_usage
    return _cached_column_usage

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

def get_google_credentials(write_access=False):
    """Get Google API credentials for service account access
    
    Args:
        write_access: If True, request write permissions for Drive
    """
    if not GOOGLE_API_AVAILABLE:
        app.logger.warning("Google API libraries not available")
        return None
    
    service_account_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if not service_account_json:
        app.logger.warning("GOOGLE_SERVICE_ACCOUNT_JSON secret is not set - Google API features will not work")
        return None
    
    try:
        credentials_info = json.loads(service_account_json)
        if write_access:
            scopes = [
                'https://www.googleapis.com/auth/drive',
                'https://www.googleapis.com/auth/spreadsheets.readonly'
            ]
        else:
            scopes = [
                'https://www.googleapis.com/auth/drive.readonly',
                'https://www.googleapis.com/auth/spreadsheets.readonly'
            ]
        credentials = service_account.Credentials.from_service_account_info(
            credentials_info,
            scopes=scopes
        )
        return credentials
    except Exception as e:
        app.logger.error(f"Error getting Google credentials: {str(e)}")
        return None

def get_google_drive_service():
    """Get an authenticated Google Drive API service"""
    credentials = get_google_credentials()
    if not credentials:
        return None
    
    try:
        service = build('drive', 'v3', credentials=credentials, cache_discovery=False)
        return service
    except Exception as e:
        app.logger.error(f"Error creating Google Drive service: {str(e)}")
        return None

def get_google_drive_service_with_write():
    """Get an authenticated Google Drive API service with write permissions using Replit connector"""
    # Try Replit connector first (for uploads with proper user quota)
    try:
        access_token = get_replit_drive_access_token()
        if access_token:
            from google.oauth2.credentials import Credentials as OAuth2Credentials
            creds = OAuth2Credentials(token=access_token)
            service = build('drive', 'v3', credentials=creds, cache_discovery=False)
            app.logger.info("Using Replit Google Drive connector for write access")
            return service
    except Exception as e:
        app.logger.warning(f"Replit connector not available: {e}")
    
    # Fall back to service account (read-only typically works)
    credentials = get_google_credentials(write_access=True)
    if not credentials:
        return None
    
    try:
        service = build('drive', 'v3', credentials=credentials, cache_discovery=False)
        return service
    except Exception as e:
        app.logger.error(f"Error creating Google Drive service with write access: {str(e)}")
        return None

def get_replit_drive_access_token():
    """Get access token from Replit's Google Drive connector"""
    import requests
    
    hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
    if not hostname:
        return None
    
    # Get the appropriate token
    repl_identity = os.environ.get('REPL_IDENTITY')
    web_repl_renewal = os.environ.get('WEB_REPL_RENEWAL')
    
    if repl_identity:
        x_replit_token = f'repl {repl_identity}'
    elif web_repl_renewal:
        x_replit_token = f'depl {web_repl_renewal}'
    else:
        app.logger.warning("No Replit token found for connector authentication")
        return None
    
    try:
        response = requests.get(
            f'https://{hostname}/api/v2/connection?include_secrets=true&connector_names=google-drive',
            headers={
                'Accept': 'application/json',
                'X_REPLIT_TOKEN': x_replit_token
            },
            timeout=10
        )
        
        if response.status_code != 200:
            app.logger.warning(f"Replit connector returned status {response.status_code}")
            return None
        
        data = response.json()
        connection = data.get('items', [{}])[0] if data.get('items') else {}
        settings = connection.get('settings', {})
        
        # Try different paths for access token
        access_token = settings.get('access_token') or \
                       settings.get('oauth', {}).get('credentials', {}).get('access_token')
        
        if access_token:
            app.logger.info("Successfully retrieved Replit Drive access token")
            return access_token
        
        app.logger.warning("No access token found in Replit connector response")
        return None
        
    except Exception as e:
        app.logger.error(f"Error getting Replit Drive access token: {e}")
        return None

def find_or_create_folder(parent_folder_id, folder_name):
    """Find a subfolder by name, or create it if it doesn't exist"""
    service = get_google_drive_service_with_write()
    if not service:
        return None, "Google Drive API not configured with write access"
    
    try:
        # Search for existing folder
        query = f"'{parent_folder_id}' in parents and name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = service.files().list(
            q=query,
            fields='files(id, name)',
            pageSize=1
        ).execute()
        
        files = results.get('files', [])
        
        if files:
            # Folder exists
            folder_id = files[0]['id']
            app.logger.info(f"Found existing folder '{folder_name}' with ID: {folder_id}")
            return folder_id, None
        
        # Create the folder
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_folder_id]
        }
        
        folder = service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()
        
        folder_id = folder.get('id')
        app.logger.info(f"Created new folder '{folder_name}' with ID: {folder_id}")
        return folder_id, None
        
    except Exception as e:
        app.logger.error(f"Error finding/creating folder '{folder_name}': {str(e)}")
        return None, str(e)

def upload_json_to_drive(folder_id, filename, json_data):
    """Upload a JSON file to a Google Drive folder"""
    from io import BytesIO
    from googleapiclient.http import MediaIoBaseUpload
    
    service = get_google_drive_service_with_write()
    if not service:
        return None, "Google Drive API not configured with write access"
    
    try:
        # Prepare the JSON content
        json_content = json.dumps(json_data, indent=2)
        
        # Create in-memory buffer
        buffer = BytesIO(json_content.encode('utf-8'))
        
        # Create media upload
        media = MediaIoBaseUpload(
            buffer,
            mimetype='application/json',
            resumable=False
        )
        
        # File metadata
        file_metadata = {
            'name': filename,
            'parents': [folder_id],
            'mimeType': 'application/json'
        }
        
        # Upload the file
        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, name, webViewLink'
        ).execute()
        
        file_id = file.get('id')
        file_name = file.get('name')
        web_link = file.get('webViewLink', '')
        
        app.logger.info(f"Uploaded JSON file '{file_name}' to Drive with ID: {file_id}")
        return {
            'id': file_id,
            'name': file_name,
            'webViewLink': web_link
        }, None
        
    except Exception as e:
        app.logger.error(f"Error uploading JSON to Drive: {str(e)}")
        return None, str(e)

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

def check_for_new_supplemental_file():
    """Check if there's a new supplemental data file (PlantData Google Sheet)"""
    global _supplemental_file_id, _supplemental_file_name
    
    settings = load_app_settings()
    folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
    supplemental_prefix = settings.get('data_source', {}).get('supplemental_file_prefix', 'PlantData')
    
    if not folder_id or not supplemental_prefix:
        return False, None, None, "Supplemental data not configured"
    
    # Store the current ID before checking
    previous_file_id = _supplemental_file_id
    
    service = get_google_drive_service()
    if not service:
        return False, None, None, "Google Drive API not configured"
    
    try:
        # Search for Google Sheets in the folder that match the prefix
        query = f"'{folder_id}' in parents and name contains '{supplemental_prefix}' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        
        results = service.files().list(
            q=query,
            fields='files(id, name, modifiedTime)',
            orderBy='modifiedTime desc',
            pageSize=10
        ).execute()
        
        files = results.get('files', [])
        
        if not files:
            return False, None, None, f"No Google Sheets found with prefix '{supplemental_prefix}'"
        
        # Get the most recently modified file
        latest_file = files[0]
        file_id = latest_file['id']
        file_name = latest_file['name']
        modified_time = latest_file.get('modifiedTime', 'Unknown')
        
        # Check if this is a different file than before
        is_new_file = (previous_file_id != file_id) if previous_file_id else True
        
        if is_new_file:
            app.logger.info(f"Found new supplemental file: '{file_name}' (modified: {modified_time}), was: {_supplemental_file_name or 'None'}")
        
        return is_new_file, file_id, file_name, f"Supplemental: '{file_name}' (modified: {modified_time})"
        
    except Exception as e:
        app.logger.error(f"Error checking for new supplemental file: {str(e)}")
        return False, None, None, f"Error: {str(e)}"

def reload_supplemental_and_merge():
    """Reload just the supplemental data and re-merge with cached main data"""
    global _cached_plant_data, _cached_supplemental_data, _supplemental_file_id, _supplemental_file_name
    
    # We need the base (unmerged) main data - reload it fresh
    # Clear supplemental cache so it gets reloaded
    _cached_supplemental_data = None
    
    # Force a full reload to get fresh supplemental data merged
    _cached_plant_data = None
    _clear_disk_cache()
    
    return load_plant_data(force_reload=True)

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

def export_google_sheet_tab_as_csv(file_id, gid):
    """Export a specific tab of a Google Sheet as CSV data using the Google Sheets API.
    
    Args:
        file_id: The Google Drive file ID of the spreadsheet
        gid: The sheet tab ID (found in URL as gid=XXXXX)
    
    Returns:
        Tuple of (csv_content, error_message)
    """
    try:
        from googleapiclient.discovery import build
        
        # Get Google credentials from service account
        credentials = get_google_credentials()
        if not credentials:
            return None, "Google credentials not available"
        
        # Build Sheets API service
        sheets_service = build('sheets', 'v4', credentials=credentials, cache_discovery=False)
        
        # First, get sheet metadata to find the sheet name from gid
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        
        target_sheet_name = None
        for sheet in spreadsheet.get('sheets', []):
            if str(sheet['properties']['sheetId']) == str(gid):
                target_sheet_name = sheet['properties']['title']
                break
        
        if not target_sheet_name:
            return None, f"Sheet with gid={gid} not found in spreadsheet"
        
        app.logger.info(f"Found sheet '{target_sheet_name}' for gid={gid}")
        
        # Get all values from the sheet
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=file_id,
            range=f"'{target_sheet_name}'"
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            return None, f"Sheet '{target_sheet_name}' is empty"
        
        # Convert to CSV format
        import csv
        from io import StringIO
        
        output = StringIO()
        writer = csv.writer(output)
        for row in values:
            writer.writerow(row)
        
        csv_content = output.getvalue()
        app.logger.info(f"Exported {len(values)} rows from sheet '{target_sheet_name}'")
        return csv_content, None
        
    except Exception as e:
        app.logger.error(f"Error exporting Google Sheet tab (gid={gid}): {str(e)}")
        return None, f"Error: {str(e)}"

def export_google_sheet_tab_by_name(file_id, tab_name):
    """Export a specific tab of a Google Sheet as CSV data by tab NAME.
    
    This is more robust than using gid because tab names stay consistent
    even when new files are created (gids are unique per file).
    
    Args:
        file_id: The Google Drive file ID of the spreadsheet
        tab_name: The name of the sheet tab to export
    
    Returns:
        Tuple of (csv_content, error_message)
    """
    try:
        from googleapiclient.discovery import build
        
        # Get Google credentials from service account
        credentials = get_google_credentials()
        if not credentials:
            return None, "Google credentials not available"
        
        # Build Sheets API service
        sheets_service = build('sheets', 'v4', credentials=credentials, cache_discovery=False)
        
        # Get spreadsheet metadata to find the sheet by name
        spreadsheet = sheets_service.spreadsheets().get(spreadsheetId=file_id).execute()
        
        # List available sheet names for debugging
        available_sheets = [sheet['properties']['title'] for sheet in spreadsheet.get('sheets', [])]
        app.logger.info(f"Available sheets in spreadsheet: {available_sheets}")
        
        # Find the sheet by name (case-insensitive match)
        target_sheet_name = None
        for sheet in spreadsheet.get('sheets', []):
            sheet_title = sheet['properties']['title']
            if sheet_title.lower() == tab_name.lower():
                target_sheet_name = sheet_title  # Use actual case from sheet
                break
        
        if not target_sheet_name:
            return None, f"Sheet named '{tab_name}' not found in spreadsheet. Available sheets: {available_sheets}"
        
        app.logger.info(f"Found sheet '{target_sheet_name}' matching requested name '{tab_name}'")
        
        # Get all values from the sheet
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=file_id,
            range=f"'{target_sheet_name}'"
        ).execute()
        
        values = result.get('values', [])
        
        if not values:
            return None, f"Sheet '{target_sheet_name}' is empty"
        
        # Convert to CSV format
        import csv
        from io import StringIO
        
        output = StringIO()
        writer = csv.writer(output)
        for row in values:
            writer.writerow(row)
        
        csv_content = output.getvalue()
        app.logger.info(f"Exported {len(values)} rows from sheet '{target_sheet_name}'")
        return csv_content, None
        
    except Exception as e:
        app.logger.error(f"Error exporting Google Sheet tab by name '{tab_name}': {str(e)}")
        return None, f"Error: {str(e)}"

def load_attribution_data(force_reload=False):
    """Load column attribution data from 'Column Sources' tab of PlantData Google Sheet.
    
    The attribution tab contains metadata about each column including:
    - Column name (field)
    - Source information
    - Description
    - Algorithm version
    - Last updated
    - Any other attribution metadata
    
    Returns a dictionary keyed by normalized column field name.
    """
    global _cached_attribution_data, _supplemental_file_id, _attribution_load_failed_no_creds
    
    # Check if we should retry - if previous attempt failed due to missing credentials
    # and credentials are now available, retry loading (takes priority over disk cache)
    if _attribution_load_failed_no_creds and not force_reload:
        service_account_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        if service_account_json:
            app.logger.info("Credentials now available, retrying attribution data load...")
            force_reload = True
            _attribution_load_failed_no_creds = False
            # Clear any stale disk cache from previous failed attempts
            if os.path.exists(ATTRIBUTION_CACHE_FILE):
                try:
                    os.remove(ATTRIBUTION_CACHE_FILE)
                    app.logger.info("Removed stale attribution disk cache for retry")
                except Exception as e:
                    app.logger.warning(f"Could not remove stale attribution cache: {e}")
    
    # Return cached data if available and not forcing reload
    if not force_reload and _cached_attribution_data is not None:
        # Validate that cached data is not empty
        if isinstance(_cached_attribution_data, pd.DataFrame) and not _cached_attribution_data.empty:
            return _cached_attribution_data
        # If cached data is empty, clear it and continue to reload
        app.logger.info("Memory cached attribution data is empty, reloading...")
        _cached_attribution_data = None
    
    # Try to load from disk cache first (only if not forcing reload)
    if not force_reload and os.path.exists(ATTRIBUTION_CACHE_FILE):
        try:
            disk_cache = pd.read_pickle(ATTRIBUTION_CACHE_FILE)
            # Validate disk cache is not empty before using
            if isinstance(disk_cache, pd.DataFrame) and not disk_cache.empty:
                _cached_attribution_data = disk_cache
                app.logger.info(f"Loaded attribution data from disk cache ({len(disk_cache)} rows)")
                return _cached_attribution_data
            else:
                app.logger.info("Disk cache attribution data is empty, will reload from source")
        except Exception as e:
            app.logger.warning(f"Could not load attribution cache from disk: {e}")
    
    settings = load_app_settings()
    folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
    supplemental_prefix = settings.get('data_source', {}).get('supplemental_file_prefix', 'PlantData')
    # Tab name for attribution data - more robust than gid since it works across different files
    attribution_tab_name = settings.get('data_source', {}).get('attribution_tab_name', 'Column Sources')
    
    if not folder_id or not supplemental_prefix:
        app.logger.warning("Attribution data not configured - folder_id or supplemental_file_prefix not set in app_settings.json")
        return {}
    
    try:
        # Check if credentials are available before trying to load
        service_account_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
        if not service_account_json:
            app.logger.warning("Attribution load skipped - GOOGLE_SERVICE_ACCOUNT_JSON not available yet (will retry on next request)")
            _attribution_load_failed_no_creds = True
            return {}
        
        # Find the Google Sheet (use existing supplemental file ID if available)
        file_id = _supplemental_file_id
        if not file_id:
            file_id, file_name, message = find_google_sheet_in_folder(folder_id, supplemental_prefix)
        
        if not file_id:
            app.logger.warning(f"No supplemental data file found for attribution - check Google Drive folder access")
            # Don't set retry flag here - this is a configuration issue, not a credential timing issue
            return {}
        
        # Export the attribution tab by name (more robust than gid)
        csv_content, error = export_google_sheet_tab_by_name(file_id, attribution_tab_name)
        if error:
            app.logger.warning(f"Could not load attribution tab '{attribution_tab_name}': {error}")
            return {}
        
        # Parse CSV into DataFrame
        csv_data = StringIO(csv_content)
        df = pd.read_csv(csv_data)
        
        if df.empty:
            app.logger.info("Attribution tab is empty")
            return {}
        
        # Store the raw dataframe for the attribution page
        app.logger.info(f"Loaded {len(df)} rows of attribution data")
        app.logger.info(f"Attribution columns: {list(df.columns)}")
        
        # Cache the data and clear retry flag on success
        _cached_attribution_data = df
        _attribution_load_failed_no_creds = False  # Clear flag on successful load
        
        # Save to disk cache
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            df.to_pickle(ATTRIBUTION_CACHE_FILE)
        except Exception as e:
            app.logger.warning(f"Could not save attribution cache to disk: {e}")
        
        return df
        
    except Exception as e:
        app.logger.error(f"Error loading attribution data: {str(e)}")
        return {}

def get_attribution_for_field(field_name):
    """Get attribution info for a specific field/column.
    
    Returns a dictionary with all attribution metadata for the field,
    or None if not found.
    """
    attribution_df = load_attribution_data()
    
    if attribution_df is None or (isinstance(attribution_df, pd.DataFrame) and attribution_df.empty):
        return None
    
    if isinstance(attribution_df, dict):
        return None
    
    # Try to find matching row - look for field name in first column or a 'field' column
    df = attribution_df
    
    # Normalize the field name for matching
    field_normalized = field_name.lower().replace('_', ' ').strip()
    
    # Try matching against each column that might contain field names
    for col in df.columns:
        col_values = df[col].astype(str).str.lower().str.replace('_', ' ').str.strip()
        matches = df[col_values == field_normalized]
        if not matches.empty:
            # Return first matching row as dictionary
            row = matches.iloc[0]
            return {k: v for k, v in row.items() if pd.notna(v) and str(v).strip()}
    
    return None

def get_all_attributions():
    """Get all attribution data as a list of dictionaries for the attribution page."""
    attribution_df = load_attribution_data()
    
    if attribution_df is None or (isinstance(attribution_df, pd.DataFrame) and attribution_df.empty):
        return []
    
    if isinstance(attribution_df, dict):
        return []
    
    # Convert each row to a dictionary, filtering out NaN values
    attributions = []
    for _, row in attribution_df.iterrows():
        attr = {k: v for k, v in row.items() if pd.notna(v) and str(v).strip()}
        if attr:
            attributions.append(attr)
    
    return attributions

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
    if supplemental_df is None:
        return main_df
    
    try:
        # Handle case where supplemental data has headers but no rows
        # We still want to add those columns (as empty) so assignments are preserved
        if supplemental_df.empty:
            main_cols = set(main_df.columns)
            supp_cols = set(supplemental_df.columns)
            new_cols = supp_cols - main_cols - {'genus', 'species'}
            
            if new_cols:
                app.logger.info(f"Supplemental data has headers but no rows - adding {len(new_cols)} empty columns to preserve assignments")
                main_df = main_df.copy()
                for col in new_cols:
                    main_df[col] = ''
                # Only set has_community_data if it doesn't already exist
                if 'has_community_data' not in main_df.columns:
                    main_df['has_community_data'] = False
                # Update column labels for the new columns
                update_column_labels_from_headers(list(new_cols))
            
            return main_df
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
            # Still add the community data flag based on key matches
            supp_keys = set(supplemental_df['_merge_key'].unique())
            main_df['has_community_data'] = main_df['_merge_key'].isin(supp_keys)
            main_df = main_df.drop('_merge_key', axis=1)
            return main_df
        
        # Select only the merge key and new columns from supplemental data
        supp_subset = supplemental_df[['_merge_key'] + list(new_cols)].copy()
        
        # Remove duplicates in supplemental data (keep first occurrence)
        supp_subset = supp_subset.drop_duplicates(subset=['_merge_key'], keep='first')
        
        # Merge
        merged_df = main_df.merge(supp_subset, on='_merge_key', how='left', indicator=True)
        
        # Add has_community_data flag based on whether merge found a match
        merged_df['has_community_data'] = merged_df['_merge'].apply(lambda x: x == 'both')
        
        # Clean up merge key and indicator
        merged_df = merged_df.drop(['_merge_key', '_merge'], axis=1)
        
        # Fill NaN in new columns with empty string
        for col in new_cols:
            if col in merged_df.columns:
                merged_df[col] = merged_df[col].fillna('')
        
        community_count = merged_df['has_community_data'].sum()
        app.logger.info(f"Successfully merged {len(new_cols)} supplemental columns: {list(new_cols)}")
        app.logger.info(f"Species with community data: {community_count} of {len(merged_df)}")
        
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
                    
                    # Pre-compute column usage stats for fast admin page
                    compute_column_usage(df)
                    
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
                
                # Pre-compute column usage stats for fast admin page
                compute_column_usage(df)
                
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
            
            # Pre-compute column usage stats for fast admin page
            compute_column_usage(df)
            
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

# In-memory cache for screen configs (invalidated on save)
_cached_screen_configs = {}

def load_screen_config(screen_type, force_reload=False):
    """Load screen configuration for species detail view (cached in memory)"""
    global _cached_screen_configs
    
    # Check cache first
    if not force_reload and screen_type in _cached_screen_configs:
        return _cached_screen_configs[screen_type]
    
    try:
        with open(f'config/screen_{screen_type}.json', 'r') as f:
            config = json.load(f)
        
        # Apply original column labels from column_labels.json
        # This ensures labels match original spreadsheet headers
        labels = load_column_labels()
        for col in config.get('columns', []):
            field_name = col.get('field')
            if field_name:
                if field_name in labels:
                    col['label'] = labels[field_name]
                else:
                    col['label'] = field_name.replace('_', ' ').title()
        
        # Cache the result
        _cached_screen_configs[screen_type] = config
        return config
    except FileNotFoundError:
        app.logger.error(f"Screen configuration file for {screen_type} not found")
        return {"screen_name": screen_type.title(), "columns": []}
    except Exception as e:
        app.logger.error(f"Error loading screen configuration for {screen_type}: {str(e)}")
        return {"screen_name": screen_type.title(), "columns": []}

def invalidate_screen_config_cache(screen_type=None):
    """Invalidate the screen config cache (all or specific screen)"""
    global _cached_screen_configs
    if screen_type:
        _cached_screen_configs.pop(screen_type, None)
    else:
        _cached_screen_configs = {}

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
    
    # Community data filter - only show species with supplemental data
    if filters.get('community_data'):
        if 'has_community_data' in filtered_df.columns:
            filtered_df = filtered_df[filtered_df['has_community_data'] == True]
    
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
        community_data_raw = request.args.get('community_data', '')
        filters = {
            'name': request.args.get('name', '').strip(),
            'start_seed_watch': request.args.get('start_seed_watch', ''),
            'germination_code': request.args.get('germination_code', ''),
            'light': request.args.get('light', ''),
            'moisture': request.args.get('moisture', ''),
            'community_data': community_data_raw == 'true'  # Boolean for filter logic
        }
        # Keep string version for URL generation
        url_filters = {
            'name': filters['name'],
            'start_seed_watch': filters['start_seed_watch'],
            'germination_code': filters['germination_code'],
            'light': filters['light'],
            'moisture': filters['moisture'],
            'community_data': 'true' if filters['community_data'] else ''  # String for URLs
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
                             filter_options=filter_options, current_filters=filters,
                             url_filters=url_filters, cloudinary_available=CLOUDINARY_AVAILABLE)
        
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
        
        # Get Cloudinary images for this species and screen
        cloudinary_images = {}
        if CLOUDINARY_AVAILABLE:
            try:
                # Extract genus and species from botanical_name (e.g., "Geranium maculatum")
                botanical = species.get('botanical_name', '')
                parts = botanical.split(' ', 1)
                genus = parts[0] if len(parts) >= 1 else ''
                species_name = parts[1] if len(parts) >= 2 else ''
                if genus and species_name:
                    cloudinary_images = cloudinary_service.get_species_images(genus, species_name, screen_type)
                    app.logger.debug(f"Cloudinary images for {genus} {species_name}: {len(cloudinary_images)} groups")
            except Exception as e:
                app.logger.error(f"Error fetching Cloudinary images: {e}")
        
        # Get filter parameters to preserve state
        current_filters = {
            'name': request.args.get('name', ''),
            'start_seed_watch': request.args.get('start_seed_watch', ''),
            'germination_code': request.args.get('germination_code', ''),
            'light': request.args.get('light', ''),
            'moisture': request.args.get('moisture', '')
        }
        
        # Load attribution data for field tooltips
        attribution_data = {}
        try:
            all_attributions = get_all_attributions()
            # Build lookup by field name (first column value in each row)
            for attr in all_attributions:
                if attr:
                    # Get first non-empty value as the key (column/field name)
                    first_key = list(attr.keys())[0] if attr else None
                    if first_key:
                        field_name = str(attr.get(first_key, '')).lower().replace(' ', '_')
                        attribution_data[field_name] = attr
        except Exception as e:
            app.logger.warning(f"Could not load attribution data: {e}")
        
        return render_template('species_detail.html', 
                             species=species, 
                             screen_config=screen_config,
                             current_screen=screen_type,
                             valid_screens=valid_screens,
                             current_filters=current_filters,
                             botanical_name=unquote(botanical_name),
                             cloudinary_images=cloudinary_images,
                             attribution_data=attribution_data,
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
        
        # Get Cloudinary images for this species and screen
        cloudinary_images = {}
        if CLOUDINARY_AVAILABLE:
            try:
                # Extract genus and species from botanical_name (e.g., "Geranium maculatum")
                botanical = species.get('botanical_name', '')
                parts = botanical.split(' ', 1)
                genus = parts[0] if len(parts) >= 1 else ''
                species_name = parts[1] if len(parts) >= 2 else ''
                if genus and species_name:
                    cloudinary_images = cloudinary_service.get_species_images(genus, species_name, screen_type)
            except Exception as e:
                app.logger.error(f"Error fetching Cloudinary images: {e}")
        
        # Get attribution data
        attribution_data = {}
        try:
            all_attributions = load_attribution_data()
            for attr in all_attributions:
                if attr:
                    first_key = list(attr.keys())[0] if attr else None
                    if first_key:
                        field_name = str(attr.get(first_key, '')).lower().replace(' ', '_')
                        attribution_data[field_name] = attr
        except Exception as e:
            app.logger.warning(f"Could not load attribution data for API: {e}")
        
        # Return JSON data
        return {
            "species": species,
            "screen_config": screen_config,
            "current_screen": screen_type,
            "cloudinary_images": cloudinary_images,
            "attribution_data": attribution_data
        }
        
    except Exception as e:
        app.logger.error(f"Error in API endpoint: {str(e)}")
        return {"error": str(e)}, 500

@app.route('/api/cloudinary/test')
def api_cloudinary_test():
    """Test Cloudinary connection"""
    if not CLOUDINARY_AVAILABLE:
        return jsonify({'success': False, 'error': 'Cloudinary service not available'})
    
    result = cloudinary_service.test_cloudinary_connection()
    return jsonify(result)

@app.route('/api/cloudinary/images/<path:botanical_name>')
def api_cloudinary_images(botanical_name):
    """Get all Cloudinary images for a species"""
    if not CLOUDINARY_AVAILABLE:
        return jsonify({'error': 'Cloudinary service not available'}), 500
    
    try:
        # Parse botanical name to genus and species
        parts = unquote(botanical_name).split(' ', 1)
        genus = parts[0] if parts else ''
        species_name = parts[1] if len(parts) > 1 else ''
        
        if not genus:
            return jsonify({'error': 'Could not parse species name'}), 400
        
        # Get all images
        screen_id = request.args.get('screen')
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        
        if screen_id:
            images = cloudinary_service.get_species_images(genus, species_name, screen_id, force_refresh)
        else:
            images = cloudinary_service.get_species_images(genus, species_name, force_refresh=force_refresh)
        
        return jsonify({
            'genus': genus,
            'species': species_name,
            'images': images
        })
        
    except Exception as e:
        app.logger.error(f"Error fetching Cloudinary images: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/image-groups')
def api_image_groups():
    """Get all configured image groups for admin UI"""
    if not CLOUDINARY_AVAILABLE:
        return jsonify({'error': 'Cloudinary service not available', 'groups': []})
    
    try:
        groups = cloudinary_service.get_all_image_groups()
        return jsonify({'groups': groups})
    except Exception as e:
        app.logger.error(f"Error getting image groups: {e}")
        return jsonify({'error': str(e), 'groups': []})

@app.route('/api/refresh-images')
def refresh_images_endpoint():
    """API endpoint to refresh Cloudinary image cache"""
    if not CLOUDINARY_AVAILABLE:
        return jsonify({
            'status': 'unavailable',
            'message': 'Cloudinary service not configured',
            'refreshed': False
        })
    
    try:
        cloudinary_service.clear_image_cache()
        return jsonify({
            'status': 'refreshed',
            'message': 'Image cache cleared. Fresh images will be loaded on next species view.',
            'refreshed': True
        })
    except Exception as e:
        app.logger.error(f"Error refreshing image cache: {e}")
        return jsonify({
            'status': 'error',
            'message': f'Error clearing image cache: {str(e)}',
            'refreshed': False
        })

@app.route('/about')
def about_page():
    """About page with organization info and contact links"""
    settings = load_app_settings()
    is_development = os.environ.get('REPLIT_ENVIRONMENT') == 'development'
    return render_template('about.html', settings=settings, is_development=is_development)

@app.route('/attribution')
def attribution_page():
    """Full attribution page showing data sources for all columns"""
    try:
        attributions = get_all_attributions()
        
        # Get the column headers from attribution data for display
        headers = []
        if attributions:
            # Get keys from first attribution (all rows should have same structure)
            headers = list(attributions[0].keys()) if attributions else []
        
        settings = load_app_settings()
        is_development = os.environ.get('REPLIT_ENVIRONMENT') == 'development'
        
        return render_template('attribution.html',
                             attributions=attributions,
                             headers=headers,
                             settings=settings,
                             is_development=is_development)
    except Exception as e:
        app.logger.error(f"Error loading attribution page: {str(e)}")
        flash(f'Error loading attribution data: {str(e)}', 'error')
        return render_template('attribution.html', attributions=[], headers=[])

@app.route('/admin')
def admin_dashboard():
    """Admin dashboard listing all admin pages"""
    admin_pages = [
        {
            'title': 'Review Contributions',
            'url': '/admin/review',
            'description': 'Review and approve community-submitted photos and knowledge',
            'icon': 'check-circle'
        },
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
            'title': 'Column Usage Grid',
            'url': '/admin/column-usage',
            'description': 'Grid view showing which columns are used on which pages',
            'icon': 'grid'
        },
        {
            'title': 'Refresh Data',
            'url': '/admin/refresh',
            'description': 'Manually refresh cached plant data from Google Drive',
            'icon': 'refresh-cw'
        },
        {
            'title': 'Column Reorder',
            'url': '/admin/column-reorder',
            'description': 'Reorder columns on each screen configuration',
            'icon': 'list'
        }
    ]
    
    return render_template('admin_dashboard.html', admin_pages=admin_pages, cloudinary_available=CLOUDINARY_AVAILABLE)

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
    """API endpoint to check if new data files exist in Google Drive folder and reload if needed.
    
    Checks BOTH main data file AND supplemental PlantData file independently.
    If either has changed, the appropriate data is reloaded.
    """
    global _cached_plant_data, _cached_supplemental_data, _current_file_id, _current_file_name
    
    settings = load_app_settings()
    folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
    file_prefix = settings.get('data_source', {}).get('file_prefix', '')
    
    # Try folder-based approach first
    if folder_id and file_prefix:
        # Store the current file ID before checking
        previous_file_id = _current_file_id
        
        # Check MAIN file
        file_id, file_name, main_message = find_latest_file_in_folder(folder_id, file_prefix)
        
        if file_id is None:
            return jsonify({
                'status': 'error',
                'message': f'Could not find main files: {main_message}',
                'reloaded': False,
                'supplemental': _get_supplemental_file_info()
            })
        
        # Check if main file is different
        is_new_main_file = (previous_file_id != file_id)
        
        # Check SUPPLEMENTAL file independently
        is_new_supp_file, supp_file_id, supp_file_name, supp_message = check_for_new_supplemental_file()
        
        # Determine what needs reloading
        if is_new_main_file:
            # New main file - reload everything
            _cached_plant_data = None
            _cached_supplemental_data = None
            df = load_plant_data(force_reload=True, file_id_override=file_id)
            supp_info = _get_supplemental_file_info()
            
            msg_parts = [f"New main file loaded: '{file_name}'."]
            if is_new_supp_file and supp_file_name:
                msg_parts.append(f"New supplemental file: '{supp_file_name}'.")
            msg_parts.append(f"Loaded {len(df)} species.")
            
            return jsonify({
                'status': 'updated',
                'message': ' '.join(msg_parts),
                'reloaded': True,
                'main_updated': True,
                'supplemental_updated': is_new_supp_file,
                'species_count': len(df),
                'file_name': file_name,
                'supplemental': supp_info
            })
        elif is_new_supp_file:
            # Only supplemental file changed - reload supplemental and re-merge
            app.logger.info(f"New supplemental file detected: '{supp_file_name}' - reloading")
            df = reload_supplemental_and_merge()
            supp_info = _get_supplemental_file_info()
            
            return jsonify({
                'status': 'updated',
                'message': f"New supplemental data loaded: '{supp_file_name}'. Main file unchanged: '{file_name}'. {len(df)} species loaded.",
                'reloaded': True,
                'main_updated': False,
                'supplemental_updated': True,
                'species_count': len(df),
                'file_name': file_name,
                'supplemental': supp_info
            })
        else:
            # Neither file changed
            df = _cached_plant_data if _cached_plant_data is not None else load_plant_data()
            supp_info = _get_supplemental_file_info()
            return jsonify({
                'status': 'unchanged',
                'message': f"Already using latest files. Main: '{file_name}'. {len(df) if df is not None else 0} species loaded.",
                'reloaded': False,
                'main_updated': False,
                'supplemental_updated': False,
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
    
    # Also check supplemental in legacy mode
    is_new_supp_file, supp_file_id, supp_file_name, supp_message = check_for_new_supplemental_file()
    
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
    
    if was_modified or is_new_supp_file:
        _cached_plant_data = None
        _cached_supplemental_data = None
        df = load_plant_data(force_reload=True)
        supp_info = _get_supplemental_file_info()
        
        msg_parts = []
        if was_modified:
            msg_parts.append(f'Main data updated! {message}')
        if is_new_supp_file and supp_file_name:
            msg_parts.append(f"Supplemental updated: '{supp_file_name}'")
        msg_parts.append(f'Loaded {len(df)} species.')
        
        return jsonify({
            'status': 'updated',
            'message': ' '.join(msg_parts),
            'reloaded': True,
            'main_updated': was_modified,
            'supplemental_updated': is_new_supp_file,
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
            'main_updated': False,
            'supplemental_updated': False,
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
    is_development = os.environ.get('REPLIT_ENVIRONMENT') == 'development'
    return render_template('explain_wetland.html', is_development=is_development)

@app.route('/explain/coefficient-of-conservatism')
def explain_conservatism():
    """Explanation page for Coefficient of Conservatism values"""
    is_development = os.environ.get('REPLIT_ENVIRONMENT') == 'development'
    return render_template('explain_conservatism.html', is_development=is_development)

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
    global _cached_plant_data, _cached_column_usage
    try:
        # Load image groups for display
        image_groups = []
        if CLOUDINARY_AVAILABLE:
            try:
                all_groups = cloudinary_service.get_all_image_groups()
                image_groups = all_groups
            except Exception as e:
                app.logger.error(f"Error loading image groups: {e}")
        
        # Use cached column usage data for fast page load
        cached = get_cached_column_usage()
        app.logger.debug(f"Column usage cache status: {cached is not None}, plant data cache: {_cached_plant_data is not None}")
        
        if cached is not None:
            app.logger.debug(f"Using cached column usage data with {len(cached['data'])} columns")
            return render_template('admin_column_usage.html', 
                                 column_usage=cached['data'],
                                 total_columns=cached['total_columns'],
                                 total_species=cached['total_species'],
                                 is_development=True,
                                 image_groups=image_groups,
                                 cloudinary_available=CLOUDINARY_AVAILABLE)
        
        # If column usage cache is empty but plant data is cached, rebuild quickly
        if _cached_plant_data is not None and not _cached_plant_data.empty:
            app.logger.debug("Rebuilding column usage from cached plant data")
            compute_column_usage(_cached_plant_data)
            cached = get_cached_column_usage()
            if cached:
                return render_template('admin_column_usage.html', 
                                     column_usage=cached['data'],
                                     total_columns=cached['total_columns'],
                                     total_species=cached['total_species'],
                                     is_development=True,
                                     image_groups=image_groups,
                                     cloudinary_available=CLOUDINARY_AVAILABLE)
        else:
            return render_template('admin_column_usage.html', 
                                     column_usage={},
                                     is_development=True,
                                     image_groups=image_groups,
                                     cloudinary_available=CLOUDINARY_AVAILABLE)
        
        # Ultimate fallback - load data fresh (slow, but shouldn't happen often)
        app.logger.debug("Loading plant data fresh for column usage")
        df = load_plant_data()
        if df.empty:
            flash('No plant data available to analyze columns', 'error')
            return render_template('admin_column_usage.html', column_usage={}, 
                                 image_groups=image_groups, cloudinary_available=CLOUDINARY_AVAILABLE)
        
        # Ensure column usage is computed
        compute_column_usage(df)
        cached = get_cached_column_usage()
        if cached:
            app.logger.debug(f"Fresh load successful with {len(cached['data'])} columns")
            return render_template('admin_column_usage.html', 
                                 column_usage=cached['data'],
                                 total_columns=cached['total_columns'],
                                 total_species=cached['total_species'],
                                 is_development=True,
                                 image_groups=image_groups,
                                 cloudinary_available=CLOUDINARY_AVAILABLE)
        
        flash('Column usage data not available', 'error')
        return render_template('admin_column_usage.html', column_usage={}, is_development=True,
                             image_groups=image_groups, cloudinary_available=CLOUDINARY_AVAILABLE)
        
    except Exception as e:
        app.logger.error(f"Error analyzing column usage: {str(e)}")
        flash(f'Error analyzing column usage: {str(e)}', 'error')
        return render_template('admin_column_usage.html', 
                             column_usage={}, 
                             is_development=True,
                             image_groups=[],
                             cloudinary_available=CLOUDINARY_AVAILABLE)

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
            
            # Invalidate screen config cache for this screen
            invalidate_screen_config_cache(screen_name)
        
        # Invalidate column usage cache so changes appear immediately
        global _cached_column_usage, _cached_plant_data
        _cached_column_usage = None
        
        # Rebuild cache using already-cached plant data (fast)
        if _cached_plant_data is not None and not _cached_plant_data.empty:
            compute_column_usage(_cached_plant_data)
        
        return {"success": True, "message": "Column configuration updated"}
        
    except Exception as e:
        app.logger.error(f"Error toggling column: {str(e)}")
        return {"error": str(e)}, 500

@app.route('/admin/column-reorder')
def admin_column_reorder():
    """Admin page for reordering columns on species screens"""
    # Initialize defaults for image group display
    screen_image_groups = []
    
    try:
        screen = request.args.get('screen', 'identification')
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification', 'planting']
        
        if screen not in valid_screens:
            screen = 'identification'
        
        screens = [
            {'id': 'identification', 'name': 'Identification', 'icon': 'search'},
            {'id': 'collection', 'name': 'Collection', 'icon': 'package'},
            {'id': 'processing', 'name': 'Processing', 'icon': 'settings'},
            {'id': 'storage', 'name': 'Storage', 'icon': 'archive'},
            {'id': 'stratification', 'name': 'Stratification', 'icon': 'thermometer'},
            {'id': 'planting', 'name': 'Planting', 'icon': 'sun'}
        ]
        
        screen_config = load_screen_config(screen)
        columns = screen_config.get('columns', [])
        active_screen_name = screen_config.get('screen_name', screen.title())
        
        # Get image groups that will appear on this screen
        if CLOUDINARY_AVAILABLE:
            try:
                all_groups = cloudinary_service.get_all_image_groups()
                for group_id, group_data in all_groups.items():
                    if screen in group_data.get('default_screens', []):
                        screen_image_groups.append({
                            'id': group_id,
                            'label': group_data.get('label', group_id),
                            'icon': group_data.get('icon', 'image'),
                            'description': group_data.get('description', '')
                        })
            except Exception as e:
                app.logger.error(f"Error loading image groups for reorder page: {e}")
        
        return render_template('admin_column_reorder.html',
                             screens=screens,
                             active_screen=screen,
                             active_screen_name=active_screen_name,
                             columns=columns,
                             screen_image_groups=screen_image_groups,
                             cloudinary_available=CLOUDINARY_AVAILABLE)
    except Exception as e:
        app.logger.error(f"Error loading column reorder page: {e}")
        flash(f'Error loading reorder page: {str(e)}', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/save-column-order', methods=['POST'])
def save_column_order():
    """Save the new column order for a screen"""
    try:
        data = request.get_json()
        screen = data.get('screen')
        order = data.get('order', [])
        
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification', 'planting']
        if screen not in valid_screens:
            return {"error": f"Invalid screen: {screen}"}, 400
        
        if not order:
            return {"error": "No column order provided"}, 400
        
        screen_config = load_screen_config(screen)
        current_columns = screen_config.get('columns', [])
        
        columns_by_field = {c.get('field'): c for c in current_columns}
        
        new_columns = []
        for field in order:
            if field in columns_by_field:
                new_columns.append(columns_by_field[field])
        
        screen_config['columns'] = new_columns
        
        with open(f'config/screen_{screen}.json', 'w') as f:
            json.dump(screen_config, f, indent=2)
        
        # Invalidate caches
        invalidate_screen_config_cache(screen)
        global _cached_column_usage, _cached_plant_data
        _cached_column_usage = None
        
        # Rebuild cache using already-cached plant data (fast)
        if _cached_plant_data is not None and not _cached_plant_data.empty:
            compute_column_usage(_cached_plant_data)
        
        app.logger.info(f"Column order saved for screen: {screen}")
        return {"success": True}
        
    except Exception as e:
        app.logger.error(f"Error saving column order: {str(e)}")
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

# Rate limiting for image submissions (in-memory, resets on restart)
_upload_rate_limit = {}  # IP -> {'minute': (timestamp, count), 'hour': (timestamp, count)}

def check_upload_rate_limit(ip_address):
    """Check if IP is within rate limits (1/min, 10/hour)"""
    global _upload_rate_limit
    now = datetime.now()
    
    if ip_address not in _upload_rate_limit:
        _upload_rate_limit[ip_address] = {'minute': (now, 0), 'hour': (now, 0)}
    
    limits = _upload_rate_limit[ip_address]
    
    # Check minute limit
    minute_time, minute_count = limits['minute']
    if (now - minute_time).total_seconds() < 60:
        if minute_count >= 1:
            return False, "Please wait at least 1 minute between uploads"
    else:
        limits['minute'] = (now, 0)
    
    # Check hour limit
    hour_time, hour_count = limits['hour']
    if (now - hour_time).total_seconds() < 3600:
        if hour_count >= 10:
            return False, "Upload limit reached (10 per hour). Please try again later."
    else:
        limits['hour'] = (now, 0)
    
    return True, None

def increment_upload_count(ip_address):
    """Increment upload count for IP after successful upload"""
    global _upload_rate_limit
    now = datetime.now()
    
    if ip_address not in _upload_rate_limit:
        _upload_rate_limit[ip_address] = {'minute': (now, 1), 'hour': (now, 1)}
        return
    
    limits = _upload_rate_limit[ip_address]
    
    # Increment minute count
    minute_time, minute_count = limits['minute']
    if (now - minute_time).total_seconds() < 60:
        limits['minute'] = (minute_time, minute_count + 1)
    else:
        limits['minute'] = (now, 1)
    
    # Increment hour count
    hour_time, hour_count = limits['hour']
    if (now - hour_time).total_seconds() < 3600:
        limits['hour'] = (hour_time, hour_count + 1)
    else:
        limits['hour'] = (now, 1)

# Allowed image extensions and max size
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/submit-image/<path:botanical_name>', methods=['POST'])
def submit_image(botanical_name):
    """Handle user image submission (supports multiple files)"""
    try:
        # Rate limiting disabled for now
        # ip_address = request.remote_addr
        # allowed, error_msg = check_upload_rate_limit(ip_address)
        # if not allowed:
        #     flash(error_msg, 'error')
        #     return redirect(url_for('species_detail', botanical_name=botanical_name))
        
        # Get all uploaded files
        files = request.files.getlist('images')
        if not files or all(f.filename == '' for f in files):
            flash('No images selected', 'error')
            return redirect(url_for('species_detail', botanical_name=botanical_name))
        
        # Validate required fields first
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        consent = request.form.get('consent')
        
        if not first_name or not last_name:
            flash('Please provide your first and last name', 'error')
            return redirect(url_for('species_detail', botanical_name=botanical_name))
        
        if not consent:
            flash('You must agree to share your image freely', 'error')
            return redirect(url_for('species_detail', botanical_name=botanical_name))
        
        # Build tags from checkboxes (same for all images)
        tags = []
        if request.form.get('tag_seeds'):
            tags.append('seeds')
        if request.form.get('tag_seedling'):
            tags.append('seedling')
        
        # Parse genus/species from botanical name
        parts = botanical_name.split()
        genus = parts[0] if len(parts) > 0 else 'unknown'
        species = parts[1] if len(parts) > 1 else 'unknown'
        
        # Build username for filename
        username = f"{first_name}_{last_name}"
        
        # Upload to Cloudinary
        if not CLOUDINARY_AVAILABLE:
            flash('Image upload service not available', 'error')
            return redirect(url_for('species_detail', botanical_name=botanical_name))
        
        success_count = 0
        error_count = 0
        
        for file in files:
            if file.filename == '':
                continue
                
            if not allowed_file(file.filename):
                error_count += 1
                app.logger.warning(f"Skipped invalid file type: {file.filename}")
                continue
            
            # Check file size
            file.seek(0, 2)
            size = file.tell()
            file.seek(0)
            if size > MAX_UPLOAD_SIZE:
                error_count += 1
                app.logger.warning(f"Skipped oversized file: {file.filename}")
                continue
            
            result = cloudinary_service.upload_user_image(
                file_data=file,
                genus=genus,
                species=species,
                username=username,
                tags=tags
            )
            
            if result.get('success'):
                success_count += 1
                app.logger.info(f"User image uploaded: {result.get('public_id')} by {username}")
            else:
                error_count += 1
                app.logger.error(f"User image upload failed: {result.get('error')}")
        
        # Flash appropriate message
        if success_count > 0 and error_count == 0:
            flash(f'Thank you! {success_count} image(s) submitted for review.', 'success')
        elif success_count > 0 and error_count > 0:
            flash(f'{success_count} image(s) submitted, {error_count} failed (invalid type or too large).', 'warning')
        else:
            flash('All uploads failed. Please check file types and sizes.', 'error')
        
        return redirect(url_for('species_detail', botanical_name=botanical_name))
        
    except Exception as e:
        app.logger.error(f"Error in image submission: {str(e)}")
        flash('Error uploading image. Please try again.', 'error')
        return redirect(url_for('species_detail', botanical_name=botanical_name))

# Directory for pending metadata submissions
PENDING_METADATA_DIR = 'pending_metadata'

def save_pending_metadata(botanical_name, text, categories, submitter_name):
    """Save a metadata submission as JSON file pending review"""
    import json
    import uuid
    import re
    
    # Ensure directory exists
    os.makedirs(PENDING_METADATA_DIR, exist_ok=True)
    
    # Parse genus/species
    parts = botanical_name.split()
    genus = parts[0].lower() if len(parts) > 0 else 'unknown'
    species_name = parts[1].lower() if len(parts) > 1 else 'unknown'
    
    # Generate unique ID
    unique_id = str(uuid.uuid4())[:8]
    
    # Sanitize submitter name for filename (remove non-alphanumeric except underscores)
    safe_name = re.sub(r'[^a-zA-Z0-9_]', '', submitter_name.replace(' ', '_').lower())
    if not safe_name:
        safe_name = 'anonymous'
    
    # Create filename with UUID to prevent collisions
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"{genus}_{species_name}_{safe_name}_{timestamp}_{unique_id}.json"
    filepath = os.path.join(PENDING_METADATA_DIR, filename)
    
    # Build data structure
    data = {
        'id': unique_id,
        'botanical_name': botanical_name,
        'genus': genus,
        'species': species_name,
        'text': text,
        'categories': categories,
        'submitter_name': submitter_name,
        'submitted_at': datetime.now().isoformat(),
        'status': 'pending'
    }
    
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)
    
    return filepath

def get_pending_metadata():
    """Get all pending metadata submissions"""
    import json
    
    submissions = []
    if not os.path.exists(PENDING_METADATA_DIR):
        return submissions
    
    for filename in os.listdir(PENDING_METADATA_DIR):
        if filename.endswith('.json'):
            filepath = os.path.join(PENDING_METADATA_DIR, filename)
            try:
                with open(filepath, 'r') as f:
                    data = json.load(f)
                    data['filename'] = filename
                    submissions.append(data)
            except Exception as e:
                app.logger.error(f"Error reading metadata file {filename}: {e}")
    
    # Sort by submission time, newest first
    submissions.sort(key=lambda x: x.get('submitted_at', ''), reverse=True)
    return submissions

@app.route('/submit-contribution/<path:botanical_name>', methods=['POST'])
def submit_contribution(botanical_name):
    """Handle combined image and metadata submission"""
    try:
        # Validate required fields first
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        consent = request.form.get('consent')
        
        if not first_name or not last_name:
            flash('Please provide your first and last name', 'error')
            return redirect(url_for('species_detail', botanical_name=botanical_name))
        
        if not consent:
            flash('You must agree to share your contribution freely', 'error')
            return redirect(url_for('species_detail', botanical_name=botanical_name))
        
        submitter_name = f"{first_name} {last_name}"
        username = f"{first_name}_{last_name}"
        
        # Parse genus/species
        parts = botanical_name.split()
        genus = parts[0] if len(parts) > 0 else 'unknown'
        species = parts[1] if len(parts) > 1 else 'unknown'
        
        # Track what was submitted
        image_count = 0
        image_errors = 0
        metadata_saved = False
        
        # Handle image uploads
        files = request.files.getlist('images')
        has_images = files and any(f.filename != '' for f in files)
        
        if has_images and CLOUDINARY_AVAILABLE:
            # Build image tags
            image_tags = []
            if request.form.get('tag_seeds'):
                image_tags.append('seeds')
            if request.form.get('tag_seedling'):
                image_tags.append('seedling')
            
            for file in files:
                if file.filename == '':
                    continue
                
                if not allowed_file(file.filename):
                    image_errors += 1
                    continue
                
                file.seek(0, 2)
                size = file.tell()
                file.seek(0)
                if size > MAX_UPLOAD_SIZE:
                    image_errors += 1
                    continue
                
                result = cloudinary_service.upload_user_image(
                    file_data=file,
                    genus=genus,
                    species=species,
                    username=username,
                    tags=image_tags
                )
                
                if result.get('success'):
                    image_count += 1
                    app.logger.info(f"User image uploaded: {result.get('public_id')} by {username}")
                else:
                    image_errors += 1
        
        # Handle metadata/text submission
        metadata_text = request.form.get('metadata_text', '').strip()
        metadata_error = False
        if metadata_text:
            # Build categories list
            categories = []
            if request.form.get('cat_identification'):
                categories.append('identification')
            if request.form.get('cat_collection'):
                categories.append('collection')
            if request.form.get('cat_storage'):
                categories.append('storage')
            if request.form.get('cat_processing'):
                categories.append('processing')
            if request.form.get('cat_stratification'):
                categories.append('stratification')
            
            try:
                save_pending_metadata(botanical_name, metadata_text, categories, submitter_name)
                metadata_saved = True
                app.logger.info(f"Metadata submitted for {botanical_name} by {submitter_name}")
            except Exception as e:
                metadata_error = True
                app.logger.error(f"Error saving metadata: {e}")
        
        # Build response message
        messages = []
        if image_count > 0:
            messages.append(f"{image_count} photo(s)")
        if metadata_saved:
            messages.append("your knowledge contribution")
        
        if messages:
            flash(f"Thank you! Submitted {' and '.join(messages)} for review.", 'success')
        
        # Report any errors
        if image_errors > 0 and image_count == 0:
            flash('Image uploads failed. Please check file types and sizes.', 'error')
        elif image_errors > 0:
            flash(f'{image_errors} image(s) failed to upload.', 'warning')
        
        if metadata_error:
            flash('Failed to save your knowledge submission. Please try again.', 'error')
        
        if not messages and not image_errors and not metadata_error:
            flash('No content was submitted.', 'warning')
        
        return redirect(url_for('species_detail', botanical_name=botanical_name))
        
    except Exception as e:
        app.logger.error(f"Error in contribution submission: {str(e)}")
        flash('Error submitting contribution. Please try again.', 'error')
        return redirect(url_for('species_detail', botanical_name=botanical_name))

def check_admin_auth():
    """Check if request has valid admin credentials.
    
    Returns False if credentials are not configured or don't match.
    This fails closed - if env vars are not set, no access is granted.
    """
    auth = request.authorization
    if not auth:
        return False
    
    admin_user = os.environ.get('ADMIN_USERNAME')
    admin_pass = os.environ.get('ADMIN_PASSWORD')
    
    # Fail closed: if credentials not configured, deny access
    if not admin_user or not admin_pass:
        app.logger.warning("Admin credentials not configured - access denied")
        return False
    
    return auth.username == admin_user and auth.password == admin_pass

def require_admin_auth(f):
    """Decorator to require admin authentication"""
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not check_admin_auth():
            return ('Unauthorized', 401, {'WWW-Authenticate': 'Basic realm="Admin Access"'})
        return f(*args, **kwargs)
    return decorated

@app.route('/admin/review')
@require_admin_auth
def admin_review():
    """Admin page for reviewing pending image and metadata submissions"""
    try:
        pending_image = None
        approved_images = []
        pending_image_count = 0
        
        if CLOUDINARY_AVAILABLE:
            # Get list of recently processed images to exclude (session-based)
            processed_ids = session.get('processed_image_ids', [])
            
            # Get pending images
            pending_images = cloudinary_service.get_pending_images()
            
            # Filter out recently processed images
            pending_images = [img for img in pending_images if img['public_id'] not in processed_ids]
            pending_image_count = len(pending_images)
            
            if pending_images:
                # Show the first pending image (excluding skipped ones)
                skip_index = session.get('skip_index', 0)
                if skip_index >= len(pending_images):
                    skip_index = 0
                    session['skip_index'] = 0
                
                pending_image = pending_images[skip_index]
                
                # Get approved images for the same species
                if pending_image.get('genus') and pending_image.get('species'):
                    approved_images = cloudinary_service.get_approved_images_for_species(
                        pending_image['genus'],
                        pending_image['species']
                    )
            else:
                # Clear the processed list when queue is empty
                session.pop('processed_image_ids', None)
        
        # Get pending metadata submissions
        pending_metadata = get_pending_metadata()
        pending_metadata_count = len(pending_metadata)
        
        # Apply skip index for metadata
        current_metadata = None
        if pending_metadata:
            metadata_skip_index = session.get('metadata_skip_index', 0)
            if metadata_skip_index >= len(pending_metadata):
                metadata_skip_index = 0
                session['metadata_skip_index'] = 0
            current_metadata = pending_metadata[metadata_skip_index]
        
        return render_template('admin_review.html',
                             pending_image=pending_image,
                             approved_images=approved_images,
                             pending_image_count=pending_image_count,
                             pending_metadata=current_metadata,
                             pending_metadata_count=pending_metadata_count)
        
    except Exception as e:
        app.logger.error(f"Error loading admin review: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
        return render_template('admin_review.html', pending_image=None, approved_images=[])

@app.route('/admin/review/approve', methods=['POST'])
@require_admin_auth
def admin_approve_image():
    """Approve a pending image (remove Pending tag)"""
    try:
        public_id = request.form.get('public_id')
        if not public_id:
            flash('No image specified', 'error')
            return redirect(url_for('admin_review'))
        
        result = cloudinary_service.approve_image(public_id)
        if result.get('success'):
            # Add to processed list so it doesn't show again while index updates
            processed_ids = session.get('processed_image_ids', [])
            if public_id not in processed_ids:
                processed_ids.append(public_id)
                session['processed_image_ids'] = processed_ids
            # Reset skip index since we removed an image
            session['skip_index'] = 0
            flash('Image approved successfully', 'success')
        else:
            flash(f"Error approving image: {result.get('error')}", 'error')
        
        return redirect(url_for('admin_review'))
        
    except Exception as e:
        app.logger.error(f"Error approving image: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('admin_review'))

@app.route('/admin/review/delete', methods=['POST'])
@require_admin_auth
def admin_delete_image():
    """Delete a pending image from Cloudinary"""
    try:
        public_id = request.form.get('public_id')
        if not public_id:
            flash('No image specified', 'error')
            return redirect(url_for('admin_review'))
        
        result = cloudinary_service.delete_image(public_id)
        if result.get('success'):
            # Add to processed list so it doesn't show again while index updates
            processed_ids = session.get('processed_image_ids', [])
            if public_id not in processed_ids:
                processed_ids.append(public_id)
                session['processed_image_ids'] = processed_ids
            # Reset skip index since we removed an image
            session['skip_index'] = 0
            flash('Image deleted', 'success')
        else:
            flash(f"Error deleting image: {result.get('error')}", 'error')
        
        return redirect(url_for('admin_review'))
        
    except Exception as e:
        app.logger.error(f"Error deleting image: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('admin_review'))

@app.route('/admin/review/skip', methods=['POST'])
@require_admin_auth
def admin_skip_image():
    """Skip to the next pending image"""
    # Increment skip index to show the next image in the queue
    skip_index = session.get('skip_index', 0)
    session['skip_index'] = skip_index + 1
    flash('Skipped to next image', 'info')
    return redirect(url_for('admin_review'))

# Directory for approved metadata
APPROVED_METADATA_DIR = 'approved_metadata'

@app.route('/admin/review/approve-metadata', methods=['POST'])
@require_admin_auth
def admin_approve_metadata():
    """Approve a pending metadata submission (upload to Google Drive 'Tier 1 Sources' folder)"""
    import json
    
    try:
        filename = request.form.get('filename')
        if not filename:
            flash('No submission specified', 'error')
            return redirect(url_for('admin_review'))
        
        source_path = os.path.join(PENDING_METADATA_DIR, filename)
        if not os.path.exists(source_path):
            flash('Submission not found', 'error')
            return redirect(url_for('admin_review'))
        
        # Read and update the status
        with open(source_path, 'r') as f:
            data = json.load(f)
        
        data['status'] = 'approved'
        data['approved_at'] = datetime.now().isoformat()
        
        # Get the parent folder ID from settings
        settings = load_app_settings()
        parent_folder_id = settings.get('data_source', {}).get('google_drive_folder_id', '')
        
        if not parent_folder_id:
            flash('Google Drive folder not configured in settings', 'error')
            return redirect(url_for('admin_review'))
        
        # Find or create "Tier 1 Sources" folder
        tier1_folder_id, error = find_or_create_folder(parent_folder_id, 'Tier 1 Sources')
        if not tier1_folder_id:
            flash(f'Could not access Tier 1 Sources folder: {error}', 'error')
            return redirect(url_for('admin_review'))
        
        # Upload JSON to Google Drive
        result, error = upload_json_to_drive(tier1_folder_id, filename, data)
        if not result:
            flash(f'Failed to upload to Google Drive: {error}', 'error')
            return redirect(url_for('admin_review'))
        
        # Also keep a local backup in approved_metadata/
        os.makedirs(APPROVED_METADATA_DIR, exist_ok=True)
        dest_path = os.path.join(APPROVED_METADATA_DIR, filename)
        with open(dest_path, 'w') as f:
            json.dump(data, f, indent=2)
        
        # Remove from pending after successful upload
        os.remove(source_path)
        
        # Reset skip index since we removed an item
        session['metadata_skip_index'] = 0
        
        flash(f'Knowledge submission approved and uploaded to Google Drive', 'success')
        app.logger.info(f"Metadata approved and uploaded: {filename} -> Drive ID: {result.get('id')}")
        
        return redirect(url_for('admin_review'))
        
    except Exception as e:
        app.logger.error(f"Error approving metadata: {str(e)}")
        flash(f'Error: {str(e)}', 'error')
        return redirect(url_for('admin_review'))

@app.route('/admin/review/delete-metadata', methods=['POST'])
@require_admin_auth
def admin_delete_metadata():
    """Delete a pending metadata submission"""
    try:
        filename = request.form.get('filename')
        if not filename:
            flash('No submission specified', 'error')
            return redirect(url_for('admin_review'))
        
        filepath = os.path.join(PENDING_METADATA_DIR, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
            # Reset skip index since we removed an item
            session['metadata_skip_index'] = 0
            flash('Submission deleted', 'success')
            app.logger.info(f"Metadata deleted: {filename}")
        else:
            flash('Submission not found', 'error')
        
        return redirect(url_for('admin_review'))
        
    except Exception as e:
        app.logger.error(f"Error deleting metadata: {str(e)}")
        flash(f'Error: {str(e)}', 'error')

@app.route('/admin/review/skip-metadata', methods=['POST'])
@require_admin_auth
def admin_skip_metadata():
    """Skip to the next pending metadata submission"""
    skip_index = session.get('metadata_skip_index', 0)
    session['metadata_skip_index'] = skip_index + 1
    flash('Skipped to next submission', 'info')
    return redirect(url_for('admin_review'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

import os
import pandas as pd
import logging
import json
import requests
from io import StringIO
from flask import Flask, render_template, flash, request, url_for, abort
from urllib.parse import quote, unquote

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

# Global cache for plant data to avoid reloading on every request
_cached_plant_data = None
_data_cache_timestamp = None

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
            filtered_df['botanical_name'].str.lower().str.contains(name_query, na=False) |
            filtered_df['common_name'].str.lower().str.contains(name_query, na=False)
        )
        filtered_df = filtered_df[mask]
    
    # Start collecting month filter
    if filters.get('start_seed_watch'):
        month_filter = filters['start_seed_watch']
        mask = filtered_df['start_seed_watch'].str.contains(month_filter, na=False)
        filtered_df = filtered_df[mask]
    
    # Germination code filter
    if filters.get('germination_code'):
        germ_filter = filters['germination_code']
        mask = filtered_df['germination_code'].str.contains(germ_filter, na=False)
        filtered_df = filtered_df[mask]
    
    # Light filter
    if filters.get('light'):
        light_filter = filters['light']
        mask = filtered_df['light'].str.contains(light_filter, na=False)
        filtered_df = filtered_df[mask]
    
    # Moisture filter
    if filters.get('moisture'):
        moisture_filter = filters['moisture']
        mask = filtered_df['moisture'].str.contains(moisture_filter, na=False)
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
        
        # Get unique values for filter dropdowns
        filter_options = {
            'start_seed_watch': get_unique_values(df, 'start_seed_watch'),
            'germination_code': get_unique_values(df, 'germination_code'),
            'light': get_unique_values(df, 'light'),
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
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification']
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
                             botanical_name=unquote(botanical_name))
        
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
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification']
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

@app.route('/admin/refresh-data')
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

@app.route('/admin/data-columns')
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
        valid_screens = ['identification', 'collection', 'processing', 'storage', 'stratification']
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

@app.route('/api/data-columns')
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

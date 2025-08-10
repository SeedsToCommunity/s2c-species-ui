import os
import pandas as pd
import logging
import json
from flask import Flask, render_template, flash, request, url_for, abort
from urllib.parse import quote, unquote

# Configure logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Create the Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key")

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

def load_plant_data():
    """Load and process plant data from the tab-separated file"""
    try:
        # Read the tab-separated file
        df = pd.read_csv('origdata.tabsv', sep='\t')
        
        # Clean up column names - replace spaces with underscores and make lowercase
        df.columns = df.columns.str.replace(' ', '_').str.lower()
        
        # Clean up the data - fill NaN values with empty strings
        df = df.fillna('')
        
        # Save as CSV for future reference
        df.to_csv('plants.csv', index=False)
        
        return df
        
    except FileNotFoundError:
        app.logger.error("origdata.tabsv file not found, trying plants.csv")
        # Fallback to existing CSV if tab file doesn't exist
        try:
            df = pd.read_csv('plants.csv')
            df = df.fillna('')
            return df
        except FileNotFoundError:
            app.logger.error("No data files found")
            return pd.DataFrame()
            
    except Exception as e:
        app.logger.error(f"Error reading plant data: {str(e)}")
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
